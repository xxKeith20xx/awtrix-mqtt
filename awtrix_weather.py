import json
import os
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
TOPIC_TEMP = "awtrix_72153c/custom/weather_temp"
TOPIC_HUM = "awtrix_72153c/custom/weather_hum"
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
        ICON_SNOW, ICON_FOG, ICON_HUMIDITY, ICON_FALLBACK,
    ) if icon
}


def get_weather():
    try:
        # 10-second timeout prevents the script from hanging forever if NWS is slow
        response = requests.get(WEATHER_URL, headers=HEADERS, timeout=10)
        response.raise_for_status()  # Instantly catches 4xx/5xx server errors
        data = response.json()

        # Safe extraction guarding against schema changes or empty arrays
        properties = data.get('properties', {})
        periods = properties.get('periods', [])
        if not periods:
            return None, None, None, None

        current_period = periods[0]
        temp = f"{current_period.get('temperature', '--')}°"

        # NWS can return relativeHumidity.value as explicit null; .get(k, 0)
        # only defaults on a MISSING key, not None, so handle None explicitly
        rh = current_period.get('relativeHumidity', {}).get('value')
        humidity = f"{rh if rh is not None else 0}%"

        condition = current_period.get('shortForecast', '')
        weather_icon = icon_for_condition(condition)
        humidity_icon = ICON_HUMIDITY
        return temp, weather_icon, humidity, humidity_icon
    except Exception as e:
        print(f"Error fetching weather data: {e}")
        return None, None, None, None


def publish_weather_data(temp_text, weather_icon, hum_text, hum_icon):
    # lifetime: 1200 auto-expires the data on the clock if updates halt for 20 mins
    payload_temp = {
        "text": temp_text,
        "icon": weather_icon,
        "color": [255, 255, 255],
        "scroll": False,
        "duration": 5,
        "lifetime": 1200
    }

    payload_hum = {
        "text": hum_text,
        "icon": hum_icon,
        "color": [255, 255, 255],
        "scroll": False,
        "duration": 5,
        "lifetime": 1200
    }

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set(MQTT_USER, MQTT_PASS)
        client.connect(MQTT_BROKER, MQTT_PORT, 60)

        # Run the network loop so CONNACK/PUBLISH actually flush before teardown.
        # qos=1 guarantees delivery; retain=True replays last value on Awtrix reboot.
        client.loop_start()
        info_temp = client.publish(TOPIC_TEMP, json.dumps(payload_temp), qos=1, retain=True)
        info_hum = client.publish(TOPIC_HUM, json.dumps(payload_hum), qos=1, retain=True)

        # Block until both PUBLISH packets are confirmed sent
        info_temp.wait_for_publish()
        info_hum.wait_for_publish()

        client.loop_stop()
        client.disconnect()
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
    temp_text, weather_icon, hum_text, hum_icon = get_weather()
    if temp_text and weather_icon and hum_text and hum_icon:
        publish_weather_data(temp_text, weather_icon, hum_text, hum_icon)
