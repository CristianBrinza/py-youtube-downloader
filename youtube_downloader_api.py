import os
import shutil
import tempfile
import uuid
import json
import time
import logging
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, Body
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
import yt_dlp

# -----------------------
# Logging config
# -----------------------
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("yt_downloader_api")

# -----------------------
# FFmpeg detection
# -----------------------
FFMPEG_EXISTS = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
if not FFMPEG_EXISTS:
    logger.warning("⚠️ ffmpeg/ffprobe not found: audio extraction & non-MP4 conversion disabled.")

# -----------------------
# FastAPI app + store
# -----------------------
app = FastAPI()
progress_store = {}  # task_id → { status, downloaded_bytes, total_bytes, file_path }

# -----------------------
# Middleware: logging
# -----------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    client = request.client.host if request.client else "unknown"
    logger.info(f"→ {request.method} {request.url} from {client}")
    resp = await call_next(request)
    logger.info(f"← {resp.status_code} to {client}")
    return resp

# -----------------------
# HTML UI
# -----------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>YouTube Downloader</title>
  <style>
    body { background: #000; color: #fff; font-family: Arial, sans-serif; text-align: center; padding: 2rem; }
    h1 { color: #FF0000; }
    input, select, button { margin: 0.5rem; padding: 0.5rem; font-size: 1rem; }
    input, select { width: 60%; max-width: 400px; }
    button { background: #FF0000; color: #fff; border: none; cursor: pointer; }
    button:disabled { opacity: 0.5; cursor: default; }
    #preview { margin-top: 1rem; }
    progress { width: 60%; max-width: 400px; height: 1rem; }
    #message { margin-top: 1rem; }
    a.link { color: #FF0000; text-decoration: none; }
  </style>
</head>
<body>
  <h1>YouTube Downloader</h1>
  <input type="text" id="url" placeholder="Paste YouTube URL..." />
  <div id="preview"></div><br>
  <select id="fmt">
    <optgroup label="Video">
      <option>mp4</option><option>webm</option><option>mkv</option><option>avi</option>
    </optgroup>
    <optgroup label="Audio">
      <option>mp3</option><option>aac</option><option>wav</option><option>flac</option><option>m4a</option><option>opus</option>
    </optgroup>
  </select>
  <button id="btn">Download</button><br><br>
  <progress id="prog" value="0" max="100"></progress>
  <span id="percent">0%</span>
  <div id="message"></div>

<script>
const btn = document.getElementById('btn');
const urlInput = document.getElementById('url');
const fmtSelect = document.getElementById('fmt');
const prog = document.getElementById('prog');
const percent = document.getElementById('percent');
const message = document.getElementById('message');
let lastPct = 0;

// Thumbnail preview
urlInput.addEventListener('input', () => {
  const m = urlInput.value.match(/[?&]v=([\\w-]{11})/);
  const id = m ? m[1] : null;
  document.getElementById('preview').innerHTML =
    id ? `<img src="https://img.youtube.com/vi/${id}/hqdefault.jpg" width="240">` : '';
});

btn.onclick = async () => {
  const url = urlInput.value.trim(), fmt = fmtSelect.value;
  if (!url) { alert('Enter a YouTube URL.'); return; }
  btn.disabled = true; lastPct = 0;
  document.getElementById('prog').value = 0;
  document.getElementById('percent').innerText = '0%';
  document.getElementById('message').innerText = 'Starting download...';

  const res = await fetch('/download', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({url, fmt})
  });
  if (!res.ok) {
    alert(await res.text());
    btn.disabled = false;
    return;
  }
  const { task_id } = await res.json();
  const es = new EventSource(`/progress/${task_id}`);
  es.onmessage = e => {
    const d = JSON.parse(e.data);
    if (d.total_bytes) {
      const pct = Math.floor(100 * d.downloaded_bytes / d.total_bytes);
      if (pct > lastPct) {
        lastPct = pct;
        prog.value = pct;
        percent.innerText = pct + '%';
      }
    }
    if (d.status === 'finished') {
      es.close();
      message.innerText = 'Preparing download…';
      // **Auto‐trigger download:**
      window.location.href = `/download/${task_id}`;
    }
    if (d.status === 'error') {
      es.close();
      alert('Download failed.');
      btn.disabled = false;
    }
  };
};
</script>
</body>
</html>"""

# -----------------------
# Start download task
# -----------------------
@app.post("/download")
async def start_download(background_tasks: BackgroundTasks,
                         payload: dict = Body(...)):
    url = payload.get("url")
    fmt = payload.get("fmt", "mp4")
    task_id = str(uuid.uuid4())
    progress_store[task_id] = {
        "status": "queued",
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "file_path": None
    }
    background_tasks.add_task(run_download, task_id, url, fmt)
    return {"task_id": task_id}

# -----------------------
# Progress SSE
# -----------------------
@app.get("/progress/{task_id}")
async def progress_sse(task_id: str):
    if task_id not in progress_store:
        raise HTTPException(404, "Task not found")
    def gen():
        while True:
            data = progress_store[task_id]
            yield f"data: {json.dumps(data)}\n\n"
            if data["status"] in ("finished", "error"):
                break
            time.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream")

# -----------------------
# Fetch completed file
# -----------------------
@app.get("/download/{task_id}")
async def fetch_file(task_id: str):
    info = progress_store.get(task_id)
    if not info:
        raise HTTPException(404, "Task not found")
    if info["status"] != "finished":
        raise HTTPException(400, "Not ready")
    return FileResponse(
        path=info["file_path"],
        media_type="application/octet-stream",
        filename=os.path.basename(info["file_path"])
    )

# -----------------------
# Download logic
# -----------------------
def run_download(task_id: str, url: str, fmt: str):
    temp_dir = tempfile.mkdtemp(prefix="yt_dl_")
    def hook(d):
        st = progress_store[task_id]
        if d.get("status") == "downloading":
            st["status"] = "downloading"
            st["downloaded_bytes"] = d.get("downloaded_bytes", 0)
            st["total_bytes"] = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
        elif d.get("status") == "finished":
            st["status"] = "finished"
            st["file_path"] = d.get("filename")

    opts = {
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
        "quiet": True,
        "progress_hooks": [hook]
    }

    audio = {"mp3","aac","wav","flac","m4a","opus"}
    video = {"mp4","webm","mkv","avi"}
    f = fmt.lower()

    if f in audio:
        if not FFMPEG_EXISTS:
            progress_store[task_id]["status"] = "error"
            return
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{"key":"FFmpegExtractAudio",
                                "preferredcodec":f,
                                "preferredquality":"192"}]
        })
    elif f in video:
        if f == "mp4":
            opts["format"] = "best[ext=mp4]/best"
        else:
            if not FFMPEG_EXISTS:
                progress_store[task_id]["status"] = "error"
                return
            opts.update({
                "format":"bestvideo+bestaudio",
                "postprocessors":[{"key":"FFmpegVideoConvertor","preferedformat":f}]
            })
    else:
        progress_store[task_id]["status"] = "error"
        return

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as e:
        logger.error(f"Task {task_id} error: {e}")
        progress_store[task_id]["status"] = "error"
