# ytcc-dl 容器镜像（基于 Python 源码，非 exe）
# 适配 Linux：把 Linux 二进制软链成 .exe 名，规避 ytcc_dl.py 里写死的 deno.exe/ffmpeg.exe
FROM --platform=$TARGETPLATFORM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    # 国内站点直连、海外走代理；以下为容器默认值，可用 docker-compose env 覆盖
    COOKIECLOUD_HOST=http://192.168.31.116:10888 \
    CC_UUID= \
    CC_PASSWORD= \
    PROXY_OVERSEAS=http://192.168.31.99:10808 \
    OUT_DIR=/app/output

# 系统依赖：ffmpeg、deno 运行库、可选 tkinter（若采用懒加载方案则可去掉 python3-tk）
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        # python3-tk 仅在"不采用懒加载"时需要；懒加载方案可注释掉以减小镜像
        python3-tk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 下载 deno（yt-dlp 2026.08+ 的 js 挑战求解器）。构建环境若需代理，取消下一行注释：
# ENV HTTPS_PROXY=http://192.168.31.99:10808
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
