"""
Push environmental data to Awtrix 3 as custom apps over MQTT:
  aqi      - US Air Quality Index (Open-Meteo, no key)
  pollen   - allergy index 0-12 for the ZIP (pollen.com unofficial; fails soft)
  uv       - current UV index (Open-Meteo, no key)
  sun      - next sun event (sunrise or sunset) with time (Open-Meteo)
  noon     - solar noon time (midpoint of sunrise/sunset, Open-Meteo)
  daylen   - day length in hours and minutes (Open-Meteo)
  compass  - sun azimuth as compass bearing, e.g. ENE (computed locally)
  elev     - sun elevation as day-progress %, 0 at horizon, 100 at peak (local)
  moon     - illumination % and phase, computed locally (no API)

Each value is color-coded by severity so it's glanceable. Designed to run
hourly from cron. Any single source failing only drops its own app; the
others still publish, and retained messages keep the last good value.
"""
import json
import math
import os
from datetime import datetime, timedelta, timezone

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
IC_MERCURY, IC_MERCURY_RX = "mercury", "mercury_rx"
IC_NOON, IC_DAYLEN = "solar_noon", "daylight"
IC_COMPASS, IC_ELEV = "compass", "elevation"
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
            "noScroll": True, "duration": DURATION, "lifetime": LIFETIME}


def get_sun_apps():
    """One call returns UV, next sun event, solar noon, and day length."""
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": LAT, "longitude": LON, "current": "uv_index",
                "daily": "sunrise,sunset", "forecast_days": 2,
                "timezone": "auto"}, timeout=10)
    r.raise_for_status()
    d = r.json()

    apps = {}

    uv = d.get("current", {}).get("uv_index")
    if uv is not None:
        uv = max(0, round(uv))
        apps["uv"] = {"text": f"UV{uv}", "icon": IC_UV, "color": uv_color(uv),
                       "noScroll": True, "duration": DURATION, "lifetime": LIFETIME}

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
        apps["sun"] = {"text": label, "icon": icon, "color": WHITE,
                       "noScroll": True, "duration": DURATION, "lifetime": LIFETIME}

        noon = sr0 + (ss0 - sr0) / 2
        apps["noon"] = {"text": hhmm(noon), "icon": IC_NOON,
                        "color": [255, 250, 200],
                        "noScroll": True, "duration": DURATION, "lifetime": LIFETIME}

        daylen = ss0 - sr0
        hours = int(daylen.total_seconds() // 3600)
        minutes = int((daylen.total_seconds() % 3600) // 60)
        apps["daylen"] = {"text": f"{hours}h{minutes:02d}m", "icon": IC_DAYLEN,
                          "color": [255, 220, 100],
                          "noScroll": True, "duration": DURATION, "lifetime": LIFETIME}

    return apps


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
            "noScroll": True, "duration": DURATION, "lifetime": LIFETIME}


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
            "noScroll": True, "duration": DURATION, "lifetime": LIFETIME}


# --- Mercury retrograde -----------------------------------------------------
# Low-precision Keplerian orbital elements (Paul Schlyter's formulas), good to
# roughly a degree for decades around J2000 -- plenty for spotting retrograde
# stations, which only need the *sign* of Mercury's apparent motion.
_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
_SUN_ELEMENTS = dict(N=0.0, i=0.0, w=282.9404, a=1.000000,
                      e=0.016709, M0=356.0470, M_rate=0.9856002585)
_MERCURY_ELEMENTS = dict(N=48.3313, i=7.0047, w=29.1241, a=0.387098,
                          e=0.205635, M0=168.6562, M_rate=4.0923344368)


def _kepler_E(M_rad, e):
    """Solve Kepler's equation M = E - e*sin(E) for E, in radians."""
    E = M_rad + e * math.sin(M_rad) * (1.0 + e * math.cos(M_rad))
    for _ in range(8):
        dE = (E - e * math.sin(E) - M_rad) / (1 - e * math.cos(E))
        E -= dE
        if abs(dE) < 1e-9:
            break
    return E


def _heliocentric_xy(d, N, i, w, a, e, M0, M_rate):
    """Heliocentric ecliptic x,y (AU) at d days since epoch. z is dropped --
    it only affects ecliptic latitude, not the longitude used here."""
    M = math.radians((M0 + M_rate * d) % 360.0)
    E = _kepler_E(M, e)
    xv = a * (math.cos(E) - e)
    yv = a * math.sqrt(1 - e * e) * math.sin(E)
    v, r = math.atan2(yv, xv), math.hypot(xv, yv)
    N, i, w = math.radians(N), math.radians(i), math.radians(w)
    vw = v + w
    x = r * (math.cos(N) * math.cos(vw) - math.sin(N) * math.sin(vw) * math.cos(i))
    y = r * (math.sin(N) * math.cos(vw) + math.cos(N) * math.sin(vw) * math.cos(i))
    return x, y


