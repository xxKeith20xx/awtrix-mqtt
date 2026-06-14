"""
Push environmental data to Awtrix 3 as custom apps over MQTT:
  aqi    - US Air Quality Index (Open-Meteo, no key)
  pollen - allergy index 0-12 for the ZIP (pollen.com unofficial; fails soft)
  uv     - current UV index (Open-Meteo, no key)
  sun    - next sun event (sunrise or sunset) with time (Open-Meteo)
  moon   - illumination % and phase, computed locally (no API)

Each value is color-coded by severity so it's glanceable. Designed to run
hourly from cron. Any single source failing only drops its own app; the
others still publish, and retained messages keep the last good value.
"""
import json
import math
import os
from datetime import datetime, timezone

import requests
import paho.mqtt.client as mqtt


def load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        os.environ[parts[0].strip()] = parts[1].strip().strip('"').strip("'")


load_env()

# --- Config ---------------------------------------------------------------
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = os.environ.get("MQTT_USER", "awtrix_clock")
MQTT_PASS = os.environ.get("MQTT_PASS", "mqtt00!!")
PREFIX = "awtrix_72153c"          # MQTT topic prefix (matches the weather script)

LAT = float(os.environ.get("LAT", 30.26))
LON = float(os.environ.get("LON", -97.71))
ZIP_CODE = os.environ.get("ZIP_CODE", "78702")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Google API Key for Pollen API (falls back to environment variable)
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "your_google_api_key_here")

# Icon filenames in the device /ICONS folder.
IC_AQI, IC_POLLEN, IC_UV = "aqi", "pollen", "sun"
IC_SUNRISE, IC_SUNSET = "sunrise", "sunset"
# Moon phase icons, ordered new -> ... -> full -> ... (one eighth each).
MOON_ICONS = ["moon_new", "moon_wxc", "moon_fq", "moon_wxg",
              "moon_full", "moon_wng", "moon_lq", "moon_wnc"]

LIFETIME = 4500  # ~75 min; expires if the hourly job stops (cron is hourly)
DURATION = 3     # seconds each app shows (override of the 10s global app time)


# --- Color helpers (return [r,g,b]) ---------------------------------------
GREEN = [0, 200, 80]
YELLOW = [235, 205, 0]
ORANGE = [255, 140, 0]
RED = [235, 40, 40]
PURPLE = [160, 70, 220]
WHITE = [255, 255, 255]


def aqi_color(v):
    return (GREEN if v <= 50 else YELLOW if v <= 100 else ORANGE if v <= 150
            else RED if v <= 200 else PURPLE)


def uv_color(v):
    return (GREEN if v < 3 else YELLOW if v < 6 else ORANGE if v < 8
            else RED if v < 11 else PURPLE)


def pollen_color(v):
    # Google Universal Pollen Index, 0-5
    return (GREEN if v <= 1.0 else YELLOW if v <= 2.0 else ORANGE if v <= 3.0
            else RED if v <= 4.0 else PURPLE)


# --- Data sources ---------------------------------------------------------
def get_aqi():
    r = requests.get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        params={"latitude": LAT, "longitude": LON,
                "current": "us_aqi", "timezone": "auto"}, timeout=10)
    r.raise_for_status()
    v = r.json().get("current", {}).get("us_aqi")
    if v is None:
        return None
    v = int(round(v))
    return {"text": str(v), "icon": IC_AQI, "color": aqi_color(v),
            "scroll": False, "duration": DURATION, "lifetime": LIFETIME}


