#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ytcc-dl — 多平台视频下载器 (单文件 GUI 版，支持 YouTube / X / B站 / 抖音 / 微博 / 小红书)

把「从 CookieCloud 拉已登录 cookie → yt-dlp 2026.08 三件套下载 → 统一 mp4 转封装」
整套链路封装成一个带界面的独立工具。新增平台只需在 PLATFORMS 里加一项（yt-dlp 原生支持）。

关键坑（已固化进代码）：
  1. Edge/Chrome 2024+ 默认 App-Bound Encryption (ABE)，沙箱无法离线 DPAPI 解密本地 cookie；
     用 CookieCloud 从服务端拉密文、本地 AES 解密，绕过 ABE。
  2. yt-dlp 2026.08+ 三件套缺一不可：
     - 代理必须显式传（沙箱默认 63105 是死代理），本工具默认 10808；
     - nsig 解算需 deno，--js-runtimes 必须传裸名 "deno"（PATH 里找得到才行），
       不能用 /c/.../deno.exe 这种 Git-Bash 风格路径（yt-dlp 会判 unavailable）；
     - nsig/n 挑战的 solver 已是 remote component，要 remote_components=['ejs:github'] 才会下载。
  3. deno 子进程也要走代理，故运行前注入 DENO_PROXY / HTTPS_PROXY。

打包：PyInstaller --windowed --add-binary tools/deno.exe;. --add-binary tools/ffmpeg.exe;. --collect-all yt_dlp
"""

import os
import sys
import json
import base64
import re
import subprocess
import hashlib
import shutil
import threading
import time
import urllib.request
import urllib.parse

# 单一版本来源（GUI 标题 / CLI --version / exe 文件名 / README 均引用）
from version import VERSION

# tkinter 延迟到 GUI 模式才 import：server.py 在无头容器里 import 本模块时，
# 不再因缺 python3-tk 直接崩（镜像可省掉 ~50MB 的 tk 依赖）。
tk = None
ttk = scrolledtext = filedialog = messagebox = None


def _ensure_tk():
    """GUI 模式入口：加载 tkinter 全家桶到模块全局。幂等，可重复调用。"""
    global tk, ttk, scrolledtext, filedialog, messagebox
    if tk is not None:
        return
    import tkinter as _tk
    from tkinter import ttk as _ttk, scrolledtext as _sc, filedialog as _fd, messagebox as _mb
    tk, ttk, scrolledtext, filedialog, messagebox = _tk, _ttk, _sc, _fd, _mb

HERE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    # PyInstaller 冻结后，脚本在临时目录，_MEIPASS 含打包的二进制
    APP_DIR = os.path.dirname(sys.executable)
    MEIPASS = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = HERE
    MEIPASS = HERE

# 本地配置持久化（INI + DPAPI 加密 + 30 天自动销毁）：CookieCloud 凭据 / 代理
# 一次录入本机复用。import 失败不致命——降级为「每次手填」，不影响下载主链路。
try:
    import ytcc_config
except Exception:
    ytcc_config = None


# ---------------------------------------------------------------------------
# 工具二进制定位：优先 tools/ 子目录；冻结版首次从 _MEIPASS 拷出；再退化已知路径
# ---------------------------------------------------------------------------
def _search_binary(name, extra_dirs=None):
    candidates = []
    candidates.append(os.path.join(APP_DIR, "tools", name))
    candidates.append(os.path.join(MEIPASS, name))
    if extra_dirs:
        for d in extra_dirs:
            candidates.append(os.path.join(d, name))
    for c in candidates:
        if os.path.exists(c) and os.path.getsize(c) > 1_000_000:
            return c
    return None


def locate_tools():
    """返回 {deno: path, ffmpeg: path}，并确保 tools/ 下有权限可写的副本。"""
    # 回退搜索路径只放「与用户目录无关」的通用位置；imageio_ffmpeg 的安装路径
    # 用 expanduser 动态拼（避免把本机用户名写进开源仓库）。
    _ffmpeg_fallback = os.path.join(
        os.path.expanduser("~"), ".workbuddy", "binaries", "python", "envs",
        "default", "Lib", "site-packages", "imageio_ffmpeg", "binaries")
    deno = _search_binary("deno.exe", [os.path.join(os.getcwd(), ".tools", "deno")])
    ffmpeg = _search_binary("ffmpeg.exe", [_ffmpeg_fallback])
    # 冻结版：把 _MEIPASS 里的二进制拷到 APP_DIR/tools，避免每次解包
    tools_dir = os.path.join(APP_DIR, "tools")
    os.makedirs(tools_dir, exist_ok=True)
    out = {}
    for key, src in (("deno", deno), ("ffmpeg", ffmpeg)):
        if src and src.startswith(MEIPASS) and MEIPASS != tools_dir:
            dst = os.path.join(tools_dir, os.path.basename(src))
            try:
                if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
                    shutil.copy2(src, dst)
                out[key] = dst
                continue
            except Exception:
                pass
        out[key] = src
    return out


# ---------------------------------------------------------------------------
# CookieCloud：拉取 + 解密（OpenSSL EVP_BytesToKey + AES-256-CBC）
# ---------------------------------------------------------------------------
def evp_bytes_to_key(passphrase, salt, key_len=32, iv_len=16):
    dtot = b""
    d = b""
    while len(dtot) < key_len + iv_len:
        d = hashlib.md5(d + passphrase + salt).digest()
        dtot += d
    return dtot[:key_len], dtot[key_len:key_len + iv_len]


def _domain_matches(key, domains):
    """严格按域名边界匹配：key 等于目标域名，或为其子域（*.target）。
    不能用子串匹配——否则 'x.com' 会误命中 'aihubmix.com'/'airwallex.com' 等无关站点。"""
    k = (key or "").lower()
    for d in domains:
        d = d.lower()
        if k == d or k.endswith("." + d):
            return True
    return False


def fetch_cookies(host, uuid, password, domains=("youtube.com",)):
    """从 CookieCloud 拉密文并解密，返回指定域名（精确匹配/子域）的 Netscape cookie 文本。
    domains 默认 youtube.com(+googlevideo.com)；下载 X(Twitter) 时传 ("x.com", "twitter.com")。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    host = host.rstrip("/")
    url = f"{host}/get/{uuid}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    def _open():
        # 优先：强制绕过系统代理（CookieCloud 多为局域网 host，走代理反而连不上）。
        # 回退：若用户把 CookieCloud 架在公网且必须走代理，则再用默认 opener 试一次。
        no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with no_proxy.open(req, timeout=25) as r:
                return r.read()
        except Exception:
            default = urllib.request.build_opener()
            with default.open(req, timeout=25) as r:
                return r.read()

    try:
        raw = _open()
    except Exception as e:
        raise RuntimeError(
            f"无法连接 CookieCloud 服务端 {host}（{type(e).__name__}: {e}）。"
            f"请确认：① 该地址在本机可访问；② 若走代理，确保局域网 IP 已加入 no_proxy；"
            f"③ 服务端已启动。命令行可用 `curl {url}` 自检。") from e
    try:
        resp = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"CookieCloud 返回的不是合法 JSON（{type(e).__name__}: {e}）") from e
    enc = resp.get("encrypted")
    if not enc:
        raise RuntimeError(f"CookieCloud 返回无 encrypted 字段，resp keys={list(resp.keys())}")
    data = base64.b64decode(enc)
    if data[:8] != b"Salted__":
        raise RuntimeError("CookieCloud 数据不是 OpenSSL Salted__ 格式")
    salt = data[8:16]
    ct = data[16:]
    keystr = hashlib.md5((uuid + "-" + password).encode()).hexdigest()[:16]
    key, iv = evp_bytes_to_key(keystr.encode("utf-8"), salt)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    dec = cipher.decryptor()
    plain = dec.update(ct) + dec.finalize()
    pad = plain[-1] if plain else 0
    if pad < 1 or pad > 16 or pad > len(plain):
        raise RuntimeError(
            "CookieCloud 解密失败：PKCS#7 填充非法（UUID 或密码错误？请核对 CookieCloud 凭据）")
    plain = plain[:-pad]
    payload = json.loads(plain.decode("utf-8"))
    cookie_data = payload.get("cookie_data", {})
    yt = {k: v for k, v in cookie_data.items() if _domain_matches(k, domains)}

    def clean(s):
        if s is None:
            return ""
        return str(s).replace("\t", " ").replace("\n", " ").replace("\r", " ")

    lines = ["# Netscape HTTP Cookie File"]
    count = 0
    for _dom, cookies in yt.items():
        for c in cookies:
            name = clean(c.get("name", ""))
            if not name:
                continue
            value = clean(c.get("value", ""))
            path = clean(c.get("path", "/")) or "/"
            secure = "TRUE" if c.get("secure") else "FALSE"
            exp = c.get("expirationDate") or c.get("expires") or c.get("expiry") or 0
            try:
                exp = int(float(exp))
            except Exception:
                exp = 0
            domain = clean(c.get("domain") or _dom)
            if not domain.startswith("."):
                domain = "." + domain
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{exp}\t{name}\t{value}")
            count += 1
    return "\n".join(lines) + "\n", count


