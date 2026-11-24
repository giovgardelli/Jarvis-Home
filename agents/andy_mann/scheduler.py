from sheets import get_tasks, get_sheet
from weather import get_weather_conditions
from weather_triggers import detect_active_events, build_weather_alert, get_weather_preferences

# In-memory state to track previous weather events
# Prevents sending duplicate alerts for the same event
previous_events = {}

def build_weather_messages():
    """Check weather and return any alert messages to send."""
    try:
        sheet_obj = get_sheet()

        # Get zip code from Property_Profile
        profile = sheet_obj.worksheet("Property_Profile")
        rows = profile.get_all_records()
        zip_code = None
        home_info_lines = []
        for row in rows:
            home_info_lines.append(f"{row['Field']}: {row['Value']}")
            if row["Field"].lower() == "zip code":
                zip_code = str(row["Value"]).strip()

        if not zip_code:
            print("No zip code found in Property_Profile")
            return []

        home_info_str = "\n".join(home_info_lines)

        # Get weather conditions
        conditions = get_weather_conditions(zip_code)
        if not conditions:
            return []

        # Get tasks and preferences
        tasks = get_tasks()
        preferences = get_weather_preferences(sheet_obj)

        # Detect active events
        active_events = detect_active_events(conditions)
        print(f"Active weather events: {[e['type'] for e in active_events]}")

        messages = []
        for event in active_events:
            event_key = f"{event['type']}_phase{event['phase']}"

            # Skip if we already sent this exact alert recently
            if previous_events.get(event_key) == event["detail"]:
                print(f"Skipping duplicate alert: {event_key}")
                continue

            # Build the alert message
            msg = build_weather_alert(event, tasks, home_info_str, preferences)
            if msg:
                messages.append(msg)
                previous_events[event_key] = event["detail"]

        # Clear previous events that are no longer active
        active_keys = {f"{e['type']}_phase{e['phase']}" for e in active_events}
        for key in list(previous_events.keys()):
            if key not in active_keys:
                del previous_events[key]

        return messages

    except Exception as e:
        print(f"Weather check error: {e}")
        return []