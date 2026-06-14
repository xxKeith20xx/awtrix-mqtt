# Awtrix MQTT Clock Integrations

This repository contains a collection of Python scripts that publish local environmental, weather, and Pomodoro timer data to an **Awtrix 3 clock** (such as the Ulanzi TC001) over a local MQTT broker.

All display logic is handled host-side; the clock simply acts as a display matrix showing the custom apps and notifications pushed to it.

---

## Features

1. **Awtrix Pomodoro (`awtrix_pomo_server.py`):**
   * Serves an always-on mobile-friendly web UI (port `8088`) to trigger focus/break timers.
   * Dynamically pushes live-ticking countdowns (MM:SS) with color-shifting progress bars (green $\rightarrow$ yellow $\rightarrow$ red).
   * Plays a gentle double-chirp chime (high-to-low RTTTL frequency) on the clock buzzer upon completion.
   * Runs as a user-level `systemd` service.

2. **NWS Hourly Weather (`awtrix_weather.py`):**
   * Queries the National Weather Service (NWS) API for hourly forecasts.
   * Displays local temperature and humidity.
   * Maps current conditions (storms, snow, rain, clouds, sun) to specific custom icons stored on the clock.
   * Scheduled via `cron`.

3. **Environmental Metrics (`awtrix_env.py`):**
   * **AQI:** Fetches the US Air Quality Index (Open-Meteo).
   * **UV Index:** Fetches current UV exposure levels (Open-Meteo).
   * **Sun Events:** Displays next sunrise or sunset time.
   * **Moon Phase:** Computes current moon illumination and phase locally, selecting one of 8 phase icons.
   * **Mercury Retrograde:** Computes Mercury's retrograde status locally from orbital elements (no API), showing `Rn d` (retrograde) or `Dn d` (direct) with days until the next station.
   * **Pollen:** Fetches allergy index using the official **Google Pollen API**.
   * Scheduled via `cron`.

---

## File Structure

```text
├── awtrix_env.py          # AQI, UV, Sun, Moon, Mercury retrograde, and Google Pollen script
├── awtrix_weather.py      # NWS Weather & Humidity script
├── awtrix_pomo_server.py  # Always-on Pomodoro HTTP/MQTT server
├── awtrix-pomo.service    # User systemd service file definition
├── make_icons.py          # Generates 8x8 GIF icons for /ICONS (pure stdlib)
├── .gitignore             # Git ignore configuration
└── .env                   # Secret configuration file (git-ignored)
```

---

## Configuration

Credentials and personal details are isolated into a `.env` file in the root of the project directory.

Create a `.env` file in the root of the project directory (`~/git/mqtt/.env`) with the following template:

```ini
# MQTT Broker Config
MQTT_USER=your_mqtt_user
MQTT_PASS=your_mqtt_password

# Location Config (used for UV, AQI, Moon, and Pollen)
LAT=your_latitude
LON=your_longitude
ZIP_CODE=your_zip_code

# Google Pollen API (Get key from Google Maps Platform)
GOOGLE_API_KEY=AIzaSyYourKeyHere...

# NWS API Guidelines require a contact email in the User-Agent header
NWS_CONTACT=your_email@example.com
```

---

## Deployment & Running

### 1. Cron Jobs (Hourly & Weather updates)
Configure `cron` to run the weather updates every 15 minutes and environmental updates hourly:

```bash
$ crontab -e
```

Add the following lines (adjust paths if necessary):
```cron
# Weather every 15 mins
*/15 * * * * /usr/bin/python3 /home/your_username/git/mqtt/awtrix_weather.py >> /home/your_username/awtrix_cron.log 2>&1

# Environment hourly
0 * * * * /usr/bin/python3 /home/your_username/git/mqtt/awtrix_env.py >> /home/your_username/awtrix_cron.log 2>&1
```

### 2. Pomodoro Server (Systemd User Service)
Link the service file to your systemd user configuration directory:

```bash
$ mkdir -p ~/.config/systemd/user/
$ ln -s ~/git/mqtt/awtrix-pomo.service ~/.config/systemd/user/awtrix-pomo.service
```

Start and enable the service so it runs automatically on boot:
```bash
$ systemctl --user daemon-reload
$ systemctl --user enable --now awtrix-pomo.service
```

*Access the Pomodoro web interface locally at: `http://localhost:8088`.*

---

## Custom Icons

The scripts reference 8x8 GIF icons stored directly on the Awtrix 3 device (without extensions). Upload these icons via the Awtrix web portal file browser under `/ICONS`:

* **Weather:** `sun`, `cloud`, `rain`, `storm`, `snow`, `fog`, `humidity`.
* **Environment:** `aqi`, `pollen`, `sun` (used for UV), `sunrise`, `sunset`, `mercury`.
* **Moon Phases:** `moon_new`, `moon_wxc`, `moon_fq`, `moon_wxg`, `moon_full`, `moon_wng`, `moon_lq`, `moon_wnc`.

All icons (including `mercury`) can be regenerated with `python3 make_icons.py`, which writes 8x8 GIFs into `icons/` using only the standard library (no Pillow needed).
