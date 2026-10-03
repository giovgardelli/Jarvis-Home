import os
import time
import requests

CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]

_access_token = None
_token_expiry = 0


def get_access_token() -> str:
    """Return a valid access token, refreshing if expired. Retries up to 3 times on 503."""
    global _access_token, _token_expiry

    if _access_token and time.time() < _token_expiry - 60:
        return _access_token

    last_error = None
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "refresh_token": REFRESH_TOKEN,
                    "grant_type": "refresh_token"
                },
                timeout=10
            )
            resp.raise_for_status()
            data          = resp.json()
            _access_token = data["access_token"]
            _token_expiry = time.time() + data.get("expires_in", 3600)
            return _access_token
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 503:
                wait = 2 ** attempt  # 1s, 2s, 4s
                print(f"[GoogleAuth] 503 on attempt {attempt + 1}, retrying in {wait}s...", flush=True)
                time.sleep(wait)
                last_error = e
            else:
                raise
        except requests.exceptions.RequestException as e:
            wait = 2 ** attempt
            print(f"[GoogleAuth] Request error on attempt {attempt + 1}, retrying in {wait}s...", flush=True)
            time.sleep(wait)
            last_error = e

    raise last_error


def get_credentials():
    """Return a google.oauth2.credentials.Credentials object for gspread/gcalendar."""
    from google.oauth2.credentials import Credentials
    token = get_access_token()
    return Credentials(
        token=token,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET
    )