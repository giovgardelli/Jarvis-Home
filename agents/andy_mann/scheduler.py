from sheets import get_tasks_with_due_dates, get_tasks, get_sheet
from weather import get_weather_conditions
from weather_triggers import detect_active_events, build_weather_alert, get_weather_preferences
from datetime import date

# In-memory state to track previous weather events
# Prevents sending duplicate alerts for the same event
previous_events = {}

def build_daily_message():
    tasks = get_tasks_with_due_dates()
    alerts = []

    for task in tasks:
        days_until = task["Days Until Due"]
        notice = task["Reminder Notice (Days)"]
        name = task["Task"]
        system = task["System"]
        next_due = task["Next Due"]

        if days_until <= notice:
            if days_until < 0:
                alerts.append(f"🔴 OVERDUE ({abs(days_until)}d): {name} — {system}")
            elif days_until == 0:
                alerts.append(f"🟡 DUE TODAY: {name} — {system}")
            elif days_until == 1:
                alerts.append(f"🟠 DUE TOMORROW: {name} — {system}")
            else:
                alerts.append(f"🟠 Due in {days_until}d ({next_due}): {name} — {system}")

    # Check expiring warranties
    try:
        from sheets import get_expiring_warranties
        expiring = get_expiring_warranties(days_ahead=90)
        for w in expiring:
            days = w["Days Until Expiry"]
            if days < 0:
                alerts.append(f"⚠️ WARRANTY EXPIRED: {w['Item']} (expired {abs(days)}d ago)")
            elif days == 0:
                alerts.append(f"⚠️ WARRANTY EXPIRES TODAY: {w['Item']}")
            else:
                alerts.append(f"⚠️ Warranty expiring in {days}d: {w['Item']}")
    except Exception as e:
        print(f"Warranty check error: {e}")

    if not alerts:
        return None

    msg = "🏠 *2229 Home Maintenance*\n\n"
    msg += "\n".join(alerts)
    msg += "\n\nReply to ask questions or mark tasks as done."
    return msg

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