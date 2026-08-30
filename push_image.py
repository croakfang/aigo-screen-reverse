#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推图示例: 把一张本地图片推送到爱国者副屏。

用法:
    python push_image.py <图片路径>
    python push_image.py <图片路径> --loop   # 循环推送

示例:
    python push_image.py sample.png
"""

import io
import sys
import time

from PIL import Image

from aigo_screen import AigoScreen, SCREEN_W, SCREEN_H, JPEG_QUALITY


def load_image(path: str) -> bytes:
    """加载图片,缩放到屏幕尺寸 1920x462,编码为 JPEG。"""
    img = Image.open(path).convert("RGB")
    # 直接拉伸铺满屏幕(若需保持比例留边,请自行调整)
    img = img.resize((SCREEN_W, SCREEN_H))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def main():
    if len(sys.argv) < 2:
        print("用法: python push_image.py <图片路径> [--loop]")
        sys.exit(1)

    img_path = sys.argv[1]
    loop = "--loop" in sys.argv

    jpeg = load_image(img_path)
    print("JPEG 大小: %d 字节" % len(jpeg))

    screen = AigoScreen()
    screen.open()
    props = screen.handshake()
    print("设备: %s 固件 %s" % (props.get("sn"), props["version"].get("firmware")))

    # 设备需要连续接收多帧才会显示,至少发 10 帧
    if loop:
        count = None
    else:
        count = 10

    sent = 0
    try:
        while count is None or sent < count:
            n = screen.send_image(jpeg)
            print("帧号 %d: %d 块" % (screen.frame_seq - 1, n))
            sent += 1
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("中断")

    screen.close()
    print("完成")


if __name__ == "__main__":
    main()
