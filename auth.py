import os
import json
import base64
import requests
from flask import Flask, request, redirect, url_for, session
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super_secret_key")

TOKENS_FILE = "tokens.json"

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
    tokens = {}
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE, "r") as f:
            tokens = json.load(f)
    tokens[service] = token_data
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=4)


@app.route("/")
def index():
    print(">>> Index page requested")
    return '''
    <h1>Fitbit to Strava Auth</h1>
    <p>Using port 8080</p>
    <ul>
        <li><a href="/login/strava">Login to Strava</a></li>
        <li><a href="/login/fitbit">Login to Fitbit</a></li>
    </ul>
    '''

@app.route("/login/strava")
def login_strava():
    print(">>> Strava login initiated")
    redirect_uri = "http://127.0.0.1:8080/callback/strava"
    # Using read,activity:read_all,activity:write
    scopes = "read,activity:read_all,activity:write"
    url = f"{STRAVA_AUTH_URL}?client_id={STRAVA_CLIENT_ID}&response_type=code&redirect_uri={redirect_uri}&scope={scopes}"
    return redirect(url)

@app.route("/callback/strava")
def callback_strava():
    print(">>> Strava callback received")
    error = request.args.get("error")
    if error:
        return f"Error: {error}"
    
    code = request.args.get("code")
    data = {
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code"
    }
    resp = requests.post(STRAVA_TOKEN_URL, data=data)
    if resp.status_code == 200:
        token_data = resp.json()
        print(f">>> Strava token response: {token_data}")
        save_tokens("strava", token_data)
        return "Strava tokens saved successfully! <a href='/'>Go back</a>"
    
    print(f">>> Strava token error: {resp.status_code} - {resp.text}")
    return f"Failed to get Strava tokens: {resp.text}"

@app.route("/login/fitbit")
def login_fitbit():
    print(">>> Fitbit login initiated")
    if not FITBIT_CLIENT_ID or not FITBIT_CLIENT_SECRET:
        return "Error: FITBIT_CLIENT_ID or FITBIT_CLIENT_SECRET not found in .env"
    
    import urllib.parse
    redirect_uri = "http://127.0.0.1:8080/callback/fitbit"
    # Fitbit scopes MUST be space-separated
    scopes = "activity heartrate profile"
    params = {
        "client_id": FITBIT_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scopes
    }
    url = f"{FITBIT_AUTH_URL}?{urllib.parse.urlencode(params)}"
    print(f">>> Redirecting to Fitbit: {url}")
    return redirect(url)

@app.route("/callback/fitbit")
def callback_fitbit():
    print(">>> Fitbit callback received")
    error = request.args.get("error")
    if error:
        print(f">>> Fitbit error: {error}")
        return f"Error from Fitbit: {error}. Description: {request.args.get('error_description')}"
    
    code = request.args.get("code")
    redirect_uri = "http://127.0.0.1:8080/callback/fitbit"
    
    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": code,
        "client_id": FITBIT_CLIENT_ID
    }
    print(f">>> Exchanging code for tokens. Redirect URI: {redirect_uri}")
    resp = requests.post(FITBIT_TOKEN_URL, headers=headers, data=data)
    
    if resp.status_code == 200:
        save_tokens("fitbit", resp.json())
        return "Fitbit tokens saved successfully! <a href='/'>Go back</a>"
    
    print(f">>> Failed to get tokens: {resp.status_code} - {resp.text}")
    return f"Failed to get Fitbit tokens (Status {resp.status_code}): {resp.text}"

if __name__ == "__main__":
    print(">>> Starting auth server on http://127.0.0.1:8080")
    app.run(port=8080, debug=True)
