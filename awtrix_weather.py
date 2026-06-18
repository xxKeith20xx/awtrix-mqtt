import json
import os
import re
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

# Configuration
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = os.environ.get("MQTT_USER", "awtrix_clock")
MQTT_PASS = os.environ.get("MQTT_PASS", "mqtt00!!")
PREFIX = "awtrix_72153c"
TOPIC_TEMP = f"{PREFIX}/custom/weather_temp"
TOPIC_HUM = f"{PREFIX}/custom/weather_hum"
TOPIC_FEELS = f"{PREFIX}/custom/weather_feels"
TOPIC_DEW = f"{PREFIX}/custom/weather_dew"
WEATHER_URL = "https://api.weather.gov/gridpoints/EWX/155,90/forecast/hourly"
NWS_CONTACT = os.environ.get("NWS_CONTACT", "your_email@example.com")
HEADERS = {'User-Agent': f'(myhomeclock, {NWS_CONTACT})'}

# Device HTTP address (NOT the MQTT broker) for the icon-presence check.
DEVICE_IP = "192.168.0.239"  # clock's IP

# --- Icon references ------------------------------------------------------
# These are LOCAL filenames in the device /ICONS folder (no extension), not
# LaMetric IDs. Upload sun.gif, cloud.gif, rain.gif, storm.gif, snow.gif,
# fog.gif, humidity.gif into /ICONS via the web Files browser. No LaMetric
# dependency, no ID lookups — fully self-contained.
ICON_CLEAR = "sun"
ICON_CLOUD = "cloud"
ICON_RAIN = "rain"
ICON_STORM = "storm"
ICON_SNOW = "snow"
ICON_FOG = "fog"
ICON_HUMIDITY = "humidity"
ICON_FEELS = "feels"
ICON_DEW = "dewpoint"
ICON_WIND = "wind"
ICON_RAINPROB = "umbrella"
ICON_FALLBACK = ICON_CLEAR  # used only for genuinely unrecognized conditions

# Condition -> ordered icon candidates. Rules are checked top to bottom and the
# FIRST keyword match wins, so they're ordered by severity/specificity. Within a
# match, the first non-empty candidate is used (so winter precip falls back to
# rain, not sun, if no dedicated snow icon is loaded). Keywords are matched as
# substrings of the lowercased NWS shortForecast.
CONDITION_RULES = [
    (("thunder", "storm", "tstm"), [ICON_STORM, ICON_RAIN]),
    # Winter/ice checked BEFORE rain so "freezing rain" and "rain and snow"
    # resolve to ice/snow rather than plain rain.
    (("snow", "sleet", "freezing", "wintry", "flurries", "ice", "blizzard",
      "hail", "graupel", "pellets", "frost"),
     [ICON_SNOW, ICON_RAIN, ICON_CLOUD]),
    (("rain", "shower", "drizzle"), [ICON_RAIN]),
    (("fog", "haze", "mist", "smoke"), [ICON_FOG, ICON_CLOUD]),
    (("cloud", "overcast"), [ICON_CLOUD]),
    (("clear", "sunny", "fair"), [ICON_CLEAR]),
]


def icon_for_condition(condition):
    text = (condition or "").lower()
    for keywords, candidates in CONDITION_RULES:
        if any(k in text for k in keywords):
            return next((c for c in candidates if c), ICON_FALLBACK)
    return ICON_FALLBACK


# Every icon the script can actually emit — auto-derived so the presence check
# never drifts out of sync with the mapping above.
REQUIRED_ICONS = {
    icon for icon in (
        ICON_CLEAR, ICON_CLOUD, ICON_RAIN, ICON_STORM,
        ICON_SNOW, ICON_FOG, ICON_HUMIDITY, ICON_FEELS,
        ICON_DEW, ICON_WIND, ICON_RAINPROB, ICON_FALLBACK,
    ) if icon
}


GREEN = [0, 200, 80]
YELLOW = [235, 205, 0]
ORANGE = [255, 140, 0]
RED = [235, 40, 40]
PURPLE = [160, 70, 220]


def feels_color(f):
    return (GREEN if f < 80 else YELLOW if f < 90 else ORANGE if f < 100
            else RED if f < 110 else PURPLE)


def dewpoint_color(f):
    return (GREEN if f < 55 else YELLOW if f < 60 else ORANGE if f < 65
            else RED if f < 70 else PURPLE)


def wind_color(mph):
    return (GREEN if mph < 10 else YELLOW if mph < 20 else ORANGE if mph < 30
            else RED)


def rain_color(pct):
    return (GREEN if pct < 20 else YELLOW if pct < 40 else ORANGE if pct < 60
            else RED if pct < 80 else PURPLE)


