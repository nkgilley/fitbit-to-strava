import os
import json
import base64
import requests
from datetime import datetime

TOKENS_FILE = "tokens.json"

class FitbitClient:
    def __init__(self):
        self.client_id = os.getenv("FITBIT_CLIENT_ID")
        self.client_secret = os.getenv("FITBIT_CLIENT_SECRET")
        self.tokens = self._load_tokens()
        
    def _load_tokens(self):
        if not os.path.exists(TOKENS_FILE):
            raise Exception("Tokens file not found. Please run auth.py and login first.")
        with open(TOKENS_FILE, "r") as f:
            data = json.load(f)
            if "fitbit" not in data:
                raise Exception("Fitbit tokens not found. Please run auth.py and login first.")
            return data["fitbit"]

    def _save_tokens(self):
        with open(TOKENS_FILE, "r") as f:
            data = json.load(f)
        data["fitbit"] = self.tokens
        with open(TOKENS_FILE, "w") as f:
            json.dump(data, f, indent=4)

    def _refresh_token(self):
        auth_header = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.tokens["refresh_token"],
            "client_id": self.client_id
        }
        resp = requests.post("https://api.fitbit.com/oauth2/token", headers=headers, data=data)
        if resp.status_code == 200:
            self.tokens = resp.json()
            self._save_tokens()
        else:
            raise Exception(f"Failed to refresh Fitbit token: {resp.text}")

    def _request(self, method, url, **kwargs):
        headers = kwargs.get("headers", {})
        headers["Authorization"] = f"Bearer {self.tokens['access_token']}"
        kwargs["headers"] = headers
        
        resp = requests.request(method, url, **kwargs)
        if resp.status_code == 401: # Token expired
            print("  Fitbit token expired, refreshing...")
            self._refresh_token()
            headers["Authorization"] = f"Bearer {self.tokens['access_token']}"
            resp = requests.request(method, url, **kwargs)
        
        if resp.status_code != 200:
            print(f"  Fitbit API Error ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
            
        return resp.json()

    def get_hr_data(self, date_str, start_time_str, end_time_str):
        """
        date_str: 'YYYY-MM-DD'
        start_time_str: 'HH:MM'
        end_time_str: 'HH:MM'
        """
        url = f"https://api.fitbit.com/1/user/-/activities/heart/date/{date_str}/1d/1sec/time/{start_time_str}/{end_time_str}.json"
        data = self._request("GET", url)
        
        hr_points = {}
        try:
            dataset = data["activities-heart-intraday"]["dataset"]
            for point in dataset:
                # Format: 'HH:MM:SS' -> value
                hr_points[point["time"]] = point["value"]
        except KeyError:
            pass
            
        return hr_points
