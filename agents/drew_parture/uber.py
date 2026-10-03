import os
from jarvis_core.config import TIMEZONE
import urllib.parse
import json
from datetime import datetime, timedelta
import pytz

PT = pytz.timezone(TIMEZONE)

DESTINATIONS = {
    "lax": ("LAX Airport", 33.9416, -118.4085, "Los Angeles International Airport", "Los Angeles, CA 90045"),
    "los angeles international": ("LAX Airport", 33.9416, -118.4085, "Los Angeles International Airport", "Los Angeles, CA 90045"),
    "airport": ("LAX Airport", 33.9416, -118.4085, "Los Angeles International Airport", "Los Angeles, CA 90045"),
    "sfo": ("SFO Airport", 37.6213, -122.3790, "San Francisco International Airport", "San Francisco, CA 94128"),
    "san francisco airport": ("SFO Airport", 37.6213, -122.3790, "San Francisco International Airport", "San Francisco, CA 94128"),
}

PRODUCT_IDS = {
    "uberx": "a1111c8c-c720-46c3-8534-2fcdd730040d",
    "uber black": "d4abaae7-f4d6-4152-91cc-77523e8165a8",
    "uber comfort": "18d3c1a8-3e55-4d68-9e4b-b22f0e3e6a9b",
    "uber xl": "821415d8-3bf5-4ea1-b681-89a4848de51e",
}

def build_uber_link(dest_name, dest_lat, dest_lng, dest_line1, dest_line2, service="uberx"):
    product_id = PRODUCT_IDS.get(service.lower(), PRODUCT_IDS["uberx"])

    params = urllib.parse.urlencode({
        "action": "setPickup",
        "pickup": "my_location",
        "dropoff[latitude]": str(dest_lat),
        "dropoff[longitude]": str(dest_lng),
        "dropoff[nickname]": dest_name,
        "dropoff[formatted_address]": f"{dest_line1}, {dest_line2}",
        "product_id": product_id
    })

    return f"https://m.uber.com/ul/?{params}"
    
def parse_uber_request(text, flight_context=None):
    """Parse a ride request from natural language."""
    import re
    text_lower = text.lower()
    result = dict()

    # Account
    result["account"] = "business" if "business" in text_lower else "personal"

    # Service type
    if "black" in text_lower:
        result["service"] = "uber black"
        result["service_label"] = "Uber Black"
    elif "comfort" in text_lower:
        result["service"] = "uber comfort"
        result["service_label"] = "Uber Comfort"
    elif "xl" in text_lower:
        result["service"] = "uber xl"
        result["service_label"] = "Uber XL"
    else:
        result["service"] = "uberx"
        result["service_label"] = "UberX"

    # Destination — explicit first
    result["for_airport"] = False
    for key, (name, lat, lng, line1, line2) in DESTINATIONS.items():
        if key in text_lower:
            result["destination_name"] = name
            result["destination_lat"] = lat
            result["destination_lng"] = lng
            result["destination_line1"] = line1
            result["destination_line2"] = line2
            result["for_airport"] = True
            break

    # If no explicit destination but flight context exists — infer airport
    if "destination_lat" not in result and flight_context:
        origin = flight_context.get("origin", "").upper()
        dest = flight_context.get("destination", "").upper()

        # We're going TO the flight, so destination is origin airport
        airport_code = origin if origin else dest
        if airport_code in DESTINATIONS:
            name, lat, lng, line1, line2 = DESTINATIONS[airport_code]
            result["destination_name"] = name
            result["destination_lat"] = lat
            result["destination_lng"] = lng
            result["destination_line1"] = line1
            result["destination_line2"] = line2
            result["for_airport"] = True
        elif airport_code == "LAX" or not airport_code:
            # Default to LAX
            result["destination_name"] = "LAX Airport"
            result["destination_lat"] = 33.9416
            result["destination_lng"] = -118.4085
            result["destination_line1"] = "Los Angeles International Airport"
            result["destination_line2"] = "Los Angeles, CA 90045"
            result["for_airport"] = True

    # If still no destination, try extracting from text
    if "destination_lat" not in result:
        patterns = [
            r"(?:to|drop.?off at|going to|take me to|ride to)\s+(.+?)(?:\s+(?:personal|business|now|please|asap|uber|uberx|black|comfort|xl|prior|before|early|hours?)|\s*$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                dest = match.group(1).strip()
                result["destination_name"] = dest.title()
                result["destination_lat"] = None
                result["destination_lng"] = None
                result["destination_line1"] = dest.title()
                result["destination_line2"] = ""
                break

    return result

def format_uber_message(request, flight=None):
    """Format the Uber booking message with deep link."""
    dest_name = request.get("destination_name", "your destination")
    dest_lat = request.get("destination_lat")
    dest_lng = request.get("destination_lng")
    dest_line1 = request.get("destination_line1", dest_name)
    dest_line2 = request.get("destination_line2", "")
    service = request.get("service", "uberx")
    service_label = request.get("service_label", "UberX")
    account = request.get("account", "personal")
    hours_before = request.get("hours_before", 2)

    msg = f"🚗 *{service_label} to {dest_name}*\n\n"

    # Flight context
    if flight and request.get("for_airport"):
        try:
            dep = datetime.fromisoformat(flight["departure_time"])
            dep_pt = dep.astimezone(PT)
            suggested_pickup = dep_pt - timedelta(hours=hours_before)
            msg += f"✈️ Flight departs: *{dep_pt.strftime('%I:%M %p PT')}*\n"
            msg += f"⏰ Suggested pickup: *{suggested_pickup.strftime('%I:%M %p PT')}*\n\n"
        except:
            pass

    # Business warning
    if account == "business":
        msg += "⚠️ *Switch to your business account in Uber before tapping*\n\n"

    # Generate link
    if dest_lat and dest_lng:
        link = build_uber_link(dest_name, dest_lat, dest_lng, dest_line1, dest_line2, service)
        msg += f"👉 [Book {service_label}]({link})"
    else:
        msg += f"👉 [Open Uber](https://m.uber.com/)\n"
        msg += f"_Set destination to: {dest_name}_"

    return msg