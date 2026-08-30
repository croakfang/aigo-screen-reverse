# aigo-screen-reverse

Communication protocol documentation and standalone image / stream pushing examples for the AIGO (Aigo) Star Canopy chassis secondary display.

> For personal use and learning only. Device and brand names belong to their respective owners.
>
> 中文版: [README.md](./README.md)

---

## Table of Contents

- [Device Info](#device-info)
- [Protocol Overview](#protocol-overview)
- [0x5A Control Frame (text protocol)](#0x5a-control-frame-text-protocol)
- [0x5C Image Frame (binary chunks)](#0x5c-image-frame-binary-chunks)
- [Handshake](#handshake)
- [Examples](#examples)
- [Installation](#installation)
- [Known Commands](#known-commands)

---

## Device Info

| Item | Value |
|------|-------|
| Product | AIGO Star Canopy chassis secondary display |
| USB type | HID device |
| VID / PID | `0x1D6B` (7531) / `0x0103` (259) |
| Vendor / Product | Aigo Inc. / Aigo USB Device |
| HID usagePage / usage | `0xFF00` / `1` |
| HID report size | 1025 bytes (1 byte report_id + 1024 bytes data) |
| Screen resolution | 1920 × 462 |
| Device OS | Linux (the `conn` response returns `"OS":"Linux"`) |
| Firmware | V1.0.12 (tested unit) |

> Note: the display is a **HID** device, not a serial port. The `serialport`
> module in the official software belongs to a different CDC device
> (VID_1B3F PID_0123) and is unrelated to the display.

---

## Protocol Overview

There are **two frame formats**:

| Purpose | Frame head | Description |
|---------|-----------|-------------|
| Control / handshake | `0x5A` | Text protocol with byte escaping and checksum |
| Image data | `0x5C` | Binary chunked, carries JPEG |

HID layer: every `write` starts with a report_id byte `0x00`, followed by the
full frame. When reading, strip the leading `0x00` before parsing.

---

## 0x5A Control Frame (text protocol)

### Frame structure

```
0x5A + lenfield(2 bytes big-endian) + escape(payload + checksum) + 0x5A
```

- `lenfield = len(payload) + 5` (logical length before escaping)
- `checksum = (sum(lenfield two bytes) + sum(payload)) & 0xFF`
- Byte escaping: `0x5A -> 5B 01`, `0x5B -> 5B 02`
  - Escaping guarantees no raw `0x5A` inside the frame, so the receiver finds
    the frame tail by scanning for a raw `0x5A`.
  - The `checksum` byte is also escaped (e.g. checksum `0x5B` is written as `5B 02`).

### Request format (payload text)

```
<METHOD> <cmd> 1\r\n
SeqNumber=N\r\n
Date=<milliseconds>\r\n
[ContentType=json\r\n
ContentLength=N\r\n]\r\n
<body>
```

- `METHOD`: `POST` / `STATE`
- `ContentType=json` and `ContentLength` are present when there is a JSON body.

### Response format

```
<version> 200\r\n
AckNumber=N\r\n
[ContentType=json\r\n
ContentLength=N\r\n]\r\n
<body>
```

- The first line is `1 200` (version=1, status code 200).
- `AckNumber` corresponds to the request `SeqNumber` (usually Ack = Seq + 1).

---

## 0x5C Image Frame (binary chunks)

### Frame structure (24-byte header + data)

| Offset | Length | Meaning |
|--------|--------|---------|
| `[0]` | 1 | Frame head `0x5C` |
| `[1:3]` | 2 | Frame length - 3 (big-endian) |
| `[3]` | 1 | Frame sequence number (starts at 21, low 8 bits, increments) |
| `[4:6]` | 2 | Total block count (big-endian) |
| `[6:8]` | 2 | Current block index, 0-based (big-endian) |
| `[8]` | 1 | Type `0x01` |
| `[9:24]` | 15 | Reserved, all zero |
| `[24:]` | - | Data, 1000 bytes per block, last block is the remainder |

### Chunking rules

- JPEG data is split into **1000-byte blocks**.
- One frame (~108 blocks) carries about 107KB of JPEG (1920×462, quality 85).
- The device reassembles the full JPEG in block order.

### Key details

1. **Frame sequence number starts at 21** (official initial value) and
   increments per frame.
2. **You must send multiple frames (>=10) in a row** for the device to display —
   sending a single frame results in a black screen.
3. Image frames have **no escaping and no checksum**; they are pure binary chunks.

---

## Handshake

```
1. POST conn 1            (Seq=0)  -> returns device properties JSON
2. POST power 1 resume    (Seq=1)  -> wakes the device (easy to miss!)
3. STATE all 1 heartbeat  (Seq=2)  -> heartbeat
4. POST realtimeDisplay 1 (Seq=3)  -> enable realtime transport
```

### Example `conn` response (device properties)

```json
{
  "OS": "Linux",
  "version": {
    "app": "V1.0.12",
    "firmware": "V1.0.12",
    "sdk": "V1.2.7",
    "hardware": "V1.0"
  },
  "space": 74180,
  "brightness": 100,
  "degree": 270,
  "sn": "BYZL2625WC01CM001444"
}
```

---

## Examples

| File | Description |
|------|-------------|
| `aigo_screen.py` | Protocol core library (frame building / parsing / device wrapper) |
| `push_image.py` | Push image: send a local image to the display |
| `push_screen.py` | Push stream: stream a screen region to the display in real time |

### push_image.py

```bash
pip install hidapi pillow

python push_image.py sample.png          # push one image (sends 10 frames)
python push_image.py sample.png --loop   # push in a loop
```

### push_screen.py

```bash
pip install hidapi pillow mss

python push_screen.py              # default 10 FPS
python push_screen.py --fps 15     # 15 FPS
python push_screen.py --quality 90 # JPEG quality 90
```

> The capture region defaults to the whole primary monitor. Edit
> `CAPTURE_X/Y/W/H` at the top of `push_screen.py` to capture a specific region.

---

## Installation

### Requirements

- Python 3.8+
- Windows (the examples are implemented for Windows; `hidapi` is cross-platform)

### Install dependencies

```bash
pip install hidapi pillow
# for streaming, additionally:
pip install mss
```

### Before running

1. **Quit the official AIGO software** (it holds the HID device exclusively).
2. Make sure the display is connected over USB.

---

## Known Commands

Device commands extracted from the bytecode constant table:

```
conn, waterBlockScreen, waterBlockScreenId, brightness, rotate, recovery,
sysinfoDisplay, displayInSleep, fanLCDSet, mediaDelete, config, power,
timeout, realtimeDisplay, mode, snSet, reboot
```

---

## Disclaimer

This project is for learning and personal device use only. Do not use it for
commercial purposes. The protocol implementation may change with official
firmware updates.
