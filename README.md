# Fitbit HR to Strava

This tool automatically fetches missing heart rate data from your Fitbit account and merges it into your existing Strava activities. It identifies Strava activities without heart rate data, downloads their GPS streams, retrieves matching Fitbit HR data, generates a new TCX file, and handles the replacement process.

## Features
- **Safety First:** Two-phase sync process (Upload first, Cleanup later).
- **Automatic Backups:** Saves the original Strava data as a `.tcx` file in the `backups/` directory before any changes.
- **Intelligent Merging:** Aligns Fitbit HR data with Strava GPS time streams using UTC/Local time offsets.
- **Rate Limited:** Built-in delays to respect Strava and Fitbit API limits.
- **Granular Control:** Sync all activities, a specific number, or a single activity ID.
- **Web Control Center:** Real-time dashboard to monitor syncs, scan history, and manage skipped activities.

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

3. **Start the Control Center**
   ```bash
   python app.py
   ```
   Open `http://127.0.0.1:8080` and log in to both services. This creates `tokens.json`.

## Usage Guide

### Web Dashboard (Recommended)
Trigger scans and syncs directly from the web interface. You can see live terminal output as activities are processed.

### CLI Usage
You can still run the tool directly from the terminal:
```bash
# Sync recent history
python main.py --pages 1 --limit 5 --bypass-duplicate

# Sync a specific file (Garmin Export)
python main.py --file ~/Downloads/activity.fit --bypass-duplicate

# Run log cleanup
python main.py --cleanup
```

## Troubleshooting
- **403 Permission Denied (Fitbit):** Ensure your Fitbit App Type is set to **Personal**. Intraday HR data is restricted for "Server" or "Client" app types.
- **Time Offsets:** The tool uses `start_date` (UTC) for TCX timestamps and `start_date_local` for Fitbit data matching.
- **Port Conflicts:** If port 8080 is in use, you can change it at the bottom of `app.py`.