def probe_video_codec(ffmpeg_path, path):
    """探测输入文件的视频编码。返回小写四字码，如 'av1' / 'vp9' / 'h264' / 'hevc'。
    探测失败返回 None。"""
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-i", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="ignore", timeout=60)
        txt = proc.stderr + proc.stdout
        m = re.search(r"Video:\s*([a-zA-Z0-9_]+)", txt)
        if not m:
            return None
        raw = m.group(1).lower()
        # 归一化常见别名
        norm = {
            "av01": "av1", "av1": "av1",
            "vp09": "vp9", "vp9": "vp9",
            "hev1": "hevc", "hvc1": "hevc", "hevc": "hevc",
            "avc1": "h264", "h264": "h264", "h263": "h263", "vp8": "vp8",
        }
        return norm.get(raw, raw)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 画质预设 → yt-dlp format 字符串
#   - 1080p / 2K / 4K：限制高度，并带 /best 兜底（X 等只给单一流时不会报错）
#   - 最佳：bv*+ba/best（原默认）
# ---------------------------------------------------------------------------
QUALITY_PRESETS = {
    "1080p": "bv[height<=1080]+ba/best[height<=1080]/best",
    "2K (1440p)": "bv[height<=1440]+ba/best[height<=1440]/best",
    "4K (2160p)": "bv[height<=2160]+ba/best[height<=2160]/best",
    "最佳": "bv*+ba/best",
    "自定义": None,
}


def build_format(quality, custom_fmt=None):
    """根据画质预设返回 yt-dlp format；自定义时直接用 custom_fmt。"""
    if quality == "自定义" and custom_fmt and custom_fmt.strip():
        return custom_fmt.strip()
    return QUALITY_PRESETS.get(quality, QUALITY_PRESETS["1080p"])


# ---------------------------------------------------------------------------
# 代理模式：system=采用系统代理（默认，点「一键代理」即可开关）
#           auto=智能分流 / all=全部走代理 / none=全部直连
#   GUI 下拉显示中文标签，内部与配置文件、CLI 统一用四个键。
# ---------------------------------------------------------------------------
PROXY_MODE_AUTO, PROXY_MODE_ALL, PROXY_MODE_NONE = "auto", "all", "none"
PROXY_MODE_SYSTEM = "system"
PROXY_MODE_LABELS = {
    PROXY_MODE_SYSTEM: "采用系统代理（推荐）",
    PROXY_MODE_AUTO: "智能分流（仅海外3站）",
    PROXY_MODE_ALL: "全部走代理",
    PROXY_MODE_NONE: "全部直连",
}
_PROXY_LABEL_TO_MODE = {v: k for k, v in PROXY_MODE_LABELS.items()}
OVERSEAS_PLATFORMS = ("YouTube", "X (Twitter)", "TikTok")


def proxy_mode_key(value):
    """把 GUI 中文标签 / CLI 参数 / 配置旧值统一归一到四个模式键。"""
    s = str(value or "").strip()
    if s in _PROXY_LABEL_TO_MODE:
        return _PROXY_LABEL_TO_MODE[s]
    return s if s in PROXY_MODE_LABELS else PROXY_MODE_SYSTEM


def detect_system_proxy():
    """读取系统代理：Windows WinINET 注册表优先，再回退环境变量。

    返回 (proxy_url, source)；没开系统代理返回 ('', '')。
    注册表里的 ProxyServer 可能是 "http=host:port;https=host:port" 或裸 "host:port"。
    """
    if os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            try:
                enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
                server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "").strip()
            finally:
                winreg.CloseKey(key)
            if enabled and server:
                picked = ""
                if "=" in server:
                    table = dict(part.split("=", 1) for part in server.split(";")
                                 if "=" in part)
                    picked = table.get("https") or table.get("http") or ""
                else:
                    picked = server
                picked = picked.strip()
                if picked and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", picked):
                    picked = "http://" + picked
                if picked and "127.0.0.1:1" not in picked:  # WinINET 关闭占位值
                    return picked, "Windows 系统代理"
        except Exception:
            pass
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
              "HTTP_PROXY", "http_proxy"):
        val = (os.environ.get(k) or "").strip()
        if val:
            return val, k
    return "", ""


def effective_proxy(platform, mode, proxy, enabled=True):
    """按代理模式决定本次任务真正使用的代理地址（''=直连）。

    system 模式在代理框留空时自动读取系统代理；auto/system 都只让
    YouTube / X / TikTok 走代理，国内站点强制直连。enabled=False 是
    GUI「一键代理」关闭态——无论什么模式都强制直连。
    """
    if not enabled:
        return ""
    mode = proxy_mode_key(mode)
    proxy = str(proxy or "").strip()
    if mode == PROXY_MODE_NONE:
        return ""
    if mode == PROXY_MODE_SYSTEM and not proxy:
        proxy, _src = detect_system_proxy()
    if mode == PROXY_MODE_ALL:
        return proxy
    return proxy if platform in OVERSEAS_PLATFORMS else ""


