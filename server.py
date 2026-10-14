"""
ytcc-dl Web 入口（容器版）—— 包装 ytcc_dl.run_download，提供轻量 Web UI + SSE 进度。

设计要点：
- 不依赖 GUI（tkinter 懒加载，无头环境 import 不炸）
- 配置全部来自环境变量（COOKIECLOUD_HOST / CC_UUID / CC_PASSWORD / PROXY_OVERSEAS / OUT_DIR）
- 后台线程跑下载，SSE 把 run_download 的 log_fn 实时推给前端
- 下载产物写到 OUT_DIR（容器已挂卷）

用法（容器内）：
    python server.py
浏览器访问 http://<极空间IP>:8899
"""
import os
import sys
import json
import threading
import time
import importlib.util

# ---- 懒加载 tkinter：容器无头环境 import tkinter 会失败，包一层 try ----
try:
    import tkinter  # noqa: F401  （若镜像没装 python3-tk，这里失败但不影响下载逻辑）
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ytcc_dl", os.path.join(HERE, "ytcc_dl.py"))
ytcc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ytcc)

from flask import Flask, request, Response, render_template_string

app = Flask(__name__)

# 简单内存状态：task_id -> {status, logs[]}
STATE = {}
_LOCK = threading.Lock()

PAGE = """
<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>ytcc-dl</title>
<style>body{font-family:system-ui;max-width:760px;margin:24px auto;padding:0 16px}
textarea,input,select{width:100%;box-sizing:border-box;margin:6px 0;padding:8px;font-size:14px}
button{background:#2563eb;color:#fff;border:0;padding:10px 16px;border-radius:6px;cursor:pointer}
pre{background:#0b1020;color:#cde;padding:12px;border-radius:8px;max-height:320px;overflow:auto;white-space:pre-wrap}
.row{display:flex;gap:8px}.row>*{flex:1}</style></head>
<body>
<h2>ytcc-dl · 多平台视频下载</h2>
<div class="row">
  <select id="platform">
    <option>YouTube</option><option>X (Twitter)</option><option>Bilibili (B站)</option>
    <option>抖音 (Douyin)</option><option>微博 (Weibo)</option><option>小红书 (Xiaohongshu)</option>
  </select>
  <input id="quality" value="1080p" placeholder="画质 如 1080p / 720p">
</div>
<input id="url" placeholder="粘贴视频链接（支持各平台短链，如 t.cn / v.douyin.com / xhslink.com）">
<button onclick="start()">开始下载</button>
<h3>进度</h3>
<pre id="log">等待…</pre>
<script>
function start(){
  const body=new URLSearchParams({url:url.value,platform:platform.value,quality:quality.value});
  fetch('/download',{method:'POST',body}).then(r=>r.json()).then(j=>{
    const es=new EventSource('/progress/'+j.task_id);
    es.onmessage=e=>{log.textContent+=e.data+"\n";log.scrollTop=log.scrollHeight;};
  });
}
</script>
</body></html>
"""


def _worker(task_id, url, platform, quality):
    logs = []
    with _LOCK:
        STATE[task_id]["logs"] = logs

    def log_fn(msg):
        logs.append(msg)
        with _LOCK:
            STATE[task_id]["updated"] = time.time()

    def done_fn(ok, path, err):
        with _LOCK:
            STATE[task_id]["status"] = "done" if ok else "error"
            STATE[task_id]["result"] = path
            STATE[task_id]["error"] = err

    try:
        host = os.environ.get("COOKIECLOUD_HOST", "http://192.168.31.116:10888")
        uuid = os.environ.get("CC_UUID", "")
        password = os.environ.get("CC_PASSWORD", "")
        conf = ytcc.PLATFORMS.get(platform, ytcc.PLATFORMS["YouTube"])
        proxy = os.environ.get("PROXY_OVERSEAS", "") if conf.get("proxy_default") else ""
        out_dir = os.environ.get("OUT_DIR", "/app/output")
        os.makedirs(out_dir, exist_ok=True)

        # 拉 cookie（若配置了凭据）
        cookies_file = None
        if uuid and password:
            try:
                text, n = ytcc.fetch_cookies(host, uuid, password, domains=conf.get("domains"))
                cookies_file = os.path.join(out_dir, "cookies.txt")
                with open(cookies_file, "w", encoding="utf-8") as f:
                    f.write(text)
                log_fn(f"✓ CookieCloud 拉取成功（{n} 条 {platform} cookie）")
            except Exception as e:
                log_fn(f"⚠ 拉取 cookie 失败（将尝试免登录）：{e}")
                cookies_file = None

        tools = ytcc.locate_tools()
        fmt = ytcc.build_format(quality)
        ytcc.run_download(url, cookies_file, proxy, tools["deno"], tools["ffmpeg"],
                          out_dir, True, log_fn, done_fn, fmt=fmt, reencode=True, platform=platform)
        if STATE[task_id]["status"] not in ("done", "error"):
            done_fn(True, out_dir, None)
    except Exception as e:
        done_fn(False, None, str(e))


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url", "").strip()
    platform = request.form.get("platform", "YouTube").strip()
    quality = request.form.get("quality", "1080p").strip()
    ok, msg = ytcc.is_valid_media_url(url)
    if not ok:
        return {"task_id": None, "error": msg}, 400
    task_id = str(int(time.time() * 1000))
    with _LOCK:
        STATE[task_id] = {"status": "running", "logs": [], "updated": time.time()}
    threading.Thread(target=_worker, args=(task_id, url, platform, quality), daemon=True).start()
    return {"task_id": task_id}


@app.route("/progress/<task_id>")
def progress(task_id):
    def gen():
        last = -1
        while True:
            with _LOCK:
                st = STATE.get(task_id)
                if not st:
                    yield "data: (任务不存在)\n\n"; break
                n = len(st["logs"])
                if n != last:
                    for line in st["logs"][last:]:
                        yield f"data: {line}\n\n"
                    last = n
                if st["status"] in ("done", "error"):
                    yield f"data: === {'完成' if st['status']=='done' else '失败'} ===\n\n"
                    break
            time.sleep(0.5)
    return Response(gen(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8899, threaded=True)
