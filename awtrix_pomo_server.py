"""
Awtrix Pomodoro — phone/web trigger.

Runs a small always-on web server. Open it on your phone (LAN or Tailscale),
tap a button, and it drives a live countdown on the clock over MQTT:
a MM:SS custom app with a progress bar (green -> yellow -> red), then a
held notification + chime when the session ends.

No CLI needed to trigger. Designed to run as a background service.
"""
import json
import os
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

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
PREFIX = "awtrix_72153c"

HTTP_HOST = "127.0.0.1"  # localhost only; reach it via Cloudflare Tunnel + Access
HTTP_PORT = 8088

POMO_TOPIC = f"{PREFIX}/custom/pomo"
NOTIFY_TOPIC = f"{PREFIX}/notify"
NOTIFY_DISMISS = f"{PREFIX}/notify/dismiss"
ICON = "tomato"
PUSH_SEC = 1            # push countdown to the clock every 1s (live ticking)
POMO_DURATION = 10      # seconds the pomo app stays on screen per rotation slot
LIFETIME = 30           # app auto-clears if pushes stop for this long
# RTTTL melody played on the buzzer when a session ends.
# Four quick notes: high double-chirp (E5, G5) then low double-chirp (E4, G4).
DONE_RTTTL = "pomo:d=16,o=5,b=220:e,g,p,e4,g4"

# --- Persistent MQTT client ----------------------------------------------
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.reconnect_delay_set(min_delay=1, max_delay=30)
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()


def pub(topic, payload, retain=False):
    data = "" if payload == "" else json.dumps(payload)
    client.publish(topic, data, qos=1, retain=retain)


def clear_app():
    pub(POMO_TOPIC, "", retain=True)   # empty retained payload removes the app


def clear_countdown(stick):
    """Remove the live countdown, whichever channel it used."""
    if stick:
        pub(NOTIFY_DISMISS, "")        # dismiss the held notification
    else:
        clear_app()


def push_countdown(text, frac, stick):
    payload = {
        "text": text, "icon": ICON, "color": [255, 255, 255],
        "progress": int(frac * 100), "progressC": bar_color(frac),
        "scroll": False,
    }
    if stick:
        # Held notification pins to screen, replacing the prior tick.
        payload.update(hold=True, stack=False)
        pub(NOTIFY_TOPIC, payload)
    else:
        # Rotating custom app: lingers POMO_DURATION when it cycles in.
        payload.update(duration=POMO_DURATION, lifetime=LIFETIME)
        pub(POMO_TOPIC, payload, retain=True)


# --- Session worker -------------------------------------------------------
_cancel = threading.Event()
_worker = None
_lock = threading.Lock()
_state = {"running": False, "label": "", "remaining": 0, "total": 0, "end": 0}


def bar_color(frac_elapsed):
    if frac_elapsed < 0.5:
        return [0, 200, 80]
    if frac_elapsed < 0.8:
        return [235, 205, 0]
    return [235, 40, 40]


def run_session(total_sec, label, stick):
    end = time.time() + total_sec
    _cancel.clear()
    with _lock:
        _state.update(running=True, label=label, total=total_sec,
                      remaining=total_sec, end=end)
    while True:
        rem = int(round(end - time.time()))
        if _cancel.is_set():
            clear_countdown(stick)
            with _lock:
                _state.update(running=False, remaining=0, end=0)
            return
        if rem <= 0:
            break
        frac = 1 - rem / total_sec
        mm, ss = divmod(rem, 60)
        push_countdown(f"{mm}:{ss:02d}", frac, stick)
        with _lock:
            _state.update(remaining=rem)
        _cancel.wait(timeout=min(PUSH_SEC, max(1, rem)))
    # completed normally — remove the live countdown (held notification or
    # rotating app), then show a transient done alert that REPLACES it
    # (stack:false), plays the chime, and is not held, so the normal app
    # rotation resumes on its own once the alert finishes.
    clear_countdown(stick)
    pub(NOTIFY_TOPIC, {
        "text": f"{label} done", "icon": ICON, "color": [0, 200, 80],
        "stack": False, "duration": 6, "wakeup": True, "pushIcon": 0,
        "rtttl": DONE_RTTTL,
    })
    with _lock:
        _state.update(running=False, remaining=0, end=0)


def cancel_current():
    global _worker
    if _worker and _worker.is_alive():
        _cancel.set()
        _worker.join(timeout=8)
    _cancel.clear()


