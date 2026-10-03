from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar.readonly"
]

flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)

# Force fresh consent screen
creds = flow.run_local_server(port=0, prompt="consent")

with open("token.json", "w") as f:
    f.write(creds.to_json())

print("Done! token.json has been created.")
print("Scopes granted:", creds.scopes)