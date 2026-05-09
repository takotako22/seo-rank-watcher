"""
OAuth2のリフレッシュトークンを取得するワンタイムスクリプト。
ローカルで一度だけ実行し、得られたトークンをRailwayの環境変数に設定する。

使い方:
  pip install google-auth-oauthlib
  python get_refresh_token.py
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

CLIENT_CONFIG = {
    "installed": {
        "client_id": input("GSC_CLIENT_ID を入力: ").strip(),
        "client_secret": input("GSC_CLIENT_SECRET を入力: ").strip(),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

print("\n===== Railway に設定する環境変数 =====")
print(f"GSC_REFRESH_TOKEN = {creds.refresh_token}")
print("=====================================")
