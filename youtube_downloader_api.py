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
# FastAPI app + in-memory store
# -----------------------
app = FastAPI()
progress_store = {}  # task_id → { status, downloaded_bytes, total_bytes, file_path }

# -----------------------
# Middleware: request logging
# -----------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    client = request.client.host if request.client else "unknown"
    logger.info(f"→ {request.method} {request.url} from {client}")
    resp = await call_next(request)
    logger.info(f"← {resp.status_code} to {client}")
    return resp

# -----------------------
# Helpers for /formats
# -----------------------
def label_from_height(h: int) -> str:
    if h >= 2160: return "4K"
    if h >= 1440: return "2K"
    if h >= 1080: return "1080p"
    if h >= 720:  return "720p"
    return f"{h}p"

# -----------------------
# GET /formats: list available resolutions & bitrates
# -----------------------
@app.get("/formats")
async def get_formats(url: str = Query(...)):
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    fmts = info.get("formats", [])
    video_map, audio_map = {}, {}
    for f in fmts:
        if f.get("vcodec")!="none" and f.get("acodec")=="none":
            h = f.get("height")
            if h and (h not in video_map or f["tbr"] > video_map[h]["tbr"]):
                video_map[h] = {"height": h, "tbr": f["tbr"]}
        if f.get("acodec")!="none" and f.get("vcodec")=="none":
            abr = f.get("abr")
            if abr and (abr not in audio_map or f["tbr"] > audio_map[abr]["tbr"]):
                audio_map[abr] = {"abr": abr, "tbr": f["tbr"]}
    return {
        "video": [{"height": h, "label": label_from_height(h)} for h in sorted(video_map, reverse=True)],
        "audio": [{"abr": a, "label": f"{a} kbps"} for a in sorted(audio_map, reverse=True)]
    }

