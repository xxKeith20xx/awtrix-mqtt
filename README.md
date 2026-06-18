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
   * **Solar Noon:** Midpoint of sunrise/sunset — when the sun is at its highest (Open-Meteo).
   * **Day Length:** Total daylight hours and minutes from sunrise to sunset (Open-Meteo).
   * **Compass:** Sun's current azimuth as a 16-point compass direction, e.g. ENE (computed locally).
   * **Elevation:** Sun's height as a day-progress percentage — 0% at horizon, 100% at peak. Low = light flooding through windows, high = overhead. Skips at night (computed locally).
   * **Moon Phase:** Computes current moon illumination and phase locally, selecting one of 8 phase icons.
   * **Mercury Retrograde:** Computes Mercury's retrograde status locally from orbital elements (no API), showing `Rn d` (retrograde) or `Dn d` (direct) with days until the next station.
   * **Pollen:** Fetches allergy index using the official **Google Pollen API**.
   * Scheduled via `cron`.

---

## App Quick Reference

All 12 apps rotate on the clock (2 seconds each, ~24 second full cycle). Colors shift by severity — green is calm, red/purple means pay attention.

### Weather Apps (every 15 min)

| App | Example | Meaning |
|-----|---------|---------|
| **temp** | `84°` | Current temperature from NWS, icon matches conditions (sun/cloud/rain/etc.) |
| **humidity** | `62%` | Current relative humidity |

### Environmental Apps (hourly)

| App | Example | Meaning |
|-----|---------|---------|
| **aqi** | `42` | Air quality index. Green ≤50, Yellow ≤100, Orange ≤150, Red ≤200, Purple 200+ |
| **pollen** | `2.0` | Worst of tree/grass/weed allergens on a 0–5 scale (Google Pollen API) |
| **uv** | `UV6` | Sunburn risk. Green <3, Yellow <6, Orange <8, Red <11, Purple 11+ |
| **sun** | `8:35` | Time of the *next* sunrise or sunset (icon tells you which) |
| **noon** | `1:32` | Solar noon — when the sun reaches its highest point today |
| **daylen** | `14h03m` | Total daylight today from sunrise to sunset |
| **compass** | `ENE` | Which compass direction the sun is in right now (16-point) |
| **elev** | `45%` | How high the sun is — 0% at horizon, 100% at peak. Low % = light floods windows. Skips at night |
| **moon** | `73%` | Moon illumination with a phase icon (new → full → new) |
| **mercury** | `D45d` | Mercury status: D = direct (normal), R = retrograde. Number = days until it switches |

### Data Sources

| Source | Apps | Needs API key? |
|--------|------|----------------|
| Open-Meteo | aqi, uv, sun, noon, daylen | No |
| NWS | temp, humidity | No (needs contact email) |
| Google Pollen API | pollen | Yes |
| Local computation | compass, elev, moon, mercury | No (pure math) |

---

## File Structure

```text
├── awtrix_env.py          # AQI, UV, Sun, Noon, Day Length, Compass, Elevation, Moon, Mercury, Pollen
├── awtrix_weather.py      # NWS Weather & Humidity script
├── awtrix_pomo_server.py  # Always-on Pomodoro HTTP/MQTT server
├── awtrix-pomo.service    # User systemd service file definition
├── make_icons.py          # Generates 8x8 GIF icons for /ICONS (pure stdlib)
├── icons/                 # Generated icon GIFs (upload to device /ICONS)
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
* **Environment:** `aqi`, `pollen`, `sun` (used for UV), `sunrise`, `sunset`.
* **Sun Position:** `solar_noon`, `daylight`, `compass`, `elevation`.
* **Mercury:** `mercury` (direct), `mercury_rx` (retrograde).
* **Moon Phases:** `moon_new`, `moon_wxc`, `moon_fq`, `moon_wxg`, `moon_full`, `moon_wng`, `moon_lq`, `moon_wnc`.

Icons generated by `make_icons.py`: `mercury`, `mercury_rx`, `solar_noon`, `daylight`, `compass`, `elevation`. Run `python3 make_icons.py` to regenerate into `icons/` (pure stdlib, no Pillow needed). The remaining icons (weather, moon, aqi, pollen, etc.) are hand-made and uploaded manually.
