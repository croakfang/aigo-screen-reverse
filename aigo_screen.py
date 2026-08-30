#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爱国者(AIGO)星璨辰屏显版副屏 HID 通讯协议核心库。

本模块实现了副屏的完整通讯协议:
  1. 0x5A 控制帧(文本协议): 握手、设备控制
  2. 0x5C 画面帧(二进制分块): JPEG 图片推流

设备信息:
  - USB HID 设备: VID 0x1D6B (7531), PID 0x0103 (259)
  - 厂商 Aigo Inc., 产品 Aigo USB Device
  - 屏幕分辨率 1920 x 462
  - 设备内部为 Linux 系统

依赖:
  pip install hidapi pillow
"""

import json
import struct
import time

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
    payload = raw[:-1]
    checksum = raw[-1]
    if (sum(len_field) + sum(payload)) & 0xFF != checksum:
        return extract_control_payload(buf[1:])
    return payload, buf[i + 1:]


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

    def _write(self, data: bytes):
        """HID 写入,首字节为 report id 0x00。"""
        self.dev.write(b"\x00" + data)

    def _read_payload(self, timeout=3.0):
        """读取一个完整的 0x5A 响应 payload。"""
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            d = bytes(self.dev.read(REPORT_SIZE, 200))
            if d:
                if d[0] == 0x00:
                    d = d[1:]
                buf += d
                p, rest = extract_control_payload(buf)
                if p is not None:
                    return p
                buf = rest if rest else b""
        return None

    def request(self, method: str, cmd: str, seq: int, body=None):
        """发送控制命令并读取响应 payload。"""
        self._write(build_control_frame(build_request(method, cmd, seq, body)))
        return self._read_payload()

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