# ---------------------------------------------------------------------------
# 平台配置：CookieCloud 拉取域名、cookie 文件名、是否强制 cookie、默认代理、提示
#   yt-dlp 原生支持 B站/抖音/微博，无需单独写下载器；本表只管「cookie 域名 / 代理 / 提示」。
#   顺序即 GUI 下拉顺序：前三个走代理（海外），后四个直连（国内）。
# ---------------------------------------------------------------------------
PLATFORMS = {
    "YouTube": {
        "domains": ("youtube.com", "youtu.be", "googlevideo.com", "youtubei.googleapis.com"),
        "cookie_file": "youtube_cookies.txt",
        "need_cookie": True,
        "proxy_default": "http://127.0.0.1:10808",
        "note": "需登录 cookie；海外站点",
    },
    "X (Twitter)": {
        "domains": ("x.com", "twitter.com", "t.co"),
        "cookie_file": "x_cookies.txt",
        "need_cookie": False,
        "proxy_default": "http://127.0.0.1:10808",
        "note": "公开视频可免登录；海外站点",
    },
    "TikTok": {
        "domains": ("tiktok.com",),
        "cookie_file": "tiktok_cookies.txt",
        "need_cookie": False,
        "proxy_default": "http://127.0.0.1:10808",
        # 风控实况（2026-10-25 测）：国内直连不可达；本地代理出口多被 TikTok 标记
        # ("Your IP address is blocked")，遇此报错就换干净落地（如自己的 VPS 节点）。
        "note": "公开视频可免登录；⚠ 代理出口 IP 被墙会报 IP blocked，换干净落地即可",
    },
    "Bilibili (B站)": {
        "domains": ("bilibili.com", "b23.tv"),
        "cookie_file": "bilibili_cookies.txt",
        "need_cookie": False,
        "proxy_default": "",
        "note": "公开视频免登录（高清需登录）；国内站点",
        "extra_opts": {"http_headers": {"Referer": "https://www.bilibili.com/"}},
    },
    "抖音 (Douyin)": {
        "domains": ("douyin.com", "v.douyin.com"),
        "cookie_file": "douyin_cookies.txt",
        "need_cookie": False,
        "proxy_default": "",
        "note": "部分视频需登录 cookie；国内站点",
    },
    "微博 (Weibo)": {
        "domains": ("weibo.com", "weibo.cn"),
        "cookie_file": "weibo_cookies.txt",
        "need_cookie": False,
        "proxy_default": "",
        "note": "公开视频免登录；支持 t.cn 短链直连解析；国内站点",
    },
    "小红书 (Xiaohongshu)": {
        "domains": ("xiaohongshu.com", "xhslink.com", "xhslink.cn"),
        "cookie_file": "xiaohongshu_cookies.txt",
        "need_cookie": False,
        "proxy_default": "",
        "note": "部分内容需登录 cookie；支持 xhslink.com / xhslink.cn 短链直连解析；国内站点",
    },
}

# 链接 host → 平台（用于自动识别，避免用户选错平台导致 cookie/代理不匹配）
PLATFORM_HOST_HINTS = {
    "youtube.com": "YouTube", "youtu.be": "YouTube",
    "x.com": "X (Twitter)", "twitter.com": "X (Twitter)", "t.co": "X (Twitter)",
    "bilibili.com": "Bilibili (B站)", "b23.tv": "Bilibili (B站)",
    "douyin.com": "抖音 (Douyin)", "v.douyin.com": "抖音 (Douyin)",
    "weibo.com": "微博 (Weibo)", "weibo.cn": "微博 (Weibo)", "t.cn": "微博 (Weibo)",
    "xiaohongshu.com": "小红书 (Xiaohongshu)", "xhslink.com": "小红书 (Xiaohongshu)",
    "xhslink.cn": "小红书 (Xiaohongshu)",
    # TikTok：vm.tiktok.com 短链由 yt-dlp 的 TikTokVMIE 原生解析，**不要**加进
    # 下面的 SHORT_LINK_HOSTS 自研解析（反而坏事）；这里只做平台自动识别。
    "tiktok.com": "TikTok", "vm.tiktok.com": "TikTok",
}


def detect_platform(url):
    """根据链接 host 推断平台；识别不出返回 None。
    必须按 host 边界匹配（host == 目标域名 或为其子域 *.target），
    不能用整串子串匹配——否则 'x.com' 会误命中 'max.com'/'box.com'，
    查询串里出现的 't.cn' 也会把无关链接误判成微博。"""
    host = _host_of(url)
    if not host:
        return None
    for h, plat in PLATFORM_HOST_HINTS.items():
        if host == h or host.endswith("." + h):
            return plat
    return None


# 短链域名：yt-dlp 自带 extractor 不认，下载前需先解析成真链。
#  - 微博 t.cn       → weibo.com/...（且 302 会先落 passport.weibo.com 中转页，需抠 url= 真链）
#  - 抖音 v.douyin.com→ douyin.com/video/<id>（DouyinIE 只认完整链接，不认 v.douyin.com）
#  - 小红书 xhslink.com / xhslink.cn → xiaohongshu.com/explore/<id>（XiaoHongShuIE 只认完整链接，
#    且强制要求 www. 前缀；.cn 与 .com 解析目标一致，缺一不可，否则 .cn 链接会静默失败）
# 推特 t.co 由 TwitterShortenerIE 原生支持、油管 youtu.be 由 YoutubeIE 原生支持，均无需在此处理。
SHORT_LINK_HOSTS = {"t.cn", "v.douyin.com", "xhslink.com", "xhslink.cn"}


def _host_of(url):
    try:
        return urllib.parse.urlparse(url or "").netloc.lower()
    except Exception:
        return ""


def _unwrap_weibo_passport(url):
    """微博 passport visitor 中转页：t.cn 302 后会落到
    passport.weibo.com/visitor/visitor?...&url=<真实 weibo 链接>，真实地址藏在 url= 参数里。
    WeiboIE 只认 weibo.com，故需把真链抠出来。"""
    try:
        p = urllib.parse.urlparse(url or "")
        if "passport.weibo.com" in p.netloc:
            q = urllib.parse.parse_qs(p.query)
            real = q.get("url", [None])[0]
            if real:
                return real
    except Exception:
        pass
    return url


def _extract_redirect_path(url):
    """小红书短链（xhslink.com/.cn）对过期/需登录的笔记不会直接 302，而是落到
    xiaohongshu.com/404 或 /login 页，真实笔记地址编码在 `redirectPath=` / `redirect_url=`
    查询参数里（URL 编码）。把真链抠出来，否则会把 404/login 页喂给 yt-dlp 而失败。

    两种结构都要覆盖：
      - 顶层参数： /login?redirectPath=<编码真链>&...           （parse_qs 可解）
      - 嵌套参数： /404?source=...?redirectPath=<编码真链>&... （redirectPath 嵌在 source 值内，
        需用正则直接扫描原始 URL）
    仅当目标确实是 xiaohongshu.com 的笔记页时才采用，避免误用其它跳转。"""
    try:
        # 1) 顶层参数（parse_qs）
        p = urllib.parse.urlparse(url or "")
        q = urllib.parse.parse_qs(p.query)
        for key in ("redirectPath", "redirect_url", "target_url"):
            cand = q.get(key, [None])[0]
            if cand:
                cand = urllib.parse.unquote(cand)
                if _is_xhs_note(cand):
                    return cand
        # 2) 嵌套参数（正则直接扫原始 URL，命中 redirectPath=https%3A%2F%2F...）
        m = re.search(r'(?:redirectPath|redirect_url|target_url)=(https?%3A%2F%2F[^&\s]+)', url or "")
        if m:
            cand = urllib.parse.unquote(m.group(1))
            if _is_xhs_note(cand):
                return cand
    except Exception:
        pass
    return None


