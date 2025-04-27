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
        # video-only
        if f.get("vcodec")!="none" and f.get("acodec")=="none":
            h = f.get("height")
            if h and (h not in video_map or f["tbr"] > video_map[h]["tbr"]):
                video_map[h] = {"height": h, "tbr": f["tbr"]}
        # audio-only
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
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YouTube Downloader</title>
<style>
 body{background:#000;color:#fff;font-family:Arial,sans-serif;text-align:center;padding:2rem}
 h1{color:#FF0000}
 input,select,button{margin:0.5rem;padding:0.5rem;font-size:1rem}
 input,select{width:60%;max-width:400px}
 button{background:#FF0000;color:#fff;border:none;cursor:pointer}
 button:disabled{opacity:0.5;cursor:default}
 #preview{margin-top:1rem}
 progress{width:60%;max-width:400px;height:1rem}
 #message{margin-top:1rem}
 a.link{color:#FF0000;text-decoration:none}
</style></head><body>
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
 const btn=document.getElementById('btn'),
       urlI=document.getElementById('url'),
       fmt=document.getElementById('fmt'),
       qual=document.getElementById('qual'),
       prog=document.getElementById('prog'),
       pct=document.getElementById('percent'),
       msg=document.getElementById('message');
 let last=0, fmts=null;

 urlI.addEventListener('blur',async()=>{
  const u=urlI.value.trim(), m=u.match(/[?&]v=([\\w-]{11})/),
        pre=document.getElementById('preview');
  pre.innerHTML=m?`<img src="https://img.youtube.com/vi/${m[1]}/hqdefault.jpg" width="240">`:'';
  if(!u) return;
  const r=await fetch(`/formats?url=${encodeURIComponent(u)}`);
  fmts=await r.json(); updateOptions();
 });

 fmt.addEventListener('change',updateOptions);
 function updateOptions(){
  if(!fmts) return;
  const f=fmt.value, v=['mp4','webm','mkv','avi'];
  qual.innerHTML = v.includes(f)
    ? fmts.video.map(o=>`<option value="${o.height}">${o.label}</option>`).join('')
    : fmts.audio.map(o=>`<option value="${o.abr}">${o.label}</option>`).join('');
 }

 btn.onclick=async()=>{
  const u=urlI.value.trim(), f=fmt.value, q=qual.value;
  if(!u) return alert('Enter a YouTube URL.');
  btn.disabled=true; last=0; prog.value=0; pct.innerText='0%'; msg.innerText='Starting download...';
  const r=await fetch('/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:u,fmt:f,quality:q})});
  if(!r.ok){ alert(await r.text()); btn.disabled=false; return }
  const {task_id}=await r.json(),
        es=new EventSource(`/progress/${task_id}`);
  es.onmessage=e=>{
    const d=JSON.parse(e.data);
    if(d.total_bytes){
      const p=Math.floor(100*d.downloaded_bytes/d.total_bytes);
      if(p>last){ last=p; prog.value=p; pct.innerText=`${p}%` }
    }
    if(d.status==='finished'){ es.close(); msg.innerText='Downloading…'; window.location.href=`/download/${task_id}` }
    if(d.status==='error'){ es.close(); alert('Download failed.'); btn.disabled=false }
  };
 };
</script>
</body></html>"""

# -----------------------
# POST /download → start background task
# -----------------------
@app.post("/download")
async def start_download(background_tasks: BackgroundTasks,
                         payload: dict = Body(...)):
    url = payload.get("url")
    fmt = payload.get("fmt","mp4")
    quality = payload.get("quality")
    task_id = str(uuid.uuid4())
    progress_store[task_id] = {
        "status":"queued","downloaded_bytes":0,"total_bytes":0,"file_path":None
    }
    background_tasks.add_task(run_download, task_id, url, fmt, quality)
    return {"task_id":task_id}

# -----------------------
# GET /progress/{task_id} → SSE
# -----------------------
@app.get("/progress/{task_id}")
async def progress_sse(task_id: str):
    if task_id not in progress_store:
        raise HTTPException(404,"Task not found")
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
        raise HTTPException(404,"Task not found")
    if info["status"] != "finished":
        raise HTTPException(400,"Not ready")
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

    # Video branch with correct 'preferedformat'
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

        # Scan for the final file in temp_dir
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
