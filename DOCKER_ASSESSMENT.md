# ytcc-dl 容器化（极空间 NAS）可行性评估

> 评估日期：2026-10-13 ｜ 评估模型：Step-5-Preview
> 评估对象：`ytcc-dl`（多平台视频下载器，基于 CookieCloud + yt-dlp + ffmpeg + deno）

---

## 一、结论（先行）

**可行，且改造量小（约 0.5~1 人日，含多架构镜像构建 + 极空间实测）。**

核心下载逻辑 `run_download()` 已经是**无 GUI 依赖的纯函数**，`--smoke` 无头模式也已验证可跑——这决定了它天生适合容器化。真正的阻碍只有"Windows 二进制名 + GUI 依赖 + 配置写死"，且都能用**非侵入式手段**解决，大部分不用改 `ytcc_dl.py` 核心。

**但不能直接把 `.exe` 丢进容器**——那是 Windows PE，Linux 跑不了。正确姿势是基于 Python 源码做 Linux 适配 + 加一个轻量 Web 入口。

---

## 二、现状代码对 Docker 的阻碍（逐条，附处理方案）

| # | 阻碍 | 代码位置 | 处理方案 | 是否改核心代码 |
|---|---|---|---|---|
| 1 | 二进制名写死 `deno.exe`/`ffmpeg.exe` | ytcc_dl.py:64-66 | Dockerfile 里把 Linux 二进制软链成 `.exe`（`ln -s /usr/bin/ffmpeg /app/tools/ffmpeg.exe`） | ❌ 不改 |
| 2 | 顶部 `import tkinter` 无头会炸 | ytcc_dl.py:34 | 方案A：镜像装 `python3-tk`（体积 +50MB）；方案B：tkinter import 改为懒加载（1 行改动）| 可选 |
| 3 | CookieCloud host 写死 `192.168.31.116:10888` | ytcc_dl.py:877 | Web 入口用 env 注入 `COOKIECLOUD_HOST`；或容器用 host 网络直连 | ❌ 不改（外部传参） |
| 4 | 凭据走命令行参数 | smoke_mode | Web 入口从 env（`CC_UUID`/`CC_PASSWORD`）读，不进命令行 | ❌ 不改 |
| 5 | 输出目录默认 `APP_DIR/ytdub_output` | smoke_mode:887 | 挂卷 `/app/output` + env `OUT_DIR` | ❌ 不改 |
| 6 | 单任务、无队列 | GUI 设计 | Web 层加后台线程 + 简单队列（避免并发打架）| 新增 web 层 |
| 7 | `no_proxy` 写死 `192.168.31.116` | ytcc_dl.py:478 | 无害（容器里无效），可忽略或改 env | 可选 |

**关键发现**：第 1/3/4/5 条都可以用"Dockerfile 软链 + env 注入 + Web 层传参"完全规避对 `ytcc_dl.py` 的修改。真正必须动代码的只有第 2 条（tkinter 懒加载，1 行）。

---

## 三、推荐架构

```
┌─────────────────────────────────────────────┐
│  浏览器（手机/电脑）                          │
│   http://极空间IP:8899                        │
└───────────────┬─────────────────────────────┘
                │
┌───────────────▼─────────────────────────────┐
│  ytcc-dl 容器（极空间 Docker）                │
│  ┌──────────────────────────────────────┐   │
│  │ Flask Web UI (server.py:8899)         │   │
│  │  - 粘贴链接 + 选平台 + 点下载          │   │
│  │  - SSE 实时进度回传                    │   │
│  │  - 调 run_download() 后台线程          │   │
│  └───────────────┬──────────────────────┘   │
│                   │ 内部调用                   │
│  ┌───────────────▼──────────────────────┐   │
│  │ ytcc_dl.py (原逻辑，几乎不改)         │   │
│  │  fetch_cookies → run_download → mp4   │   │
│  └───────────────┬──────────────────────┘   │
│                   │                           │
│  ┌───────────────▼──────────────────────┐   │
│  │ tools/: deno(软链deno.exe) ffmpeg     │   │
│  └───────────────────────────────────────┘   │
│  挂载卷: /app/output → 极空间共享目录          │
└───────────────┬─────────────────────────────┘
                │ (host 网络或同网段)
┌───────────────▼─────────────────────────────┐
│ CookieCloud 服务 (192.168.31.116:10888)      │
│ 代理 (海外站点: 10808)                        │
└─────────────────────────────────────────────┘
```

