# Fitbit HR to Strava

This tool automatically fetches missing heart rate data from your Fitbit account and merges it into your existing Strava activities. It identifies Strava activities without heart rate data, downloads their GPS streams, retrieves matching Fitbit HR data, generates a new TCX file, and handles the replacement process.

## Features
- **Safety First:** Two-phase sync process (Upload first, Verify/Cleanup later).
- **Automatic Backups:** Saves the original Strava data as a `.tcx` file in the `backups/` directory before any changes.
- **Intelligent Merging:** Aligns Fitbit HR data with Strava GPS time streams using UTC/Local time offsets.
- **Real-time Web Dashboard:** Trigger scans, monitor progress via a live terminal stream, and manage your sync history.
- **Database Driven:** Uses SQLAlchemy to support any database (SQLite, Postgres, etc.) for secure token and history storage.
- **Photo Protection:** Automatically skips activities with photos to prevent accidental data loss.

## Setup Instructions

1. **Install Dependencies**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure API Credentials**
   - Copy `.env.example` to `.env`: `cp .env.example .env`
   - Create your API applications at:
     - **Strava**: [strava.com/settings/api](https://www.strava.com/settings/api)
       - Callback Domain: `127.0.0.1`
     - **Fitbit**: [dev.fitbit.com/apps](https://dev.fitbit.com/apps)
       - **Application Type: MUST BE "Personal"**
       - Callback URL: `http://127.0.0.1:8080/callback/fitbit`
   - (Optional) Set `DATABASE_URL` in `.env` to use a specific database. Defaults to `sqlite:///data.db`.

3. **Start the Control Center**
   ```bash
   python app.py
   ```
   Open `http://127.0.0.1:8080` in your browser.

## Usage Guide

### 1. Authenticate
Use the badges in the header to confirm your connection. Use the **Auth Strava** and **Auth Fitbit** buttons in the sidebar if needed.

### 2. Scan History
Click **Update Scan Count** to see how many activities are missing heart rate data and how many are **Fixable** (meaning data exists in your Fitbit account).

### 3. Sync
Set your **Activity Count** (how many to process now) and **History Depth** (how many pages to scan). Click **Start Sync** and watch the progress in the live console.

### 4. Verify & Cleanup
Check the new activities on Strava. Once verified, manually delete the originals and click **Verify Deletions** to move them to your completed history.

## CLI Usage
You can still run the core logic from the terminal:
```bash
# Sync recent history
python main.py --pages 1 --limit 5 --bypass-duplicate

# Sync a specific file (Garmin Export)
python main.py --file ~/Downloads/activity.fit --bypass-duplicate

# Run log cleanup & backfill stats
python main.py --cleanup
```

## Troubleshooting
- **403 Permission Denied (Fitbit):** Ensure your Fitbit App Type is set to **Personal**. Intraday HR data is restricted for "Server" or "Client" app types.
- **Duplicate Uploads:** Use the `Bypass Duplicate` checkbox (or `--bypass-duplicate` flag) to shift the start time by 30 seconds, preventing Strava from blocking the upload.
- **Port Conflicts:** If port 8080 is in use, you can change it at the bottom of `app.py`.
