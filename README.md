#  py-youtube-downloader

A 🚀 **FastAPI**-based 🎬/🎧 downloader API using 📥 **yt-dlp** and optionally 🔧 **ffmpeg** for format conversions.

---

## ✨ Features

- 📺 Download videos (MP4, AVI, …)
- 🎵 Extract audio (MP3, AAC, WAV, …)
- 📦 Native progressive MP4 without ffmpeg
- 🔄 Auto-merge/convert for other formats via ffmpeg
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
   # .venv\\Scripts\\Activate.ps1  # Windows PowerShell
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
🔗 Access at `http://127.0.0.1:8000`

---

### 📡 Endpoint

`GET /download`

| Param | Description        |
|-------|--------------------|
| `url` | 🌐 YouTube video URL |
| `fmt` | 🎞️/🎵 desired format |

**Example:**
```bash
curl -L "http://127.0.0.1:8000/download?url=<YOUTUBE_URL>&fmt=mp3" -o output.mp3
```

---

## 🗒️ Logging

All events logged at **INFO** level:
- 🔍 Incoming requests
- 🔄 Responses & status codes
- ❗ Errors

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
2. 🌿 Create branch `feature/YourFeature`
3. ✍️ Commit changes
4. ⬆️ Push to branch
5. 🔃 Open Pull Request

---

## 📝 License

MIT License ©️ [LICENSE](LICENSE)

