# -*- coding: utf-8 -*-
"""
ytcc-dl 使用教程视频 —— 自合成版
思路：不靠 AI 视频模型画汉字（CJK 必崩），改为
      PIL(微软雅黑) 画真实界面 + edge-tts 中文配音 + ffmpeg 组装。
      所有汉字由字体渲染，100% 正确。
"""
import os
import asyncio
import subprocess
from PIL import Image, ImageDraw, ImageFont

BASE = r"C:\Users\youqw\WorkBuddy\2026-07-18-10-52-20\ytcc-dl"
FFMPEG = os.path.join(BASE, "tools", "ffmpeg.exe")
FONT_R = r"C:\Windows\Fonts\msyh.ttc"
FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"
OUTDIR = os.path.join(BASE, "generated-videos")
WORK = os.path.join(OUTDIR, "_work_tutorial")
os.makedirs(WORK, exist_ok=True)

W, H = 1280, 720
PROXY = "http://127.0.0.1:10809"
# 在线神经语音（微软 Xiaoxiao）：自然度远高于 SAPI 的 Huihui
VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "-2%"


def f(size, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT_R, size)


# --------------------------------------------------------------------------
# 控件绘制
# --------------------------------------------------------------------------
def bg_gradient(d):
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(28 + 18 * t), int(42 + 26 * t), int(68 + 38 * t)))


def win_frame(d):
    wx, wy, ww, wh = 180, 46, 920, 566
    d.rounded_rectangle([wx, wy, wx + ww, wy + wh], radius=6, fill="#f0f0f0", outline="#4a4a4a", width=1)
    d.rounded_rectangle([wx, wy, wx + ww, wy + 34], radius=6, fill="#0078d4")
    d.rectangle([wx, wy + 20, wx + ww, wy + 34], fill="#0078d4")
    d.text((wx + 12, wy + 8), "ytcc-dl · 多平台视频下载器", font=f(14), fill="white")
    for bx, lab in [(wx + ww - 96, "─"), (wx + ww - 64, "□"), (wx + ww - 32, "✕")]:
        d.rectangle([bx, wy + 6, bx + 26, wy + 28], outline="#dbeafe")
        d.text((bx + 8, wy + 9), lab, font=f(12), fill="white")
    return wx, wy, ww, wh


def entry(d, x, y, w, value, masked=False, hint_color=None):
    h = 24
    d.rectangle([x, y, x + w, y + h], fill="white", outline="#a0a0a0")
    txt = "•" * min(len(value), 24) if masked else value
    d.text((x + 6, y + 4), txt, font=f(11), fill=hint_color or "#1a1a1a")
    return (x, y, x + w, y + h)


def combo(d, x, y, w, value):
    h = 24
    d.rectangle([x, y, x + w, y + h], fill="white", outline="#a0a0a0")
    d.text((x + 6, y + 4), value, font=f(11), fill="#1a1a1a")
    d.polygon([(x + w - 16, y + 9), (x + w - 6, y + 9), (x + w - 11, y + 16)], fill="#555555")
    return (x, y, x + w, y + h)


def button(d, x, y, w, text, primary=False):
    h = 26
    fill, fg = ("#0078d4", "white") if primary else ("#e1e1e1", "#1a1a1a")
    d.rounded_rectangle([x, y, x + w, y + h], radius=3, fill=fill, outline="#adadad")
    tw = d.textlength(text, font=f(11))
    d.text((x + (w - tw) / 2, y + 6), text, font=f(11), fill=fg)
    return (x, y, x + w, y + h)


def check(d, x, y, text, checked=True):
    d.rectangle([x, y + 4, x + 13, y + 17], fill="white", outline="#707070")
    if checked:
        d.line([(x + 3, y + 10), (x + 6, y + 14), (x + 10, y + 5)], fill="#1a1a1a", width=2)
    d.text((x + 18, y + 3), text, font=f(11), fill="#1a1a1a")
    return (x, y, x + 22 + len(text) * 12, y + 20)


def logbox(d, x, y, w, h, lines):
    d.rectangle([x, y, x + w, y + h], fill="white", outline="#a0a0a0")
    yy = y + 6
    for ln, col in lines:
        d.text((x + 8, yy), ln, font=f(11), fill=col)
        yy += 17
    return (x, y, x + w, y + h)


