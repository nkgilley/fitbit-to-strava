import os
import json
import requests
import time

TOKENS_FILE = "tokens.json"

class StravaClient:
    def __init__(self):
        self.client_id = os.getenv("STRAVA_CLIENT_ID")
        self.client_secret = os.getenv("STRAVA_CLIENT_SECRET")
        self.tokens = self._load_tokens()

    def _load_tokens(self):
        if not os.path.exists(TOKENS_FILE):
            raise Exception("Tokens file not found. Please run auth.py and login first.")
        with open(TOKENS_FILE, "r") as f:
            data = json.load(f)
            if "strava" not in data:
                raise Exception("Strava tokens not found. Please run auth.py and login first.")
            return data["strava"]

    def _save_tokens(self):
        if not os.path.exists(TOKENS_FILE):
            data = {}
        else:
            with open(TOKENS_FILE, "r") as f:
                data = json.load(f)
        
        data["strava"] = self.tokens
        with open(TOKENS_FILE, "w") as f:
            json.dump(data, f, indent=4)

    def _refresh_token(self):
        print("  Refreshing Strava token...")
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.tokens["refresh_token"]
        }
        resp = requests.post("https://www.strava.com/oauth/token", data=data)
        if resp.status_code == 200:
            print("  Strava token refresh successful.")
            new_tokens = resp.json()
            self.tokens.update(new_tokens)
            self._save_tokens()
        else:
            print(f"  Strava token refresh failed: {resp.status_code} - {resp.text}")
            raise Exception(f"Failed to refresh Strava token: {resp.text}")

    def _request(self, method, url, **kwargs):
        headers = kwargs.get("headers", {})
        headers["Authorization"] = f"Bearer {self.tokens['access_token']}"
        kwargs["headers"] = headers

        resp = requests.request(method, url, **kwargs)
        if resp.status_code == 401:  # Token expired
            self._refresh_token()
            headers["Authorization"] = f"Bearer {self.tokens['access_token']}"
            resp = requests.request(method, url, **kwargs)

        if resp.status_code not in [200, 201, 204]:
            print(f"  Strava API Error ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
            
        if resp.status_code == 204:
            return True
            
        return resp.json()

    def get_activities(self, per_page=30, page=1):
        url = f"https://www.strava.com/api/v3/athlete/activities?per_page={per_page}&page={page}"
        return self._request("GET", url)

    def get_activity_streams(self, activity_id):
        keys = "time,latlng,distance,altitude,watts,cadence,velocity_smooth"
        url = f"https://www.strava.com/api/v3/activities/{activity_id}/streams?keys={keys}&key_by_type=true"
        return self._request("GET", url)

    def get_athlete(self):
        url = "https://www.strava.com/api/v3/athlete"
        return self._request("GET", url)

    def upload_activity(self, file_path, data_type="tcx", name=None, description=None, trainer=0, commute=0, gear_id=None, activity_type=None):
        # We handle upload separately because it uses multipart/form-data and polling
        url = "https://www.strava.com/api/v3/uploads"
        
        def do_post():
            headers = {"Authorization": f"Bearer {self.tokens['access_token']}"}
            data = {
                "data_type": data_type,
                "trainer": trainer,
                "commute": commute
            }
            if name: data["name"] = name
            if description: data["description"] = description
            if gear_id: data["gear_id"] = gear_id
            if activity_type: data["activity_type"] = activity_type
                
            with open(file_path, "rb") as f:
                files = {"file": f}
                return requests.post(url, headers=headers, data=data, files=files)

        resp = do_post()
        if resp.status_code == 401:
            print("  Strava upload 401, attempting token refresh...")
            self._refresh_token()
            resp = do_post()
            
        resp.raise_for_status()
        upload_data = resp.json()
        upload_id = upload_data.get("id")
        
        print(f"  Waiting for Strava to process upload {upload_id}...")
        for _ in range(20):
            time.sleep(2)
            check_url = f"https://www.strava.com/api/v3/uploads/{upload_id}"
            headers = {"Authorization": f"Bearer {self.tokens['access_token']}"}
            check_resp = requests.get(check_url, headers=headers)
            if check_resp.status_code == 401:
                self._refresh_token()
                headers["Authorization"] = f"Bearer {self.tokens['access_token']}"
                check_resp = requests.get(check_url, headers=headers)
            
            check_resp.raise_for_status()
            status_data = check_resp.json()
            
            print(f"    Status: {status_data.get('status')}")
            
            if status_data.get("activity_id"):
                return status_data
            elif status_data.get("error"):
                raise Exception(f"Strava upload error: {status_data.get('error')}")
            elif "Your upload is a duplicate" in (status_data.get("status") or ""):
                raise Exception("Strava flagged this as a duplicate activity.")
        
        raise Exception("Strava upload timed out.")
            
    def update_activity(self, activity_id, **kwargs):
        url = f"https://www.strava.com/api/v3/activities/{activity_id}"
        return self._request("PUT", url, json=kwargs)

    def delete_activity(self, activity_id):
        url = f"https://www.strava.com/api/v3/activities/{activity_id}"
        try:
            return self._request("DELETE", url)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return True
            raise e