def get_uv_and_sun():
    """One call returns both UV (current) and today's/tomorrow's sun times."""
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": LAT, "longitude": LON, "current": "uv_index",
                "daily": "sunrise,sunset", "forecast_days": 2,
                "timezone": "auto"}, timeout=10)
    r.raise_for_status()
    d = r.json()

    uv_app = None
    uv = d.get("current", {}).get("uv_index")
    if uv is not None:
        uv = max(0, round(uv))
        uv_app = {"text": f"UV{uv}", "icon": IC_UV, "color": uv_color(uv),
                  "scroll": False, "duration": DURATION, "lifetime": LIFETIME}

    sun_app = None
    daily = d.get("daily", {})
    rises, sets = daily.get("sunrise", []), daily.get("sunset", [])
    if rises and sets:
        now = datetime.now().astimezone()

        def parse(s):
            return datetime.fromisoformat(s).replace(tzinfo=now.tzinfo)

        def hhmm(dt):
            return dt.strftime("%-I:%M")  # 6:29, no leading zero

        sr0, ss0 = parse(rises[0]), parse(sets[0])
        if now < sr0:                      # before today's sunrise
            label, icon = hhmm(sr0), IC_SUNRISE
        elif now < ss0:                    # daytime -> next is sunset
            label, icon = hhmm(ss0), IC_SUNSET
        elif len(rises) > 1:               # after sunset -> tomorrow's sunrise
            label, icon = hhmm(parse(rises[1])), IC_SUNRISE
        else:
            label, icon = hhmm(sr0), IC_SUNRISE
        sun_app = {"text": label, "icon": icon, "color": WHITE,
                   "scroll": False, "duration": DURATION, "lifetime": LIFETIME}

    return uv_app, sun_app


def get_pollen():
    """Google Pollen API forecast. Returns None on any problem (fails soft)."""
    if not GOOGLE_API_KEY or GOOGLE_API_KEY == "your_google_api_key_here":
        print("Google API Key not configured, skipping Pollen app.")
        return None
    url = "https://pollen.googleapis.com/v1/forecast:lookup"
    params = {
        "key": GOOGLE_API_KEY,
        "location.latitude": LAT,
        "location.longitude": LON,
        "days": 1,
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    daily_info = r.json().get("dailyInfo", [])
    if not daily_info:
        return None
    # Google returns indices for TREE, GRASS, and WEED. Take the maximum.
    pollen_types = daily_info[0].get("pollenTypeInfo", [])
    max_val = 0.0
    for p_type in pollen_types:
        idx_info = p_type.get("indexInfo", {})
        if idx_info:
            val = idx_info.get("value")
            if val is not None:
                max_val = max(max_val, float(val))
    return {"text": f"{max_val:.1f}", "icon": IC_POLLEN, "color": pollen_color(max_val),
            "scroll": False, "duration": DURATION, "lifetime": LIFETIME}


def get_moon():
    """Illumination %, phase, and the matching phase icon. No network."""
    # Reference new moon: 2000-01-06 18:14 UTC; synodic month length.
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    syn = 29.530588853
    days = (datetime.now(timezone.utc) - ref).total_seconds() / 86400.0
    phase = (days % syn) / syn                      # 0=new .. 0.5=full
    illum = round((1 - math.cos(2 * math.pi * phase)) * 50)  # 0..100 %
    # Map phase to one of 8 equal buckets centered on new/quarter/full/etc.
    icon = MOON_ICONS[int(((phase + 0.0625) % 1.0) * 8) % 8]
    return {"text": f"{illum}%", "icon": icon, "color": [200, 200, 170],
            "scroll": False, "duration": DURATION, "lifetime": LIFETIME}


# --- MQTT publish ---------------------------------------------------------
def publish(apps):
    """apps: dict of app_name -> payload dict (None values skipped)."""
    apps = {k: v for k, v in apps.items() if v}
    if not apps:
        print("Nothing to publish (all sources failed).")
        return
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set(MQTT_USER, MQTT_PASS)
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        infos = []
        for name, payload in apps.items():
            topic = f"{PREFIX}/custom/{name}"
            infos.append(client.publish(topic, json.dumps(payload),
                                        qos=1, retain=True))
        for info in infos:
            info.wait_for_publish()
        client.loop_stop()
        client.disconnect()
        print("Published:", ", ".join(apps))
    except Exception as e:
        print(f"MQTT Delivery failed: {e}")


def safe(fn, label):
    try:
        return fn()
    except Exception as e:
        print(f"{label} skipped (non-fatal): {e}")
        return None


if __name__ == "__main__":
    aqi_app = safe(get_aqi, "AQI")
    pollen_app = safe(get_pollen, "Pollen")
    moon_app = safe(get_moon, "Moon")
    uv_app, sun_app = safe(get_uv_and_sun, "UV/Sun") or (None, None)

    publish({
        "aqi": aqi_app,
        "pollen": pollen_app,
        "uv": uv_app,
        "sun": sun_app,
        "moon": moon_app,
    })