def caption(d, text):
    d.rectangle([0, H - 96, W, H], fill=(14, 19, 30))
    font = f(17)
    tw = d.textlength(text, font=font)
    d.text(((W - tw) / 2, H - 62), text, font=font, fill="#ffffff")


def highlight(d, box, tag, color="#ff8c00"):
    x0, y0, x1, y1 = box
    p = 6
    d.rounded_rectangle([x0 - p, y0 - p, x1 + p, y1 + p], radius=6, outline=color, width=3)
    tw = d.textlength(tag, font=f(12, True))
    d.rounded_rectangle([x0 - p, y0 - p - 27, x0 - p + tw + 16, y0 - p - 5], radius=4, fill=color)
    d.text((x0 - p + 8, y0 - p - 25), tag, font=f(12, True), fill="white")


# --------------------------------------------------------------------------
# 窗口内容（按真实 ytcc_dl.py GUI 布局 1:1 还原）
# --------------------------------------------------------------------------
def draw_app(st):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    bg_gradient(d)
    wx, wy, ww, wh = win_frame(d)

    cx = wx + 18
    y = wy + 34 + 12
    reg = {}

    # CookieCloud 区
    d.text((cx, y), "CookieCloud 服务器（绕过 Edge ABE 加密）", font=f(12, True), fill="#1a1a1a")
    y += 22
    for lab, val, masked in [("Host", st["host"], False), ("UUID", st["uuid"], False), ("密码", st["pwd"], True)]:
        d.text((cx, y + 5), lab, font=f(11), fill="#1a1a1a")
        reg[lab] = entry(d, cx + 110, y, 470, val, masked=masked)
        y += 30
    d.text((cx, y + 5), st.get("cc_status_text", "尚未拉取"), font=f(11),
           fill=st.get("cc_status_color", "#6b6b6b"))
    reg["cc"] = button(d, cx + 110, y, 130, "① 拉取 Cookies")
    y += 34

    # 下载设置
    d.text((cx, y + 4), "下载设置", font=f(12, True), fill="#1a1a1a")
    y += 24
    d.text((cx, y + 5), "平台", font=f(11), fill="#1a1a1a")
    reg["plat"] = combo(d, cx + 110, y, 140, st["platform"])
    d.text((cx + 264, y + 5), st.get("note", ""), font=f(9), fill="#6b6b6b")
    y += 30
    d.text((cx, y + 5), "视频 URL", font=f(11), fill="#1a1a1a")
    reg["url"] = entry(d, cx + 110, y, 620, st["url"])
    y += 30
    d.text((cx, y + 5), "画质", font=f(11), fill="#1a1a1a")
    reg["q"] = combo(d, cx + 110, y, 140, st["quality"])
    check(d, cx + 264, y + 2, "输出 MP4（已默认·跨设备兼容）")
    y += 30
    d.text((cx, y + 5), "代理", font=f(11), fill="#1a1a1a")
    reg["proxy"] = entry(d, cx + 110, y, 200, st["proxy"])
    check(d, cx + 330, y + 2, "兼容模式(重编码H.264)")
    y += 30
    d.text((cx, y + 5), "自定义格式", font=f(11), fill="#1a1a1a")
    entry(d, cx + 110, y, 620, "选「自定义」画质时生效，如 bv[height<=720]+ba/best", hint_color="#8a8a8a")
    y += 30
    d.text((cx, y + 5), "输出目录", font=f(11), fill="#1a1a1a")
    entry(d, cx + 110, y, 560, st["outdir"])
    button(d, cx + 682, y, 48, "浏览")
    y += 36

    reg["dl"] = button(d, cx, y, 130, "② 开始下载", primary=True)
    y += 36
    d.text((cx, y), "运行日志", font=f(12, True), fill="#1a1a1a")
    y += 22
    reg["log"] = logbox(d, cx, y, 730, 96, st["logs"])

    caption(d, st["caption"])
    return img, reg


