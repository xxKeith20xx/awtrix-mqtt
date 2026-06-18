"""
Awtrix Dashboard — a web view of everything the clock shows.

Runs a small always-on web server that mirrors all the glanceable metrics
pushed to the Awtrix clock (weather, air quality, sun, sky) on a single
auto-refreshing page. Reuses the exact data functions from awtrix_env.py and
awtrix_weather.py, and serves the same 8x8 GIF icons, so the page always
matches the clock.

A background thread refreshes the data every REFRESH_SEC (default 60s) and
caches it; the page polls /data on the same cadence. Designed to run as a
background service and be exposed via a Cloudflare Tunnel.
"""
import json
import os
import re
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

import awtrix_env as env
import awtrix_weather as wx

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "icons")

HTTP_HOST = "127.0.0.1"  # localhost only; reach it via Cloudflare Tunnel
HTTP_PORT = 8089
REFRESH_SEC = 60         # how often the server refetches data


# --- Data gathering -------------------------------------------------------
def safe(fn, label):
    try:
        return fn()
    except Exception as e:
        print(f"dashboard: {label} failed (non-fatal): {e}")
        return None


def _item(label, payload):
    """Turn an Awtrix custom-app payload into a dashboard card, or None."""
    if not payload:
        return None
    c = payload.get("color", [255, 255, 255])
    return {
        "label": label,
        "value": payload.get("text", ""),
        "color": f"{c[0]},{c[1]},{c[2]}",
        "icon": payload.get("icon", ""),
    }


def gather():
    """Collect every metric, grouped, reusing the clock's own data functions."""
    w = safe(wx.get_weather, "weather") or {}
    aqi = safe(env.get_aqi, "aqi")
    pollen = safe(env.get_pollen, "pollen")
    sun = safe(env.get_sun_apps, "sun apps") or {}
    pos = safe(env.get_sun_position, "sun position") or {}
    moon = safe(env.get_moon, "moon")
    mercury = safe(env.get_mercury, "mercury")

    groups = [
        {"name": "Weather", "items": [
            _item("Temperature", w.get("weather_temp")),
            _item("Feels like", w.get("weather_feels")),
            _item("Wind", w.get("weather_wind")),
            _item("Rain · next hr", w.get("weather_rain")),
            _item("Humidity", w.get("weather_hum")),
            _item("Dew point", w.get("weather_dew")),
        ]},
        {"name": "Air Quality", "items": [
            _item("AQI", aqi),
            _item("Pollen", pollen),
            _item("UV index", sun.get("uv")),
        ]},
        {"name": "Sun", "items": [
            _item("Next sun event", sun.get("sun")),
            _item("Solar noon", sun.get("noon")),
            _item("Day length", sun.get("daylen")),
            _item("Sun bearing", pos.get("compass")),
            _item("Sun elevation", pos.get("elev")),
        ]},
        {"name": "Sky", "items": [
            _item("Moon", moon),
            _item("Mercury · days to station", mercury),
        ]},
    ]
    for g in groups:
        g["items"] = [i for i in g["items"] if i]
    return {"updated": time.time(), "groups": [g for g in groups if g["items"]]}


_cache = {"updated": 0, "groups": []}
_cache_lock = threading.Lock()


def refresher():
    global _cache
    while True:
        data = gather()
        with _cache_lock:
            _cache = data
        time.sleep(REFRESH_SEC)


# --- Web UI ---------------------------------------------------------------
PAGE = """<!doctype html><html lang=en><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<meta name=apple-mobile-web-app-capable content=yes>
<title>Awtrix Dashboard</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box;font-family:-apple-system,system-ui,sans-serif}
body{margin:0;background:#0f0f14;color:#eee;padding:22px 16px 40px;
 max-width:760px;margin:0 auto}
h1{font-size:18px;font-weight:600;letter-spacing:.6px;margin:0 0 2px;text-transform:uppercase;color:#cfcfd6}
#sub{font-size:12px;color:#777;margin-bottom:20px}
h2{font-size:12px;font-weight:600;letter-spacing:1px;text-transform:uppercase;
 color:#8a8a96;margin:22px 0 10px;border-bottom:1px solid #23232c;padding-bottom:6px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.card{background:#181820;border:1px solid #23232c;border-radius:12px;padding:12px;
 display:flex;align-items:center;gap:11px;border-left:3px solid var(--accent)}
.card img{width:24px;height:24px;image-rendering:pixelated;flex:none}
.meta{min-width:0}
.label{font-size:11px;color:#9a9aa6;line-height:1.2}
.value{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;
 color:var(--accent);line-height:1.15}
#err{color:#c66;font-size:13px;margin-top:12px}
</style></head><body>
<h1>Awtrix Dashboard</h1>
<div id=sub>loading…</div>
<div id=root></div>
<div id=err></div>
<script>
function card(it){
 const a=`rgb(${it.color})`;
 const img=it.icon?`<img src="/icon/${encodeURIComponent(it.icon)}" alt="">`:'';
 return `<div class=card style="--accent:${a}">${img}
  <div class=meta><div class=label>${it.label}</div>
  <div class=value>${it.value||'—'}</div></div></div>`;
}
function render(d){
 const root=document.getElementById('root');
 root.innerHTML=d.groups.map(g=>
  `<h2>${g.name}</h2><div class=grid>${g.items.map(card).join('')}</div>`).join('');
 const ago=Math.max(0,Math.round(Date.now()/1000-d.updated));
 document.getElementById('sub').textContent=
  d.updated?`updated ${ago}s ago · refreshes every 60s`:'no data yet';
}
async function sync(){
 try{const d=await(await fetch('/data')).json();render(d);
  document.getElementById('err').textContent='';}
 catch(e){document.getElementById('err').textContent='connection error — retrying';}
}
setInterval(sync,60000);   // refetch every minute
sync();
</script></body></html>"""

_ICON_RE = re.compile(r"^[A-Za-z0-9_]+$")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/data":
            with _cache_lock:
                body = json.dumps(_cache).encode()
            self._send(200, body, "application/json")
        elif path.startswith("/icon/"):
            name = path[len("/icon/"):].rsplit(".", 1)[0]
            if not _ICON_RE.match(name):
                self._send(404, b"bad icon", "text/plain")
                return
            fp = os.path.join(ICON_DIR, f"{name}.gif")
            if not os.path.isfile(fp):
                self._send(404, b"no icon", "text/plain")
                return
            with open(fp, "rb") as f:
                self._send(200, f.read(), "image/gif")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    threading.Thread(target=refresher, daemon=True).start()
    print(f"Dashboard on http://{HTTP_HOST}:{HTTP_PORT}")
    ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler).serve_forever()
