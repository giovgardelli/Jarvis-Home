from datetime import date
from sheets import get_tasks, get_home_info

# ─── Event → System Mapping ───────────────────────────────────────────────────
# Maps weather events to home systems they affect
# Users can override these via WhatsApp

EVENT_SYSTEM_MAP = {
    "fire": ["HVAC", "Interior", "Exterior", "Roof"],
    "high_aqi": ["HVAC", "Interior"],
    "winds": ["Roof", "Exterior", "Interior"],
    "santa_ana": ["Roof", "Exterior", "HVAC", "Interior"],
    "rain": ["Exterior", "Roof", "Plumbing"],
    "heat": ["HVAC", "Plumbing"],
    "cold": ["Plumbing", "HVAC", "Exterior"],
    "hail": ["Roof", "Exterior"],
    "earthquake": ["Plumbing", "Roof", "Exterior", "HVAC"],
    "humidity": ["HVAC", "Interior"]
}

# ─── Event → Phase Thresholds ─────────────────────────────────────────────────

PHASE_THRESHOLDS = {
    "fire": {
        "phase1_triggers": ["Red Flag Warning", "Fire Warning", "Fire Weather Watch"],
        "phase2_aqi": 150,
        "phase3_aqi": 50
    },
    "high_aqi": {
        "phase1_aqi": 100,
        "phase2_aqi": 150,
        "phase3_aqi": 50
    },
    "winds": {
        "phase1_triggers": ["Wind Advisory", "High Wind Watch"],
        "phase2_triggers": ["High Wind Warning"],
        "phase3_triggers": []
    },
    "santa_ana": {
        "phase1_triggers": ["Red Flag Warning"],
        "phase2_triggers": ["High Wind Warning"],
        "phase3_triggers": []
    },
    "rain": {
        "phase1_triggers": ["Flood Watch", "Flash Flood Watch"],
        "phase2_triggers": ["Flood Warning", "Flash Flood Warning"],
        "phase3_triggers": []
    },
    "heat": {
        "phase1_triggers": ["Heat Advisory", "Excessive Heat Watch"],
        "phase2_triggers": ["Excessive Heat Warning"],
        "phase3_temp": 90
    },
    "cold": {
        "phase1_triggers": ["Freeze Watch", "Wind Chill Advisory"],
        "phase2_triggers": ["Freeze Warning", "Wind Chill Warning"],
        "phase3_temp": 40
    },
    "hail": {
        "phase1_triggers": ["Severe Thunderstorm Watch"],
        "phase2_triggers": ["Severe Thunderstorm Warning"],
        "phase3_triggers": []
    },
    "earthquake": {
        "phase2_magnitude": 4.0,
        "phase3_hours": 24
    },
    "humidity": {
        "phase1_triggers": [],
        "phase2_triggers": [],
        "phase3_triggers": []
    }
}

# ─── Phase Detection ──────────────────────────────────────────────────────────

def detect_active_events(conditions):
    """
    Given weather conditions, return a list of active events with their phase.
    Each event: {"type": "fire", "phase": 1, "detail": "Red Flag Warning"}
    """
    active = []
    alerts = conditions.get("alerts", [])
    aqi_data = conditions.get("air_quality")
    forecast = conditions.get("forecast")
    earthquakes = conditions.get("earthquakes", [])
    alert_events = [a.get("event", "") for a in alerts]

    # Fire / AQI
    aqi = aqi_data.get("aqi") if aqi_data else None
    fire_alert = any(t in alert_events for t in
                     PHASE_THRESHOLDS["fire"]["phase1_triggers"])

    if fire_alert and aqi and aqi >= PHASE_THRESHOLDS["fire"]["phase2_aqi"]:
        active.append({
            "type": "fire",
            "phase": 2,
            "detail": f"Active fire conditions + AQI {aqi}"
        })
    elif fire_alert:
        active.append({
            "type": "fire",
            "phase": 1,
            "detail": next((a["event"] for a in alerts
                           if a["event"] in PHASE_THRESHOLDS["fire"]["phase1_triggers"]),
                          "Fire warning")
        })
    elif aqi and aqi <= PHASE_THRESHOLDS["fire"]["phase3_aqi"]:
        # Only send phase 3 if we were previously in a fire event
        # (handled by state tracking in scheduler)
        pass

    # High AQI without fire warning
    if not fire_alert and aqi:
        if aqi >= PHASE_THRESHOLDS["high_aqi"]["phase2_aqi"]:
            active.append({
                "type": "high_aqi",
                "phase": 2,
                "detail": f"AQI {aqi} - {aqi_data.get('category', '')}"
            })
        elif aqi >= PHASE_THRESHOLDS["high_aqi"]["phase1_aqi"]:
            active.append({
                "type": "high_aqi",
                "phase": 1,
                "detail": f"AQI {aqi} - {aqi_data.get('category', '')}"
            })

    # Winds / Santa Ana
    for event_type, nws_events in [
        ("winds", PHASE_THRESHOLDS["winds"]),
        ("santa_ana", PHASE_THRESHOLDS["santa_ana"]),
        ("rain", PHASE_THRESHOLDS["rain"]),
        ("heat", PHASE_THRESHOLDS["heat"]),
        ("cold", PHASE_THRESHOLDS["cold"]),
        ("hail", PHASE_THRESHOLDS["hail"])
    ]:
        phase2 = nws_events.get("phase2_triggers", [])
        phase1 = nws_events.get("phase1_triggers", [])
        if any(t in alert_events for t in phase2):
            active.append({
                "type": event_type,
                "phase": 2,
                "detail": next((a["event"] for a in alerts
                               if a["event"] in phase2), event_type)
            })
        elif any(t in alert_events for t in phase1):
            active.append({
                "type": event_type,
                "phase": 1,
                "detail": next((a["event"] for a in alerts
                               if a["event"] in phase1), event_type)
            })

    # Temperature-based heat/cold phase 3
    if forecast:
        temp = forecast.get("temperature")
        if temp:
            if temp > PHASE_THRESHOLDS["heat"].get("phase3_temp", 90):
                if not any(e["type"] == "heat" for e in active):
                    active.append({
                        "type": "heat",
                        "phase": 2,
                        "detail": f"Temperature {temp}°F"
                    })
            if temp < 32:
                if not any(e["type"] == "cold" for e in active):
                    active.append({
                        "type": "cold",
                        "phase": 2,
                        "detail": f"Temperature {temp}°F - freezing conditions"
                    })

    # Earthquakes
    for quake in earthquakes:
        if quake["magnitude"] >= PHASE_THRESHOLDS["earthquake"]["phase2_magnitude"]:
            active.append({
                "type": "earthquake",
                "phase": 2,
                "detail": f"M{quake['magnitude']} - {quake['place']} at {quake['time']}"
            })

    return active