# --------------------------------------------------------------------------
# 幻灯片
# --------------------------------------------------------------------------
DEFAULT_LOGS = [("✓ 工具就绪：deno=deno.exe, ffmpeg=ffmpeg.exe", "#107c10")]

SLIDES = [
    dict(
        name="01_cover",
        cover=True,
        title="ytcc-dl 使用教程",
        sub="支持 6 大平台 · 三步完成下载 · 单文件 Windows 工具",
        narr="ytcc-dl，一个把 yt-dlp 封装成单文件界面的多平台视频下载器。"
             "支持 YouTube、X、B站、抖音、微博、小红书，一共六个平台。"
             "下面用一个微博短链，演示完整流程。",
    ),
    dict(
        name="02_cookie",
        host="http://your-cc-host:10888", uuid="your-cc-uuid", pwd="your-cc-password",
        platform="YouTube", url="", quality="1080p", proxy="http://127.0.0.1:10808",
        outdir=r"...\ytcc-dl\ytdub_output", logs=DEFAULT_LOGS,
        cc_status_text="已拉取 32 条 YouTube cookie", cc_status_color="#107c10",
        caption="第一步：填 CookieCloud 地址 / UUID / 密码，点「① 拉取 Cookies」",
        narr="第一步，配置登录态。填入你的 CookieCloud 服务器地址、UUID 和密码，"
             "然后点左边的「拉取 Cookies」按钮。"
             "它会自动绕过浏览器的 ABE 加密，把登录 cookie 取回来，"
             "成功后右边会显示拉到了多少条。",
        hl=[("cc", "① 点这里")],
    ),
    dict(
        name="03_url",
        host="http://your-cc-host:10888", uuid="your-cc-uuid", pwd="your-cc-password",
        platform="微博", url="http://t.cn/AXlxbkzB", quality="1080p", proxy="",
        outdir=r"...\ytcc-dl\ytdub_output", logs=DEFAULT_LOGS,
        note="公开视频免登录；支持 t.cn 短链直连解析",
        cc_status_text="已拉取 32 条 cookie", cc_status_color="#107c10",
        caption="第二步：粘贴链接（t.cn 短链自动解析），平台已自动切到「微博」",
        narr="第二步，粘贴视频链接。直接粘贴 t.cn 这样的短链也没问题，"
             "工具会自动解析成真实地址，还会根据域名自动把平台切到微博。"
             "画质、代理、输出目录都可以按需调整。",
        hl=[("url", "② 粘贴链接"), ("plat", "自动识别")],
    ),
    dict(
        name="04_download",
        host="http://your-cc-host:10888", uuid="your-cc-uuid", pwd="your-cc-password",
        platform="微博", url="http://t.cn/AXlxbkzB", quality="1080p", proxy="",
        outdir=r"...\ytcc-dl\ytdub_output",
        logs=[("▶ 开始下载（yt-dlp + deno nsig 解算 + CookieCloud cookie）…", "#1a1a1a"),
              ("[download] 100% of 12.4MiB in 00:08", "#1a1a1a"),
              ("✓ 转封装为 MP4 完成（跨设备兼容）", "#107c10"),
              ("✓ 已保存到：ytdub_output\\微博视频.mp4", "#107c10")],
        note="公开视频免登录；支持 t.cn 短链直连解析",
        cc_status_text="已拉取 32 条 cookie", cc_status_color="#107c10",
        caption="第三步：点「② 开始下载」，日志实时滚动，完成后统一输出 MP4",
        narr="第三步，点「开始下载」。下方运行日志会实时输出进度。"
             "下载完成后，视频会统一转封装成 MP4，保存在输出目录里，"
             "手机和电脑都能正常播放。",
        hl=[("dl", "③ 开始下载"), ("log", "实时日志")],
    ),
    dict(
        name="05_outro",
        cover=True, title="就是这么简单",
        sub="ytcc-dl · 仅供个人学习使用，请遵守各平台服务条款",
        narr="以上就是 ytcc-dl 的完整用法。"
             "提醒一句：这个工具仅供个人学习使用，请遵守各个平台的服务条款。",
    ),
]


