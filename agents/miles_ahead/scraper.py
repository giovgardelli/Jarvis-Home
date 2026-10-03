import os
import time
import requests

SERPAPI_KEY = os.getenv("SERPAPI_KEY")

def get_google_flights(origin, destination, date_str, max_retries=3):
    """
    Get flight prices from Google Flights via SerpApi.
    date_str format: YYYY-MM-DD
    Returns list of dicts: {flight, airline, departs, arrives, price}.
    Returns None if the call fails after retries — distinct from a
    successful call that simply found no flights ([]).
    """
    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": date_str,
        "currency": "USD",
        "hl": "en",
        "type": "2",  # one way
        "stops": "1",  # nonstop only
        "api_key": SERPAPI_KEY
    }

    data = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get("https://serpapi.com/search", params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            break
        except requests.exceptions.RequestException as e:
            print(f"SerpApi error (attempt {attempt}/{max_retries}) for {origin}→{destination} on {date_str}: {e}", flush=True)
            if attempt < max_retries:
                time.sleep(2 ** attempt)

    if data is None:
        return None

    try:
        flights = []
        all_flights = data.get("best_flights", []) + data.get("other_flights", [])

        for result in all_flights:
            for leg in result.get("flights", []):
                flight_info = dict()
                flight_info["flight"] = leg.get("flight_number", "")
                flight_info["airline"] = leg.get("airline", "")
                flight_info["departs"] = leg.get("departure_airport", {}).get("time", "")
                flight_info["arrives"] = leg.get("arrival_airport", {}).get("time", "")
                flight_info["price"] = result.get("price", 0)
                if flight_info["flight"] and flight_info["price"]:
                    flights.append(flight_info)

        print(f"SerpApi returned {len(flights)} flights for {origin}→{destination} on {date_str}", flush=True)
        return flights
    except Exception as e:
        print(f"SerpApi response parsing error: {e}", flush=True)
        return None

def get_southwest_price(origin, destination, date_str, flight_number):
    """Get price for a specific Southwest flight number."""
    all_flights = get_google_flights(origin, destination, date_str)
    if not all_flights:
        return None
    flight_num_clean = flight_number.replace("WN", "").strip()
    for f in all_flights:
        if flight_num_clean in f.get("flight", "").replace(" ", ""):
            return f.get("price")
    return None

def get_cheapest_southwest(origin, destination, date_str):
    """Get the cheapest Southwest flight for a route/date."""
    all_flights = get_google_flights(origin, destination, date_str)
    if not all_flights:
        return None
    sw_flights = [f for f in all_flights if "southwest" in f.get("airline", "").lower()]
    if not sw_flights:
        return None
    return min(sw_flights, key=lambda x: x.get("price", 999))