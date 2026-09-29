# Seerr, Radarr & Sonarr Request Resender 🚀

A robust, zero-dependency Python CLI utility to find approved, stuck, or un-synced requests in **Overseerr** or **Jellyseerr** and retry/resend them to **Radarr** (for movies) or **Sonarr** (for TV shows).

It includes two primary operation modes:
1. **Overseerr Retry Mode (Default)**: Calls Overseerr's internal `POST /api/v1/request/{id}/retry` endpoint.
2. **Direct Servarr Sync Mode (Radarr & Sonarr)**: Queries Radarr or Sonarr API directly (`/api/v3/movie` or `/api/v3/series`), injects any missing approved items, and triggers immediate download searches (bypassing Overseerr queue issues entirely).

---

## ✨ Features

- **Zero External Dependencies**: Built using standard Python 3 libraries (`urllib`, `json`, `argparse`). No `pip install` required!
- **Dual Servarr Support**: Automatic movie routing to **Radarr** and TV show routing to **Sonarr**.
- **Strict Approval Filtering**: Only targets requests with `APPROVED` status (`request.status == 2`) and in `PROCESSING`/`PENDING` media state. Skips declined or unapproved items.
- **Direct Servarr Injection**: Directly communicates with Radarr & Sonarr to add missing media and trigger instant release searches.
- **Automatic Redirect Resolution**: Handles HTTP 301, 302, 303, 307, and 308 redirects automatically (preserves POST payloads across HTTPS redirects, reverse proxies, and URL base paths).
- **Date & Media Age Filters**: Filter requests created more than $N$ days ago (`--older-than-days`) or within the last $N$ days (`--newer-than-days`).
- **Dry-Run Mode**: Safely preview matching requests without modifying anything (`--dry-run`).
- **Environment & `.env` Support**: Automatically reads environment variables or `.env` files.

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.7+

### 2. Environment Setup (Optional)
Create a `.env` file in the project directory:

```env
SEERR_URL=http://192.168.1.100:5055
SEERR_API_KEY=your_overseerr_api_key

# Optional: Direct Radarr Injection Mode (Movies)
RADARR_URL=http://192.168.1.100:7878
RADARR_API_KEY=your_radarr_api_key

# Optional: Direct Sonarr Injection Mode (TV Shows)
SONARR_URL=http://192.168.1.100:8989
SONARR_API_KEY=your_sonarr_api_key
```

---

## 📖 Usage Examples

### 1. Preview Approved Requests (Dry-Run)
```bash
python3 resend_seerr_requests.py --url http://192.168.1.100:5055 --api-key YOUR_KEY --dry-run
```

### 2. Direct Sync Mode with Both Radarr & Sonarr
```bash
python3 resend_seerr_requests.py \
  --url http://192.168.1.100:5055 --api-key OVERSEERR_KEY \
  --radarr-url http://192.168.1.100:7878 --radarr-api-key RADARR_KEY \
  --sonarr-url http://192.168.1.100:8989 --sonarr-api-key SONARR_KEY
```

### 3. Sync TV Shows Only (Sonarr Direct Mode)
```bash
python3 resend_seerr_requests.py \
  --media-type tv \
  --sonarr-url http://192.168.1.100:8989 --sonarr-api-key SONARR_KEY
```

### 4. Auto-Confirm Execution
```bash
python3 resend_seerr_requests.py --yes
```

---

## ⚙️ Command Line Options

| Argument | Description |
| :--- | :--- |
| `--url` | Base URL for Overseerr/Jellyseerr (or `$SEERR_URL`) |
| `--api-key` | Overseerr/Jellyseerr API Key (or `$SEERR_API_KEY`) |
| `--radarr-url` | Radarr Base URL for Direct Movie Injection (or `$RADARR_URL`) |
| `--radarr-api-key` | Radarr API Key (or `$RADARR_API_KEY`) |
| `--radarr-root-folder` | Radarr Root Folder Path (e.g. `/movies`) |
| `--sonarr-url` | Sonarr Base URL for Direct TV Show Injection (or `$SONARR_URL`) |
| `--sonarr-api-key` | Sonarr API Key (or `$SONARR_API_KEY`) |
| `--sonarr-root-folder` | Sonarr Root Folder Path (e.g. `/tv`) |
| `--media-type` | Filter by `movie` (Radarr), `tv` (Sonarr), or `all` (default: `all`) |
| `--status` | Status filter: `approved_processing` (default), `processing`, `pending`, `failed`, `all` |
| `--older-than-days N` | Only target requests created more than $N$ days ago |
| `--newer-than-days N` | Only target requests created within the last $N$ days |
| `--request-ids` | Comma-separated request IDs (e.g. `101,105,108`) |
| `--dry-run` | Preview actions without executing |
| `--yes`, `-y` | Skip interactive prompt and execute immediately |
| `--delay` | Delay in seconds between API calls (default: `0.5`s) |

---

## 📄 License
MIT License
