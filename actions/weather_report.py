# actions/weather_report.py

import json
import urllib.request
import webbrowser
from urllib.parse import quote_plus
from typing import Dict, Any, Optional

_WEATHER_CODE_MAP = {
    0: "Clear",
    1: "Mainly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime Fog",
    51: "Light Drizzle",
    53: "Drizzle",
    55: "Dense Drizzle",
    61: "Slight Rain",
    63: "Moderate Rain",
    65: "Heavy Rain",
    71: "Slight Snow",
    73: "Moderate Snow",
    75: "Heavy Snow",
    77: "Snow Grains",
    80: "Rain Showers",
    81: "Moderate Showers",
    82: "Violent Showers",
    85: "Snow Showers",
    86: "Heavy Snow Showers",
    95: "Thunderstorm",
    96: "Thunderstorm with Hail",
    99: "Thunderstorm with Heavy Hail",
}


def _fetch_open_meteo(
    city: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Secondary weather provider via Open-Meteo API (free, fast, keyless fallback)."""
    detected_city = city or "Local Area"
    try:
        if lat is None or lon is None:
            if not city:
                return None
            geo_url = (
                f"https://geocoding-api.open-meteo.com/v1/search?name={quote_plus(city)}"
                "&count=1&language=en&format=json"
            )
            req = urllib.request.Request(geo_url, headers={"User-Agent": "BrahmaAI/1.0"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                gdata = json.loads(resp.read().decode("utf-8"))
                results = gdata.get("results")
                if not results:
                    return None
                res = results[0]
                lat = res.get("latitude")
                lon = res.get("longitude")
                detected_city = res.get("name") or city

        if lat is None or lon is None:
            return None

        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}"
            "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,apparent_temperature"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "BrahmaAI/1.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            current = data.get("current", {})
            temp_c = int(round(current.get("temperature_2m", 20)))
            code = current.get("weather_code", 0)
            desc = _WEATHER_CODE_MAP.get(code, "Clear")
            humidity = f"{current.get('relative_humidity_2m', 60)}%"
            wind = f"{current.get('wind_speed_10m', 10)} km/h"
            feels_like = int(round(current.get("apparent_temperature", temp_c)))

            return {
                "status": "success",
                "city": detected_city,
                "temp_c": temp_c,
                "condition": desc,
                "humidity": humidity,
                "wind": wind,
                "feels_like": feels_like,
                "summary": f"{temp_c}°C, {desc} in {detected_city}",
            }
    except Exception as e:
        print(f"[Weather] Open-Meteo fallback notice: {e}")
        return None


def get_live_weather(city: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetches real-time live weather data using wttr.in with Open-Meteo fallback.
    Auto-detects device physical location dynamically via hardware/Wi-Fi and IP when no city is passed.
    Returns structured metrics: city, temp_c, condition, humidity, wind, and summary.
    """
    target_city = city.strip() if city and city.strip() else None
    if target_city and target_city.lower() in ("here", "my location", "current location", "auto", "local", "device"):
        target_city = None

    detected_lat = None
    detected_lon = None

    # Dynamically auto-detect physical device location
    if not target_city:
        try:
            from core.device_location import get_device_location
            loc = get_device_location()
            target_city = loc.get("city")
            detected_lat = loc.get("latitude")
            detected_lon = loc.get("longitude")
        except Exception as e:
            print(f"[Weather] Device location auto-detect notice: {e}")
            target_city = None

    if detected_lat is not None and detected_lon is not None:
        url = f"https://wttr.in/{detected_lat:.4f},{detected_lon:.4f}?format=j1"
    elif target_city:
        encoded_city = quote_plus(target_city)
        url = f"https://wttr.in/{encoded_city}?format=j1"
    else:
        url = "https://wttr.in/?format=j1"

    fallback = {
        "status": "unavailable",
        "city": target_city or "Local Area",
        "temp_c": 0,
        "condition": "Unknown",
        "humidity": "N/A",
        "wind": "N/A",
        "feels_like": 0,
        "summary": "Weather telemetry temporarily offline.",
    }

    # Primary attempt: wttr.in
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "curl/7.68.0"}
        )
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            current = data.get("current_condition", [{}])[0]
            nearest = data.get("nearest_area", [{}])[0]

            detected_city = target_city
            if not detected_city:
                area_names = nearest.get("areaName", [{}])
                detected_city = area_names[0].get("value") if area_names else "Current Location"

            temp_c = int(current.get("temp_C", 20))
            desc = current.get("weatherDesc", [{}])[0].get("value", "Clear")
            humidity = f"{current.get('humidity', '60')}%"
            wind_speed = f"{current.get('windspeedKmph', '10')} km/h"
            feels_like = int(current.get("FeelsLikeC", temp_c))

            return {
                "status": "success",
                "city": detected_city,
                "temp_c": temp_c,
                "condition": desc,
                "humidity": humidity,
                "wind": wind_speed,
                "feels_like": feels_like,
                "summary": f"{temp_c}°C, {desc} in {detected_city}",
            }
    except Exception as e:
        print(f"[Weather] Live weather fetch notice (wttr.in): {e}")

    # Secondary attempt: Open-Meteo fallback
    om_res = _fetch_open_meteo(target_city, detected_lat, detected_lon)
    if om_res:
        return om_res

    return fallback


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None
):
    """
    Weather report action.
    Fetches real-time live weather metrics and optionally opens Google Weather.
    """
    city = parameters.get("city") if parameters else None
    time_param = parameters.get("time", "today") if parameters else "today"

    weather = get_live_weather(city)
    city_name = weather.get("city", "your area")

    if weather.get("status") == "unavailable":
        msg = f"I was unable to retrieve live weather telemetry for {city_name} right now. Please try again shortly."
        _speak_and_log(msg, player)
        return msg

    temp = weather.get("temp_c", 20)
    cond = weather.get("condition", "Clear")

    msg = f"The weather in {city_name} is currently {temp} degrees Celsius with {cond}."
    _speak_and_log(msg, player)

    # Optional browser fallback if user explicitly asks or city was specified
    search_query = f"weather in {city_name} {time_param}"
    if parameters and parameters.get("open_browser", False):
        try:
            encoded_query = quote_plus(search_query)
            webbrowser.open(f"https://www.google.com/search?q={encoded_query}")
        except Exception:
            pass

    if session_memory:
        try:
            session_memory.set_last_search(query=search_query, response=msg)
        except Exception:
            pass

    return msg


def _speak_and_log(message: str, player=None):
    if player:
        try:
            player.write_log(f"Brahma AI: {message}")
        except Exception:
            pass
