# 第三方组件与许可证说明（THIRD PARTY）

`ytcc-dl` 本身以 MIT 许可证开源，但其运行/打包依赖以下第三方组件。发布与分发时请留意各自的许可证义务（尤其是把 `ytcc-dl.exe` 作为二进制分发出去时）。

| 组件 | 用途 | 许可证 | 分发义务要点 |
|------|------|--------|--------------|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 视频元数据解析与下载核心 | **Unlicense**（公共领域） | 无强制义务；保留其 LICENSE 与 NOTICE 为佳 |
| [FFmpeg](https://ffmpeg.org/) | 音视频合并 / 转封装 / 重编码（内置） | **LGPL 2.1**（含部分 GPL 可选部件） | 若启用了 GPL 部件，整体需按 GPL 提供源码；以 LGPL 默认配置分发需保留 LGPL 文本并允许替换库 |
| [deno](https://deno.com/) | yt-dlp 2026.08+ 的 nsig/n 挑战 JS 运行时（内置） | **MIT** | 保留版权声明 |
| [PyInstaller](https://pyinstaller.org/) | 把脚本打包为单文件 exe | **GPL 2.0**（引导加载器 bootloader） | 打包出的 exe 内含 GPL 引导代码；本项目源码已开源即满足"提供对应源码"义务。若你修改 bootloader，需公开修改 |
| [cryptography](https://cryptography.io/) | CookieCloud 密文 AES 解密 | **Apache-2.0 / BSD** | 保留 NOTICE |
| [Flask](https://flask.palletsprojects.com/) | 可选 Web 服务（server.py，Docker 模式） | **BSD-3-Clause** | 保留版权声明 |
| Tkinter | 桌面 GUI | **PSF License（Python）** | 与 Python 同条款，随 Python 分发 |

## 关于「分发 exe 二进制」的额外说明

- 本项目源码（MIT）与打包进 exe 的 **PyInstaller 引导加载器（GPL 2.0）** 许可证不同。由于本项目源码已在本仓库公开，GPL 对"对应源码"的要求已满足。
- 若你对 PyInstaller bootloader 本身做了修改并重新打包，需公开该修改。
- FFmpeg 默认以 LGPL 配置编译即可满足大多数再分发场景；如需启用 GPL 部件（如某些滤镜/编解码器），则需整体按 GPL 提供源码。
- 不要在公开仓库/发布附件中附带任何**真实的登录 cookie 文件**（`*cookies.txt`）或 `.env`——它们等同你的账号凭证。

## 商标与平台 ToS

YouTube、X(Twitter)、Bilibili、抖音、微博、小红书等为其各自权利人的商标。本工具仅封装公开的下载接口，不隶属于任何平台，也不为其背书。使用时请遵守各平台服务条款与所在司法辖区法律。