def start(minutes, label, stick=False):
    global _worker
    cancel_current()
    _worker = threading.Thread(
        target=run_session, args=(int(float(minutes) * 60), label, stick),
        daemon=True)
    _worker.start()


# --- Web UI ---------------------------------------------------------------
PAGE = """<!doctype html><html lang=en><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<meta name=apple-mobile-web-app-capable content=yes>
<title>Pomodoro</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box;font-family:-apple-system,system-ui,sans-serif}
body{margin:0;background:#15151a;color:#eee;display:flex;flex-direction:column;
 align-items:center;gap:18px;padding:28px 18px;min-height:100vh}
h1{font-size:20px;font-weight:600;margin:4px 0 0;letter-spacing:.5px}
#status{font-variant-numeric:tabular-nums;font-size:44px;font-weight:700;
 min-height:52px;color:#bdbdc7}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;width:100%;max-width:340px}
button{border:0;border-radius:14px;padding:20px 0;font-size:18px;font-weight:600;
 color:#fff;background:#2b6cb0;cursor:pointer;-webkit-tap-highlight-color:transparent}
button:active{filter:brightness(1.25)}
.break{background:#2f855a}.cancel{background:#9b2c2c;grid-column:1/3}
.row{display:flex;gap:10px;width:100%;max-width:340px}
input[type=number]{flex:1;border-radius:14px;border:1px solid #333;background:#1f1f27;color:#eee;
 font-size:18px;padding:0 14px;text-align:center}
.pin{display:flex;align-items:center;justify-content:center;gap:10px;
 width:100%;max-width:340px;font-size:16px;color:#cfcfd6}
.pin input{width:20px;height:20px}
.small{font-size:13px;color:#777}
</style></head><body>
<h1>POMODORO</h1>
<div id=status>--:--</div>
<div class=grid>
 <button onclick="go(25,'Focus')">Focus 25</button>
 <button onclick="go(50,'Focus')">Focus 50</button>
 <button class=break onclick="go(5,'Break')">Break 5</button>
 <button class=break onclick="go(15,'Break')">Break 15</button>
</div>
<div class=row>
 <input id=mins type=number inputmode=numeric placeholder="min" min=1 max=180>
 <button onclick="goCustom()">Start</button>
</div>
<label class=pin><input type=checkbox id=stick> Pin to screen during session</label>
<div class=grid><button class=cancel onclick="cancel()">Cancel</button></div>
<div class=small id=hint>idle</div>
<script>
let endTs=0, running=false, label='';
function fmt(s){s=Math.max(0,s);const m=Math.floor(s/60);return m+':'+String(s%60).padStart(2,'0')}
function render(){
 const st=document.getElementById('status'),hi=document.getElementById('hint');
 if(running){st.textContent=fmt(Math.round(endTs-Date.now()/1000));hi.textContent=label+' running'}
 else{st.textContent='--:--';hi.textContent='idle'}
}
async function sync(){
 try{const s=await(await fetch('/status')).json();
 running=s.running;endTs=s.end||0;label=s.label||'';render()}catch(e){}
}
async function go(m,l){
 const stick=document.getElementById('stick').checked?1:0;
 await fetch(`/start?min=${m}&label=${l}&stick=${stick}`);sync()}
function goCustom(){const m=+document.getElementById('mins').value;if(m>0)go(m,'Focus')}
async function cancel(){await fetch('/cancel');sync()}
setInterval(()=>{if(running)render()},250); // smooth local tick, no server hit
setInterval(sync,3000);                      // periodic resync + detect done/cancel
sync();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/" or u.path == "/index.html":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif u.path == "/start":
            mins = q.get("min", ["25"])[0]
            label = q.get("label", ["Focus"])[0][:12]
            stick = q.get("stick", ["0"])[0] == "1"
            try:
                start(mins, label, stick)
                self._send(200, b'{"ok":true}', "application/json")
            except Exception as e:
                self._send(400, json.dumps({"ok": False, "err": str(e)}).encode(),
                           "application/json")
        elif u.path == "/cancel":
            cancel_current()
            clear_app()
            with _lock:
                _state.update(running=False, remaining=0)
            self._send(200, b'{"ok":true}', "application/json")
        elif u.path == "/status":
            with _lock:
                body = json.dumps(_state).encode()
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    print(f"Pomodoro server on http://{HTTP_HOST}:{HTTP_PORT}")
    ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler).serve_forever()
