import os
import json
import base64
import requests
from datetime import datetime
from database import SessionLocal, Token

class FitbitClient:
    def __init__(self):
        self.client_id = os.getenv("FITBIT_CLIENT_ID")
        self.client_secret = os.getenv("FITBIT_CLIENT_SECRET")
        self.tokens = self._load_tokens()
        
    def _load_tokens(self):
        db = SessionLocal()
        token_record = db.query(Token).filter(Token.service == "fitbit").first()
        db.close()
        if not token_record:
            raise Exception("Fitbit tokens not found in database. Please login via the dashboard first.")
        return token_record.other_data

    def _save_tokens(self):
        db = SessionLocal()
        token_record = db.query(Token).filter(Token.service == "fitbit").first()
        if not token_record:
            token_record = Token(service="fitbit")
            db.add(token_record)
        
        token_record.access_token = self.tokens.get("access_token")
        token_record.refresh_token = self.tokens.get("refresh_token")
        token_record.expires_at = self.tokens.get("expires_in") # Fitbit uses expires_in
        token_record.other_data = self.tokens
        
        db.commit()
        db.close()

    def _refresh_token(self):
        print("  Refreshing Fitbit token...")
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
            print("  Fitbit token refresh successful.")
            self.tokens = resp.json()
            self._save_tokens()
        else:
            print(f"  Fitbit token refresh failed: {resp.status_code} - {resp.text}")
            raise Exception(f"Failed to refresh Fitbit token: {resp.text}")

    def _update_rate_limits(self, headers):
        limit = headers.get("Fitbit-Rate-Limit-Limit")
        remaining = headers.get("Fitbit-Rate-Limit-Remaining")
        retry_after = headers.get("Retry-After")
        
        if limit and remaining:
            try:
                db = SessionLocal()
                from database import RateLimit
                from datetime import datetime, timedelta
                
                rl = db.query(RateLimit).filter(RateLimit.service == "fitbit").first()
                if not rl:
                    rl = RateLimit(service="fitbit")
                    db.add(rl)
                
                rl.limit = int(limit)
                rl.remaining = int(remaining)
                
                # If we have a retry_after, use it, otherwise assume 1 hour reset for Fitbit
                if retry_after:
                    rl.reset_at = datetime.utcnow() + timedelta(seconds=int(retry_after))
                else:
                    rl.reset_at = datetime.utcnow() + timedelta(hours=1)
                    
                db.commit()
                db.close()
            except:
                pass

    def _request(self, method, url, **kwargs):
        headers = kwargs.get("headers", {})
        headers["Authorization"] = f"Bearer {self.tokens['access_token']}"
        kwargs["headers"] = headers
        
        resp = requests.request(method, url, **kwargs)
        if resp.status_code == 401: # Token expired
            self._refresh_token()
            headers["Authorization"] = f"Bearer {self.tokens['access_token']}"
            resp = requests.request(method, url, **kwargs)
        
        self._update_rate_limits(resp.headers)

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
                hr_points[point["time"]] = point["value"]
        except KeyError:
            pass
            
        return hr_points