def make_cover(slide):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    bg_gradient(d)
    t = slide["title"]
    ft = f(64, True)
    tw = d.textlength(t, font=ft)
    d.text(((W - tw) / 2, 250), t, font=ft, fill="#ffffff")
    d.line([(W / 2 - 220, 340), (W / 2 + 220, 340)], fill="#0078d4", width=4)
    s = slide["sub"]
    fs = f(24)
    sw = d.textlength(s, font=fs)
    d.text(((W - sw) / 2, 372), s, font=fs, fill="#9fd0ff")
    caption(d, slide["narr"][:40] + "…")
    return img


def build_images():
    paths = []
    for s in SLIDES:
        if s.get("cover"):
            img = make_cover(s)
        else:
            img, reg = draw_app(s)
            d = ImageDraw.Draw(img)
            for key, tag in s.get("hl", []):
                if key in reg:
                    highlight(d, reg[key], tag)
        p = os.path.join(WORK, s["name"] + ".png")
        img.save(p)
        paths.append(p)
        print("  图:", s["name"])
    return paths


async def _edge_all(proxy):
    """在线神经语音批量合成。"""
    import edge_tts
    paths = []
    for s in SLIDES:
        p = os.path.join(WORK, s["name"] + ".mp3")
        comm = edge_tts.Communicate(s["narr"], VOICE, proxy=proxy, rate=RATE)
        await comm.save(p)
        paths.append(p)
    return paths


def _sapi_all():
    """回退：Windows SAPI 离线中文合成。"""
    import win32com.client
    sp = win32com.client.Dispatch("SAPI.SpVoice")
    voices = sp.GetVoices()
    zh_idx = 0
    for i in range(voices.Count):
        if "Chinese" in voices.Item(i).GetDescription():
            zh_idx = i
            break
    sp.Voice = voices.Item(zh_idx)
    sp.Rate = 0
    sp.Volume = 100
    print("  使用语音:", voices.Item(zh_idx).GetDescription())
    paths = []
    for s in SLIDES:
        p = os.path.join(WORK, s["name"] + ".wav")
        st = win32com.client.Dispatch("SAPI.SpFileStream")
        st.Open(p, 3)
        sp.AudioOutputStream = st
        sp.Speak(s["narr"])
        st.Close()
        paths.append(p)
        print("  音:", s["name"])
    return paths


def build_audio():
    """优先在线神经语音（自然度高），失败回退 Windows SAPI 离线。"""
    for proxy in (None, PROXY):
        try:
            paths = asyncio.run(_edge_all(proxy))
            print("  引擎: edge-tts 在线神经语音 %s（proxy=%s, rate=%s）"
                  % (VOICE, proxy or "直连", RATE))
            for s in SLIDES:
                print("  音:", s["name"])
            return paths
        except Exception as e:
            print("  edge-tts 失败(proxy=%s): %s —— 尝试下一个" % (proxy or "直连", type(e).__name__))
    print("  引擎: Windows SAPI 离线（回退）")
    return _sapi_all()


def assemble(imgs, auds):
    segs = []
    for i, (im, au) in enumerate(zip(imgs, auds)):
        seg = os.path.join(WORK, "seg_%02d.mp4" % i)
        cmd = [FFMPEG, "-y", "-loop", "1", "-framerate", "30", "-i", im,
               "-i", au, "-c:v", "libx264", "-tune", "stillimage",
               "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p",
               "-shortest", seg]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-1500:])
            raise SystemExit("ffmpeg 段合成失败: " + im)
        segs.append(seg)
        print("  段:", os.path.basename(seg))

    lst = os.path.join(WORK, "concat.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for s in segs:
            fh.write("file '%s'\n" % s.replace("\\", "/"))
    final = os.path.join(OUTDIR, "ytcc-dl_使用教程_v2_自然语音.mp4")
    cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", final]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        raise SystemExit("ffmpeg 拼接失败")
    return final


def main():
    print("1/3 画界面图…")
    imgs = build_images()
    print("2/3 生成中文配音（在线神经语音优先，失败回退 SAPI）…")
    auds = build_audio()
    print("3/3 ffmpeg 组装…")
    final = assemble(imgs, auds)
    print("\n完成:", final)
    print("大小: %.1f MB" % (os.path.getsize(final) / 1024 / 1024))


if __name__ == "__main__":
    main()
