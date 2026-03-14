import os
import json
import base64
import requests
import subprocess
import threading
import time
import queue
from flask import Flask, request, redirect, url_for, session, Response
from datetime import datetime
from dotenv import load_dotenv
from database import init_db, SessionLocal, Token, SyncedActivity, SkippedActivity, ScanResult

load_dotenv()
init_db()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super_secret_key")

# Simple thread-safe queue for terminal lines
terminal_queue = queue.Queue()
terminal_history = [] # Store last 100 lines for page reloads
process_status = {"running": False, "message": "Idle"}
scan_results = {"count": 0, "fixable_count": 0, "last_scan": "Never", "scanning": False}

# STRAVA CONFIG
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"

# FITBIT CONFIG
FITBIT_CLIENT_ID = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
FITBIT_AUTH_URL = "https://www.fitbit.com/oauth2/authorize"
FITBIT_TOKEN_URL = "https://api.fitbit.com/oauth2/token"

def save_tokens(service, token_data):
    db = SessionLocal()
    token = db.query(Token).filter(Token.service == service).first()
    if not token:
        token = Token(service=service)
        db.add(token)
    
    token.access_token = token_data.get("access_token")
    token.refresh_token = token_data.get("refresh_token")
    token.expires_at = token_data.get("expires_at") or token_data.get("expires_in")
    token.other_data = token_data
    db.commit()
    db.close()

def log_terminal(line):
    global terminal_history
    terminal_queue.put(line)
    if line != "[DONE]":
        terminal_history.append(line)
        if len(terminal_history) > 100:
            terminal_history.pop(0)

