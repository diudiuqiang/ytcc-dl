# ytcc-dl — CookieCloud 视频下载器（独立 exe 版）

把「从自托管的 **CookieCloud** 读取会话数据 → **yt-dlp 2026.08 三件套**下载 →（可选）mp4 转封装」整套链路，**封装成一个带界面的单文件 exe**。无需 Python 环境，双击即用。

支持 **YouTube / X(Twitter) / B站 / 抖音 / 微博 / 小红书** 六大平台，并自动解析各平台的短链（`t.cn`、`v.douyin.com`、`xhslink.com` / `xhslink.cn`）。

## 解决了什么

多平台下载在工程上有几个常见的兼容问题，这套工具把它们集中处理掉：

1. **Edge/Chrome 默认 App-Bound Encryption (ABE)**：新版浏览器不再允许外部程序离线读取本地 cookie 数据库（`yt-dlp` 会报 `Failed to decrypt with DPAPI`）。
   → 改用 **CookieCloud**（浏览器扩展把你自己的会话数据同步到你自托管的服务器），由工具从服务端取回密文、本地 AES 解密，**不再依赖读取浏览器本地 cookie 数据库**。
2. **yt-dlp 2026.08+ 三件套缺一不可**：
   - 代理必须显式传（沙箱默认 63105 是死代理）→ 本工具默认 `http://127.0.0.1:10808`；
   - nsig 解算需要 **deno**，且 `--js-runtimes` 必须传裸名 `deno`（PATH 找得到才行），不能用 `/c/.../deno.exe` 这种 Git-Bash 路径（yt-dlp 会判 `unavailable`）；
   - nsig/n 挑战的 solver 已是 **remote component**，要 `remote_components=['ejs:github']` 才会下载，否则 `n challenge solving failed`。
3. **deno 子进程也要走代理** → 运行前注入 `DENO_PROXY` / `HTTPS_PROXY`。

## 短链自动解析

下列短链 yt-dlp 自带 extractor 不认，本工具在下载前自动解析成真链：

| 短链 | 解析目标 | 特殊处理 |
|------|----------|----------|
| `t.cn/...` | `weibo.com/...` | 302 会先落 `passport.weibo.com` 中转页，需抠 `url=` 真链 |
| `v.douyin.com/...` | `douyin.com/video/<id>` | `DouyinIE` 只认完整链接 |
| `xhslink.com/...` / `xhslink.cn/...` | `xiaohongshu.com/explore/<id>` | 强制要求 `www.` 前缀；真链常藏在 `redirectPath=` 参数里（含 404/login 中转页） |

> 推特 `t.co` 由 `TwitterShortenerIE` 原生支持、油管 `youtu.be` 由 `YoutubeIE` 原生支持，均无需额外处理。

## 用法（桌面 GUI）

1. 在 Edge/Chrome 装 **CookieCloud 扩展**，把你自己的会话数据同步到自托管服务器（CookieCloud 可按域名同步多个站点）。
2. 打开 `ytcc-dl.exe`：
   - 填 **Host / UUID / 密码**（你的 CookieCloud 服务器）；
   - 【下载设置】里选 **平台**（或直接粘贴链接，工具会根据链接域名自动识别平台）；
   - 点 **① 拉取 Cookies**（按所选平台从服务端解密出对应域名 cookie，写到对应 `*_cookies.txt`）；
   - 填 **视频 URL**（支持上述短链）；选 **画质**；勾选 **转 MP4** 与 **兼容模式(重编码 H.264)**；
   - 点 **② 开始下载**。

> **画质预设**：`1080p`（默认）/ `2K(1440p)` / `4K(2160p)` / `最佳` / `自定义`。

> ⚠️ **为什么默认要重编码（兼容模式）？** YouTube 现在大量推 **AV1** 编码，很多手机/老旧播放器没有 AV1 解码器。工具会自动探测：遇到 **AV1 / VP9 / HEVC** 会重编码为 **H.264(yuv420p)**；已是 H.264 则直接 copy。

3. 文件落在输出目录（`ytdub_output/`），日志区实时显示进度。

> ⚠️ cookie 文件等同你的登录凭证，用完建议删除；CookieCloud 密码也不要长期明文留存。

## 命令行 / 自动化模式（`--smoke`）

