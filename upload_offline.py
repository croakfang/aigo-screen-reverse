#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传一次 JPG/PNG/MP4，供屏幕在通电后独立显示或循环播放。"""

import argparse
import json
import math
import sys

from aigo_screen import AigoScreen, IMG_CHUNK, validate_media_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", nargs="?", help="准备好的 .jpg/.png/.mp4 文件")
    parser.add_argument("--name", help="屏端文件名（同格式、小写扩展名，不含目录）")
    parser.add_argument("--brightness", type=int, default=100, help="亮度 0~100，默认 100")
    parser.add_argument("--block-delay", type=float, default=0.015,
                        help="块间隔秒数，默认 0.015；大文件可能上传数分钟")
    parser.add_argument("--dry-run", action="store_true", help="只检查文件，不连接或修改屏幕")
    parser.add_argument("--status", action="store_true", help="只查询 conn 状态，不上传或改设置")
    args = parser.parse_args(argv)
    if args.status and (args.media or args.dry_run or args.name):
        parser.error("--status 不能与文件、--dry-run 或 --name 同时使用")
    if not args.status and not args.media:
        parser.error("请指定媒体文件，或使用 --status")
    if not 0 <= args.brightness <= 100 or not 0 <= args.block_delay <= 1:
        parser.error("亮度应为 0~100，块间隔应为 0~1 秒")

    screen = AigoScreen()
    try:
        if not args.status:
            path, name, size, media_type = validate_media_file(args.media, args.name)
            print("文件: %s -> %s，%.2f MiB，类型 %d，%d 块" %
                  (path, name, size / 1024**2, media_type, math.ceil(size / IMG_CHUNK)))
            if args.dry_run:
                print("检查通过，未连接屏幕。编码兼容性仍需实测；推荐无音轨 H.264 MP4。")
                return 0
        screen.open()
        if args.status:
            state = json.loads(screen.request_checked("POST", "conn", 0))
            print(json.dumps(state, ensure_ascii=False, indent=2))
        else:
            def progress(index, count):
                if index % 256 == 0 or index == count:
                    print("上传: %d/%d (%.1f%%)" % (index, count, 100 * index / count), flush=True)

            result = screen.upload_offline(path, name, args.brightness, args.block_delay, progress)
            print("上传确认成功，屏端背景: %s" % result["after"].get("background"))
            print("固件: %s，space: %s（推测单位 KiB，不是总容量）" %
                  (result["after"].get("version", {}).get("firmware"), result["after"].get("space")))
            print("程序退出后不再传图或发送心跳；请观察播放/循环并验证断电再通电。")
        return 0
    except KeyboardInterrupt:
        print("上传中断，文件可能不完整。可重新上传，或启动官方软件恢复主机主题。", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print("失败: %s" % error, file=sys.stderr)
        print("上传过程中可能已改亮度/超时或写入部分媒体；未承诺自动回滚。", file=sys.stderr)
        return 1
    finally:
        screen.close()


if __name__ == "__main__":
    sys.exit(main())
