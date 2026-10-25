"""
ytcc-dl Web 入口（容器版）—— 包装 ytcc_dl.run_download，提供轻量 Web UI + SSE 进度。

设计要点：
- 不依赖 GUI（tkinter 懒加载，无头环境 import 不炸）
- 配置优先级：环境变量（COOKIECLOUD_HOST / CC_UUID / CC_PASSWORD / PROXY_OVERSEAS / OUT_DIR）
  > 本机保存的配置文件（本地直跑时与 GUI 共用同一份凭据，见 ytcc_config.py；
  容器里无 APPDATA，自然回落为空，行为与原来一致）
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
# 运行时私有目录（cookie 等凭证的落点）：优先 /tmp，容器/服务端均不与产物目录混淆
APP_RUNTIME_DIR = os.environ.get("YTCC_RUNTIME_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else HERE, ".ytcc_runtime")
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
    {{ platform_options|safe }}
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


def _platform_options():
    """平台下拉从 ytcc.PLATFORMS 动态生成——加平台只改主程序一处。"""
    return "\n    ".join(
        f"<option{' selected' if name == 'YouTube' else ''}>{name}</option>"
        for name in ytcc.PLATFORMS)


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
        # 环境变量优先；本地直跑（无环境变量）时回退到本机保存的配置，
        # 与 GUI 共用同一份凭据。容器里无 APPDATA 目录，load 自然返回空，行为不变。
        srv_cfg = {}
        if getattr(ytcc, "ytcc_config", None) is not None:
            srv_cfg, _st = ytcc.ytcc_config.load_config()
        host = os.environ.get("COOKIECLOUD_HOST") or srv_cfg.get("cc_host", "")
        uuid = os.environ.get("CC_UUID") or srv_cfg.get("cc_uuid", "")
        password = os.environ.get("CC_PASSWORD") or srv_cfg.get("cc_password", "")
        conf = ytcc.PLATFORMS.get(platform, ytcc.PLATFORMS["YouTube"])
        proxy = (os.environ.get("PROXY_OVERSEAS") or srv_cfg.get("proxy") or "") if conf.get("proxy_default") else ""
        out_dir = os.environ.get("OUT_DIR", "/app/output")
        os.makedirs(out_dir, exist_ok=True)

        # 拉 cookie（若配置了凭据）
        cookies_file = None
        if uuid and password and host:
            try:
                text, n = ytcc.fetch_cookies(host, uuid, password, domains=conf.get("domains"))
                # cookie 是登录凭证：放独立子目录，绝不混进 OUT_DIR 产物
                # （用户同步/分享下载目录时不会顺手把凭证带出去）。
                cc_dir = os.path.join(APP_RUNTIME_DIR, "_cc")
                os.makedirs(cc_dir, exist_ok=True)
                cookies_file = os.path.join(cc_dir, f"{platform}_cookies.txt")
                with open(cookies_file, "w", encoding="utf-8") as f:
                    f.write(text)
                log_fn(f"✓ CookieCloud 拉取成功（{n} 条 {platform} cookie → {cookies_file}）")
            except Exception as e:
                log_fn(f"⚠ 拉取 cookie 失败（将尝试免登录）：{e}")
                cookies_file = None
        elif not host:
            log_fn("⚠ 未配置 COOKIECLOUD_HOST，跳过拉取 cookie，尝试免登录下载")

        tools = ytcc.locate_tools()
        fmt = ytcc.build_format(quality)
        ytcc.run_download(url, cookies_file, proxy, tools["deno"], tools["ffmpeg"],
                          out_dir, log_fn, done_fn, fmt=fmt, reencode=True,
                          platform=platform, cc_host=host)
    except Exception as e:
        done_fn(False, None, str(e))


@app.route("/")
def index():
    return render_template_string(PAGE, platform_options=_platform_options())


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
