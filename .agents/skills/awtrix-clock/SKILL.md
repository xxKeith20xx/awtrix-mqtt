---
name: awtrix-clock
description: >
  Push data to an Awtrix 3 LED matrix (Ulanzi TC001) over local MQTT as custom
  apps, and run a phone-triggered pomodoro. Use this when adding/editing display
  apps (weather, environment, etc.), changing rotation timing, working with
  device icons, or modifying the pomodoro web server. Covers the non-obvious
  firmware constraints so changes don't repeat past mistakes.
---

# Awtrix 3 Clock System

Three programs publish to one Awtrix 3 clock via a local Mosquitto broker. The
clock displays "custom apps" (data pages) that rotate, plus interrupting
notifications. All display logic lives off-device; the clock only renders what
it's sent.

## Topology

- Script host (Linux): runs the scripts + Mosquitto broker at `127.0.0.1:1883`.
- Clock: HTTP at `http://192.168.0.239`, MQTT topic prefix `awtrix_72153c`.
- Custom app topic: `awtrix_72153c/custom/<name>`; notifications:
  `awtrix_72153c/notify`; dismiss held notification: `.../notify/dismiss`.
- MQTT creds: `awtrix_clock` / `mqtt00!!` (move to env before publishing repo).

## Files (all in `~/git/mqtt/`)

- `awtrix_weather.py` — NWS hourly forecast -> `weather_temp`, `weather_hum`
  apps. Cron every 15 min. Condition->icon mapping in `CONDITION_RULES`.
- `awtrix_env.py` — Open-Meteo + pollen.com + computed moon/Mercury -> `aqi`,
  `pollen`, `uv`, `sun`, `moon`, `mercury` apps. Cron hourly.
- `awtrix_pomo_server.py` — always-on HTTP server (`127.0.0.1:8088`) serving a
  mobile web page that triggers a pomodoro countdown. Runs as a user systemd
  service (`awtrix-pomo.service`); exposed via Cloudflare Tunnel + Access.
- `make_icons.py` — generates 8x8 GIF icons from pixel-index grids (pure
  stdlib, no Pillow). Output icons must be uploaded to the device `/ICONS`
  folder. Add new icons to its `ICONS` dict and re-run.

## CRITICAL firmware gotchas (the source of most past bugs)

1. **Icons live on the DEVICE, referenced by filename without extension.** The
   script sends `"icon": "sun"`; the clock resolves `/ICONS/sun.gif`. Icons must
   be **8x8 GIF** (not JPG — static JPGs render broken; not the LaMetric
   `_icon_thumb` preview, which is ~100px). LaMetric numeric IDs were abandoned
   because the inherited ones were invalid; this project uses self-made GIFs.
2. **MQTT publish must flush before disconnect.** Use `loop_start()`, publish
   with `qos=1`, then `info.wait_for_publish()` before `loop_stop()`/`disconnect()`.
   Connecting and immediately disconnecting silently drops messages.
3. **`retain=True` on app publishes** so the clock replays the last value after
   a reboot. But retained junk persists — to fully clear an app, publish an
   empty retained payload to its topic (`mosquitto_pub ... -r -n`).
4. **Per-app display time:** custom apps take `"duration": <sec>`. Native apps
   (Time/Date/Temp/Hum/Battery) share ONE global time (`ATIME` setting) and
   cannot be individually timed. To get "Time 10s, everything else 3s": disable
   the other native apps in the web UI, set `ATIME=10`, and put `"duration": 3`
   on every custom app. Pomodoro overrides to `"duration": 10`.
5. **`lifetime` vs `duration`:** `lifetime` = seconds before a stale app
   auto-removes if not refreshed; `duration` = seconds shown per rotation. Set
   `lifetime` comfortably above the cron interval.
6. **Pinning content = a held notification.** `"hold": true` interrupts rotation
   and stays until replaced or dismissed. To later REPLACE it (e.g. a "done"
   alert), the replacing notification MUST include `"stack": false`, or it
   queues behind the held one (symptom: screen stuck, no further action). A
   non-held notification auto-clears and rotation resumes.
7. **Sound:** TC001 has a monophonic piezo buzzer. Only RTTTL works (inline
   `"rtttl"` or a `MELODIES/*.txt` file via `"sound":"name"`). No MIDI/MP3/
   polyphony. If silent, the buzzer is disabled in device Settings — test with a
   direct `/notify` publish carrying an `rtttl` string.

## Data source notes

- **NWS** needs a contact in the User-Agent header. `relativeHumidity.value` can
  be explicit `null` — guard for None, not just missing key.
- **AQI/UV/sun:** Open-Meteo, free, no key. US AQI via the air-quality endpoint;
  `uv_index` + `sunrise`/`sunset` via the forecast endpoint.
- **Pollen:** Open-Meteo has NO US pollen (Europe only). Uses pollen.com's
  unofficial endpoint (needs User-Agent + Referer headers); fragile by design
  and fails soft (drops the pollen app, never blocks others). Robust upgrade =
  Google Pollen API (needs key) — one-function swap in `get_pollen`.
- **Moon:** computed locally from date (synodic cycle); no API. Phase selects
  one of 8 `moon_*.gif` icons.
- **Mercury retrograde:** computed locally from low-precision Keplerian
  orbital elements (Earth + Mercury); no API. Determines retrograde by the
  sign of the change in Mercury's geocentric ecliptic longitude, and finds
  the next station by scanning forward day-by-day for a sign flip. Text is
  `Rn d`/`Dn d` (days to next station); color is red when retrograde, green
  when direct. Single `mercury.gif` icon (shaded-sphere) for both states.

## Deploy / common operations

- Edit a script -> copy to `~/git/mqtt/` -> run once manually to push now
  (`python3 ~/git/mqtt/<script>.py`); cron handles it thereafter.
- New/changed icon -> edit `make_icons.py`, run it, upload the GIF(s) to the
  device `/ICONS` via web UI Files, then reference by filename in the script.
- Pomo server change -> replace file, `systemctl --user restart awtrix-pomo`.
- Set rotation timing -> web UI Settings `ATIME`; per-app via `duration`.
- Crons:
  - `*/15 * * * * /usr/bin/python3 ~/git/mqtt/awtrix_weather.py >> ~/awtrix_cron.log 2>&1`
  - `0 * * * * /usr/bin/python3 ~/git/mqtt/awtrix_env.py >> ~/awtrix_cron.log 2>&1`

## How to add a new data app (recipe)

1. Write a `get_x()` that returns a payload dict: `{"text", "icon",
   "color":[r,g,b], "scroll": false, "duration": 3, "lifetime": <n>}` or `None`
   on failure (so it fails soft).
2. If it needs an icon, add an 8x8 grid to `make_icons.py`, regenerate, upload
   the GIF to `/ICONS`.
3. Add the app to the `publish({...})` call keyed by its app name (becomes the
   topic suffix). Wrap the fetch in `safe()` so one failure can't break others.
4. Run the script manually to verify, then rely on cron.

## Standard payload fields used here

`text`, `icon`, `color` ([r,g,b]), `scroll` (bool), `duration` (sec on screen),
`lifetime` (sec to auto-expire), `progress`/`progressC` (bar), and for
notifications: `hold`, `stack`, `wakeup`, `rtttl`, `pushIcon`.
