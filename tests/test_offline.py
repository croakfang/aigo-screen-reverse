import contextlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aigo_screen as protocol
import upload_offline


def response(ack, status=200, body=None):
    payload = ("1 %d\r\nAckNumber=%d\r\n" % (status, ack)).encode()
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode()
        payload += ("ContentType=json\r\nContentLength=%d\r\n" % len(data)).encode()
        payload += b"\r\n" + data
    else:
        payload += b"\r\n"
    return protocol.build_control_frame(payload)


class FakeHID:
    def __init__(self, fail_command=None, fail_blocks=False):
        self.queue = []
        self.commands = []
        self.blocks = []
        self.fail_command = fail_command
        self.fail_blocks = fail_blocks
        self.name = None
        self.completed = False
        self.closed = False

    def write(self, report):
        if len(report) != 1025 or report[0] != 0:
            raise AssertionError("Expected a full HID report with report ID 0")
        data = report[1:]
        if data[0] == 0x5A:
            payload, _ = protocol.extract_control_payload(data)
            head, _, body = payload.partition(b"\r\n\r\n")
            lines = head.decode().split("\r\n")
            method, command, version = lines[0].split()
            headers = dict(line.split("=", 1) for line in lines[1:])
            seq = int(headers["SeqNumber"])
            content = json.loads(body) if body else None
            self.commands.append((method, command, content))
            if command == "transport":
                self.name = content["fileName"]
            if command == "transported":
                if not self.blocks:
                    raise AssertionError("Finalized without media blocks")
                self.completed = True
            status = 400 if command == self.fail_command else 200
            state = None
            if command == "conn":
                state = {"version": {"firmware": "V1.0.12"}, "space": 74000,
                         "background": [self.name] if self.completed else []}
            self.queue.append(response(seq + 1, status, state))
        elif data[0] == 0x5C:
            length = struct.unpack_from(">H", data, 1)[0] + 3
            self.blocks.append(data[:length])
            total, index = struct.unpack_from(">HH", data, 4)
            if index == total - 1:
                self.queue.append(response(0, 500 if self.fail_blocks else 200))
        else:
            raise AssertionError("Unexpected frame type")
        return len(report)

    def read(self, size, timeout):
        return list(self.queue.pop(0)) if self.queue else []

    def close(self):
        self.closed = True


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media = Path(self.temp.name) / "sample.mp4"
        self.content = b"\x00\x00\x00\x18ftypisom" + bytes(range(256)) * 8
        self.media.write_bytes(self.content)

    def screen(self, device=None):
        screen = protocol.AigoScreen()
        screen.dev = device or FakeHID()
        return screen

    def test_mp4_header_matches_official_class_fixture(self):
        blocks = list(protocol.iter_media_blocks(io.BytesIO(b"x" * 1001), 1001, 0))
        self.assertEqual(blocks[0][:24].hex(), "5c03fd150002000000000000000000000000000000000000")
        self.assertEqual(len(blocks[1]), 25)
        self.assertEqual(blocks[1][6:9], b"\x00\x01\x00")

    def test_jpg_blocks_preserve_realtime_layout(self):
        content = bytes(range(256)) * 8
        self.assertEqual(list(protocol.iter_media_blocks(io.BytesIO(content), len(content), 1)),
                         protocol.build_image_blocks(content, 21))

    def test_detect_file_size_change(self):
        with self.assertRaises(ValueError):
            list(protocol.iter_media_blocks(io.BytesIO(b"x"), 2, 0))
        with self.assertRaises(ValueError):
            list(protocol.iter_media_blocks(io.BytesIO(b"xx"), 1, 0))

    def test_split_and_coalesced_responses_are_retained(self):
        # Captured V1.0.12 completion frame, including checksum 0x28.
        captured = bytes.fromhex("5a001b31203230300d0a41636b4e756d6265723d300d0a0d0a285a")
        screen = self.screen()
        screen.dev.queue = [b"\x00" + captured[:10], captured[10:] + response(8)]
        self.assertEqual(protocol.parse_response(screen._read_payload())[1]["AckNumber"], "0")
        self.assertEqual(protocol.parse_response(screen._read_payload())[1]["AckNumber"], "8")

    def test_stale_ack_is_not_accepted(self):
        screen = self.screen()
        screen.dev.queue = [response(99)]
        # FakeHID appends the actual response behind the stale response.
        state = json.loads(screen.request_checked("POST", "conn", 0))
        self.assertEqual(state["space"], 74000)

    def test_upload_full_transaction_and_reassemble_file(self):
        screen = self.screen()
        result = screen.upload_offline(self.media, block_delay=0)
        self.assertEqual([command for _, command, _ in screen.dev.commands],
                         ["conn", "power", "brightness", "timeout", "realtimeDisplay",
                          "osdState", "transport", "transported", "conn"])
        self.assertEqual(screen.dev.commands[6][2],
                         {"type": "media", "fileSize": len(self.content), "fileName": "sample.mp4"})
        self.assertEqual(b"".join(block[24:] for block in screen.dev.blocks), self.content)
        self.assertTrue(all(block[8] == 0 for block in screen.dev.blocks))
        self.assertEqual(result["after"]["background"], ["sample.mp4"])

    def test_rejection_stops_before_media_blocks(self):
        screen = self.screen(FakeHID(fail_command="transport"))
        with self.assertRaises(RuntimeError):
            screen.upload_offline(self.media, block_delay=0)
        self.assertFalse(screen.dev.blocks)
        self.assertNotIn("transported", [c for _, c, _ in screen.dev.commands])

    def test_failed_completion_is_not_finalized(self):
        screen = self.screen(FakeHID(fail_blocks=True))
        with self.assertRaises(RuntimeError):
            screen.upload_offline(self.media, block_delay=0)
        self.assertNotIn("transported", [c for _, c, _ in screen.dev.commands])

    def test_unsafe_files_are_rejected_before_device_write(self):
        screen = self.screen()
        for name in ("firmware.zip", "../sample.mp4", "sample.jpg", "sample.MP4"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                screen.upload_offline(self.media, device_name=name)
        self.assertFalse(screen.dev.commands)
        firmware = Path(self.temp.name) / "firmware.zip"
        firmware.write_bytes(b"PK")
        with self.assertRaises(ValueError):
            protocol.validate_media_file(firmware)

    def test_dry_run_does_not_open_device(self):
        with patch.object(protocol.AigoScreen, "open") as opened, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(upload_offline.main([str(self.media), "--dry-run"]), 0)
        opened.assert_not_called()

    def test_status_does_not_modify_settings(self):
        screen = self.screen()
        device = screen.dev
        with patch.object(upload_offline, "AigoScreen", return_value=screen), \
                patch.object(screen, "open"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(upload_offline.main(["--status"]), 0)
        self.assertEqual([c for _, c, _ in device.commands], ["conn"])
        self.assertTrue(device.closed)

    def test_cli_closes_device_on_failure(self):
        screen = self.screen(FakeHID(fail_command="transport"))
        device = screen.dev
        with patch.object(upload_offline, "AigoScreen", return_value=screen), \
                patch.object(screen, "open"), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(upload_offline.main([str(self.media), "--block-delay", "0"]), 1)
        self.assertTrue(device.closed)


if __name__ == "__main__":
    unittest.main()
