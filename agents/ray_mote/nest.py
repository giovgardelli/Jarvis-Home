"""
nest.py — Google Nest thermostats through the Smart Device Management (SDM) API.

One Google Device Access project (NEST_PROJECT_ID) and a refresh token from
get_token.py. The API works in Celsius; everything outside this file is
Fahrenheit. Docs: developers.google.com/nest/device-access
"""
import os
import time
import requests

API = "https://smartdevicemanagement.googleapis.com/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TRAIT = "sdm.devices.traits."
COMMAND = "sdm.devices.commands."
CACHE_SECONDS = 30     # status questions within this window reuse the last read

_access_token = None
_token_expiry = 0
_devices = None
_devices_at = 0


class NestError(Exception):
    """A Nest call failed; str(e) is safe to say out loud."""


def f_to_c(f):
    return (f - 32) * 5 / 9


def c_to_f(c):
    return c * 9 / 5 + 32 if c is not None else None


def _env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise NestError(f"Ray Mote isn't set up yet ({name} is missing).")
    return value


def _token():
    global _access_token, _token_expiry
    if _access_token and time.time() < _token_expiry - 60:
        return _access_token
    try:
        resp = requests.post(TOKEN_URL, data={
            "client_id": _env("NEST_CLIENT_ID"),
            "client_secret": _env("NEST_CLIENT_SECRET"),
            "refresh_token": _env("NEST_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        }, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[Nest] Token refresh failed: {e}", flush=True)
        raise NestError("I couldn't sign in to Nest. The access may need renewing.")
    data = resp.json()
    _access_token = data["access_token"]
    _token_expiry = time.time() + data.get("expires_in", 3600)
    return _access_token


def _call(method, path, body=None):
    try:
        resp = requests.request(method, f"{API}/{path}", json=body, timeout=15,
                                headers={"Authorization": f"Bearer {_token()}"})
    except requests.exceptions.RequestException as e:
        print(f"[Nest] {method} {path} failed: {e}", flush=True)
        raise NestError("I couldn't reach Nest right now.")
    if resp.status_code >= 400:
        print(f"[Nest] {method} {path} → {resp.status_code}: {resp.text[:300]}", flush=True)
        if resp.status_code == 429:
            raise NestError("Nest says too many changes at once. Give it a minute.")
        raise NestError("Nest didn't accept that change.")
    return resp.json() if resp.text else {}


def _parse(device):
    traits = device.get("traits", {})

    def t(name):
        return traits.get(TRAIT + name, {})

    rooms = [r.get("displayName") for r in device.get("parentRelations", []) if r.get("displayName")]
    eco = t("ThermostatEco")
    setpoint = t("ThermostatTemperatureSetpoint")
    return {
        "name": device["name"],
        "label": t("Info").get("customName") or (rooms[0] if rooms else "Thermostat"),
        "room": rooms[0] if rooms else "",
        "online": t("Connectivity").get("status", "ONLINE") == "ONLINE",
        "mode": t("ThermostatMode").get("mode", "OFF"),
        "modes": t("ThermostatMode").get("availableModes", []),
        "eco": eco.get("mode") == "MANUAL_ECO",
        "hvac": t("ThermostatHvac").get("status", "OFF"),
        "ambient_f": c_to_f(t("Temperature").get("ambientTemperatureCelsius")),
        "heat_f": c_to_f(setpoint.get("heatCelsius")),
        "cool_f": c_to_f(setpoint.get("coolCelsius")),
    }


def thermostats(fresh=False):
    """Every thermostat on the account, parsed (Fahrenheit)."""
    global _devices, _devices_at
    if fresh or _devices is None or time.time() - _devices_at > CACHE_SECONDS:
        data = _call("GET", f"enterprises/{_env('NEST_PROJECT_ID')}/devices")
        _devices = [_parse(d) for d in data.get("devices", [])
                    if d.get("type") == "sdm.devices.types.THERMOSTAT"]
        _devices_at = time.time()
    return _devices


def _command(device, command, params):
    global _devices
    _call("POST", f"{device['name']}:executeCommand", {"command": COMMAND + command, "params": params})
    _devices = None   # next read shows the change


def set_mode(device, mode):
    """HEAT, COOL, HEATCOOL or OFF."""
    _command(device, "ThermostatMode.SetMode", {"mode": mode})


def set_eco(device, on):
    _command(device, "ThermostatEco.SetMode", {"mode": "MANUAL_ECO" if on else "OFF"})


def set_heat(device, f):
    _command(device, "ThermostatTemperatureSetpoint.SetHeat", {"heatCelsius": round(f_to_c(f), 2)})


def set_cool(device, f):
    _command(device, "ThermostatTemperatureSetpoint.SetCool", {"coolCelsius": round(f_to_c(f), 2)})


def set_range(device, heat_f, cool_f):
    _command(device, "ThermostatTemperatureSetpoint.SetRange",
             {"heatCelsius": round(f_to_c(heat_f), 2), "coolCelsius": round(f_to_c(cool_f), 2)})