def _is_xhs_note(url):
    try:
        p = urllib.parse.urlparse(url or "")
        return "xiaohongshu.com" in p.netloc and ("/explore/" in url or "/discovery/item/" in url)
    except Exception:
        return False


def _ensure_xhs_www(url):
    """XiaoHongShuIE 强制要求 www. 前缀，裸 xiaohongshu.com 匹配不到；解析到裸域时补上。
    只改 netloc（用 urlunparse 重建），不能用整串 replace——否则查询串里若出现
    'xiaohongshu.com' 字样会被误替换。"""
    try:
        p = urllib.parse.urlparse(url or "")
        if p.netloc.lower() == "xiaohongshu.com":
            return urllib.parse.urlunparse(p._replace(netloc="www.xiaohongshu.com"))
    except Exception:
        pass
    return url


def resolve_short_url(url, timeout=20):
    """跟随重定向解析短链（如微博 t.cn），返回最终真实 URL。
    yt-dlp 的 WeiboIE 只认 weibo.com / weibo.cn，不认 t.cn，故下载前先解析成真链。
    非短链或解析失败时返回 None（调用方按原链尝试，避免误伤）。
    短链多为国内服务，强制直连解析（不走全局代理，代理反而可能连不上）。"""
    if not url or _host_of(url) not in SHORT_LINK_HOSTS:
        return None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        # 强制直连（ProxyHandler({}) 覆盖任何全局代理设置）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as r:
            final = r.geturl()
            final = _unwrap_weibo_passport(final)  # 抠出中转页里的真链
            if final and final.rstrip("/") != url.rstrip("/"):
                # 小红书短链的中转页（404/login）把真链藏在 redirectPath 参数里，优先抠出
                redir = _extract_redirect_path(final)
                if redir:
                    return _ensure_xhs_www(redir)
                # 直接 302 到笔记页的情况：补 www. 前缀以满足 XiaoHongShuIE
                return _ensure_xhs_www(final)
            # 兜底：个别短链用 200 + <meta refresh>/JS 跳转，从 body 取跳转地址
            try:
                body = r.read().decode("utf-8", "ignore")
                m = re.search(r'content=["\']\s*\d+;\s*url=([^"\']+)', body, re.I)
                if m:
                    cand = urllib.parse.urljoin(url, m.group(1))
                    cand = _extract_redirect_path(cand) or cand
                    return _ensure_xhs_www(cand)
            except Exception:
                pass
            return None
    except Exception:
        return None


def is_valid_media_url(url):
    """校验用户输入的是合法 http(s) 视频链接。
    返回 (ok, msg)。若像本地路径/文件则给出明确提示，避免出现
    'Unsupported url scheme: \"c\"' 这类莫名其妙的报错。"""
    import re as _re
    s = (url or "").strip()
    if not s:
        return False, "URL 不能为空"
    # Windows 绝对路径：C:\... 或 C:/...（会触发 urllib 把 c: 当协议）
    if _re.match(r'^[A-Za-z]:[\\/]', s):
        return False, ("你输入的像是本地文件路径（%s…），不是视频链接。\n"
                       "请粘贴以 http(s):// 开头的视频网页地址。" % s[:40])
    if s.lower().startswith("file:"):
        return False, "你输入的是本地文件协议（file://），本工具只支持在线下载视频网页。\n请粘贴网页视频地址。"
    if not _re.match(r'^https?://', s, _re.IGNORECASE):
        return False, "URL 必须以 http:// 或 https:// 开头。当前输入：%s" % s[:60]
    return True, ""


# ---------------------------------------------------------------------------
# 下载（yt-dlp import API）
# ---------------------------------------------------------------------------
# run_download 会改写进程级 os.environ（代理 / PATH）供 deno 子进程与 yt-dlp
# 内部请求使用。多线程并发下载时这些全局变量会互相污染（一个任务设代理、
# 另一个清空），故用全局锁把整个下载段串行化——server.py 的多任务模式
# 由此获得确定性的环境语义，GUI 模式本就是一次一个，无感。
_DOWNLOAD_LOCK = threading.Lock()


def run_download(url, cookies_file, proxy, deno_path, ffmpeg_path, out_dir,
                 log_fn, done_fn, fmt=None, reencode=True, platform=None,
                 cc_host=None):
    import yt_dlp

    with _DOWNLOAD_LOCK:
        _run_download_locked(url, cookies_file, proxy, deno_path, ffmpeg_path,
                             out_dir, log_fn, done_fn, fmt=fmt,
                             reencode=reencode, platform=platform, cc_host=cc_host)