def _wind_mph(wind_str):
    """Parse NWS windSpeed (e.g. '5 mph' or '5 to 10 mph') to an int. Ranges
    take the higher number, so wind chill / display reflect the gustier end."""
    nums = re.findall(r'\d+', wind_str or '')
    return int(nums[-1]) if nums else 0


def _feels_like(temp_f, rh, wind_str):
    """Heat index (hot+humid) or wind chill (cold+windy), else actual temp."""
    if temp_f >= 80 and rh >= 40:
        hi = 0.5 * (temp_f + 61.0 + (temp_f - 68) * 1.2 + rh * 0.094)
        if hi >= 80:
            hi = (-42.379 + 2.04901523*temp_f + 10.14333127*rh
                  - 0.22475541*temp_f*rh - 0.00683783*temp_f**2
                  - 0.05481717*rh**2 + 0.00122874*temp_f**2*rh
                  + 0.00085282*temp_f*rh**2 - 0.00000199*temp_f**2*rh**2)
        return round(hi)
    wind = _wind_mph(wind_str)
    if temp_f <= 50 and wind > 3:
        return round(35.74 + 0.6215*temp_f - 35.75*wind**0.16 + 0.4275*temp_f*wind**0.16)
    return temp_f


def get_weather():
    try:
        response = requests.get(WEATHER_URL, headers=HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()

        properties = data.get('properties', {})
        periods = properties.get('periods', [])
        if not periods:
            return {}

        cur = periods[0]
        apps = {}

        temp_val = cur.get('temperature')
        condition = cur.get('shortForecast', '')
        rh = cur.get('relativeHumidity', {}).get('value')

        if temp_val is not None:
            apps["weather_temp"] = {
                "text": f"{temp_val}°", "icon": icon_for_condition(condition),
                "color": [255, 255, 255], "noScroll": True, "pos": 0,
                "duration": 3, "lifetime": 1200}

        if temp_val is not None and rh is not None:
            feels = _feels_like(temp_val, rh, cur.get('windSpeed', ''))
            apps["weather_feels"] = {
                "text": f"{feels}°", "icon": ICON_FEELS,
                "color": feels_color(feels), "noScroll": True, "pos": 1,
                "duration": 3, "lifetime": 1200}

        if rh is not None:
            apps["weather_hum"] = {
                "text": f"{rh}%", "icon": ICON_HUMIDITY,
                "color": [255, 255, 255], "noScroll": True, "pos": 2,
                "duration": 3, "lifetime": 1200}

        dp = cur.get('dewpoint', {}).get('value')
        if dp is not None:
            dp_f = round(dp * 9 / 5 + 32)
            apps["weather_dew"] = {
                "text": f"{dp_f}°", "icon": ICON_DEW,
                "color": dewpoint_color(dp_f), "noScroll": True, "pos": 3,
                "duration": 3, "lifetime": 1200}

        wind = _wind_mph(cur.get('windSpeed', ''))
        wind_dir = cur.get('windDirection', '')
        apps["weather_wind"] = {
            "text": f"{wind_dir}{wind}", "icon": ICON_WIND,
            "color": wind_color(wind), "noScroll": True, "pos": 4,
            "duration": 3, "lifetime": 1200}

        pop = cur.get('probabilityOfPrecipitation', {}).get('value')
        if pop is not None:
            apps["weather_rain"] = {
                "text": f"{round(pop)}%", "icon": ICON_RAINPROB,
                "color": rain_color(pop), "noScroll": True, "pos": 5,
                "duration": 3, "lifetime": 1200}

        return apps
    except Exception as e:
        print(f"Error fetching weather data: {e}")
        return {}


def publish_weather(apps):
    apps = {k: v for k, v in apps.items() if v}
    if not apps:
        print("No weather data to publish.")
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


def check_icons():
    """Warn if any required icon file is missing from the device ICONS folder.

    Queries the firmware's file-list endpoint. The exact path is version-
    dependent; /list?dir=/ICONS is the common convention but some builds
    differ, so failures here are reported, not fatal — they never block the
    weather push.
    """
    if "x" in DEVICE_IP:
        print("Icon check skipped: set DEVICE_IP to the clock's address.")
        return
    try:
        r = requests.get(f"http://{DEVICE_IP}/list",
                         params={"dir": "/ICONS"}, timeout=5)
        r.raise_for_status()
        # Expected: list of {"name": "...", "type": "file"}; strip extensions.
        names = {entry.get("name", "").rsplit(".", 1)[0].lstrip("/")
                 for entry in r.json()}
        names = {n.split("/")[-1] for n in names}
        missing = REQUIRED_ICONS - names
        if missing:
            print(f"WARNING: icons missing from device ICONS folder: "
                  f"{sorted(missing)}")
        else:
            print("Icon check OK: all required icons present.")
    except Exception as e:
        print(f"Icon check could not verify device (non-fatal): {e}")


if __name__ == "__main__":
    check_icons()
    apps = get_weather()
    publish_weather(apps)
