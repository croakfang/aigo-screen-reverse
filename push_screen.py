#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推流示例: 把电脑屏幕的指定区域实时推送到爱国者副屏。

用法:
    python push_screen.py [--fps 10] [--quality 85]

区域选择:
    运行后会自动截取整个主屏,缩放为 1920x462 推送。
    若想截取指定区域,可修改下方 X/Y/W/H 参数。

依赖:
    pip install pillow mss hidapi
"""

import io
import sys
import time

from PIL import Image

from aigo_screen import AigoScreen, SCREEN_W, SCREEN_H

# 截屏区域(主屏像素坐标,None 表示整屏)
CAPTURE_X = None
CAPTURE_Y = None
CAPTURE_W = None
CAPTURE_H = None


def grab_frame(sct, box) -> bytes:
    """截取屏幕区域并编码为 JPEG。"""
    shot = sct.grab(box)
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    img = img.resize((SCREEN_W, SCREEN_H))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=args_quality)
    return buf.getvalue()


def main():
    global args_quality
    fps = 10
    args_quality = 85
    for a in sys.argv[1:]:
        if a == "--fps" or a == "--quality":
            continue
    if "--fps" in sys.argv:
        fps = int(sys.argv[sys.argv.index("--fps") + 1])
    if "--quality" in sys.argv:
        args_quality = int(sys.argv[sys.argv.index("--quality") + 1])

    try:
        import mss
    except ImportError:
        print("缺少 mss 库,请先安装: pip install mss")
        sys.exit(1)

    with mss.mss() as sct:
        # 确定截屏区域
        mon = sct.monitors[1]          # 主屏
        box = {
            "left": CAPTURE_X if CAPTURE_X is not None else mon["left"],
            "top": CAPTURE_Y if CAPTURE_Y is not None else mon["top"],
            "width": CAPTURE_W if CAPTURE_W is not None else mon["width"],
            "height": CAPTURE_H if CAPTURE_H is not None else mon["height"],
        }
        print("截屏区域:", box)

        screen = AigoScreen()
        screen.open()
        props = screen.handshake()
        print("设备: %s 固件 %s" % (props.get("sn"), props["version"].get("firmware")))
        print("推流中... Ctrl+C 停止")

        interval = 1.0 / fps
        try:
            while True:
                t0 = time.time()
                jpeg = grab_frame(sct, box)
                n = screen.send_image(jpeg)
                dt = time.time() - t0
                print("\r帧号 %d: %d 块, %.1f FPS   " %
                      (screen.frame_seq - 1, n, 1.0 / dt if dt > 0 else 0), end="", flush=True)
                sleep = interval - dt
                if sleep > 0:
                    time.sleep(sleep)
        except KeyboardInterrupt:
            print("\n停止")

        screen.close()


if __name__ == "__main__":
    main()