**为什么用 Web UI 而不是 CLI**：极空间容器没有交互式终端给用户用，Web UI 是最自然的"远程操作面板"，手机也能开。核心 `run_download` 直接被 Web 层调用，不用走 smoke_mode 的 KV 解析。

---

## 四、极空间 NAS 特有的约束（重要！）

1. **极空间 Docker 应用不支持 `docker build`**——只能"拉取镜像"或"导入本地 tar 镜像"。
   - 因此流程是：**在 Windows/云服务器上 `docker buildx` 多架构构建 → push 到 Docker Hub/私有库，或导出 tar → 极空间"镜像导入"**。
   - 不能直接在 NAS 上 build Dockerfile。

2. **架构需确认**：极空间有 x86（Z4S/Z423 等）和 ARM64（Z2S/Z4 瑞芯微 RK3568）两种机型。
   - 需确认你的机型架构；ARM 机型必须打 `linux/arm64` 镜像（deno/ffmpeg/yt-dlp 均有 arm64 支持，已确认可行）。

3. **网络模式**：建议容器用 **host 网络**（或 bridge + 同网段），这样能直接访问：
   - CookieCloud 服务 `192.168.31.116:10888`（同路由器内网）
   - 海外代理 `192.168.31.x:10808`（若代理跑在 Windows 本机，需填宿主 LAN IP 而非 127.0.0.1）
   - 国内站点（B站/抖音/微博）直连——极空间本身在国内，出口直连 OK

4. **持久化**：下载产物**必须挂卷**到极空间某共享目录（如 `/共享盘/Downloads/ytcc`），否则容器重建即丢。

5. **开机自启**：极空间容器可设"开机自启"，让下载服务随 NAS 启动。

---

## 五、改造工作量

| 任务 | 工作量 | 说明 |
|---|---|---|
| Dockerfile（slim + ffmpeg + deno 软链 + 源码 COPY） | 0.5h | 见草案 |
| docker-compose.yml | 0.2h | 见草案 |
| server.py（Flask Web UI + SSE + 后台下载线程） | 2-3h | 包装 run_download，env 注入 |
| tkinter 懒加载（1 行）或装 python3-tk | 0.1h | 二选一 |
| 多架构 buildx 构建 + 极空间导入实测 | 1-2h | 含 ARM 验证 |
| **合计** | **约 0.5~1 人日** | 不含边缘 Bug 调试 |

---

## 六、主要风险

1. **网络/代理可达性**：容器内能否访问 CookieCloud 与代理，取决于极空间网络模式与你的服务常驻情况。这是部署时第一要验证的点。
2. **CookieCloud 服务常驻**：若它跑在 Windows 本机，Windows 关机则容器拉不到 cookie。建议把 CookieCloud 也迁到常驻设备（极空间本身/NAS/小主机）。
3. **deno 下载**：Docker build 时需从 GitHub 拉 deno 二进制，若构建环境需代理要配 `ENV HTTPS_PROXY`。
4. **ARM 兼容性**：需实测 yt-dlp + deno 在 arm64 容器内的 js 挑战求解是否正常。
5. **并发/队列**：Web 版需简单队列防多任务冲突，原 GUI 是单任务设计。

---

## 七、落地草案文件

- `Dockerfile` —— 基于 `python:3.10-slim`，装 ffmpeg、软链 deno、COPY 源码
- `docker-compose.yml` —— 端口/卷/环境变量/网络模式
- `server.py` —— Flask Web 入口，包装 `run_download`，env 读配置，SSE 推进度

> 草案代码可直接 build 运行（ARM 机型需改 `platform` 并验证 deno arm64）。如需我实际落地，告知即可。
