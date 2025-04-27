import os
import shutil
import tempfile
import uuid
import json
import time
import logging
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, Body, Query
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
# Helper: label from height
# -----------------------
def label_from_height(h: int) -> str:
    if h >= 2160: return "4K"
    if h >= 1440: return "2K"
    if h >= 1080: return "1080p"
    if h >= 720:  return "720p"
    return f"{h}p"

# -----------------------
# Formats endpoint
# -----------------------
@app.get("/formats")
async def get_formats(url: str = Query(...)):
    ydl_opts = {"quiet": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    fmts = info.get("formats", [])
    video_res = {}
    audio_bs  = {}
    for f in fmts:
        # video-only streams
        if f.get("vcodec") != "none" and f.get("acodec") == "none":
            h = f.get("height")
            if h:
                # keep highest tbr per resolution
                if h not in video_res or f.get("tbr", 0) > video_res[h]["tbr"]:
                    video_res[h] = {"height": h, "tbr": f.get("tbr", 0)}
        # audio-only streams
        if f.get("acodec") != "none" and f.get("vcodec") == "none":
            abr = f.get("abr")
            if abr:
                if abr not in audio_bs or f.get("tbr", 0) > audio_bs[abr]["tbr"]:
                    audio_bs[abr] = {"abr": abr, "tbr": f.get("tbr", 0)}

    video_list = [
        {"height": h, "label": label_from_height(h)}
        for h in sorted(video_res.keys(), reverse=True)
    ]
    audio_list = [
        {"abr": abr, "label": f"{abr} kbps"}
        for abr in sorted(audio_bs.keys(), reverse=True)
    ]
    return {"video": video_list, "audio": audio_list}

# -----------------------
# HTML UI
# -----------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>YouTube Downloader</title>
  <style>
    body { background: #000; color: #fff; font-family: Arial,sans-serif; text-align:center; padding:2rem; }
    h1 { color:#FF0000; }
    input, select, button { margin:0.5rem; padding:0.5rem; font-size:1rem; }
    input, select { width:60%; max-width:400px; }
    button { background:#FF0000; color:#fff; border:none; cursor:pointer; }
    button:disabled { opacity:0.5; cursor:default; }
    #preview { margin-top:1rem; }
    progress { width:60%; max-width:400px; height:1rem; }
    #message { margin-top:1rem; }
    a.link { color:#FF0000; text-decoration:none; }
  </style>
</head>
<body>
  <h1>YouTube Downloader</h1>
  <input id="url" placeholder="Paste YouTube URL..." /><br>
  <div id="preview"></div><br>
  <select id="fmt">
    <optgroup label="Video">
      <option>mp4</option><option>webm</option><option>mkv</option><option>avi</option>
    </optgroup>
    <optgroup label="Audio">
      <option>mp3</option><option>aac</option><option>wav</option><option>flac</option><option>m4a</option><option>opus</option>
    </optgroup>
  </select>
  <select id="qual"></select><br>
  <button id="btn">Download</button><br><br>
  <progress id="prog" value="0" max="100"></progress>
  <span id="percent">0%</span>
  <div id="message"></div>

<script>
const btn = document.getElementById('btn');
const urlInput = document.getElementById('url');
const fmtSelect = document.getElementById('fmt');
const qualSelect = document.getElementById('qual');
const prog = document.getElementById('prog');
const percent = document.getElementById('percent');
const message = document.getElementById('message');
let lastPct = 0;

// Preview thumbnail & load formats
let formatsCache = null;
urlInput.addEventListener('blur', async () => {
  const url = urlInput.value.trim();
  const m = url.match(/[?&]v=([\\w-]{11})/);
  document.getElementById('preview').innerHTML = m
    ? `<img src="https://img.youtube.com/vi/${m[1]}/hqdefault.jpg" width="240">`
    : '';
  if (!url) return;
  const res = await fetch(`/formats?url=${encodeURIComponent(url)}`);
  formatsCache = await res.json();
  updateQualityOptions();
});

// Update quality options when format changes
fmtSelect.addEventListener('change', updateQualityOptions);
function updateQualityOptions() {
  if (!formatsCache) return;
  const fmt = fmtSelect.value;
  let opts = [];
  if (['mp4','webm','mkv','avi'].includes(fmt)) {
    opts = formatsCache.video.map(v =>
      `<option value="${v.height}">${v.label}</option>`
    );
  } else {
    opts = formatsCache.audio.map(a =>
      `<option value="${a.abr}">${a.label}</option>`
    );
  }
  qualSelect.innerHTML = opts.join('') || '<option>default</option>';
}

// Download flow
btn.onclick = async () => {
  const url = urlInput.value.trim();
  const fmt = fmtSelect.value;
  const quality = qualSelect.value;
  if (!url) return alert('Enter a YouTube URL.');
  btn.disabled = true; lastPct = 0;
  prog.value = 0; percent.innerText = '0%';
  message.innerText = 'Starting download...';

  const res = await fetch('/download', {
    method: 'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({url, fmt, quality})
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
      const pct = Math.floor(100*d.downloaded_bytes/d.total_bytes);
      if (pct>lastPct) {
        lastPct=pct; prog.value=pct; percent.innerText=pct+'%';
      }
    }
    if (d.status==='finished') {
      es.close(); message.innerText='Preparing download…';
      window.location.href=`/download/${task_id}`;
    }
    if (d.status==='error') {
      es.close(); alert('Download failed.'); btn.disabled=false;
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
async def start_download(
    background_tasks: BackgroundTasks,
    payload: dict = Body(...)
):
    url = payload["url"]
    fmt = payload.get("fmt", "mp4")
    quality = payload.get("quality")
    task_id = str(uuid.uuid4())
    progress_store[task_id] = {
        "status": "queued",
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "file_path": None
    }
    background_tasks.add_task(run_download, task_id, url, fmt, quality)
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
def run_download(task_id: str, url: str, fmt: str, quality: str):
    temp_dir = tempfile.mkdtemp(prefix="yt_dl_")
    def hook(d):
        st = progress_store[task_id]
        if d.get("status") == "downloading":
            st.update({
                "status": "downloading",
                "downloaded_bytes": d.get("downloaded_bytes", 0),
                "total_bytes": d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            })
        elif d.get("status") == "finished":
            st.update({"status": "finished", "file_path": d.get("filename")})

    opts = {
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
        "quiet": True,
        "progress_hooks": [hook]
    }

    audio_exts = {"mp3","aac","wav","flac","m4a","opus"}
    video_exts = {"mp4","webm","mkv","avi"}
    f = fmt.lower()

    if f in audio_exts:
        if not FFMPEG_EXISTS:
            progress_store[task_id]["status"] = "error"
            return
        # limit by abr
        abr = int(quality) if quality and quality.isdigit() else None
        if abr:
            opts["format"] = f"bestaudio[abr<={abr}]/bestaudio"
        else:
            opts["format"] = "bestaudio"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": f,
            "preferredquality": "192"
        }]

    elif f in video_exts:
        h = int(quality) if quality and quality.isdigit() else None
        if f == "mp4":
            if h:
                opts["format"] = f"bestvideo[height<={h}]+bestaudio/best"
            else:
                opts["format"] = "best[ext=mp4]/best"
        else:
            if not FFMPEG_EXISTS:
                progress_store[task_id]["status"] = "error"
                return
            if h:
                opts["format"] = f"bestvideo[height<={h}]+bestaudio/best"
            else:
                opts["format"] = "bestvideo+bestaudio"
            opts["postprocessors"] = [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": f
            }]

    else:
        progress_store[task_id]["status"] = "error"
        return

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as e:
        logger.error(f"Task {task_id} error: {e}")
        progress_store[task_id]["status"] = "error"
