"""
get_token.py — link your Nest account to Ray Mote (run once, on any computer
with a browser; standard library only).

    python agents/ray_mote/get_token.py

Asks for the Device Access project id and the OAuth client id/secret
(README.md, "Set up Nest access"), opens Google's Nest sign-in, then prints
the lines for agents/ray_mote/.env and the thermostats it can see.
"""
import json
import os
import urllib.parse
import urllib.request
import webbrowser

REDIRECT = "https://www.google.com"
SCOPE = "https://www.googleapis.com/auth/sdm.service"


def ask(name, prompt):
    return os.environ.get(name) or input(prompt).strip()


def post(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data)) as resp:
        return json.load(resp)


def main():
    project = ask("NEST_PROJECT_ID", "Device Access project id: ")
    client_id = ask("NEST_CLIENT_ID", "OAuth client id: ")
    secret = ask("NEST_CLIENT_SECRET", "OAuth client secret: ")

    url = (f"https://nestservices.google.com/partnerconnections/{project}/auth?"
           + urllib.parse.urlencode({"redirect_uri": REDIRECT, "access_type": "offline", "prompt": "consent",
                                     "client_id": client_id, "response_type": "code", "scope": SCOPE}))
    print("\n1. Sign in with the Google account your Nest app uses, and allow every")
    print("   thermostat. (Opening it now; if nothing opens, paste this into a browser:)\n")
    print(url + "\n")
    webbrowser.open(url)
    print("2. You'll land on google.com. Copy that page's whole address from the address bar.")
    pasted = input("\nPaste it here: ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).get("code", [pasted])[0]

    tokens = post("https://oauth2.googleapis.com/token", {
        "client_id": client_id, "client_secret": secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": REDIRECT})
    if "refresh_token" not in tokens:
        raise SystemExit(f"No refresh token came back: {tokens}")

    # Listing the devices once finishes linking the account
    req = urllib.request.Request(
        f"https://smartdevicemanagement.googleapis.com/v1/enterprises/{project}/devices",
        headers={"Authorization": f"Bearer {tokens['access_token']}"})
    with urllib.request.urlopen(req) as resp:
        devices = json.load(resp).get("devices", [])
    print("\nThermostats found (the name is what you say: \"set upstairs to 70\"):")
    for d in devices:
        if d.get("type") != "sdm.devices.types.THERMOSTAT":
            continue
        name = d.get("traits", {}).get("sdm.devices.traits.Info", {}).get("customName")
        rooms = [r.get("displayName") for r in d.get("parentRelations", [])]
        print(f"  - {name or '(no name)'}   room: {', '.join(r for r in rooms if r) or '-'}")

    print("\nAdd to agents/ray_mote/.env:\n")
    print(f"NEST_PROJECT_ID={project}")
    print(f"NEST_CLIENT_ID={client_id}")
    print(f"NEST_CLIENT_SECRET={secret}")
    print(f"NEST_REFRESH_TOKEN={tokens['refresh_token']}")


if __name__ == "__main__":
    main()
