# aigo-screen-reverse

爱国者(AIGO)星璨辰屏显版机箱副屏的通讯协议说明,附独立的推图 / 推流 / 离线媒体上传示例程序。

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
- [离线资源上传](#离线资源上传)
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
| 画面 / 媒体数据 | `0x5C` | 二进制分块,用于实时 JPEG 或完整媒体文件 |

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
| `[8]` | 1 | 类型:MP4 `0x00`、JPG `0x01`、PNG `0x02` |
| `[9:24]` | 15 | 保留,全 0 |
| `[24:]` | - | 数据,每块 1000 字节,最后一块为剩余 |

### 分块规则

- JPEG 数据按 **1000 字节/块** 切分
- 每帧(108 块左右)可封装约 107KB 的 JPEG(1920×462,质量 85)
- 设备端按块号顺序重组出完整 JPEG

### 关键细节

1. **帧序号从 21 开始**(官方初始值),每发一帧递增
2. **实时推图**实测需连续发多帧(≥10 帧)才显示；离线媒体上传只发送一次完整文件
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
| `upload_offline.py` | 上传离线 JPG/PNG/MP4；屏幕自行显示，程序完成后退出 |

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

## 离线资源上传

已在 **VID 1D6B / PID 0103、固件 V1.0.12** 实测：JPG 上传后断电保存并通电自动显示；无音轨 H.264 MP4 上传后，退出上传程序仍播放，断电重启后先播原开机动画，再自动切换到视频并循环。无需官方软件常驻或电脑持续传图。它设置的是普通背景媒体，保留原开机动画。

```bash
pip install hidapi

python upload_offline.py sample.jpg
python upload_offline.py prepared.mp4 --dry-run   # 检查文件，不连接屏幕
python upload_offline.py prepared.mp4            # 上传一次，完成后退出
python upload_offline.py prepared.mp4 --name background.mp4 --brightness 100
python upload_offline.py --status                # 仅查询屏端状态
```

运行前彻底退出官方 AIGO 软件和其他推流程序。图片/视频应预先准备为屏幕尺寸；本 demo 原样上传，不缩放、不转码。实测视频为 1920×462、30 FPS、H.264 Main / yuv420p、68 秒。PNG 类型来自官方实现，尚未实测；其他视频编码、音轨、GIF 和其他固件未验证。

测试源视频约 22.2 MiB，其中大部分是 PCM 音轨。下面使用独立的 `ffmpeg` 删除音轨、字幕和数据流，复制视频流并重新封装，得到约 3.5 MiB 的上传副本，画面不重新编码。源文件保持不变。`ffmpeg` 需另外安装或指定已有可执行文件的路径，不是 Python 依赖。

```bash
ffmpeg -i "Night City Pixel Clip.mp4" -map 0:v:0 -c:v copy -an -sn -dn -map_metadata -1 -movflags +faststart prepared.mp4
python upload_offline.py prepared.mp4
```

### 上传协议与设置

```text
POST conn                                # 记录设备状态
POST power          {"event":"resume"}
POST brightness     {"value":100}        # 可用 --brightness 调整
POST timeout        {"value":0}          # 禁止超时息屏，设置会保留
POST realtimeDisplay {"enable":false}
POST osdState       {"enable":false}
POST transport      {"type":"media","fileSize":字节数,"fileName":"prepared.mp4"}
0x5C 文件分块                            # 1000 字节/块，MP4 类型 0
等待 1 200 / AckNumber=0                  # V1.0.12 媒体接收完成响应
POST transported    {"md5":"todo","fileName":"prepared.mp4"}
POST conn                                # 检查 background 中的文件名
关闭 HID 并退出                           # 不再传帧或发送心跳
```

每个控制阶段确认状态码 200 和 `AckNumber=SeqNumber+1` 后才继续。`md5:"todo"` 与官方实现一致，并非实际摘要校验。demo 只接受 JPG/PNG/MP4，拒绝 ZIP，不进入固件上传路径，不改 `mode` / `logo`。

- 默认每块间隔 15 ms，约 3.5 MiB 的视频上传耗时约一分钟；可用 `--block-delay` 调整，默认值已实测。
- demo 采用官方主机配置中的 **32 MiB 单文件上限**作为保守限制；这不是已验证的固件硬上限或总存储容量。
- `conn.space` 的实测差值支持按 KiB 估算剩余空间，没有总容量字段。demo 上传前保留估算的 4 MiB 余量。覆盖文件后空间数值可能不会立即回升，回收机制尚未确认。
- 设置和上传会改变屏端背景与亮度/超时状态。中断或失败可能留下不完整文件，不保证自动回滚或删除旧文件；可重新上传，启动官方软件可恢复主机主题。没有实现屏端媒体删除。
- 上传确认成功后仍需观察实际播放、循环以及断电再通电；其他硬件/固件行为不能由本次测试推定。

### 开发验证

```bash
python -m unittest discover -s tests -v
```

测试不连接硬件，覆盖分块格式、拆分/合并响应、ACK 匹配、失败中止和 CLI 关闭设备。`--status` 可用于真实设备的只读连通验证。

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
timeout, realtimeDisplay, osdState, mode, snSet, reboot, transport, transported
```

---

## 免责声明

本项目仅用于学习交流与个人设备使用,请勿用于商业用途。
协议实现可能随官方固件升级而变化。