```bash
ytcc-dl.exe --smoke \
  --host "http://192.168.1.100:10888" \
  --uuid "your-cookiecloud-uuid" \
  --password "你的密码" \
  --url "https://www.youtube.com/watch?v=xxxx" \
  --proxy "http://127.0.0.1:10808" \
  --platform "YouTube" \          # YouTube / X (Twitter) / Bilibili (B站) / 抖音 (Douyin) / 微博 (Weibo) / 小红书 (Xiaohongshu)
  --quality "1080p" \
  --format "bv[height<=480]+ba/best[height<=480]" \  # 仅当 --quality 自定义时生效
  --out "downloads" --no-mp4 \   # --no-mp4 跳过 mp4；--no-reencode 关闭重编码（极速模式）
```

- 日志同时写 `smoke.log`（与 exe 同目录）并回显，不需要 GUI 窗口。
- 首次运行会自动把 `deno.exe` / `ffmpeg.exe` 从包内解包到 exe 同目录的 `tools/`，之后复用。

## 服务端 / Docker 模式（可选）

`server.py` 把下载能力包装成轻量 Web UI + SSE 进度，适合部署到 NAS / 服务器（如极空间），无头环境运行。

- 配置全部来自环境变量：`COOKIECLOUD_HOST` / `CC_UUID` / `CC_PASSWORD` / `PROXY_OVERSEAS` / `OUT_DIR`
- 启动后浏览器访问 `http://<服务器IP>:8899`
- 配套 `Dockerfile` / `docker-compose.yml` / `DOCKER_ASSESSMENT.md` 为草案，可按需自行构建

## 从源码构建 exe

> ⚠️ 构建环境必须有 **tkinter**（GUI 依赖）。WorkBuddy 的 managed Python 3.13 是 `--without-tk` 构建、缺 tkinter，需改用**系统 Python 3.10**（自带 tkinter）建 venv 再打包。

```bash
cd ytcc-dl
# 1) 准备外部二进制（不入库，自行下载放入 tools/）
#    deno.exe  -> https://github.com/denoland/deno/releases
#    ffmpeg.exe -> https://ffmpeg.org/download.html （或 imageio-ffmpeg 提供的静态构建）
mkdir -p tools && cp <你的 deno.exe> tools/deno.exe && cp <你的 ffmpeg.exe> tools/ffmpeg.exe

# 2) 用含 tkinter 的 Python 建 venv（推荐系统 Python 3.10）
python3.10 -m venv .build_venv   # 用本机自带 tkinter 的 Python 3.10
.build_venv/Scripts/python.exe -m pip install -r requirements.txt
.build_venv/Scripts/python.exe build_exe.py
# 产物： dist/ytcc-dl.exe  （已内嵌 deno.exe + ffmpeg.exe）
```

## 目录结构

```
ytcc-dl/
├── ytcc_dl.py          # 主程序（GUI + 下载逻辑 + 短链解析）
├── server.py           # 可选 Flask Web 服务（Docker / 服务端模式）
├── build_exe.py        # PyInstaller 构建脚本
├── requirements.txt
├── Dockerfile          # Docker 草案
├── docker-compose.yml  # Docker 草案
├── DOCKER_ASSESSMENT.md# 极空间 / Docker 可行性评估
├── LICENSE             # MIT
├── THIRDPARTY.md       # 第三方组件许可证与义务
└── README.md
```

> 注：`tools/`（deno.exe / ffmpeg.exe）与 `dist/`（构建产物、cookie 文件）按 `.gitignore` 不入库，请从官网获取后自行放置。

## 延伸

ytdub（视频中文化 skill）的下载层可复用同一套 CookieCloud + yt-dlp 三件套；本项目是其「只下载原片」的独立形态。

## 许可证

本项目以 [MIT License](./LICENSE) 开源。第三方组件的许可证与义务见 [THIRDPARTY.md](./THIRDPARTY.md)。

## 免责声明

- 本工具为**个人学习 / 自用**用途，用于下载**你自己有权访问**的内容。请遵守各平台服务条款与所在司法辖区法律，不要下载受版权保护或无权获取的内容。
- **Cookie 等同账号凭证**：切勿将任何 `*_cookies.txt` 或 `.env` 提交到公开仓库 / 分享给他人。
- 作者不对使用本工具产生的任何后果负责，使用即代表你理解并自担风险。
