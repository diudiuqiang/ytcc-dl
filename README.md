# ytcc-dl — CookieCloud 视频下载器（独立 exe 版）

把「从 **CookieCloud** 拉已登录 cookie → **yt-dlp 2026.08 三件套**下载 → 统一 mp4 转封装」整套链路，**封装成一个带界面的单文件 exe**。无需 Python 环境，双击即用。

支持 **YouTube / X(Twitter) / B站 / 抖音 / 微博 / 小红书 / TikTok** 七大平台，并自动解析各平台的短链（`t.cn`、`v.douyin.com`、`xhslink.com` / `xhslink.cn`）。

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

> 推特 `t.co` 由 `TwitterShortenerIE` 原生支持、油管 `youtu.be` 由 `YoutubeIE` 原生支持、TikTok `vm.tiktok.com` 由 `TikTokVMIE` 原生支持，均无需额外处理。

## ⚠️ TikTok 专有说明

- TikTok 国内不可直连，**必须走代理**（平台切换时代理框会自动填 `http://127.0.0.1:10808`，按你的实际代理改）。
- TikTok 对代理出口 IP 有风控：命中时报 `Your IP address is blocked from accessing this post`。这不是工具问题——**换一个干净落地**（如你自己的 VPS 节点）即可，家宽/住宅 IP 通过率最高。
- 公开视频免登录；部分区域/年龄限制内容需登录 cookie（CookieCloud 同步 `tiktok.com` 域名后照常拉取）。
- 图集（photo post）视 yt-dlp 版本支持程度而定，老版本可能只取到封面，遇此情况升级 yt-dlp。

## 用法（桌面 GUI）

1. 在 Edge/Chrome 装 **CookieCloud 扩展**，把你自己的会话数据同步到自托管服务器（CookieCloud 可按域名同步多个站点）。
2. 打开 `ytcc-dl.exe`：
   - 填 **Host / UUID / 密码**（你的 CookieCloud 服务器；默认值是占位符，必须改成真实地址）；
   - 点 **💾 保存配置**——凭据存入本机配置文件，**下次打开自动填入，不用再手填**（详见下文「本地配置记忆」）；
   - 【下载设置】里选 **平台**（或直接粘贴链接，工具会根据链接域名自动识别平台）；
   - 点 **① 拉取 Cookies**（按所选平台从服务端解密出对应域名 cookie，写到对应 `*_cookies.txt`）；
   - 填 **视频 URL**（支持上述短链）；选 **画质**；勾选 **兼容模式(重编码 H.264)**；
   - 点 **② 开始下载**。

> **画质预设**：`1080p`（默认）/ `2K(1440p)` / `4K(2160p)` / `最佳` / `自定义`。

> **输出恒为 mp4**（跨设备兼容，避免 mkv 在手机/别的机器上无声），没有开关、也不需要开关。**兼容模式**控制是否把视频重编码为 H.264——YouTube 现在大量推 AV1 编码，很多手机/老旧播放器没有 AV1 解码器；工具自动探测，遇到 AV1/VP9/HEVC 重编码为 H.264(yuv420p)，已是 H.264 则直接 copy。

3. 文件落在输出目录（`ytcc-dl_output/`），日志区实时显示进度。

> ⚠️ cookie 文件等同你的登录凭证，用完建议删除。

## 本地配置记忆（一次录入，本机复用）

CookieCloud 的 Host / UUID / 密码、代理地址这些本机固定参数，只需填一次：

- 点 **💾 保存配置**（或每次 **① 拉取 Cookies** 成功后自动保存）写入配置文件，之后每次打开自动预填；
- 配置文件位置：`%APPDATA%\ytcc-dl\config.ini`（Windows；其他平台在 `~/.config/ytcc-dl/`），可用环境变量 `YTCC_CONFIG_PATH` 覆盖；
- **密码字段加密存储**：Windows 用 DPAPI（同一个用户、同一台机器才能解密，拷到别处即废字节）；`host` / `uuid` / `代理` 为明文——需要变更时可直接手改；
- **30 天自动销毁**：超过 30 天没有成功使用记录，配置文件自动删除，下次打开提示重填（常用的人每次拉取 cookie 成功都会自动续期，碰不到销毁）；
- 删除配置文件 = 重置；GUI 日志区会显示当前配置来源与保存时间。

