# aigo-screen-reverse

爱国者(AIGO)星璨辰屏显版机箱副屏的通讯协议说明,附独立的推图 / 推流示例程序。

> 仅用于学习与个人设备使用。设备、品牌名称等归原权利人所有。

> English version: [README_EN.md](./README_EN.md)

---

## 目录

- [设备信息](#设备信息)
- [协议总览](#协议总览)
- [0x5A 控制帧](#0x5a-控制帧文本协议)
- [0x5C 画面帧](#0x5c-画面帧二进制分块)
- [握手流程](#握手流程)
- [示例程序](#示例程序)
- [安装与运行](#安装与运行)
- [已知命令列表](#已知命令列表)

---

## 设备信息

| 项目 | 值 |
|------|-----|
| 产品 | 爱国者 AIGO 星璨辰屏显版(机箱副屏) |
| USB 类型 | HID 设备 |
| VID / PID | `0x1D6B` (7531) / `0x0103` (259) |
| 厂商 / 产品 | Aigo Inc. / Aigo USB Device |
| HID usagePage / usage | `0xFF00` / `1` |
| HID 报告大小 | 1025 字节(1 字节 report_id + 1024 字节数据) |
| 屏幕分辨率 | 1920 × 462 |
| 设备系统 | Linux(conn 响应返回 `"OS":"Linux"`) |
| 固件版本 | V1.0.12(实测样机) |

> 注意:副屏是 **HID** 设备,不是串口。官方软件里 `serialport` 模块对应的是
> 另一台 CDC 设备(VID_1B3F PID_0123),与副屏无关。

---

## 协议总览

通讯分**两套帧格式**:

| 用途 | 帧头 | 说明 |
|------|------|------|
| 控制命令 / 握手 | `0x5A` | 文本协议,带字节转义和校验 |
| 画面数据 | `0x5C` | 二进制分块,封装 JPEG |

HID 层:每次 `write` 首字节为 report_id `0x00`,后跟完整帧;
读取同理,剥掉首字节 `0x00` 后再解析帧。

---

## 0x5A 控制帧(文本协议)

### 帧结构

```
0x5A + lenfield(2 字节大端) + escape(payload + checksum) + 0x5A
```

- `lenfield = len(payload) + 5`(未转义的逻辑长度)
- `checksum = (sum(lenfield 两个字节) + sum(payload)) & 0xFF`
- 字节转义:`0x5A -> 5B 01`,`0x5B -> 5B 02`
  - 转义保证帧内不出现裸 `0x5A`,接收端靠扫描裸 `0x5A` 判定帧尾
  - `checksum` 本身也参与转义(例:checksum=0x5B 会写成 `5B 02`)

### 请求格式(payload 文本)

```
<METHOD> <cmd> 1\r\n
SeqNumber=N\r\n
Date=<毫秒时间戳>\r\n
[ContentType=json\r\n
ContentLength=N\r\n]\r\n
<body>
```

- `METHOD`:`POST` / `STATE`
- 有 JSON body 时携带 `ContentType=json` 和 `ContentLength`

### 响应格式

```
<版本> 200\r\n
AckNumber=N\r\n
[ContentType=json\r\n
ContentLength=N\r\n]\r\n
<body>
```

- 首行是 `1 200`(版本=1,状态码 200)
- `AckNumber` 与请求的 `SeqNumber` 对应(通常 Ack = Seq + 1)

---

## 0x5C 画面帧(二进制分块)

### 帧结构(24 字节头 + 数据)

| 偏移 | 长度 | 含义 |
|------|------|------|
| `[0]` | 1 | 帧头 `0x5C` |
| `[1:3]` | 2 | 帧长 - 3(大端) |
| `[3]` | 1 | 帧序号(从 21 开始递增,低 8 位) |
| `[4:6]` | 2 | 总块数(大端) |
| `[6:8]` | 2 | 当前块号,0-based(大端) |
| `[8]` | 1 | 类型 `0x01` |
| `[9:24]` | 15 | 保留,全 0 |
| `[24:]` | - | 数据,每块 1000 字节,最后一块为剩余 |

### 分块规则

- JPEG 数据按 **1000 字节/块** 切分
- 每帧(108 块左右)可封装约 107KB 的 JPEG(1920×462,质量 85)
- 设备端按块号顺序重组出完整 JPEG

### 关键细节

1. **帧序号从 21 开始**(官方初始值),每发一帧递增
2. **必须连续发多帧(≥10 帧)设备才会显示** —— 只发一帧是黑屏
3. 画面帧**无转义、无校验**,纯二进制分块

---

## 握手流程

```
1. POST conn 1            (Seq=0)  -> 返回设备属性 JSON
2. POST power 1 resume    (Seq=1)  -> 唤醒设备(易漏!)
3. STATE all 1 heartbeat  (Seq=2)  -> 心跳
4. POST realtimeDisplay 1 (Seq=3)  -> 开启实时传输
```

### conn 响应示例(设备属性)

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

## 示例程序

| 文件 | 说明 |
|------|------|
| `aigo_screen.py` | 协议核心库(帧构造 / 解析 / 设备封装) |
| `push_image.py` | 推图:把本地图片推送到副屏 |
| `push_screen.py` | 推流:把电脑屏幕区域实时推送到副屏 |

### push_image.py 用法

```bash
pip install hidapi pillow

python push_image.py sample.png          # 推一张图(自动连发 10 帧)
python push_image.py sample.png --loop   # 循环推送
```

### push_screen.py 用法

```bash
pip install hidapi pillow mss

python push_screen.py              # 默认 10 FPS
python push_screen.py --fps 15     # 15 FPS
python push_screen.py --quality 90 # JPEG 质量 90
```

> 截屏区域默认是整个主屏,可在 `push_screen.py` 顶部修改
> `CAPTURE_X/Y/W/H` 指定区域。

---

## 安装与运行

### 环境要求

- Python 3.8+
- Windows(示例基于 Windows 实现,`hidapi` 跨平台可用)

### 安装依赖

```bash
pip install hidapi pillow
# 推流额外需要:
pip install mss
```

### 运行前注意

1. **关闭官方 AIGO 软件**(它独占 HID 设备)
2. 确认副屏通过 USB 连接

---

## 已知命令列表

从字节码常量表提取的设备命令:

```
conn, waterBlockScreen, waterBlockScreenId, brightness, rotate, recovery,
sysinfoDisplay, displayInSleep, fanLCDSet, mediaDelete, config, power,
timeout, realtimeDisplay, mode, snSet, reboot
```

---

## 免责声明

本项目仅用于学习交流与个人设备使用,请勿用于商业用途。
协议实现可能随官方固件升级而变化。
