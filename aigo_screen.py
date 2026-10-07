#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爱国者(AIGO)星璨辰屏显版副屏 HID 通讯协议核心库。

本模块实现了副屏的完整通讯协议:
  1. 0x5A 控制帧(文本协议): 握手、设备控制
  2. 0x5C 画面帧(二进制分块): JPEG 图片推流
  3. 普通媒体上传: JPG/PNG/MP4 背景，完成后由屏幕本地显示

设备信息:
  - USB HID 设备: VID 0x1D6B (7531), PID 0x0103 (259)
  - 厂商 Aigo Inc., 产品 Aigo USB Device
  - 屏幕分辨率 1920 x 462
  - 设备内部为 Linux 系统

依赖:
  pip install hidapi pillow
"""

import json
import math
import struct
import time
from pathlib import Path

import hid

# ---------- 设备常量 ----------
VID = 0x1D6B
PID = 0x0103
REPORT_SIZE = 1025          # HID 报告: report_id(1) + 1024 字节数据
SCREEN_W = 1920
SCREEN_H = 462
JPEG_QUALITY = 85
IMG_CHUNK = 1000            # 画面帧每块数据字节数
FRAME_HEADER_SIZE = 24      # 画面帧头字节数
MAX_OFFLINE_FILE_SIZE = 32 * 1024 * 1024  # 官方主机配置值，作为 demo 的保守上限
MEDIA_TYPES = {".mp4": 0, ".jpg": 1, ".png": 2}


# =====================================================================
# 0x5A 控制帧(文本协议)
# =====================================================================

def _escape(b: bytes) -> bytes:
    """字节转义: 0x5A -> 5B 01, 0x5B -> 5B 02。
    保证帧内不出现裸 0x5A,帧尾靠扫描裸 0x5A 判定。"""
    out = bytearray()
    for x in b:
        if x == 0x5A:
            out += b"\x5b\x01"
        elif x == 0x5B:
            out += b"\x5b\x02"
        else:
            out.append(x)
    return bytes(out)


def _unescape(b: bytes) -> bytes:
    """反转义: 5B 01 -> 5A, 5B 02 -> 5B。"""
    out = bytearray()
    i = 0
    while i < len(b):
        if b[i] == 0x5B and i + 1 < len(b) and b[i + 1] in (0x01, 0x02):
            out.append(0x5A if b[i + 1] == 0x01 else 0x5B)
            i += 2
        else:
            out.append(b[i])
            i += 1
    return bytes(out)


def build_control_frame(payload: bytes) -> bytes:
    """
    构造 0x5A 控制帧。

    帧结构:
        0x5A + lenfield(2B 大端) + escape(payload + checksum) + 0x5A
        lenfield = len(payload) + 5
        checksum = (sum(lenfield) + sum(payload)) & 0xFF
    """
    len_field = struct.pack(">H", len(payload) + 5)
    checksum = (sum(len_field) + sum(payload)) & 0xFF
    return b"\x5a" + len_field + _escape(payload + bytes([checksum])) + b"\x5a"


def build_request(method: str, cmd: str, seq: int, body=None) -> bytes:
    """
    构造控制命令 payload。

    请求格式:
        <METHOD> <cmd> 1\\r\\n
        SeqNumber=N\\r\\n
        Date=<毫秒时间戳>\\r\\n
        [ContentType=json\\r\\n
         ContentLength=N\\r\\n]\\r\\n
        <body>

    method: POST / STATE
    """
    payload = ("%s %s 1\r\nSeqNumber=%d\r\nDate=%d\r\n" %
               (method, cmd, seq, int(time.time() * 1000))).encode("ascii")
    if body is not None:
        b = json.dumps(body, separators=(",", ":")).encode("ascii")
        payload += ("ContentType=json\r\nContentLength=%d\r\n" % len(b)).encode("ascii")
        payload += b"\r\n" + b
    else:
        payload += b"\r\n"
    return payload


def extract_control_payload(buf: bytes):
    """
    从字节流提取一个完整的 0x5A 控制帧 payload。
    返回 (payload, 剩余字节)。数据不足时返回 (None, buf)。
    """
    if not buf:
        return None, buf
    if buf[0] != 0x5A:
        i = buf.find(b"\x5a")
        if i < 0:
            return None, b""
        buf = buf[i:]
    if len(buf) < 4:
        return None, buf
    len_field = buf[1:3]
    i = buf.find(b"\x5a", 3)          # 帧尾是下一个裸 0x5A
    if i < 0:
        return None, buf
    raw = _unescape(buf[3:i])
    if not raw:
        return extract_control_payload(buf[i + 1:])
    payload = raw[:-1]
    checksum = raw[-1]
    if (sum(len_field) + sum(payload)) & 0xFF != checksum:
        return extract_control_payload(buf[1:])
    return payload, buf[i + 1:]


def parse_response(payload: bytes):
    """解析已校验控制帧中的状态码、headers、原始 body。"""
    head, separator, body = payload.partition(b"\r\n\r\n")
    if not separator:
        raise ValueError("响应缺少 header/body 分隔符")
    lines = head.decode("ascii").split("\r\n")
    version, status = lines[0].split()
    if version != "1":
        raise ValueError("不支持的协议版本: " + version)
    headers = dict(line.split("=", 1) for line in lines[1:] if line)
    if "ContentLength" in headers and int(headers["ContentLength"]) != len(body):
        raise ValueError("响应 ContentLength 不匹配")
    return int(status), headers, body


def validate_media_file(path, device_name=None):
    """离线上传只允许普通 JPG/PNG/MP4，不进入 ZIP 固件上传路径。"""
    path = Path(path)
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise ValueError("仅支持 .jpg、.png、.mp4 普通媒体文件")
    size = path.stat().st_size
    if not path.is_file() or not 0 < size <= MAX_OFFLINE_FILE_SIZE:
        raise ValueError("媒体必须是非空文件，且不超过 demo 的 32 MiB 上限")
    name = device_name if device_name is not None else path.stem + path.suffix.lower()
    if (not name or any(c in name for c in "/\\\x00\r\n")
            or len(name.encode("utf-8")) > 255
            or Path(name).suffix not in MEDIA_TYPES
            or MEDIA_TYPES[Path(name).suffix] != media_type):
        raise ValueError("屏端文件名须为同格式的小写扩展名，不能含目录或控制字符")
    with path.open("rb") as source:
        signature = source.read(12)
    valid = (signature.startswith(b"\xff\xd8") if media_type == 1 else
             signature.startswith(b"\x89PNG\r\n\x1a\n") if media_type == 2 else
             signature[4:8] == b"ftyp")
    if not valid:
        raise ValueError("文件签名与扩展名不匹配")
    return path, name, size, media_type


def iter_media_blocks(source, file_size, media_type, frame_seq=21):
    """从文件流生成 0x5C 块；MP4=0、JPG=1、PNG=2，每块 1000 字节。"""
    count = math.ceil(file_size / IMG_CHUNK)
    if not 0 < count <= 65535 or media_type not in MEDIA_TYPES.values():
        raise ValueError("无效的媒体大小或类型")
    for index in range(count):
        chunk = source.read(min(IMG_CHUNK, file_size - index * IMG_CHUNK))
        expected = min(IMG_CHUNK, file_size - index * IMG_CHUNK)
        if len(chunk) != expected:
            raise ValueError("上传文件读取不足，文件可能已改变")
        header = struct.pack(">BHBHHB15x", 0x5C, 21 + len(chunk),
                             frame_seq & 0xFF, count, index, media_type)
        yield header + chunk
    if source.read(1):
        raise ValueError("上传文件大小已改变")


# =====================================================================
# 0x5C 画面帧(二进制分块,JPEG)
# =====================================================================

def build_image_blocks(jpeg: bytes, frame_seq: int):
    """
    把一张 JPEG 拆成 0x5C 分块帧列表。

    画面帧结构(24 字节头 + 每块 1000 字节数据):
        [0]    0x5C      帧头
        [1:3]  帧长-3    2 字节大端
        [3]    帧序号    1 字节(从 21 开始递增)
        [4:6]  总块数    2 字节大端
        [6:8]  当前块号  2 字节大端(0-based)
        [8]    0x01      类型
        [9:24] 15 字节 0 保留
        [24:]  数据      每块 1000 字节,最后一块为剩余
    """
    n = (len(jpeg) + IMG_CHUNK - 1) // IMG_CHUNK
    blocks = []
    for i in range(n):
        data = jpeg[i * IMG_CHUNK:(i + 1) * IMG_CHUNK]
        frame_len = FRAME_HEADER_SIZE + len(data)
        header = bytes([
            0x5C,
            (frame_len - 3) >> 8,
            (frame_len - 3) & 0xFF,
            frame_seq & 0xFF,
            n >> 8,
            n & 0xFF,
            i >> 8,
            i & 0xFF,
            0x01,
        ])
        header += b"\x00" * 15
        blocks.append(header + data)
    return blocks


# =====================================================================
# 设备操作
# =====================================================================

class AigoScreen:
    """副屏设备封装。"""

    def __init__(self):
        self.dev = None
        self.frame_seq = 21          # 官方初始帧号
        self._read_buffer = b""

    def open(self):
        for d in hid.enumerate(VID, PID):
            if d["usage_page"] in (0xFF00, 65280):
                self.dev = hid.device()
                self.dev.open_path(d["path"])
                return
        self.dev = hid.device()
        self.dev.open(VID, PID)

    def close(self):
        if self.dev:
            self.dev.close()
            self.dev = None
        self._read_buffer = b""

    def _write(self, data: bytes):
        """HID 写入,首字节为 report id 0x00。"""
        if len(data) > REPORT_SIZE - 1:
            raise ValueError("单帧超过 HID 报告大小")
        report = b"\x00" + data.ljust(REPORT_SIZE - 1, b"\x00")
        if self.dev.write(report) != REPORT_SIZE:
            raise OSError("HID 写入不完整")

    def _read_payload(self, timeout=3.0):
        """读取一个完整的 0x5A 响应 payload。"""
        end = time.monotonic() + timeout
        while True:
            p, self._read_buffer = extract_control_payload(self._read_buffer)
            if p is not None:
                return p
            remaining = end - time.monotonic()
            if remaining <= 0:
                return None
            d = bytes(self.dev.read(REPORT_SIZE, max(1, min(200, int(remaining * 1000)))))
            if d:
                if d[0] == 0x00:
                    d = d[1:]
                self._read_buffer += d

    def request(self, method: str, cmd: str, seq: int, body=None):
        """发送控制命令并读取响应 payload。"""
        self._write(build_control_frame(build_request(method, cmd, seq, body)))
        return self._read_payload()

    def _wait_success(self, expected_ack, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            payload = self._read_payload(end - time.monotonic())
            if payload is None:
                break
            status, headers, body = parse_response(payload)
            if status != 200:
                raise RuntimeError("设备返回状态码 %d (AckNumber=%s)" %
                                   (status, headers.get("AckNumber")))
            if int(headers.get("AckNumber", -1)) == expected_ack:
                return body
        raise TimeoutError("等待 AckNumber=%d 超时；请确认官方软件已完全退出" % expected_ack)

    def request_checked(self, method, cmd, seq, body=None, timeout=3.0):
        """确认状态码 200 及 AckNumber=SeqNumber+1，返回原始响应 body。"""
        self._write(build_control_frame(build_request(method, cmd, seq, body)))
        return self._wait_success(seq + 1, timeout)

    def upload_offline(self, path, device_name=None, brightness=100,
                       block_delay=0.015, progress=None):
        """普通媒体上传；成功后设备本地显示，调用者应关闭 HID 后退出。

        JPG 与无音轨 H.264 MP4 已在 V1.0.12 验证。PNG 类型来自官方实现。
        修改 brightness、timeout、realtimeDisplay、osdState，不改 mode/logo。
        失败不保证回滚已写入的媒体；重新运行官方软件可恢复主机主题。
        """
        path, name, size, media_type = validate_media_file(path, device_name)
        if not 0 <= brightness <= 100 or not 0 <= block_delay <= 1:
            raise ValueError("亮度应为 0~100，块间隔应为 0~1 秒")
        seq = 0

        def post(command, body=None, timeout=3.0):
            nonlocal seq
            result = self.request_checked("POST", command, seq, body, timeout)
            seq += 1
            return result

        before = json.loads(post("conn"))
        space = before.get("space")
        # KiB 是实测差值支持的单位推断，不是固件声明的总容量。
        if isinstance(space, (int, float)) and space * 1024 < size + 4 * 1024 * 1024:
            raise ValueError("报告的可用空间不足以存放文件及 4 MiB 余量（space 按 KiB 估算）")
        with path.open("rb") as source:
            post("power", {"event": "resume"})
            post("brightness", {"value": brightness})
            post("timeout", {"value": 0})
            post("realtimeDisplay", {"enable": False})
            post("osdState", {"enable": False})
            post("transport", {"type": "media", "fileSize": size, "fileName": name})
            count = math.ceil(size / IMG_CHUNK)
            for index, block in enumerate(iter_media_blocks(source, size, media_type), 1):
                self._write(block)
                # 15 ms 默认间隔与已经验证的上传工具一致。
                if index < count and block_delay:
                    time.sleep(block_delay)
                if progress:
                    progress(index, count)
            # V1.0.12 的媒体块完成响应为 1 200 / AckNumber=0。
            self._wait_success(0, 5.0)
            post("transported", {"md5": "todo", "fileName": name}, timeout=10.0)
        after = json.loads(post("conn"))
        if name not in after.get("background", []):
            raise RuntimeError("上传已确认，但屏端 background 列表中没有目标文件")
        return {"fileName": name, "fileSize": size, "before": before, "after": after}

    def handshake(self):
        """
        完整握手流程,返回设备属性 dict。

        1. POST conn 1              -> 设备属性 JSON
        2. POST power 1 resume      -> 唤醒设备
        3. STATE all 1 heartbeat    -> 心跳
        4. POST realtimeDisplay 1   -> 开启实时传输
        """
        p = self.request("POST", "conn", 0)
        if p is None:
            raise RuntimeError("conn 无响应,请确认副屏已连接且官方软件已退出")
        body = p.decode("ascii", errors="replace").partition("\r\n\r\n")[2]
        props = json.loads(body)

        self.request("POST", "power", 1, {"event": "resume"})
        self.request("STATE", "all", 2, {"heartbeat": 1})
        self.request("POST", "realtimeDisplay", 3, {"enable": True})
        return props

    def send_image(self, jpeg: bytes):
        """发送一张 JPEG 画面(完整分块)。"""
        blocks = build_image_blocks(jpeg, self.frame_seq)
        for blk in blocks:
            self._write(blk)
        self.frame_seq = (self.frame_seq + 1) & 0xFF
        return len(blocks)
