"""Standalone script to authorize Google Calendar and refresh data/google_calendar_token.json"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar",
]

def main():
    secret_path = Path(os.getenv(
        "GOOGLE_CLIENT_SECRET_FILE",
        r"D:\client_secret_105367740-dkmrqgv78gcmcetp3nva06uupoujul4h.apps.googleusercontent.com.json"
    ))
    token_path = Path(os.getenv(
        "GOOGLE_CALENDAR_TOKEN_FILE",
        str(ROOT / "data" / "google_calendar_token.json")
    ))

    if not secret_path.is_file():
        print(f"ERROR: Google Client Secret file not found at: {secret_path}")
        print("Please check GOOGLE_CLIENT_SECRET_FILE in your .env file.")
        sys.exit(1)

    print("\n=======================================================")
    print("  GOOGLE CALENDAR ONE-TIME AUTHORIZATION")
    print("=======================================================")
    print(f"Client Secrets : {secret_path.name}")
    print(f"Token Output   : {token_path}")
    print("\nOpening your browser for Google Calendar authorization...")
    print("Please select your Google Account and click 'Allow' / 'Continue'.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print("\n[SUCCESS] Google Calendar token created successfully!")
    print(f"Saved to: {token_path}")
    print("You can now run: python run_all.py\n")

if __name__ == "__main__":
    main()