def _mercury_longitude(d):
    """Mercury's geocentric ecliptic longitude (degrees) at d days since epoch."""
    xs, ys = _heliocentric_xy(d, **_SUN_ELEMENTS)       # Sun as seen from Earth
    xm, ym = _heliocentric_xy(d, **_MERCURY_ELEMENTS)   # Mercury, heliocentric
    return math.degrees(math.atan2(ym + ys, xm + xs)) % 360.0


def _mercury_retrograde(d):
    """True if Mercury's geocentric longitude is currently decreasing."""
    delta = _mercury_longitude(d + 0.5) - _mercury_longitude(d - 0.5)
    return ((delta + 180) % 360) - 180 < 0


def get_mercury():
    """Mercury retrograde status, plus days until the next station (when the
    direction flips). Computed locally from orbital elements -- no API,
    same spirit as get_moon()."""
    d = (datetime.now(timezone.utc) - _EPOCH).total_seconds() / 86400.0
    retro = _mercury_retrograde(d)
    days = next((n for n in range(1, 130)
                  if _mercury_retrograde(d + n) != retro), None)
    text = f"{'R' if retro else 'D'}{days}d" if days else ("RETRO" if retro else "DIRECT")
    icon = IC_MERCURY_RX if retro else IC_MERCURY
    return {"text": text, "icon": icon, "color": RED if retro else GREEN,
            "noScroll": True, "duration": DURATION, "lifetime": LIFETIME}


# --- Sun position (local computation) ----------------------------------------
_COMPASS_POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                   "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _solar_position(lat, lon):
    """Sun azimuth (0=N clockwise), elevation, and peak elevation for today.
    Low-precision NOAA/Meeus formulas, good to ~1 degree. No network."""
    now = datetime.now(timezone.utc)
    n = (now - datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc)).total_seconds() / 86400.0

    L = (280.460 + 0.9856474 * n) % 360.0
    g_rad = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecl_lon = math.radians(L + 1.915 * math.sin(g_rad) + 0.020 * math.sin(2 * g_rad))

    obliq = math.radians(23.439 - 0.0000004 * n)
    dec = math.asin(math.sin(obliq) * math.sin(ecl_lon))
    ra = math.atan2(math.cos(obliq) * math.sin(ecl_lon), math.cos(ecl_lon))

    gmst = math.radians((280.46061837 + 360.98564736629 * n) % 360.0)
    ha = gmst + math.radians(lon) - ra

    lat_r = math.radians(lat)
    elev = math.degrees(math.asin(
        math.sin(lat_r) * math.sin(dec)
        + math.cos(lat_r) * math.cos(dec) * math.cos(ha)))
    az = math.degrees(math.atan2(
        -math.sin(ha),
        math.cos(lat_r) * math.tan(dec) - math.sin(lat_r) * math.cos(ha))) % 360.0
    peak = max(0.0, 90.0 - abs(lat - math.degrees(dec)))

    return az, elev, peak


def _elevation_color(pct):
    if pct < 25:
        return [255, 140, 50]
    if pct < 50:
        return [255, 200, 50]
    if pct < 75:
        return [255, 240, 100]
    return [255, 255, 200]


def get_sun_position():
    """Compass bearing and elevation progress. No network."""
    az, elev, peak = _solar_position(LAT, LON)
    if elev < 0:
        return {}
    compass = _COMPASS_POINTS[int((az + 11.25) % 360 / 22.5) % 16]
    pct = int(round(elev / peak * 100)) if peak > 0 else 0
    pct = min(pct, 100)
    return {
        "compass": {"text": compass, "icon": IC_COMPASS, "color": WHITE,
                    "noScroll": True, "duration": DURATION, "lifetime": LIFETIME},
        "elev": {"text": f"{pct}%", "icon": IC_ELEV, "color": _elevation_color(pct),
                 "noScroll": True, "duration": DURATION, "lifetime": LIFETIME},
    }


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
    mercury_app = safe(get_mercury, "Mercury")
    sun_apps = safe(get_sun_apps, "Sun apps") or {}
    pos_apps = safe(get_sun_position, "Sun position") or {}

    publish({
        "aqi": aqi_app,
        "pollen": pollen_app,
        "uv": sun_apps.get("uv"),
        "sun": sun_apps.get("sun"),
        "noon": sun_apps.get("noon"),
        "daylen": sun_apps.get("daylen"),
        **pos_apps,
        "moon": moon_app,
        "mercury": mercury_app,
    })