# ─── Task Matching ────────────────────────────────────────────────────────────

def get_affected_tasks(event_type, tasks):
    """Return tasks affected by a weather event."""
    affected_systems = EVENT_SYSTEM_MAP.get(event_type, [])
    affected = []
    for task in tasks:
        # Check Weather Triggers column first
        weather_triggers = task.get("Weather Triggers", "")
        if weather_triggers:
            triggers = [t.strip().lower() for t in str(weather_triggers).split(",")]
            if event_type in triggers:
                affected.append(task)
                continue
        # Fall back to system mapping
        if task.get("System", "") in affected_systems:
            affected.append(task)
    return affected

# ─── Alert Message Builder ────────────────────────────────────────────────────

def build_weather_alert(event, tasks, home_info_str, preferences):
    """Build a personalized alert message for a weather event."""
    event_type = event["type"]
    phase = event["phase"]
    detail = event["detail"]
    affected_tasks = get_affected_tasks(event_type, tasks)

    # Check if this phase is enabled in preferences
    pref_key = f"{event_type}_phase{phase}"
    prefs = dict()
    for line in home_info_str.split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            prefs[k.strip()] = v.strip()

    # Parse preferences from Weather_Preferences
    if preferences.get(pref_key, "true").lower() == "false":
        return None
    disabled = preferences.get("disabled_triggers", "")
    if event_type in [d.strip() for d in disabled.split(",")]:
        return None

    # Get HVAC filter size from home info if relevant
    filter_size = ""
    for line in home_info_str.split("\n"):
        if "filter" in line.lower():
            filter_size = line.split(":", 1)[-1].strip()
            break

    # Build emoji and header
    emoji_map = {
        "fire": "🔥", "high_aqi": "😷", "winds": "💨",
        "santa_ana": "🌬️", "rain": "🌧️", "heat": "🌡️",
        "cold": "🥶", "hail": "⛈️", "earthquake": "🏚️",
        "humidity": "💧"
    }
    phase_label = {1: "⚠️ HEADS UP", 2: "🚨 ACTIVE ALERT", 3: "✅ ALL CLEAR"}
    emoji = emoji_map.get(event_type, "⚠️")

    msg = f"{emoji} {phase_label.get(phase, 'ALERT')}: {detail}\n\n"

    if affected_tasks:
        if phase == 1:
            msg += "Recommended actions to prepare:\n"
        elif phase == 2:
            msg += "Recommended actions now:\n"
        else:
            msg += "Now that conditions have cleared:\n"

        for task in affected_tasks[:4]:  # Max 4 tasks per alert
            msg += f"• {task['Task']}"
            if phase == 1 and event_type in ["fire", "high_aqi"] and filter_size:
                msg += f" (filter size: {filter_size})"
            msg += "\n"

    msg += "\nReply to snooze or adjust these alerts."
    return msg

# ─── Preferences Reader ───────────────────────────────────────────────────────

def get_weather_preferences(sheet_obj):
    """Read Weather_Preferences tab."""
    try:
        sheet = sheet_obj.worksheet("Weather_Preferences")
        rows = sheet.get_all_records()
        return {r["Field"]: r["Value"] for r in rows}
    except:
        return {}