def run_command_stream(cmd):
    global process_status
    process_status["running"] = True
    process_status["message"] = f"Running: {' '.join(cmd)}"
    
    python_bin = os.path.join(os.getcwd(), 'venv', 'bin', 'python')
    if not os.path.exists(python_bin):
        python_bin = "python3"
    
    full_cmd = [python_bin] + cmd
    
    try:
        process = subprocess.Popen(
            full_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        for line in iter(process.stdout.readline, ""):
            log_terminal(line.strip())
        process.wait()
        if process.returncode == 0:
            process_status["message"] = "Process finished successfully."
        else:
            process_status["message"] = f"Process failed with exit code {process.returncode}."
        log_terminal("[DONE]")
    except Exception as e:
        process_status["message"] = f"Execution failed: {str(e)}"
        log_terminal(f"ERROR: {str(e)}")
    finally:
        process_status["running"] = False

def run_scan_in_background(pages):
    global scan_results
    scan_results["scanning"] = True
    log_terminal(f">>> Starting deep scan of {pages} pages...")
    try:
        from strava_client import StravaClient
        from fitbit_client import FitbitClient
        from merger import parse_date
        from datetime import timedelta
        
        strava = StravaClient()
        fitbit = FitbitClient()
        db = SessionLocal()
        
        completed_ids = {a.old_id for a in db.query(SyncedActivity).filter(SyncedActivity.status == "completed").all()}
        pending_ids = {a.old_id for a in db.query(SyncedActivity).filter(SyncedActivity.status == "pending_cleanup").all()}
        skipped_ids = {a.id for a in db.query(SkippedActivity).all()}

        missing_count = 0
        fixable_count = 0
        
        for p in range(1, int(pages) + 1):
            log_terminal(f"Scanning Strava page {p}...")
            activities = strava.get_activities(per_page=50, page=p)
            if not activities: break
            
            for a in activities:
                a_id = str(a["id"])
                if a.get("has_heartrate") or a_id in completed_ids or a_id in pending_ids or a_id in skipped_ids:
                    continue
                if a.get("total_photo_count", 0) > 0: continue
                
                missing_count += 1
                start_date_local = a.get("start_date_local")
                if not start_date_local: continue
                
                start_dt = parse_date(start_date_local)
                dur = a.get("elapsed_time") or 3600
                end_dt = start_dt + timedelta(seconds=dur)
                date_str, s_time, e_time = start_dt.strftime("%Y-%m-%d"), start_dt.strftime("%H:%M"), (end_dt + timedelta(minutes=5)).strftime("%H:%M")
                
                try:
                    hr_points = fitbit.get_hr_data(date_str, s_time, e_time)
                    if hr_points:
                        fixable_count += 1
                        log_terminal(f"  [Fixable] {a.get('name')} ({date_str})")
                    else:
                        log_terminal(f"  [No Data] {a.get('name')} ({date_str})")
                except: pass
                time.sleep(0.3)
            
        scan_results["count"] = missing_count
        scan_results["fixable_count"] = fixable_count
        scan_results["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        scan_record = db.query(ScanResult).filter(ScanResult.id == 1).first()
        if not scan_record:
            scan_record = ScanResult(id=1)
            db.add(scan_record)
        scan_record.count = missing_count
        scan_record.fixable_count = fixable_count
        scan_record.last_scan = scan_results["last_scan"]
        db.commit()
        db.close()
        log_terminal(f">>> Scan complete. Found {missing_count} total, {fixable_count} fixable.")
    except Exception as e:
        log_terminal(f"Scan failed: {e}")
    finally:
        scan_results["scanning"] = False
        log_terminal("[DONE]")

@app.route("/")
def index(): return redirect(url_for("dashboard"))

@app.route("/stream")
def stream():
    def event_stream():
        while True:
            try:
                line = terminal_queue.get(timeout=1)
                yield f"data: {line}\n\n"
            except queue.Empty:
                yield "data: \n\n"
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/scan", methods=["POST"])
def start_scan():
    if scan_results["scanning"]: return "Busy", 400
    pages = request.form.get("pages", "1")
    threading.Thread(target=run_scan_in_background, args=(pages,)).start()
    return redirect(url_for("dashboard"))

@app.route("/sync", methods=["POST"])
def start_sync():
    if process_status["running"]: return "Busy", 400
    limit = request.form.get("limit", "1")
    pages = request.form.get("pages", "1")
    bypass = "--bypass-duplicate" if "bypass" in request.form else ""
    force_elev = "--force-elevation" if "force_elev" in request.form else ""
    cmd = ["main.py", "--limit", limit, "--pages", pages]
    if bypass: cmd.append(bypass)
    if force_elev: cmd.append(force_elev)
    threading.Thread(target=run_command_stream, args=(cmd,)).start()
    return redirect(url_for("dashboard"))

@app.route("/do_cleanup", methods=["POST"])
def start_cleanup():
    if process_status["running"]: return "Busy", 400
    threading.Thread(target=run_command_stream, args=(["main.py", "--cleanup"],)).start()
    return redirect(url_for("dashboard"))

@app.route("/clear_skipped", methods=["POST"])
def clear_skipped():
    db = SessionLocal()
    db.query(SkippedActivity).delete()
    db.commit()
    db.close()
    return redirect(url_for("dashboard"))

@app.route("/dashboard")
def dashboard():
    db = SessionLocal()
    
    # Load scan results
    scan_record = db.query(ScanResult).filter(ScanResult.id == 1).first()
    global scan_results
    if scan_record and not scan_results["scanning"]:
        scan_results["count"] = scan_record.count
        scan_results["fixable_count"] = scan_record.fixable_count
        scan_results["last_scan"] = scan_record.last_scan
        scan_results["scanning"] = False

    strava_auth = db.query(Token).filter(Token.service == "strava").first() is not None
    fitbit_auth = db.query(Token).filter(Token.service == "fitbit").first() is not None
    
    completed = db.query(SyncedActivity).filter(SyncedActivity.status == "completed").order_by(SyncedActivity.date.desc()).all()
    pending = db.query(SyncedActivity).filter(SyncedActivity.status == "pending_cleanup").order_by(SyncedActivity.date.desc()).all()
    skipped = db.query(SkippedActivity).all()
    
    # Pagination
    items_per_page = 25
    total_pages = max(1, (len(completed) + items_per_page - 1) // items_per_page)
    current_page = max(1, min(int(request.args.get('page', 1)), total_pages))
    start_idx = (current_page - 1) * items_per_page
    paginated_completed = completed[start_idx : start_idx + items_per_page]
    
    db.close()

    def format_stats(item):
        dist = f"{item.distance_mi or 0.0} mi"
        dur = f"{item.duration_min or 0.0} min"
        elev = f"{item.elevation_gain_ft or 0} ft"
        return f"{dist} | {dur} | {elev}"

    initial_console = "\\n".join(terminal_history) if terminal_history else "--- System Idle ---"

    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Fitbit HR to Strava</title>
        <style>
            body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1100px; margin: 0 auto; padding: 20px; background: #f0f2f5; color: #1c1e21; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
            .grid {{ display: grid; grid-template-columns: 350px 1fr; gap: 20px; }}
            .card {{ background: white; border-radius: 8px; padding: 20px; box-shadow: 0 1px 2px rgba(0,0,0,0.1); margin-bottom: 20px; }}
            h3 {{ margin-top: 0; color: #65676b; font-size: 0.9em; text-transform: uppercase; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 12px; border-bottom: 1px solid #ebedf0; text-align: left; font-size: 0.9em; }}
            th {{ cursor: pointer; user-select: none; color: #65676b; text-transform: uppercase; font-size: 0.75em; letter-spacing: 0.5px; position: relative; }}
            th:hover {{ background: #f8f9fa; color: #1c1e21; }}
            th::after {{ content: ' ↕'; opacity: 0.3; }}
            .btn {{ display: inline-block; background: #0084ff; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; text-decoration: none; font-weight: 600; font-size: 0.9em; text-align: center; }}
            .btn:hover {{ background: #0073e6; }}
            .btn-secondary {{ background: #e4e6eb; color: #050505; }}
            .btn-danger {{ background: #fa3e3e; }}
            .auth-badge {{ padding: 4px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; }}
            .auth-ok {{ background: #e7f3ff; color: #1877f2; }}
            .auth-missing {{ background: #fff0f0; color: #fa3e3e; }}
            .status-bar {{ background: #000; color: #fff; padding: 15px; border-radius: 6px; font-family: monospace; margin-bottom: 20px; border-left: 4px solid #31a24c; font-size: 0.9em; display: flex; align-items: center; }}
            .status-label {{ color: #31a24c; font-weight: bold; margin-right: 10px; }}
            .spinner {{ width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3); border-radius: 50%; border-top-color: #00d1ff; animation: spin 1s linear infinite; margin-right: 12px; display: none; }}
            .running .spinner {{ display: inline-block; }}
            #console {{ background: #000; color: #fff; padding: 15px; border-radius: 0 0 6px 6px; font-family: monospace; height: 150px; overflow-y: auto; font-size: 0.85em; border-left: 4px solid #31a24c; white-space: pre-wrap; }}
            .console-header {{ background: #1c1e21; color: #31a24c; padding: 8px 15px; border-radius: 6px 6px 0 0; font-size: 0.75em; font-weight: bold; border-left: 4px solid #31a24c; cursor: pointer; }}
            .stat-pill {{ color: #65676b; font-size: 0.85em; }}
            .scan-box {{ background: #f7f9fc; padding: 15px; border-radius: 6px; margin-bottom: 15px; border: 1px solid #e1e4e8; }}
            .pagination {{ margin-top: 15px; display: flex; justify-content: center; align-items: center; gap: 15px; font-size: 0.9em; }}
            .modal {{ display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); }}
            .modal-content {{ background-color: #fefefe; margin: 5% auto; padding: 30px; border-radius: 12px; width: 70%; max-width: 700px; box-shadow: 0 5px 15px rgba(0,0,0,0.3); }}
            .close {{ color: #aaa; float: right; font-size: 28px; font-weight: bold; cursor: pointer; }}
            .help-step {{ margin-bottom: 20px; padding-left: 15px; border-left: 3px solid #0084ff; }}
            .help-step h4 {{ margin: 0 0 5px 0; color: #0084ff; }}
            @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
            @keyframes pulse {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} 100% {{ opacity: 1; }} }}
            .running-text {{ animation: pulse 1.5s infinite; color: #00d1ff; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Fitbit HR to Strava</h1>
            <div>
                <span class="auth-badge {'auth-ok' if strava_auth else 'auth-missing'}">Strava {'✓' if strava_auth else '✗'}</span>
                <span class="auth-badge {'auth-ok' if fitbit_auth else 'auth-missing'}">Fitbit {'✓' if fitbit_auth else '✗'}</span>
            </div>
        </div>

        <div class="status-bar {'running' if (process_status['running'] or scan_results['scanning']) else ''}">
            <div class="spinner"></div>
            <span class="status-label">SYSTEM STATUS:</span> 
            <span class="{'running-text' if (process_status['running'] or scan_results['scanning']) else ''}">{ "Scanning History..." if scan_results['scanning'] else process_status['message']}</span>
        </div>

        <details class="card" style="padding: 0; border: none; background: transparent;" open>
            <summary class="console-header">LIVE TERMINAL OUTPUT (Click to Toggle)</summary>
            <div id="console">{initial_console}</div>
        </details>

        <div class="grid">
            <div class="sidebar">
                <div class="card">
                    <h3>1. Scan History</h3>
                    <div class="scan-box">
                        <p style="margin:0; font-size:0.9em;">Missing HR: <strong>{scan_results['count']}</strong></p>
                        <p style="margin:0; font-size:0.9em;">Fixable: <strong style="color:#31a24c;">{scan_results.get('fixable_count', 0)}</strong></p>
                        <p style="margin:0; font-size:0.7em; color:#65676b; margin-top:5px;">Last scan: {scan_results['last_scan']}</p>
                    </div>
                    <form action="/scan" method="post">
                        <label style="font-size:0.8em; color:#65676b;">Search Depth (Pages)</label>
                        <input type="number" name="pages" value="1" min="1" max="50" style="width:100%; padding:8px; border:1px solid #ddd; border-radius:4px; margin: 5px 0 10px 0;">
                        <button type="submit" class="btn btn-secondary" style="width:100%;" {"disabled" if scan_results["scanning"] else ""}>
                            { "Scanning..." if scan_results["scanning"] else "Update Scan Count" }
                        </button>
                    </form>
                </div>

                <div class="card">
                    <h3>2. Import Data</h3>
                    <form action="/sync" method="post">
                        <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px;">
                            <div>
                                <label style="font-size:0.8em; color:#65676b;">Count</label>
                                <input type="number" name="limit" value="1" min="1" max="50" style="width:100%; padding:8px; border:1px solid #ddd; border-radius:4px;">
                            </div>
                            <div>
                                <label style="font-size:0.8em; color:#65676b;">History</label>
                                <input type="number" name="pages" value="1" min="1" max="50" style="width:100%; padding:8px; border:1px solid #ddd; border-radius:4px;">
                            </div>
                        </div>
                        <div style="margin-bottom:15px; font-size:0.9em;">
                            <input type="checkbox" name="bypass" id="bypass" checked> <label for="bypass">Bypass Duplicate</label><br>
                            <input type="checkbox" name="force_elev" id="force_elev"> <label for="force_elev">Force Elevation</label>
                        </div>
                        <button type="submit" class="btn" style="width:100%;" {"disabled" if process_status["running"] or not strava_auth else ""}>Start Sync</button>
                    </form>
                </div>

                <div class="card">
                    <h3>Maintenance</h3>
                    <div style="display:grid; gap:10px;">
                        <form action="/do_cleanup" method="post">
                            <button type="submit" class="btn btn-secondary" style="width:100%;" {"disabled" if process_status["running"] else ""}>Verify Deletions</button>
                        </form>
                        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:10px;">
                            <a href="/login/strava" class="btn btn-secondary" style="text-align:center; font-size:0.75em; padding: 6px;">Auth Strava</a>
                            <a href="/login/fitbit" class="btn btn-secondary" style="text-align:center; font-size:0.75em; padding: 6px;">Auth Fitbit</a>
                        </div>
                        <button class="btn btn-secondary" onclick="document.getElementById('helpModal').style.display='block'">How to Use / Help</button>
                    </div>
                </div>

                <div class="card">
                    <h3>Skipped ({len(skipped)})</h3>
                    <details style="margin-bottom: 15px; font-size: 0.85em;">
                        <summary style="cursor:pointer; color:#0084ff; font-weight:600;">Show Details</summary>
                        <ul style="padding-left: 20px; margin-top: 10px; max-height: 200px; overflow-y: auto;">
                            {"".join([f"<li><a href='https://www.strava.com/activities/{s.id}' target='_blank'>{s.name}</a></li>" for s in skipped])}
                        </ul>
                    </details>
                    <form action="/clear_skipped" method="post">
                        <button type="submit" class="btn btn-secondary btn-danger" style="width:100%; color:white; font-size:0.8em;" {f"disabled" if not skipped else ""}>Clear List</button>
                    </form>
                </div>
            </div>

            <div class="main-content">
                <div class="card" style="padding:0;">
                    <div style="padding:20px; border-bottom:1px solid #ebedf0;">
                        <h3 style="margin:0;">Pending Cleanup ({len(pending)})</h3>
                    </div>
                    <table>
                        <thead>
                            <tr><th>Date</th><th>Activity Name</th><th>Stats</th><th>Actions</th></tr>
                        </thead>
                        <tbody>
                            {"<tr><td colspan='4' style='text-align:center; color:#65676b;'>No activities waiting.</td></tr>" if not pending else ""}
                            {"".join([f"<tr><td>{i.date[:10]}</td><td>{i.name}</td><td><span class='stat-pill'>{format_stats(i)}</span></td><td><a href='https://www.strava.com/activities/{i.new_id}' target='_blank'>New</a> | <a href='https://www.strava.com/activities/{i.old_id}' target='_blank'>Original</a></td></tr>" for i in pending])}
                        </tbody>
                    </table>
                </div>

                <div class="card" style="padding:0;">
                    <div style="padding:20px; border-bottom:1px solid #ebedf0;">
                        <h3 style="margin:0;">Recently Completed ({len(completed)})</h3>
                    </div>
                    <table id="completed-table">
                        <thead>
                            <tr><th>Date</th><th>Activity Name</th><th>Stats</th><th>Link</th></tr>
                        </thead>
                        <tbody>
                            {"<tr><td colspan='4' style='text-align:center; color:#65676b;'>No history yet.</td></tr>" if not paginated_completed else ""}
                            {"".join([f"<tr><td>{i.date[:10]}</td><td>{i.name}</td><td><span class='stat-pill'>{format_stats(i)}</span></td><td><a href='https://www.strava.com/activities/{i.new_id}' target='_blank'>View Activity</a></td></tr>" for i in paginated_completed])}
                        </tbody>
                    </table>
                    <div class="pagination">
                        <a href="?page={current_page - 1}" class="btn btn-secondary" {"style='visibility:hidden'" if current_page <= 1 else ""}>&larr; Prev</a>
                        <span>Page {current_page} of {total_pages}</span>
                        <a href="?page={current_page + 1}" class="btn btn-secondary" {"style='visibility:hidden'" if current_page >= total_pages else ""}>Next &rarr;</a>
                    </div>
                    <div style="padding-bottom: 20px;"></div>
                </div>
            </div>
        </div>

        <div id="helpModal" class="modal">
            <div class="modal-content">
                <span class="close" onclick="document.getElementById('helpModal').style.display='none'">&times;</span>
                <h2>How to Use Fitbit HR to Strava</h2>
                <hr style="border:0; border-top:1px solid #eee; margin-bottom:20px;">
                <div class="help-step"><h4>Step 1: Authenticate</h4><p>Ensure both badges are blue (✓). If not, use the Re-auth buttons.</p></div>
                <div class="help-step"><h4>Step 2: Scan</h4><p>Use <b>Scan History</b> to find activities missing data. "Fixable" means Fitbit data is available.</p></div>
                <div class="help-step"><h4>Step 3: Sync</h4><p>Enter <b>Count</b> and <b>History Depth</b>, then click <b>Start Sync</b>.</p></div>
                <div class="help-step"><h4>Step 4: Cleanup</h4><p>Verify new activities in <b>Pending Cleanup</b>, manually delete originals on Strava, then click <b>Verify Deletions</b>.</p></div>
            </div>
        </div>

        <script>
            const consoleBox = document.getElementById('console');
            consoleBox.scrollTop = consoleBox.scrollHeight;
            const eventSource = new EventSource('/stream');
            eventSource.onmessage = function(event) {{
                const data = event.data.trim();
                if (data === '[DONE]') {{ setTimeout(() => window.location.reload(), 1500); return; }}
                if (data.length > 0) {{
                    if (consoleBox.innerText === '--- System Idle ---') consoleBox.innerText = '';
                    consoleBox.innerText += data + '\\n';
                    consoleBox.scrollTop = consoleBox.scrollHeight;
                }}
            }};
            document.querySelectorAll('th').forEach(th => th.addEventListener('click', (() => {{
                const table = th.closest('table');
                const tbody = table.querySelector('tbody');
                const rows = Array.from(tbody.querySelectorAll('tr'));
                if (rows.length <= 1 && rows[0].cells.length <= 1) return;
                const index = Array.from(th.parentNode.children).indexOf(th);
                const asc = th.dataset.asc = th.dataset.asc !== 'true';
                rows.sort((a, b) => {{
                    const aVal = a.children[index].innerText || a.children[index].textContent;
                    const bVal = b.children[index].innerText || b.children[index].textContent;
                    return asc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
                }}).forEach(tr => tbody.appendChild(tr));
            }})));
            window.onclick = function(event) {{ if (event.target == document.getElementById('helpModal')) {{ document.getElementById('helpModal').style.display = "none"; }} }}
        </script>
    </body>
    </html>
    '''
    return html

@app.route("/login/strava")
def login_strava():
    redirect_uri = "http://127.0.0.1:8080/callback/strava"
    scopes = "read,activity:read_all,activity:write"
    return redirect(f"{STRAVA_AUTH_URL}?client_id={STRAVA_CLIENT_ID}&response_type=code&redirect_uri={redirect_uri}&scope={scopes}")

@app.route("/callback/strava")
def callback_strava():
    code = request.args.get("code")
    data = {"client_id": STRAVA_CLIENT_ID, "client_secret": STRAVA_CLIENT_SECRET, "code": code, "grant_type": "authorization_code"}
    resp = requests.post(STRAVA_TOKEN_URL, data=data)
    if resp.status_code == 200:
        save_tokens("strava", resp.json())
        return redirect(url_for("dashboard"))
    return f"Error: {resp.text}"

@app.route("/login/fitbit")
def login_fitbit():
    import urllib.parse
    redirect_uri = "http://127.0.0.1:8080/callback/fitbit"
    scopes = "activity heartrate profile"
    params = {"client_id": FITBIT_CLIENT_ID, "response_type": "code", "redirect_uri": redirect_uri, "scope": scopes}
    return redirect(f"{FITBIT_AUTH_URL}?{urllib.parse.urlencode(params)}")

@app.route("/callback/fitbit")
def callback_fitbit():
    code = request.args.get("code")
    redirect_uri = "http://127.0.0.1:8080/callback/fitbit"
    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {auth_header}", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code, "client_id": FITBIT_CLIENT_ID}
    resp = requests.post(FITBIT_TOKEN_URL, headers=headers, data=data)
    if resp.status_code == 200:
        save_tokens("fitbit", resp.json())
        return redirect(url_for("dashboard"))
    return f"Error: {resp.text}"

if __name__ == "__main__":
    app.run(port=8080, debug=True)
