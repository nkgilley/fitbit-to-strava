# Fitbit HR to Strava

This tool automatically fetches missing heart rate data from your Fitbit account and merges it into your existing Strava activities. It identifies Strava activities without heart rate data, downloads their GPS/Sensor streams, retrieves matching Fitbit HR data, and generates a new version for upload.

## Features
- **Safety First:** Two-phase sync process (Upload first, Verify/Cleanup later).
- **Automatic Backups:** Saves original Strava data as a `.tcx` file in the `backups/` directory.
- **Deep History Scanning:** Performs a two-step check (Strava missing HR + Fitbit has data) to identify "Fixable" activities.
- **Local Data Caching:** The deep scan caches **all** metadata and high-res streams (Strava + Fitbit) locally in your database. This makes the final sync near-instant and extremely efficient with API quotas.
- **Optimized Syncing:** "Only Fixable" mode skips repetitive scanning and targets confirmed activities using cached data.
- **Real-time Web Dashboard:** Monitor progress via a live terminal stream with persistent history across reloads.
- **Data Preservation:** Automatically skips activities with photos to prevent accidental data loss.
- **Unit Support:** Fully supports Imperial units (mi/ft) for all dashboard statistics.
- **Database Driven:** Uses SQLAlchemy (SQLite by default) for secure token and history management.

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

### 1. Deep Scan
Click **Update Scan Count** in the sidebar. This downloads and caches all required data from both Strava and Fitbit. This is the only step that heavily uses your API quotas.

### 2. Instant Sync
Enter the **Activity Count** you want to process and click **Start Sync**. Since all data is now cached locally, this process is near-instant and only requires a single "Upload" request to Strava per activity.

### 3. Verify & Cleanup
Check the new activities using the **New** links in the Pending Cleanup table. Once verified, manually delete the **Original** activities on Strava and click **Verify Manual Deletions** to update your history.

## CLI Usage
You can still run the core logic from the terminal:
```bash
# Sync using the fixable list (cached data)
python main.py --only-fixable --limit 5 --bypass-duplicate

# Sync a specific file (Garmin Export)
python main.py --file ~/Downloads/activity.fit --bypass-duplicate

# Run log cleanup & backfill stats
python main.py --cleanup
```

## Troubleshooting
- **403 Permission Denied (Fitbit):** Ensure your Fitbit App Type is set to **Personal**. Intraday HR data is restricted for "Server" or "Client" app types.
- **429 Rate Limit (Fitbit):** Fitbit allows ~150 requests per hour. The tool's **Local Data Caching** ensures you only use this quota once per activity.
- **Duplicate Uploads:** The tool shifts the start time by 30 seconds by default to prevent Strava from blocking the upload.
