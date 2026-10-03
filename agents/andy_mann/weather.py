import os
import json
import requests
from datetime import datetime, timedelta

# ─── Location ────────────────────────────────────────────────────────────────

def get_lat_lon_from_zip(zip_code):
    """Convert zip code to lat/lon using Census Bureau API."""
    try:
        url = f"https://geocoding.geo.census.gov/geocoder/locations/address"
        params = {
            "street": "",
            "zip": zip_code,
            "benchmark": "2020",
            "format": "json"
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        coords = data["result"]["addressMatches"][0]["coordinates"]
        return coords["y"], coords["x"]  # lat, lon
    except:
        return None, None

def get_coords_from_zip(zip_code):
    """Fallback: use nominatim if census fails."""
    try:
        url = f"https://nominatim.openstreetmap.org/search"
        params = {
            "postalcode": zip_code,
            "country": "US",
            "format": "json"
        }
        headers = {"User-Agent": "AndyMannHomeAgent/1.0"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
        return None, None
    except:
        return None, None

def get_location(zip_code):
    """Get lat/lon from zip code, trying multiple sources."""
    lat, lon = get_lat_lon_from_zip(zip_code)
    if not lat:
        lat, lon = get_coords_from_zip(zip_code)
    return lat, lon

# ─── NWS Alerts ──────────────────────────────────────────────────────────────

def get_nws_alerts(lat, lon):
    """Get active NWS weather alerts for a location."""
    try:
        url = f"https://api.weather.gov/alerts/active"
        params = {"point": f"{lat},{lon}"}
        headers = {"User-Agent": "AndyMannHomeAgent/1.0 (contact@example.com)"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        alerts = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            alerts.append({
                "event": props.get("event", ""),
                "headline": props.get("headline", ""),
                "severity": props.get("severity", ""),
                "urgency": props.get("urgency", ""),
                "description": props.get("description", "")[:300],
                "expires": props.get("expires", "")
            })
        return alerts
    except Exception as e:
        print(f"NWS error: {e}")
        return []

def get_nws_forecast(lat, lon):
    """Get current temperature and conditions from NWS."""
    try:
        # First get the grid point
        points_url = f"https://api.weather.gov/points/{lat},{lon}"
        headers = {"User-Agent": "AndyMannHomeAgent/1.0"}
        resp = requests.get(points_url, headers=headers, timeout=10)
        data = resp.json()
        forecast_url = data["properties"]["forecastHourly"]

        # Get hourly forecast
        resp2 = requests.get(forecast_url, headers=headers, timeout=10)
        forecast = resp2.json()
        current = forecast["properties"]["periods"][0]
        return {
            "temperature": current.get("temperature"),
            "unit": current.get("temperatureUnit"),
            "wind_speed": current.get("windSpeed"),
            "short_forecast": current.get("shortForecast")
        }
    except Exception as e:
        print(f"NWS forecast error: {e}")
        return None

# ─── AirNow ───────────────────────────────────────────────────────────────────

def get_air_quality(zip_code):
    """Get current AQI from AirNow API."""
    try:
        api_key = os.getenv("AIRNOW_API_KEY")
        if not api_key:
            return None
        url = "https://www.airnowapi.org/aq/observation/zipCode/current/"
        params = {
            "format": "application/json",
            "zipCode": zip_code,
            "distance": 25,
            "API_KEY": api_key
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if not data:
            return None
        # Find the overall AQI (highest category)
        max_aqi = max(data, key=lambda x: x.get("AQI", 0))
        return {
            "aqi": max_aqi.get("AQI"),
            "category": max_aqi.get("Category", {}).get("Name", ""),
            "pollutant": max_aqi.get("ParameterName", "")
        }
    except Exception as e:
        print(f"AirNow error: {e}")
        return None

# ─── USGS Earthquake ──────────────────────────────────────────────────────────

def get_recent_earthquakes(lat, lon, min_magnitude=4.0, radius_km=100):
    """Get earthquakes near a location in the last 24 hours."""
    try:
        from datetime import timezone
        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        url = "https://earthquake.usgs.gov/fdsnws/event/1/query"
        params = {
            "format": "geojson",
            "starttime": start,
            "latitude": lat,
            "longitude": lon,
            "maxradiuskm": radius_km,
            "minmagnitude": min_magnitude,
            "orderby": "magnitude"
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        quakes = []
        for feature in data.get("features", []):
            props = feature["properties"]
            quakes.append({
                "magnitude": props.get("mag"),
                "place": props.get("place"),
                "time": datetime.fromtimestamp(
                    props.get("time", 0) / 1000
                ).strftime("%I:%M %p")
            })
        return quakes
    except Exception as e:
        print(f"USGS error: {e}")
        return []

# ─── Main Weather Check ───────────────────────────────────────────────────────

def get_weather_conditions(zip_code):
    """Get all weather conditions for a zip code."""
    print(f"Checking weather for zip: {zip_code}")
    lat, lon = get_location(zip_code)
    if not lat:
        print(f"Could not get coordinates for zip: {zip_code}")
        return None

    conditions = dict()
    conditions["zip_code"] = zip_code
    conditions["lat"] = lat
    conditions["lon"] = lon
    conditions["alerts"] = get_nws_alerts(lat, lon)
    conditions["forecast"] = get_nws_forecast(lat, lon)
    conditions["air_quality"] = get_air_quality(zip_code)
    conditions["earthquakes"] = get_recent_earthquakes(lat, lon)

    print(f"Weather check complete: {len(conditions['alerts'])} alerts, "
          f"AQI: {conditions['air_quality']}")
    return conditions