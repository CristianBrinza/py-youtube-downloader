import os
import shutil
import tempfile
import uuid
import json
import time
import logging
from fastapi import FastAPI, HTTPException, Query, Request, BackgroundTasks, Body
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
import yt_dlp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("yt_downloader_api")

# Detect ffmpeg/ffprobe
FFMPEG_EXISTS = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
if not FFMPEG_EXISTS:
    logger.warning(
        "⚠️ ffmpeg/ffprobe not found: audio extraction & non-MP4 conversion will be disabled."
    )

app = FastAPI()

# In-memory progress store
progress_store = {}


@app.middleware("http")
async def log_requests(request: Request, call_next):
    client = request.client.host if request.client else "unknown"
    logger.info(f"→ {request.method} {request.url} from {client}")
    resp = await call_next(request)
    logger.info(f"← {resp.status_code} to {client}")
    return resp


@app.get("/", response_class=HTMLResponse)
async def home():
    return """<!DOCTYPE html>
<html>
<head><title>YouTube Downloader</title></head>
<body>
  <h1>YouTube Downloader</h1>
  <input type="text" id="url" placeholder="YouTube URL" size="50" />
  <select id="fmt">
    <option value="mp4">MP4</option>
    <option value="mp3">MP3</option>
    <option value="avi">AVI</option>
    <option value="wav">WAV</option>
    <option value="m4a">M4A</option>
  </select>
  <button id="btn">Download</button>
  <br/><br/>
  <progress id="prog" value="0" max="100" style="width:300px;"></progress>
  <span id="percent"></span>
  <div id="link"></div>

  <script>
    document.getElementById("btn").onclick = async () => {
      const url = document.getElementById("url").value;
      const fmt = document.getElementById("fmt").value;
      const res = await fetch("/download", {
        method: "POST",
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, fmt })
      });
      if (!res.ok) {
        alert("Error: " + await res.text());
        return;
      }
      const { task_id } = await res.json();
      const es = new EventSource(`/progress/${task_id}`);
      es.onmessage = e => {
        const data = JSON.parse(e.data);
        if (data.total_bytes) {
          const pct = Math.floor(100 * data.downloaded_bytes / data.total_bytes);
          document.getElementById("prog").value = pct;
          document.getElementById("percent").innerText = pct + "%";
        }
        if (data.status === "finished") {
          es.close();
          document.getElementById("link").innerHTML =
            `<a href="/download/${task_id}">Download your file</a>`;
        }
      };
    };
  </script>
</body>
</html>"""


@app.post("/download")
async def start_download(
        background_tasks: BackgroundTasks,
        url: str = Body(...),
        fmt: str = Body("mp4")
):
    # Initialize progress
    task_id = str(uuid.uuid4())
    progress_store[task_id] = {
        "status": "queued",
        "downloaded_bytes": 0,
        "total_bytes": None,
        "file_path": None
    }
    # Launch background
    background_tasks.add_task(run_download, task_id, url, fmt)
    return {"task_id": task_id}


@app.get("/progress/{task_id}")
async def progress_sse(task_id: str):
    if task_id not in progress_store:
        raise HTTPException(status_code=404, detail="Task not found")

    def event_generator():
        while True:
            data = progress_store.get(task_id)
            if not data:
                break
            yield f"data: {json.dumps(data)}\n\n"
            if data['status'] in ('finished', 'error'):
                break
            time.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/download/{task_id}")
async def download_task(task_id: str):
    info = progress_store.get(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="Task not found")
    if info['status'] != 'finished':
        raise HTTPException(status_code=400, detail="Task not completed yet")
    return FileResponse(
        path=info['file_path'],
        media_type="application/octet-stream",
        filename=os.path.basename(info['file_path'])
    )


# Actual download logic

def run_download(task_id: str, url: str, fmt: str):
    temp_dir = tempfile.mkdtemp(prefix="yt_dl_")

    def hook(d):
        state = progress_store.get(task_id)
        if not state:
            return
        status = d.get('status')
        if status == 'downloading':
            state.update({
                'status': 'downloading',
                'downloaded_bytes': d.get('downloaded_bytes', 0),
                'total_bytes': d.get('total_bytes') or d.get('total_bytes_estimate')
            })
        elif status == 'finished':
            state['status'] = 'finished'
            state['file_path'] = d.get('filename')

    # Build options
    ydl_opts = {
        'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
        'quiet': True,
        'progress_hooks': [hook]
    }
    audio_formats = {"mp3", "aac", "wav", "m4a", "flac", "opus"}
    fmt_l = fmt.lower()
    if fmt_l in audio_formats:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': fmt_l,
                'preferredquality': '192',
            }]
        })
    elif fmt_l == 'mp4':
        ydl_opts['format'] = 'best[ext=mp4]/best'
    else:
        ydl_opts.update({
            'format': 'bestvideo+bestaudio',
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': fmt_l,
            }]
        })
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as e:
        progress_store[task_id]['status'] = 'error'
        logger.error(f"Task {task_id} failed: {e}")
        progress_store[task_id]['file_path'] = None

