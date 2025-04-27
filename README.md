# py-youtube-downloader

A 🚀 **FastAPI**-based 🎬/🎧 downloader API using 📥 **yt-dlp** and optionally 🔧 **ffmpeg** for format conversions.

---

## ✨ Features

- 📺 Download videos (MP4, MKV, AVI, WEBM, …)
- 🎵 Extract audio (MP3, AAC, WAV, M4A, OPUS, FLAC, …)
- 📦 Native progressive MP4 without ffmpeg
- 🔄 Auto‑merge/convert for other containers via ffmpeg
- 📥 **Batch download**: submit multiple URLs at once
- 🌐 **Web UI** for single or batch downloads with per‑item progress
- 🔄 **Re‑enable Download** button after completion for new tasks
- 📝 Detailed logs of requests, responses & errors

---

## ⚙️ Prerequisites

- 🐍 **Python** ≥ 3.9
- 📦 **pip**
- 🔧 **ffmpeg** (optional; needed for non‑MP4 formats)
  ```bash
  brew install ffmpeg
  ```

---

## 🛠 Installation

1. 📁 **Clone** the repo:
   ```bash
   git clone https://github.com/yourusername/py-youtube-downloader.git
   cd py-youtube-downloader
   ```

2. 🐣 **Create & activate venv**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # macOS/Linux
   # .\.venv\\Scripts\\Activate.ps1  # Windows PowerShell
   ```

3. 📥 **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

## 🚀 Usage

Start the API server:
```bash
uvicorn youtube_downloader_api:app --reload --host 0.0.0.0 --port 8000
```
🔗 Access the **Web UI** at `http://127.0.0.1:8000`

### 📡 Endpoints

#### 1. Get available formats

```
GET /formats?url=<YOUTUBE_URL>
```

Returns JSON:
```json
{
  "video": [ {"height": 1080, "label": "1080p"}, ... ],
  "audio": [ {"abr": 192, "label": "192 kbps"}, ... ]
}
```

#### 2. Start download (single or batch)

```
POST /download
Content-Type: application/json
```

Request body (single URL):
```json
{ "url": "<YOUTUBE_URL>", "fmt": "mp3", "quality": "" }
```

Request body (batch):
```json
{
  "urls": "<URL1>\n<URL2>\n<URL3>",
  "fmt": "mp4",
  "quality": "720"
}
```

Response:
```json
{ "task_ids": ["id1", "id2", ...] }
```

#### 3. Progress via Server‑Sent Events

```
GET /progress/{task_id}
```

Streamed updates:
```json
{ "status": "downloading", "downloaded_bytes": 102400, "total_bytes": 204800, "file_path": null }
```

#### 4. Fetch completed file

```
GET /download/{task_id}
```

Returns the file as an attachment once `status` is `finished`.

---

## 🖥️ Web UI

1. **Paste** one or more YouTube URLs (one per line).
2. **Select** desired `fmt` (video or audio) and `quality`.
3. **Click** **Download**: per‑item progress bars appear.
4. Once each item finishes, its file auto‑downloads and the **Download** button becomes clickable again for new tasks.

---

## 📝 Examples

### Single download via `curl`
```bash
curl -X POST http://127.0.0.1:8000/download \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://www.youtube.com/watch?v=EI28gmgBMfw", "fmt": "mp3"}'
```

### Batch download via `curl`
```bash
curl -X POST http://127.0.0.1:8000/download \
  -H 'Content-Type: application/json' \
  -d '{"urls": "https://youtu.be/ID1\nhttps://youtu.be/ID2", "fmt": "mp4", "quality": "720"}'
```

---

## 📋 Requirements

Pinned versions:
```
fastapi==0.115.12
uvicorn==0.34.2
yt-dlp==2025.3.31
```

---

## 🤝 Contributing

1. 🍴 Fork the repo
2. 🌿 Create branch `feature/<YourFeature>`
3. ✍️ Commit changes
4. ⬆️ Push to branch
5. 🔃 Open Pull Request

---

## 📝 License

MIT License ©️ [LICENSE](LICENSE)