命令行（`--smoke`）与 `server.py` 同样遵循「显式参数 / 环境变量 > 本机配置 > 平台默认」的优先级。

## 命令行 / 自动化模式（`--smoke`）

```bash
ytcc-dl.exe --smoke \
  --host "http://192.168.1.100:10888" \
  --uuid "your-cookiecloud-uuid" \
  --password "你的密码" \
  --url "https://www.tiktok.com/@user/video/1234567890" \
  --proxy "http://127.0.0.1:10808" \
  --platform "TikTok" \        # YouTube / X (Twitter) / Bilibili (B站) / 抖音 (Douyin) / 微博 (Weibo) / 小红书 (Xiaohongshu) / TikTok
  --quality "1080p" \
  --format "bv[height<=480]+ba/best[height<=480]" \  # 仅当 --quality 自定义时生效
  --out "downloads" --no-reencode \   # --no-reencode 关闭重编码（极速模式，可能产出手机播不了的 AV1）
```

- 日志同时写 `smoke.log`（与 exe 同目录）并回显，不需要 GUI 窗口。
- 首次运行会自动把 `deno.exe` / `ffmpeg.exe` 从包内解包到 exe 同目录的 `tools/`，之后复用。

## 服务端 / Docker 模式（可选）

`server.py` 把下载能力包装成轻量 Web UI + SSE 进度，适合部署到 NAS / 服务器（如极空间），无头环境运行。

- 配置优先级：环境变量（或 `.env`，模板见 `.env.example`：`COOKIECLOUD_HOST` / `CC_UUID` / `CC_PASSWORD` / `PROXY_OVERSEAS` / `OUT_DIR` / `HOST_DOWNLOAD_DIR`）> 本机保存的配置文件（本地直跑时与 GUI 共用同一份凭据）；容器内无 APPDATA，自然回落为空
- 启动后浏览器访问 `http://<服务器IP>:8899`；平台下拉框从 `PLATFORMS` 动态生成，加平台只改主程序一处
- 多个下载任务自动串行（代理/PATH 等进程级环境由此获得确定性语义）
- cookie 文件写在独立运行目录，**绝不混进下载产物目录**（避免分享产物时顺手泄露凭证）
- 配套 `Dockerfile` / `docker-compose.yml` / `DOCKER_ASSESSMENT.md` 为草案，可按需自行构建；镜像已无需 `python3-tk`（tkinter 懒加载）

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
├── ytcc_dl.py          # 主程序（GUI + 下载逻辑 + 短链解析，tkinter 懒加载）
├── ytcc_config.py      # 本地配置持久化（INI + DPAPI 加密 + 30 天自动销毁）
├── server.py           # 可选 Flask Web 服务（Docker / 服务端模式）
├── build_exe.py        # PyInstaller 构建脚本
├── requirements.txt
├── tests/              # 单元测试（python -m unittest discover -s tests）
├── .env.example        # Docker / server 模式配置模板（复制为 .env 填真实值）
├── Dockerfile          # Docker 草案
├── docker-compose.yml  # Docker 草案
├── DOCKER_ASSESSMENT.md# 极空间 / Docker 可行性评估
├── LICENSE             # MIT
├── THIRDPARTY.md       # 第三方组件许可证与义务
└── README.md
```

> 注：`tools/`（deno.exe / ffmpeg.exe）与 `dist/`（构建产物、cookie 文件）按 `.gitignore` 不入库，请从官网获取后自行放置。仓库内**不含任何真实服务器地址 / UUID / 密码**——所有默认值均为占位符。

## 延伸

ytdub（视频中文化 skill）的下载层可复用同一套 CookieCloud + yt-dlp 三件套；本项目是其「只下载原片」的独立形态。

## 许可证

本项目以 [MIT License](./LICENSE) 开源。第三方组件的许可证与义务见 [THIRDPARTY.md](./THIRDPARTY.md)。

## 免责声明

- 本工具为**个人学习 / 自用**用途，用于下载**你自己有权访问**的内容。请遵守各平台服务条款与所在司法辖区法律，不要下载受版权保护或无权获取的内容。
- **Cookie 等同账号凭证**：切勿将任何 `*_cookies.txt` 或 `.env` 提交到公开仓库 / 分享给他人。
- 作者不对使用本工具产生的任何后果负责，使用即代表你理解并自担风险。
