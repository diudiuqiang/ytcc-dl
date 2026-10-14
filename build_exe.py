#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 PyInstaller 把 ytcc_dl.py 打成单文件 exe（GUI，无控制台）。

用法：
    python build_exe.py
生成： dist/ytcc-dl.exe  （自包含 deno.exe + ffmpeg.exe，无需安装任何依赖）
"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    py = sys.executable
    # 依赖已在 .build_venv 装好，跳过重复安装；如需重装在 venv 里跑：
    # subprocess.run([py, "-m", "pip", "install", "-U", "-r",
    #                os.path.join(HERE, "requirements.txt")], check=True)

    spec_args = [
        py, "-m", "PyInstaller",
        "--name", "ytcc-dl",
        "--windowed",          # 无控制台窗口（GUI）
        "--onefile",           # 单文件 exe
        "--clean",
        "--add-binary", os.path.join(HERE, "tools", "deno.exe") + ";.",
        "--add-binary", os.path.join(HERE, "tools", "ffmpeg.exe") + ";.",
        "--collect-all", "yt_dlp",
        "--hidden-import", "cryptography",
        os.path.join(HERE, "ytcc_dl.py"),
    ]
    print(">>> 开始构建 exe（首次会解包 + 拷贝 ~185MB 二进制，稍等）…")
    rc = subprocess.run(spec_args).returncode
    if rc == 0:
        print("\n✓ 构建完成：", os.path.join(HERE, "dist", "ytcc-dl.exe"))
        print("  首次运行会自动把 tools/ 下的 deno/ffmpeg 解包到 exe 同目录。")
    else:
        print("\n✗ 构建失败，返回码", rc)
        sys.exit(rc)


if __name__ == "__main__":
    main()
