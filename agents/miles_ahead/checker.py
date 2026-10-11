import os
from datetime import date, datetime
from calendar_reader import get_tracked_flights, tracked_airline
from scraper import get_google_flights, get_flight_price
from sheets import get_flight, add_flight, update_flight_field, set_flight_status, get_active_flights, should_check_today

# Southwest fares bottom out around $49; anything bought below this can't
# meaningfully drop, so skip the price check to save SerpApi calls.
MIN_TRACK_PRICE = 55

# Don't alert on price drops of $5 or less — not worth cancel/rebook hassle.
MIN_SAVINGS_TO_ALERT = 5


def _parse_dep_time(s):
    """Minutes-since-midnight for a departure string in any known format, or
    None if unparseable. Handles ISO ('2026-08-11T07:45:00-07:00'), SerpApi
    ('2026-08-11 13:20'), '7:45 AM', and '13:20'."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt.hour * 60 + dt.minute
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%I:%M %p", "%H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.hour * 60 + dt.minute
        except Exception:
            continue
    return None

def sync_cancelled_flights():
    """Mark flights as Done if they're no longer in the calendar."""
    print("Syncing cancelled flights...", flush=True)
    
    # Get all calendar flights
    calendar_flights = get_tracked_flights(days_ahead=60)
    calendar_keys = set()
    for f in calendar_flights:
        calendar_keys.add(f"{f['flight_number']}_{f['date']}")
    
    # Get all active sheet flights
    active_flights = get_active_flights()
    
    for flight in active_flights:
        flight_num = flight.get("Flight", "")
        date_str = flight.get("Date", "")
        key = f"{flight_num}_{date_str}"
        
        if key not in calendar_keys:
            print(f"Flight {flight_num} on {date_str} no longer in calendar — marking Done", flush=True)
            set_flight_status(flight_num, date_str, "Done")
def run_daily_check():
    """Main daily check — find flights, compare prices, return alerts.

    Returns (alerts, summary) where summary = {"checked": n, "deals": d}:
    `checked` counts flights that got a full price comparison this run,
    `deals` counts cheaper fares found. Callers use these to send a heartbeat
    ping on a clean run (checked > 0, deals == 0) so a silent day still
    confirms the checker is alive.
    """
    print("=== MILES AHEAD DAILY CHECK ===", flush=True)
    alerts = []
    checked_count = 0
    deals_count = 0

    # First sync cancelled flights
    sync_cancelled_flights()

    # Step 1 — Find Southwest flights in calendar
    calendar_flights = get_tracked_flights(days_ahead=60)
    print(f"Found {len(calendar_flights)} tracked flights in calendar", flush=True)

    # Step 2 — Add any new flights to the sheet
    for flight in calendar_flights:
        flight_num = flight["flight_number"]
        date_str = flight["date"]
        existing = get_flight(flight_num, date_str)

        if not existing:
            print(f"New flight found: {flight_num} on {date_str} — getting initial price", flush=True)
            origin = flight["origin"]
            destination = flight["destination"]
            price = get_flight_price(origin, destination, date_str, flight_num)

            if price:
                add_flight(
                    flight_num, date_str,
                    f"{origin}-{destination}",
                    flight.get("departure_time", ""),
                    flight.get("confirmation", ""),
                    price
                )
                alerts.append(
                    f"✅ *New flight tracked!*\n"
                    f"{flight_num} · {origin}→{destination}\n"
                    f"📅 {date_str}\n"
                    f"💰 Price saved: *${price}*"
                )
                print(f"Saved {flight_num} with paid price ${price}", flush=True)
            else:
                print(f"Could not get initial price for {flight_num}", flush=True)

    # Step 3 — Check prices for active flights
    active_flights = get_active_flights()
    print(f"Active flights to monitor: {len(active_flights)}", flush=True)

    for flight in active_flights:
        flight_num = flight.get("Flight", "")
        date_str = flight.get("Date", "")
        paid_price = flight.get("Paid Price", 0)
        route = flight.get("Route", "LAX-SFO")

        try:
            paid_price = int(paid_price)
        except:
            continue

        # At/below the fare floor there's nothing to save — skip the API call
        if paid_price < MIN_TRACK_PRICE:
            print(f"Skipping {flight_num} — paid ${paid_price} is below the ${MIN_TRACK_PRICE} floor", flush=True)
            continue

        if not should_check_today(flight):
            print(f"Skipping {flight_num} — not due for check yet", flush=True)
            continue

        print(f"Checking prices for {flight_num} on {date_str} — paid ${paid_price}", flush=True)

        # Parse route
        parts = route.split("-")
        origin = parts[0] if len(parts) >= 2 else "LAX"
        destination = parts[1] if len(parts) >= 2 else "SFO"

        all_prices = get_google_flights(origin, destination, date_str)

        if all_prices is None:
            print(f"Price check FAILED for {flight_num} — SerpApi error after retries", flush=True)
            alerts.append(
                f"⚠️ *Price check failed*\n"
                f"{flight_num} · {origin}→{destination} · {date_str}\n"
                f"SerpApi didn't respond after retries — will retry tomorrow."
            )
            continue

        today = date.today().strftime("%m/%d/%Y")
        update_flight_field(flight_num, date_str, "Last Checked", today)

        if not all_prices:
            print(f"No prices returned for {flight_num}", flush=True)
            continue

        # A real price comparison is about to happen for this flight.
        checked_count += 1

        # Parse original departure time for time window filtering
        orig_departure = flight.get("Departure", "")
        def within_one_hour(flight_departs, original_departs):
            """True if the candidate departs within 60 min of the original."""
            a = _parse_dep_time(flight_departs)
            b = _parse_dep_time(original_departs)
            if a is None or b is None:
                return True  # can't parse one side — don't exclude
            diff = abs(a - b)
            diff = min(diff, 1440 - diff)  # handle wrap across midnight
            return diff <= 60

        # Same airline only (a cheaper fare on another airline can't be
        # rebooked for credit), within 1 hour of the original departure
        airline, rebook_site = tracked_airline(flight_num) or ("Southwest", "southwest.com")
        sw_flights = [
            f for f in all_prices
            if airline.lower() in f.get("airline", "").lower()
            and (not orig_departure or within_one_hour(f.get("departs", ""), orig_departure))
        ]
        cheaper_sw = [f for f in sw_flights if f.get("price", 999) < paid_price]

        if cheaper_sw:
            best = min(cheaper_sw, key=lambda x: x.get("price", 999))
            savings = paid_price - best["price"]
            update_flight_field(flight_num, date_str, "Cheaper Found", "Yes")

            if savings > MIN_SAVINGS_TO_ALERT:
                deals_count += 1
                msg = f"✈️ *Miles Ahead Found a Deal!*\n\n"
                msg += f"*{flight_num}* · {origin}→{destination}\n"
                msg += f"📅 {date_str}\n"
                msg += f"💰 You paid: *${paid_price}*\n"
                msg += f"✅ Same route now: *${best['price']}* (save *${savings}*)\n"
                if best.get("departs"):
                    msg += f"🕐 Departs: {best['departs']}\n"
                msg += f"\n👉 Rebook on {rebook_site}"
                alerts.append(msg)
                print(f"Deal found for {flight_num}: save ${savings}", flush=True)
            else:
                print(f"Cheaper price found for {flight_num} but only ${savings} — below ${MIN_SAVINGS_TO_ALERT} alert threshold", flush=True)
        else:
            update_flight_field(flight_num, date_str, "Cheaper Found", "No")
            print(f"No cheaper {airline} options for {flight_num}", flush=True)

    return alerts, {"checked": checked_count, "deals": deals_count}