def _run_download_locked(url, cookies_file, proxy, deno_path, ffmpeg_path, out_dir,
                         log_fn, done_fn, fmt=None, reencode=True,
                         platform=None, cc_host=None):
    import yt_dlp

    os.makedirs(out_dir, exist_ok=True)
    ffmpeg_dir = os.path.dirname(ffmpeg_path) if ffmpeg_path else None

    # done_fn 只允许调用一次：run_download 内部各失败路径都会调，server.py 若再
    # 兜底调用会双重触发（前端收到两次"完成"、GUI 按钮状态抖动）。
    _done_state = {"called": False}

    def done_once(ok, path, err):
        if _done_state["called"]:
            return
        _done_state["called"] = True
        done_fn(ok, path, err)

    # 微博 t.cn 短链：yt-dlp 的 WeiboIE 不认，先解析成 weibo.com / weibo.cn 真链再下
    _resolved = resolve_short_url(url)
    if _resolved and _resolved != url:
        log_fn(f"ℹ 短链解析：{url} → {_resolved}")
        url = _resolved

    # deno 子进程 / yt-dlp 内部网络都要走代理。
    # no_proxy 只放本机回环 + CookieCloud 主机名（动态提取，不硬编码内网 IP）——
    # 保证拉过 cookie 的局域网服务后续请求仍直连，同时不把拓扑写进开源仓库。
    _no_proxy_hosts = ["localhost", "127.0.0.1"]
    if cc_host:
        _cc_h = _host_of(cc_host)
        if _cc_h and _cc_h not in _no_proxy_hosts:
            _no_proxy_hosts.append(_cc_h)
    env_proxy = proxy or ""
    if env_proxy:
        os.environ["HTTPS_PROXY"] = env_proxy
        os.environ["HTTP_PROXY"] = env_proxy
        os.environ["DENO_PROXY"] = env_proxy
        os.environ["DENO_NO_PROXY"] = ""
        os.environ["no_proxy"] = ",".join(_no_proxy_hosts)
    else:
        # 国内站点（B站/抖音/微博）建议直连，显式清空代理避免误走海外代理导致失败
        for _k in ("HTTPS_PROXY", "HTTP_PROXY", "DENO_PROXY", "ALL_PROXY"):
            os.environ.pop(_k, None)
        os.environ["no_proxy"] = "*"

    # yt-dlp 2026.08 的 js_runtimes 是 PATH 裸名查找，deno 目录须进 PATH
    deno_dir = os.path.dirname(deno_path) if deno_path else ""
    if deno_dir:
        cur_path = os.environ.get("PATH", "")
        if deno_dir not in cur_path.split(os.pathsep):
            os.environ["PATH"] = deno_dir + os.pathsep + cur_path

    state = {"last": 0.0}

    def progress_hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            pct = (got / total * 100) if total else 0.0
            speed = d.get("speed") or 0
            eta = d.get("eta") or 0
            if pct - state["last"] >= 1.0 or pct >= 100:
                state["last"] = pct
                spd = f"{speed/1024/1024:.1f} MB/s" if speed else "?"
                log_fn(f"  ↓ {pct:5.1f}%  {spd}  ETA {eta}s  {os.path.basename(d.get('filename',''))}")
        elif d.get("status") == "finished":
            log_fn(f"  ✓ 下载完成：{d.get('filename')}")
        elif d.get("status") == "error":
            log_fn(f"  ✗ 段错误：{d}")

    ydl_opts = {
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "format": fmt or "bv*+ba/best",
        "merge_output_format": "mkv",
        "quiet": True,
        "no_warnings": True,
        "proxy": proxy or None,
        "js_runtimes": {"deno": {}},
        "remote_components": ["ejs:github"],
        "retries": 15,
        "fragment_retries": 15,
        "retry_sleep": 5,
        "sleep_interval": 2,
        "cookiefile": cookies_file,
        "progress_hooks": [progress_hook],
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
    }
    # 平台特定选项（如 B站需要 Referer 头）
    _pconf = PLATFORMS.get(platform, {})
    _extra = _pconf.get("extra_opts", {}) or {}
    for _k, _v in _extra.items():
        if _k == "http_headers" and isinstance(_v, dict):
            ydl_opts["http_headers"].update(_v)
        else:
            ydl_opts[_k] = _v
    if ffmpeg_dir:
        ydl_opts["ffmpeg_location"] = ffmpeg_dir

    final_path = None
    try:
        log_fn("▶ 开始下载（yt-dlp + deno nsig 解算 + CookieCloud cookie）…")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            vid = info.get("id")
            title = info.get("title", vid)
            duration = info.get("duration") or 0
            # 定位实际产出文件：优先用 yt-dlp 记录的 requested_downloads[].filepath
            # （真实落盘路径）。旧实现用 glob(title.*) + cands[-1] 有两个静默坑：
            # ① title 含 glob 元字符（[ ] * ?）或 "/"（yt-dlp 替换为全角）时匹配
            #    失败，final_path=None 直接跳过转码；
            # ② 输出目录里有历史同名文件时 cands[-1] 会拿到旧文件——转码甚至
            #    删除错误的对象。requested_downloads 天然一一对应，无此问题。
            try:
                for rd in reversed(info.get("requested_downloads") or []):
                    fp = rd.get("filepath")
                    if fp and os.path.exists(fp):
                        final_path = fp
                        break
            except Exception:
                final_path = None
            if not final_path:
                # 兜底（图集/特殊 playlist 形态 requested_downloads 可能为空）
                import glob as _glob
                cands = sorted(_glob.glob(os.path.join(out_dir, f"{title}.*")))
                cands = [c for c in cands if c.lower().endswith((".mkv", ".mp4", ".webm", ".mov"))]
                final_path = cands[-1] if cands else None
        log_fn(f"✓ 下载完成：{title}（{(duration or 0):.0f}s）")

        # 始终输出 mp4：mkv 在不少手机 / Windows 自带播放器 / 别的机器上会无声或打不开，
        # 统一转成跨设备兼容的 mp4（音频强制 aac，视频兼容模式重编码为 H.264）。
        if final_path and ffmpeg_path:
            if final_path.lower().endswith(".mp4"):
                # 已是 mp4（如微博/部分平台返回单流 mp4，无需合并），直接采用，
                # 避免 ffmpeg 对同名文件做原地转封装报 "cannot edit in-place"。
                log_fn(f"✓ 已是 MP4 格式（跨设备兼容），直接使用：{os.path.basename(final_path)}")
            else:
                mp4 = os.path.splitext(final_path)[0] + ".mp4"
                vcodec = probe_video_codec(ffmpeg_path, final_path)
                # 手机/浏览器最兼容的是 H.264(yuv420p)。AV1/VP9/HEVC 很多设备解不了，
                # 兼容模式(默认)下重编码为 H.264；极速模式直接 copy（可能产出手机播不了的 AV1）。
                incompatible = vcodec in ("av1", "vp9", "hevc", "vp8", "h263")
                if reencode and incompatible:
                    log_fn(f"▶ 转 mp4（兼容模式：视频是 {vcodec.upper()}，重编码为 H.264 手机兼容，音频转 AAC）…")
                    cmd = [ffmpeg_path, "-y", "-i", final_path,
                           "-c:v", "libx264", "-crf", "22", "-preset", "veryfast",
                           "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "192k",
                           "-movflags", "+faststart", mp4]
                else:
                    if incompatible:
                        log_fn(f"⚠ 极速模式跳过重编码：视频是 {vcodec.upper()}，部分手机/播放器可能无法解码")
                    else:
                        log_fn(f"▶ 转封装 mp4（视频已是 {vcodec or 'H.264'}，原样保留，音频转 AAC）…")
                    cmd = [ffmpeg_path, "-y", "-i", final_path, "-c:v", "copy", "-c:a", "aac",
                           "-b:a", "192k", "-movflags", "+faststart", mp4]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8", errors="ignore",
                                      timeout=1800)
                if proc.returncode == 0 and os.path.exists(mp4) and os.path.getsize(mp4) > 0:
                    log_fn(f"✓ mp4 已生成：{mp4}")
                    final_path = mp4
                    # 删掉中间的 mkv 以省空间（重编码成功说明 mp4 已可播放）
                    try:
                        src = mp4[:-4] + ".mkv"
                        if os.path.exists(src):
                            os.remove(src)
                    except Exception:
                        pass
                else:
                    log_fn("⚠ mp4 转封装失败，保留原始下载文件")
                    log_fn(proc.stderr[-500:] if proc.stderr else "")
        done_once(True, final_path, None)
    except Exception as e:
        log_fn("✗ 下载失败：" + str(e))
        import traceback
        log_fn(traceback.format_exc())
        done_once(False, final_path, str(e))


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root):
        _ensure_tk()
        self.root = root
        root.title(f"ytcc-dl v{VERSION} · 多平台视频下载器")
        root.geometry("720x700")
        root.resizable(True, True)

        self.tools = locate_tools()

        f = ttk.Frame(root, padding=10)
        f.pack(fill="both", expand=True)

        # --- CookieCloud 区 ---
        ttk.Label(f, text="CookieCloud 服务器（绕过 Edge ABE 加密）", font=("", 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ttk.Label(f, text="Host").grid(row=1, column=0, sticky="w")
        self.e_host = ttk.Entry(f, width=30)
        self.e_host.insert(0, "http://your-cc-host:10888")
        self.e_host.grid(row=1, column=1, columnspan=2, sticky="ew", padx=4)

        ttk.Label(f, text="UUID").grid(row=2, column=0, sticky="w")
        self.e_uuid = ttk.Entry(f, width=30)
        self.e_uuid.insert(0, "your-cookiecloud-uuid")
        self.e_uuid.grid(row=2, column=1, columnspan=2, sticky="ew", padx=4)

        ttk.Label(f, text="密码").grid(row=3, column=0, sticky="w")
        self.e_pass = ttk.Entry(f, width=30, show="*")
        self.e_pass.grid(row=3, column=1, columnspan=2, sticky="ew", padx=4)

        self.btn_cc = ttk.Button(f, text="① 拉取 Cookies", command=self.on_fetch_cookies)
        self.btn_cc.grid(row=4, column=0, pady=4, sticky="w")
        self.lbl_cc = ttk.Label(f, text="尚未拉取", foreground="gray")
        self.lbl_cc.grid(row=4, column=1, columnspan=2, sticky="w", padx=4)

        # 保存配置：凭据写入本机配置文件，下次打开自动预填（密码 DPAPI 加密 + 30 天自毁）
        self.btn_save = ttk.Button(f, text="💾 保存配置", command=self.on_save_config)
        self.btn_save.grid(row=5, column=0, pady=4, sticky="w")
        self.lbl_save = ttk.Label(f, text="", foreground="gray", font=("", 8))
        self.lbl_save.grid(row=5, column=1, columnspan=2, sticky="w", padx=4)

        # --- 下载区 ---
        ttk.Label(f, text="下载设置", font=("", 10, "bold")).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(8, 4))

        ttk.Label(f, text="平台").grid(row=7, column=0, sticky="w")
        self.var_platform = tk.StringVar(value="YouTube")
        self.cb_plat = ttk.Combobox(f, textvariable=self.var_platform, width=16,
                                    values=list(PLATFORMS.keys()), state="readonly")
        self.cb_plat.grid(row=7, column=1, sticky="w", padx=4)
        self.cb_plat.bind("<<ComboboxSelected>>", self.on_platform_change)
        self.lbl_note = ttk.Label(f, text="", foreground="gray", font=("", 8))
        self.lbl_note.grid(row=7, column=2, sticky="w", padx=4)

        ttk.Label(f, text="视频 URL").grid(row=8, column=0, sticky="w")
        self.e_url = ttk.Entry(f, width=60)
        self.e_url.grid(row=8, column=1, columnspan=2, sticky="ew", padx=4)

        ttk.Label(f, text="画质").grid(row=9, column=0, sticky="w")
        self.var_quality = tk.StringVar(value="1080p")
        self.cb_q = ttk.Combobox(f, textvariable=self.var_quality, width=16,
                                values=list(QUALITY_PRESETS.keys()), state="readonly")
        self.cb_q.grid(row=9, column=1, sticky="w", padx=4)
        # 输出格式固定为 mp4（跨设备兼容，避免 mkv 在手机/别的机器上无声）；
        # 「兼容模式」复选框控制是否把视频重编码为 H.264（默认开，手机最稳）。
        self.var_reencode = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="兼容模式(重编码H.264)", variable=self.var_reencode).grid(
            row=9, column=2, sticky="w", padx=4)

        # 代理：默认「采用系统代理」+「一键代理」勾选框——不用填地址、不用选平台，
        # 开箱即走代理；取消勾选=一键全直连。下拉仅给想细调的人留着。
        ttk.Label(f, text="代理模式").grid(row=10, column=0, sticky="w")
        self.var_proxy_mode = tk.StringVar(value=PROXY_MODE_LABELS[PROXY_MODE_SYSTEM])
        self.cb_mode = ttk.Combobox(f, textvariable=self.var_proxy_mode, width=20,
                                    values=list(PROXY_MODE_LABELS.values()), state="readonly")
        self.cb_mode.grid(row=10, column=1, sticky="w", padx=4)
        self.cb_mode.bind("<<ComboboxSelected>>", self.on_platform_change)
        self.var_proxy_enabled = tk.BooleanVar(value=True)
        self.cb_proxy_on = ttk.Checkbutton(f, text="一键代理（取消=全直连）",
                                           variable=self.var_proxy_enabled,
                                           command=self.on_platform_change)
        self.cb_proxy_on.grid(row=10, column=2, sticky="w", padx=4)

        ttk.Label(f, text="代理地址").grid(row=11, column=0, sticky="w")
        self.e_proxy = ttk.Entry(f, width=30)
        self.e_proxy.grid(row=11, column=1, sticky="ew", padx=4)
        ttk.Label(f, text="留空=自动采用系统代理", foreground="gray", font=("", 8)).grid(
            row=11, column=2, sticky="w", padx=4)

        ttk.Label(f, text="自定义格式").grid(row=12, column=0, sticky="w")
        self.e_fmt = ttk.Entry(f, width=50)
        self.e_fmt.grid(row=12, column=1, columnspan=2, sticky="ew", padx=4)
        self.e_fmt.insert(0, "选「自定义」画质时生效，如 bv[height<=720]+ba/best")

        ttk.Label(f, text="输出目录").grid(row=13, column=0, sticky="w")
        self.e_out = ttk.Entry(f, width=50)
        self.e_out.insert(0, os.path.join(APP_DIR, "ytcc-dl_output"))
        self.e_out.grid(row=13, column=1, sticky="ew", padx=4)
        ttk.Button(f, text="浏览", command=self.on_browse).grid(row=13, column=2, padx=4)

        self.btn_dl = ttk.Button(f, text="② 开始下载", command=self.on_download)
        self.btn_dl.grid(row=14, column=0, pady=6, sticky="w")

        # --- 日志（放在所有控件下方，行号必须 > 下载按钮的 row，否则格子重叠）---
        ttk.Label(f, text="运行日志", font=("", 10, "bold")).grid(
            row=15, column=0, columnspan=3, sticky="w", pady=(8, 2))
        self.log = scrolledtext.ScrolledText(f, width=90, height=18, state="disabled",
                                             font=("Consolas", 9))
        self.log.grid(row=16, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(16, weight=1)
        f.columnconfigure(1, weight=1)

        self._load_saved_config()
        # 没保存过代理地址时，探测系统代理填进框里（可见可改；留空也会自动检测）
        if not self.e_proxy.get().strip():
            detected, source = detect_system_proxy()
            if detected:
                self.e_proxy.insert(0, detected)
                self.log_msg(f"✓ 已自动采用{source}：{detected}（可手改；留空也会自动检测）")
            else:
                self.log_msg("ℹ 未检测到系统代理，海外站点可能无法下载（可手动填代理地址）")
        self.on_platform_change()
        self.log_msg(f"ytcc-dl v{VERSION} 就绪 · 一键代理="
                     f"{'开' if self.var_proxy_enabled.get() else '关'}")
        self._check_tools()

    def _check_tools(self):
        miss = [k for k, v in self.tools.items() if not v]
        if miss:
            self.log_msg(f"⚠ 缺少工具二进制：{miss}（请确认 tools/ 下 deno.exe 与 ffmpeg.exe 存在）")
        else:
            self.log_msg(f"✓ 工具就绪：deno={os.path.basename(self.tools['deno'])}, "
                         f"ffmpeg={os.path.basename(self.tools['ffmpeg'])}")

    def log_msg(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.configure(state="disabled")
        self.log.see("end")

    def on_browse(self):
        d = filedialog.askdirectory(initialdir=self.e_out.get())
        if d:
            self.e_out.delete(0, "end")
            self.e_out.insert(0, d)

    def on_platform_change(self, event=None):
        """切换平台 / 代理模式时刷新提示：显示该平台本次到底走不走代理。

        代理地址框不再随平台变化——它是「海外代理地址」，由代理模式统一决定
        用不用它，切国内站不会把用户手填的地址冲掉。
        """
        plat = self.var_platform.get()
        note = PLATFORMS.get(plat, {}).get("note", "")
        effective = effective_proxy(plat, self.var_proxy_mode.get(), self.e_proxy.get(),
                                    enabled=self.var_proxy_enabled.get())
        if effective:
            route = "🌐 走代理"
        elif not self.var_proxy_enabled.get():
            route = "🏠 直连（一键代理已关）"
        elif proxy_mode_key(self.var_proxy_mode.get()) == PROXY_MODE_ALL:
            route = "🏠 直连（代理地址为空）"
        else:
            route = "🏠 直连"
        self.lbl_note.configure(text=f"{route} · {note}" if note else route)

    def _mode_for_save(self):
        """保存配置时把「一键代理」开关状态落进 proxy_mode，下次启动保持一致。"""
        if not self.var_proxy_enabled.get():
            return PROXY_MODE_NONE
        return proxy_mode_key(self.var_proxy_mode.get())

    # ------------------------------------------------------------------
    # 本地配置持久化：启动预填 / 手动保存 / 拉取成功后自动续期
    # ------------------------------------------------------------------
    def _load_saved_config(self):
        """启动时把本机保存的 CookieCloud 凭据 + 代理模式/地址预填进输入框。"""
        if ytcc_config is None:
            return
        cfg, status = ytcc_config.load_config()
        path = ytcc_config.config_path()
        if status == "expired":
            self.log_msg(
                f"ℹ 本地配置超过 {ytcc_config.CONFIG_MAX_AGE_DAYS} 天未使用已自动销毁，请重新填写")
        elif status == "error":
            self.log_msg("⚠ 本地配置文件损坏已忽略（重新填写后保存即可）")
        elif status == "ok":
            for entry, key in ((self.e_host, "cc_host"), (self.e_uuid, "cc_uuid"),
                               (self.e_pass, "cc_password"), (self.e_proxy, "proxy")):
                val = cfg.get(key) or ""
                if val:
                    entry.delete(0, "end")
                    entry.insert(0, val)
            # 旧配置无 proxy_mode 键 → 归一为 system（自动采用系统代理）
            mode_key = proxy_mode_key(cfg.get("proxy_mode"))
            self.var_proxy_mode.set(PROXY_MODE_LABELS[mode_key])
            self.var_proxy_enabled.set(mode_key != PROXY_MODE_NONE)
            saved = time.strftime("%Y-%m-%d %H:%M", time.localtime(cfg.get("saved_at", 0)))
            self.log_msg(f"✓ 已加载本机保存的配置（{saved} 保存，下次打开自动填入）")
            self.lbl_save.configure(text=f"配置来源：{path}", foreground="gray")
            if cfg.get("warning"):
                self.log_msg("⚠ " + cfg["warning"])

    def on_save_config(self):
        """手动保存：把当前四个字段写进本机配置文件。"""
        if ytcc_config is None:
            messagebox.showinfo("不可用", "当前运行环境不支持配置持久化")
            return
        host = self.e_host.get().strip()
        uuid = self.e_uuid.get().strip()
        pwd = self.e_pass.get().strip()
        if not (host and uuid and pwd):
            messagebox.showwarning("缺少参数",
                                   "保存配置需要完整的 Host / UUID / 密码（代理可留空）")
            return
        if not host.lower().startswith(("http://", "https://")):
            messagebox.showwarning("Host 格式", "CookieCloud 地址需以 http:// 或 https:// 开头")
            return
        try:
            path = ytcc_config.save_config(host, uuid, pwd, self.e_proxy.get().strip(),
                                           self._mode_for_save())
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        self.lbl_save.configure(text=f"已保存 → {path}", foreground="green")
        self.log_msg(f"✓ 配置已保存到 {path}"
                     f"（密码已加密；{ytcc_config.CONFIG_MAX_AGE_DAYS} 天不使用自动销毁）")

    def _autosave_config(self, host, uuid, pwd):
        """拉取 cookie 成功 = 凭据已验证，顺手刷新配置（顺带续期 30 天）。
        必须在主线程调用（会读输入框）。失败只警告不打断主流程。"""
        if ytcc_config is None:
            return
        try:
            path = ytcc_config.save_config(host, uuid, pwd, self.e_proxy.get().strip(),
                                           self._mode_for_save())
            self.lbl_save.configure(text=f"已自动保存 → {path}", foreground="green")
            self.log_msg("✓ 凭据验证成功，配置已自动保存（下次打开自动填入）")
        except Exception as e:
            self.log_msg(f"⚠ 配置自动保存失败（不影响本次使用）：{e}")

    def _cookie_domains(self, plat):
        return PLATFORMS.get(plat, {}).get("domains", ("youtube.com",))

    def _cookies_path(self):
        plat = self.var_platform.get()
        name = PLATFORMS.get(plat, {}).get("cookie_file", "cookies.txt")
        return os.path.join(APP_DIR, name)

    def on_fetch_cookies(self):
        host = self.e_host.get().strip()
        uuid = self.e_uuid.get().strip()
        pwd = self.e_pass.get().strip()
        plat = self.var_platform.get()
        if not (host and uuid and pwd):
            messagebox.showwarning("缺少参数", "请填 Host / UUID / 密码")
            return
        domains = self._cookie_domains(plat)
        cpath = self._cookies_path()
        self.btn_cc.configure(state="disabled")
        self.log_msg(f"▶ 从 CookieCloud 拉取 {host} / {uuid} （{plat} 域名）…")

        def work():
            try:
                text, n = fetch_cookies(host, uuid, pwd, domains=domains)
                with open(cpath, "w", encoding="utf-8") as fh:
                    fh.write(text)
                self.root.after(0, lambda: self.lbl_cc.configure(
                    text=f"已拉取 {n} 条 {plat} cookie → {os.path.basename(cpath)}",
                    foreground="green"))
                self.root.after(0, lambda: self.log_msg(
                    f"✓ CookieCloud 解密成功，{n} 条 {plat} cookie 已写入"))
                # 凭据已验证：顺手刷新本地配置（下次打开自动填入，并续期 30 天）
                self.root.after(0, lambda: self._autosave_config(host, uuid, pwd))
            except Exception as e:
                emsg = str(e)
                self.root.after(0, lambda: self.lbl_cc.configure(text="拉取失败", foreground="red"))
                self.root.after(0, lambda emsg=emsg: self.log_msg("✗ CookieCloud 拉取失败：" + emsg))
            finally:
                self.root.after(0, lambda: self.btn_cc.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    def on_download(self):
        url = self.e_url.get().strip()
        plat = self.var_platform.get()
        if not url:
            messagebox.showwarning("缺少参数", "请填视频 URL")
            return
        ok, msg = is_valid_media_url(url)
        if not ok:
            messagebox.showwarning("URL 无效", msg)
            return
        # 根据链接自动识别平台（避免选错导致 cookie/代理不匹配）
        detected = detect_platform(url)
        if detected and detected != self.var_platform.get():
            self.log_msg(f"ℹ 检测到链接属于「{detected}」，自动切换平台")
            self.var_platform.set(detected)
            self.on_platform_change()
        plat = self.var_platform.get()
        cpath = self._cookies_path()
        # 硬需求平台（YouTube）缺 cookie 直接拦截；其余平台缺 cookie 仅警告后尝试免登录
        conf = PLATFORMS.get(plat, {})
        if not os.path.exists(cpath):
            if conf.get("need_cookie"):
                messagebox.showwarning("尚未拉取 Cookie",
                                       f"{plat} 需要登录 cookie，请先点「① 拉取 Cookies」")
                return
            else:
                self.log_msg(f"⚠ 未检测到 {plat} cookie，将尝试免登录下载（部分视频会失败）")
                cpath = None
        if not (self.tools.get("deno") and self.tools.get("ffmpeg")):
            messagebox.showerror("缺少工具", "tools/ 下缺少 deno.exe 或 ffmpeg.exe")
            return

        # 默认自动采用系统代理；海外三站走代理、国内站点恒直连；
        # 「一键代理」取消勾选则强制全直连
        proxy = effective_proxy(plat, self.var_proxy_mode.get(), self.e_proxy.get(),
                                enabled=self.var_proxy_enabled.get())
        out_dir = self.e_out.get().strip() or os.path.join(APP_DIR, "ytcc-dl_output")
        quality = self.var_quality.get()
        cust = self.e_fmt.get().strip()
        if quality == "自定义":
            if (not cust) or ("生效" in cust):
                messagebox.showwarning("缺少自定义格式",
                                       "请先在『自定义格式』框填写 yt-dlp format 字符串")
                return
        fmt = build_format(quality, cust)
        reencode = self.var_reencode.get()
        self.btn_dl.configure(state="disabled")
        self.log_msg("=" * 60)
        self.log_msg(f"▶ 平台={plat}  画质={quality}  兼容模式={reencode}  "
                     f"代理={self.var_proxy_mode.get()} → {'启用' if proxy else '直连'}")

        def done(ok, path, err):
            if ok:
                self.root.after(0, lambda: self.log_msg(f"🎉 完成！文件：{path}"))
            else:
                self.root.after(0, lambda: self.log_msg("❌ 任务失败，见上方日志"))
            self.root.after(0, lambda: self.btn_dl.configure(state="normal"))

        def work():
            run_download(url, cpath, proxy, self.tools["deno"],
                         self.tools["ffmpeg"], out_dir,
                         lambda m: self.root.after(0, lambda: self.log_msg(m)),
                         done, fmt=fmt, reencode=reencode, platform=plat,
                         cc_host=self.e_host.get().strip())

        threading.Thread(target=work, daemon=True).start()


def parse_kv(argv):
    """把 ['--key','value','--flag'] 解析成 dict。"""
    d, i = {}, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                d[a] = argv[i + 1]
                i += 2
            else:
                d[a] = True
                i += 1
        else:
            i += 1
    return d


def smoke_mode(args):
    """无 GUI 命令行冒烟测试：拉 cookie + 下载。日志写 smoke.log 并回显。"""
    import time
    log_path = os.path.join(APP_DIR, "smoke.log")
    fh = open(log_path, "w", encoding="utf-8")

    def log(m):
        line = f"[{time.strftime('%H:%M:%S')}] {m}"
        # 控制台可能是 GBK 编码（无法显示 ▶/✗/✓ 等符号）或 windowed 下无 stdout，
        # print 失败绝不能影响文件日志写入，故单独 try。
        try:
            print(line)
        except Exception:
            pass
        fh.write(line + "\n")
        fh.flush()

    try:
        # 本地保存的配置作默认值，优先级：命令行显式参数 > 配置文件 > 平台默认
        cfg, cfg_status = ({}, "missing")
        if ytcc_config is not None:
            cfg, cfg_status = ytcc_config.load_config()
            if cfg_status == "expired":
                log(f"ℹ 本地配置超过 {ytcc_config.CONFIG_MAX_AGE_DAYS} 天未使用已自动销毁")
            elif cfg_status == "error":
                log("⚠ 本地配置文件损坏，已忽略")
        host = args.get("--host") or cfg.get("cc_host") or ""
        uuid = args.get("--uuid") or cfg.get("cc_uuid")
        password = args.get("--password") or cfg.get("cc_password")
        url = args.get("--url")
        if cfg_status == "ok" and (cfg.get("cc_host") or cfg.get("cc_uuid")):
            log("ℹ 使用本机保存的配置（--host/--uuid/--password/--proxy 可覆盖）")
        if not host:
            log("✗ 缺少 --host（CookieCloud 服务器地址，如 http://192.168.1.100:10888）")
            return 2
        # 代理模式优先：显式 --proxy-mode > 本机配置 > 默认智能分流
        _plat = args.get("--platform") or "YouTube"
        _conf = PLATFORMS.get(_plat, PLATFORMS["YouTube"])
        _mode = proxy_mode_key(args.get("--proxy-mode") or cfg.get("proxy_mode"))
        _enabled = "--no-proxy" not in args
        _raw_proxy = args.get("--proxy")
        if _raw_proxy is None:
            _raw_proxy = cfg.get("proxy")
            if not _raw_proxy and _mode == PROXY_MODE_SYSTEM:
                pass  # system 模式留空 → effective_proxy 自动读系统代理
            elif not _raw_proxy:
                _raw_proxy = _conf.get("proxy_default", "http://127.0.0.1:10808")
        proxy = effective_proxy(_plat, _mode, _raw_proxy, enabled=_enabled)
        log(f"ytcc-dl v{VERSION} · 代理模式={_mode} · 一键代理={'开' if _enabled else '关'}"
            f" · 本次{'走代理 ' + proxy if proxy else '直连'}")
        out_dir = args.get("--out") or os.path.join(APP_DIR, "smoke_out")
        reencode = "--no-reencode" not in args
        platform = _plat
        quality = args.get("--quality") or "1080p"
        cust = args.get("--format")
        fmt = build_format(quality, cust) if cust else build_format(quality)
        if not (uuid and password and url):
            log("✗ 缺少必需参数：--uuid / --password / --url")
            return 2
        ok, msg = is_valid_media_url(url)
        if not ok:
            log("✗ " + msg)
            return 2
        tools = locate_tools()
        if not (tools.get("deno") and tools.get("ffmpeg")):
            log("✗ 缺少 deno/ffmpeg 二进制（请确认 tools/ 下存在）")
            return 3

        domains = _conf.get("domains", ("youtube.com",))
        log(f"▶ 从 CookieCloud 拉取 {host} / {uuid} （{platform}）…")
        text, n = fetch_cookies(host, uuid, password, domains=domains)
        cookies_file = os.path.join(APP_DIR, "smoke_cookies.txt")
        with open(cookies_file, "w", encoding="utf-8") as f:
            f.write(text)
        log(f"✓ CookieCloud 解密成功，{n} 条 {platform} cookie → {cookies_file}")

        log(f"▶ 开始下载：{url} [platform={platform}] [format={fmt}] [reencode={reencode}]")
        run_download(url, cookies_file, proxy, tools["deno"], tools["ffmpeg"],
                     out_dir, log, lambda ok, p, e: None, fmt=fmt,
                     reencode=reencode, platform=platform, cc_host=host)
        log("✓ smoke 完成，详见上方日志")
        return 0
    except Exception as e:
        log("✗ smoke 失败：" + str(e))
        import traceback
        log(traceback.format_exc())
        return 1
    finally:
        fh.close()


def main():
    argv = sys.argv[1:]
    if "--version" in argv or "-V" in argv:
        # 纯文本输出，不加载 tkinter（窗口版 exe 下 stdout 可能不存在）
        try:
            print(f"ytcc-dl {VERSION}")
        except Exception:
            pass
        return
    _ensure_tk()
    if "--smoke" in argv:
        sys.exit(smoke_mode(parse_kv(argv)))
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
