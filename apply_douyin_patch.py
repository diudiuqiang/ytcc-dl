#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把抖音签名补丁打进 .build_venv 的 yt_dlp/extractor/tiktok.py。

背景：抖音详情接口要求 x-secsdk-web-signature 签名，上游 yt-dlp 未实现，
一律报误导性的 "Fresh cookies needed"。补丁来自 viking1304/yt-dlp@douyin-fix
分支（仅替换 DouyinIE 的 _real_extract）。

用法：
    python apply_douyin_patch.py            # 应用（自动备份原版为 .orig）
    python apply_douyin_patch.py --status   # 查看当前状态
    python apply_douyin_patch.py --revert   # 恢复原版

⚠ 每次 pip install -U yt-dlp / -r requirements.txt 后都要重新执行本脚本，再 build_exe.py。
"""
import os, sys, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, ".build_venv", "Lib", "site-packages", "yt_dlp", "extractor", "tiktok.py")
PATCH = os.path.join(HERE, "patches", "tiktok_douyin_fix.py")
BACKUP = TARGET + ".orig"
MARK = "x-secsdk-web-signature"


def is_patched(path):
    try:
        with open(path, encoding="utf-8") as f:
            return MARK in f.read()
    except OSError:
        return False


def main():
    if not os.path.exists(TARGET):
        print("✗ 找不到目标:", TARGET, "（请先在 .build_venv 装好 yt-dlp）")
        return 2
    if "--status" in sys.argv:
        print("tiktok.py:", TARGET)
        print("  当前:", "已打补丁" if is_patched(TARGET) else "原版（未打补丁）")
        print("  备份:", "存在" if os.path.exists(BACKUP) else "无")
        return 0
    if "--revert" in sys.argv:
        if not os.path.exists(BACKUP):
            print("✗ 无备份文件，无法恢复:", BACKUP)
            return 2
        shutil.copy2(BACKUP, TARGET)
        print("✓ 已恢复原版 tiktok.py")
        return 0

    if is_patched(TARGET):
        print("✓ tiktok.py 已是补丁版，无需重复应用")
        return 0
    if not os.path.exists(BACKUP):
        shutil.copy2(TARGET, BACKUP)
        print("✓ 已备份原版 →", os.path.basename(BACKUP))
    shutil.copy2(PATCH, TARGET)
    print("✓ 抖音签名补丁已应用到 .build_venv")
    print("  下一步: python build_exe.py（重新打包 exe）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