# -----------------------
# GET / → UI
# -----------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YouTube Batch Downloader</title>
  <style>
    body{background:#000;color:#fff;font-family:Arial,sans-serif;padding:2rem}
    h1{text-align:center;color:#FF0000}
    .controls{margin-bottom:1rem;text-align:center}
    .controls button{margin:0 .5rem;padding:.5rem 1rem;font-size:1rem;cursor:pointer;background:#FF0000;border:none;color:#fff}
    .controls button:disabled{opacity:.5;cursor:default}
    #items-container{max-width:600px;margin:0 auto}
    .item{background:#111;padding:1rem;margin-bottom:1rem;position:relative}
    .item input, .item select{width:100%;margin:.5rem 0;padding:.5rem;font-size:1rem;box-sizing: border-box}
    .item img{width:120px;height:auto;display:block;margin:.5rem 0}
    .remove-btn{position:absolute;top:.5rem;right:.5rem;background:#900;border:none;color:#fff;padding:.25rem .5rem;cursor:pointer}
    #progress-container{max-width:600px;margin:2rem auto}
    .progress-item{background:#111;padding:1rem;margin-bottom:1rem}
    .progress-item progress{width:100%;height:1rem}
    .progress-item span{display:inline-block;width:2rem;text-align:right;margin-left:.5rem}
    #message{text-align:center;margin-top:1rem}
  </style>
</head>
<body>
  <h1>YouTube Batch Downloader</h1>
  
  <div id="items-container"></div>
  
  <div class="controls">
    <button id="add-btn">Add Link</button>
    <button id="download-btn">Download All</button>
  </div>
  <div id="progress-container"></div>
  <div id="message"></div>

<script>
let nextId = 0;
const itemsFmts = {};
const itemsContainer = document.getElementById('items-container');
const addBtn = document.getElementById('add-btn');
const downloadBtn = document.getElementById('download-btn');
const progressContainer = document.getElementById('progress-container');
const msg = document.getElementById('message');

addBtn.addEventListener('click', addItem);
function addItem(){
  const id = nextId++;
  const div = document.createElement('div');
  div.className = 'item';
  div.dataset.id = id;
  div.innerHTML = `
    <button class="remove-btn">×</button>
    <input type="text" placeholder="YouTube URL" class="url-input"/>
    <select class="fmt-select">
      <optgroup label="Video">
        <option>mp4</option><option>webm</option><option>mkv</option><option>avi</option>
      </optgroup>
      <optgroup label="Audio">
        <option>mp3</option><option>aac</option><option>wav</option><option>flac</option><option>m4a</option><option>opus</option>
      </optgroup>
    </select>
    <select class="qual-select"></select>
    <img class="preview-img" src="" alt="Preview"/>
  `;
  itemsContainer.appendChild(div);

  const urlInput = div.querySelector('.url-input');
  const fmtSelect = div.querySelector('.fmt-select');
  const qualSelect = div.querySelector('.qual-select');
  const previewImg = div.querySelector('.preview-img');
  const removeBtn = div.querySelector('.remove-btn');

  urlInput.addEventListener('blur', async()=>{
    const url = urlInput.value.trim();
    previewImg.src = '';
    if(!url) return;
    const m = url.match(/[?&]v=([\\w-]{11})/);
    if(m) previewImg.src = `https://img.youtube.com/vi/${m[1]}/hqdefault.jpg`;
    const res = await fetch(`/formats?url=${encodeURIComponent(url)}`);
    const data = await res.json();
    itemsFmts[id] = data;
    updateQual(id);
  });

  fmtSelect.addEventListener('change', ()=>updateQual(id));

  function updateQual(i){
    const data = itemsFmts[i];
    if(!data) return;
    const f = fmtSelect.value;
    const videoExts = ['mp4','webm','mkv','avi'];
    let opts = [];
    if(videoExts.includes(f)) {
      opts = data.video.map(o=>({v:o.height, t:o.label}));
    } else {
      opts = data.audio.map(o=>({v:o.abr, t:o.label}));
    }
    qualSelect.innerHTML = opts.map(o=>`<option value="${o.v}">${o.t}</option>`).join('');
  }

  removeBtn.addEventListener('click', ()=>div.remove());
}

// start with one item
addItem();

downloadBtn.addEventListener('click', async()=>{
  const items = Array.from(itemsContainer.querySelectorAll('.item')).map(div=>({
    url: div.querySelector('.url-input').value.trim(),
    fmt: div.querySelector('.fmt-select').value,
    quality: div.querySelector('.qual-select').value
  })).filter(i=>i.url);
  if(!items.length) return alert('Add at least one URL');
  addBtn.disabled = true;
  downloadBtn.disabled = true;
  progressContainer.innerHTML = '';
  msg.innerText = 'Starting downloads...';

  const res = await fetch('/download',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({items})
  });
  if(!res.ok){
    alert(await res.text());
    addBtn.disabled = false;
    downloadBtn.disabled = false;
    return;
  }
  const { task_ids } = await res.json();

  const promises = task_ids.map((id, idx)=>{
    const pDiv = document.createElement('div');
    pDiv.className = 'progress-item';
    pDiv.innerHTML = `
      <strong>Item ${idx+1}:</strong>
      <progress value="0" max="100"></progress>
      <span>0%</span>`;
    progressContainer.appendChild(pDiv);
    const pr = pDiv.querySelector('progress');
    const pct = pDiv.querySelector('span');
    return new Promise(resolve=>{
      const es = new EventSource(`/progress/${id}`);
      es.onmessage = e=>{
        const d = JSON.parse(e.data);
        if(d.total_bytes){
          const p = Math.floor(100 * d.downloaded_bytes / d.total_bytes);
          pr.value = p; pct.innerText = p + '%';
        }
        if(d.status==='finished'){
          es.close();
          window.open(`/download/${id}`, '_blank');
          resolve();
        }
        if(d.status==='error'){
          es.close();
          pct.innerText = 'Error';
          resolve();
        }
      };
    });
  });

  await Promise.all(promises);
  msg.innerText = 'All downloads finished.';
  addBtn.disabled = false;
  downloadBtn.disabled = false;
});
</script>
</body>
</html>"""

# -----------------------
# POST /download → handle batch items
# -----------------------
@app.post("/download")
async def start_download(background_tasks: BackgroundTasks, payload: dict = Body(...)):
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "No items provided")
    task_ids = []
    for it in items:
        url = it.get("url")
        fmt = it.get("fmt","mp4")
        quality = it.get("quality")
        if not url:
            continue
        tid = str(uuid.uuid4())
        progress_store[tid] = {"status":"queued","downloaded_bytes":0,"total_bytes":0,"file_path":None}
        background_tasks.add_task(run_download, tid, url, fmt, quality)
        task_ids.append(tid)
    return {"task_ids": task_ids}

# -----------------------
# GET /progress/{task_id} → SSE
# -----------------------
@app.get("/progress/{task_id}")
async def progress_sse(task_id: str):
    if task_id not in progress_store:
        raise HTTPException(404, "Task not found")
    def gen():
        while True:
            d = progress_store[task_id]
            yield f"data: {json.dumps(d)}\n\n"
            if d["status"] in ("finished","error"):
                break
            time.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream")

# -----------------------
# GET /download/{task_id} → file
# -----------------------
@app.get("/download/{task_id}")
async def fetch_file(task_id: str):
    info = progress_store.get(task_id)
    if not info:
        raise HTTPException(404, "Task not found")
    if info["status"] != "finished":
        raise HTTPException(400, "Not ready")
    return FileResponse(info["file_path"],
                        media_type="application/octet-stream",
                        filename=os.path.basename(info["file_path"]))

# -----------------------
# Background download + conversion
# -----------------------
def run_download(task_id: str, url: str, fmt: str, quality: str):
    temp_dir = tempfile.mkdtemp(prefix="yt_dl_")

    def progress_hook(d):
        if d.get("status") == "downloading":
            st = progress_store[task_id]
            st.update({
                "status": "downloading",
                "downloaded_bytes": d.get("downloaded_bytes", 0),
                "total_bytes":    d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            })

    audio_exts = {"mp3","aac","wav","flac","m4a","opus"}
    video_exts = {"mp4","webm","mkv","avi"}
    f = fmt.lower()

    opts = {
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
        "quiet": True,
        "progress_hooks": [progress_hook],
    }

    # Audio branch
    if f in audio_exts:
        if not FFMPEG_EXISTS:
            progress_store[task_id]["status"] = "error"
            return
        abr = int(quality) if quality and quality.isdigit() else None
        opts["format"] = f"bestaudio[abr<={abr}]/bestaudio" if abr else "bestaudio"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": f,
            "preferredquality": "192"
        }]

    # Video branch
    elif f in video_exts:
        h = int(quality) if quality and quality.isdigit() else None
        if f == "mp4":
            if h:
                opts["format"] = f"bestvideo[height<={h}]+bestaudio/best"
                opts["postprocessors"] = [{
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": f
                }]
                opts["merge_output_format"] = f
            else:
                opts["format"] = "best[ext=mp4]/best"
        else:
            if h:
                opts["format"] = f"bestvideo[height<={h}]+bestaudio/best"
            else:
                opts["format"] = "bestvideo+bestaudio"
            opts["postprocessors"] = [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": f
            }]
            opts["merge_output_format"] = f

    else:
        progress_store[task_id]["status"] = "error"
        return

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)

        # Pick the final file (matching chosen extension)
        target_ext = f
        found = False
        for fn in os.listdir(temp_dir):
            if fn.lower().endswith(f".{target_ext}"):
                progress_store[task_id].update({
                    "status": "finished",
                    "file_path": os.path.join(temp_dir, fn)
                })
                found = True
                break
        if not found:
            entries = list(os.scandir(temp_dir))
            if entries:
                progress_store[task_id].update({
                    "status": "finished",
                    "file_path": entries[0].path
                })
            else:
                progress_store[task_id]["status"] = "error"

    except Exception as e:
        logger.error(f"Task {task_id} error: {e}")
        progress_store[task_id]["status"] = "error"
