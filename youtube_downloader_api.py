import os
import shutil
import tempfile
import logging
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
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
  <form action=\"/download\" method=\"get\">
    <input type=\"text\" name=\"url\" placeholder=\"YouTube URL\" size=\"50\" required/>
    <select name=\"fmt\">
      <option value=\"mp4\">MP4</option>
      <option value=\"mp3\">MP3</option>
      <option value=\"avi\">AVI</option>
      <option value=\"wav\">WAV</option>
      <option value=\"m4a\">M4A</option>
    </select>
    <button type=\"submit\">Download</button>
  </form>
</body>
</html>"""

@app.get("/download")
async def download(
    url: str,
    fmt: str = Query("mp4", regex="^[a-zA-Z0-9]+$")
):
    logger.info(f"Download requested: url={url}, fmt={fmt}")
    temp_dir = tempfile.mkdtemp(prefix="yt_dl_")
    logger.info(f"Created temp dir: {temp_dir}")

    ydl_opts = {
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
        "quiet": True,
    }

    audio_formats = {"mp3", "aac", "wav", "m4a", "flac", "opus"}
    fmt_l = fmt.lower()

    if fmt_l in audio_formats:
        if not FFMPEG_EXISTS:
            raise HTTPException(
                status_code=500,
                detail=(
                    "ffmpeg/ffprobe not installed—cannot extract audio. "
                    "Install with `brew install ffmpeg` or `sudo apt install ffmpeg`."
                )
            )
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": fmt_l,
                "preferredquality": "192",
            }],
        })

    elif fmt_l == "mp4":
        ydl_opts["format"] = "best[ext=mp4]/best"

    else:
        if not FFMPEG_EXISTS:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"ffmpeg/ffprobe not installed—cannot convert to {fmt_l}. "
                    "Install ffmpeg or use fmt=mp4 for native MP4."
                )
            )
        ydl_opts.update({
            "format": "bestvideo+bestaudio",
            "postprocessors": [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": fmt_l,
            }],
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info("Starting yt_dlp.extract_info()")
            info = ydl.extract_info(url, download=True)
            logger.info("Download finished")
            title = info.get("title", "video")
            filename = f"{title}.{fmt_l}"
            file_path = os.path.join(temp_dir, filename)

            if not os.path.exists(file_path):
                files = os.listdir(temp_dir)
                if not files:
                    raise FileNotFoundError("No file found after download.")
                file_path = os.path.join(temp_dir, files[0])
                logger.info(f"Fallback to {file_path}")

    except Exception as e:
        logger.exception("Download/convert failed")
        raise HTTPException(status_code=400, detail=str(e))

    return FileResponse(
        path=file_path,
        media_type="application/octet-stream",
        filename=os.path.basename(file_path),
    )

