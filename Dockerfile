# ytcc-dl 容器镜像（基于 Python 源码，非 exe）
# 适配 Linux：把 Linux 二进制软链成 .exe 名，规避 ytcc_dl.py 里写死的 deno.exe/ffmpeg.exe
FROM --platform=$TARGETPLATFORM python:3.10-slim

# 默认值全部留空/占位：部署时经 docker-compose 环境变量或 .env 注入真实值，
# 不在镜像里硬编码任何内网地址（仓库公开，勿泄露拓扑）。
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    COOKIECLOUD_HOST= \
    CC_UUID= \
    CC_PASSWORD= \
    PROXY_OVERSEAS= \
    OUT_DIR=/app/output

# 系统依赖：ffmpeg、deno 运行库。ytcc_dl.py 已做 tkinter 懒加载（GUI 模式才
# import），无头容器不需要 python3-tk，镜像省 ~50MB。
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 下载 deno（yt-dlp 2026.08+ 的 js 挑战求解器）。构建环境若需代理，构建时传参：
#   docker build --build-arg HTTPS_PROXY=http://your-proxy:port -t ytcc-dl .
ARG HTTPS_PROXY=
RUN arch=$(dpkg --print-architecture) && \
    case "$arch" in \
      amd64) DENO_ARCH=x86_64 ;; \
      arm64) DENO_ARCH=aarch64 ;; \
      *) echo "unsupported arch $arch"; exit 1 ;; \
    esac && \
    DENO_VER=1.46.3 && \
    curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VER}/deno-${DENO_ARCH}-unknown-linux-gnu.zip" -o /tmp/deno.zip && \
    apt-get update && apt-get install -y --no-install-recommends unzip && \
    unzip -o /tmp/deno.zip -d /usr/local/bin/ && \
    chmod +x /usr/local/bin/deno && rm -f /tmp/deno.zip

# Python 依赖
COPY requirements.txt /app/requirements.txt
# Flask 仅容器 Web 入口用，不污染 exe 构建链（requirements.txt 仍只服务 PyInstaller）
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir flask

# 应用源码
COPY ytcc_dl.py /app/ytcc_dl.py
COPY server.py /app/server.py

# 关键 workaround：把 Linux 二进制软链成 ytcc_dl.py 期望的 .exe 名
RUN mkdir -p /app/tools && \
    ln -sf /usr/bin/ffmpeg /app/tools/ffmpeg.exe && \
    ln -sf /usr/local/bin/deno /app/tools/deno.exe

VOLUME ["/app/output"]
EXPOSE 8899

CMD ["python", "server.py"]
