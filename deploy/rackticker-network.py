#!/usr/bin/python3
"""RackTicker's network keeper: Wi-Fi setup without a keyboard, screen or SSH.

Runs as root under systemd (rackticker-network.service), beside the display.

* First boot with no Wi-Fi saved: after a short grace it opens the
  "RackTicker-Setup" network. Join it with a phone, a setup page opens, pick your
  Wi-Fi and type its password. The panel shows the steps while this is on.
* Wi-Fi lost for five minutes (router swapped, password changed, moved house):
  the same setup network comes up, and every few minutes it tries the saved
  Wi-Fi again, so when the old network returns it reconnects by itself.
* Wi-Fi working but the internet down: nothing to set up, so it stays put.

It never touches a working connection. "Online" means a default route through
something other than its own setup network, or an active Wi-Fi connection with a
real address; only when neither holds for the whole grace period does it act.
`--check` prints what it sees and what it would do, and changes nothing.
"""
from __future__ import annotations

import html
import http.server
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs

HOTSPOT = "RackTicker-Setup"
AP_IP = "10.42.0.1"
STATE = Path("/run/rackticker-network/network.json")
DNS_CONF = Path("/etc/NetworkManager/dnsmasq-shared.d/rackticker-portal.conf")
BOOT_GRACE = 90          # seconds after start before setup opens when nothing is saved
DROP_GRACE = 300         # seconds offline before setup opens when a network is saved
RETRY_SAVED = 240        # seconds between tries of the saved network while in setup
QUIET_AFTER_VISIT = 180  # do not drop the setup network while someone is using the page
TICK = 10
# Forgot the control page's password and no SSH? Unplug RackTicker as soon as its
# panel lights up, three times in a row: the next start clears the password.
QUICK_BOOTS = Path("/var/lib/rackticker/quick-boots")
ACCESS = Path("/var/lib/rackticker/access.json")
SETTLED = 90   # seconds of running that mean "this was not a quick unplug"


BOOT_ID = Path("/proc/sys/kernel/random/boot_id")


def count_quick_boot(boot=None):
    """Count this power-up (restarts of this service within one boot do not count);
    on the third quick one in a row, clear the password."""
    try:
        boot = boot or BOOT_ID.read_text().strip()
    except OSError:
        return ""
    try:
        count_text, _, last = QUICK_BOOTS.read_text().strip().partition(" ")
        count = int(count_text or 0)
    except (OSError, ValueError):
        count, last = 0, ""
    if last == boot:
        return ""   # the same boot: a service restart, not an unplug
    count += 1
    if count >= 3:
        cleared = ACCESS.exists()
        ACCESS.unlink(missing_ok=True)
        count = 0
        notice = "PASSWORD CLEARED" if cleared else ""
    else:
        notice = ""
    try:
        QUICK_BOOTS.parent.mkdir(parents=True, exist_ok=True)
        with open(QUICK_BOOTS, "w") as stream:
            stream.write(f"{count} {boot}")
            stream.flush()
            os.fsync(stream.fileno())   # a power cut comes next: make it stick
    except OSError:
        pass
    return notice


def nmcli(*args, timeout=45):
    try:
        done = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        return done.returncode, done.stdout.strip(), done.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)


def fields(line):
    """Split `nmcli -t -e yes` output, where colons inside values are escaped."""
    parts, current, index = [], [], 0
    while index < len(line):
        if line[index] == "\\" and index + 1 < len(line):
            current.append(line[index + 1])
            index += 2
            continue
        if line[index] == ":":
            parts.append("".join(current))
            current = []
        else:
            current.append(line[index])
        index += 1
    parts.append("".join(current))
    return parts


def usable(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.version == 4 and not (ip.is_loopback or ip.is_link_local or address.startswith("10.42."))


def addresses():
    """{device: [IPv4 addresses]} for every interface but loopback."""
    found = {}
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return found
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 3 and parts[2] == "inet" and parts[1] != "lo":
            address = parts[3].split("/")[0]
            if usable(address):
                found.setdefault(parts[1], []).append(address)
    return found


def default_route():
    try:
        out = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.strip()


def active():
    """[(type, name, device)] of active NetworkManager connections."""
    code, out, _ = nmcli("-t", "-e", "yes", "-f", "TYPE,NAME,DEVICE", "con", "show", "--active")
    return [tuple(fields(line)[:3]) for line in out.splitlines()] if code == 0 else None


def online():
    """(online, ssid, address). Conservative: any sign of a working network counts."""
    found = addresses()
    connections = active()
    ssid = next((name for kind, name, _ in connections or [] if "wireless" in kind and name != HOTSPOT), "")
    address = next((ips[0] for ips in found.values() if ips), "")
    if default_route() and address:
        return True, ssid, address
    if ssid and address:
        return True, ssid, address
    if connections is None and address:   # nmcli failed: trust the address, never tear down
        return True, ssid, address
    return False, "", ""


def saved():
    code, out, _ = nmcli("-t", "-e", "yes", "-f", "TYPE,NAME", "con", "show")
    if code != 0:
        return None   # unknown: behave as if something is saved (the gentler path)
    return [fields(line)[1] for line in out.splitlines() if "wireless" in fields(line)[0] and fields(line)[1] != HOTSPOT]


def scan():
    """[(ssid, signal, secured)] strongest first, taken before the setup network starts."""
    nmcli("dev", "wifi", "rescan", timeout=20)
    time.sleep(3)
    code, out, _ = nmcli("-t", "-e", "yes", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", timeout=20)
    best = {}
    for line in out.splitlines() if code == 0 else []:
        ssid, signal, security = (fields(line) + ["", "", ""])[:3]
        if ssid and ssid != HOTSPOT:
            best[ssid] = max(best.get(ssid, (0, security)), (int(signal or 0), security))
    return sorted(((s, v[0], bool(v[1] and v[1] != "--")) for s, v in best.items()), key=lambda row: -row[1])[:20]


class Keeper:
    def __init__(self):
        self.started = time.monotonic()
        self.offline_since = None
        self.setup = False
        self.networks = []
        self.last_retry = 0.0
        self.last_visit = 0.0
        self.request = None       # (ssid, password) from the setup page
        self.message = ""
        self.lock = threading.Lock()
        self.portal = None
        self.notice = ""          # shown on the panel's start-up screen
        self.settled = False

    # --- decisions ---------------------------------------------------------------

    def decide(self, now, is_online, profiles):
        """What to do this tick: "stay", "open", "close", "retry" or "connect"."""
        if self.setup:
            if is_online:
                return "close"
            if self.request:
                return "connect"
            if profiles and now - self.last_retry >= RETRY_SAVED and now - self.last_visit >= QUIET_AFTER_VISIT:
                return "retry"
            return "stay"
        if is_online:
            return "stay"
        grace = DROP_GRACE if profiles is None or profiles else BOOT_GRACE
        offline_for = now - (self.offline_since or now)
        return "open" if offline_for >= grace else "stay"

    # --- actions -------------------------------------------------------------------

    def open_setup(self):
        self.networks = scan()
        DNS_CONF.parent.mkdir(parents=True, exist_ok=True)
        DNS_CONF.write_text(f"address=/#/{AP_IP}\n")   # every name answers with the setup page
        nmcli("con", "delete", HOTSPOT)
        nmcli("con", "add", "type", "wifi", "ifname", "wlan0", "con-name", HOTSPOT, "autoconnect", "no",
              "ssid", HOTSPOT, "mode", "ap", "802-11-wireless.band", "bg", "ipv4.method", "shared",
              "ipv4.addresses", f"{AP_IP}/24", "ipv6.method", "disabled")
        code, _, error = nmcli("con", "up", HOTSPOT)
        if code != 0:
            self.message = f"Could not start the setup network: {error}"
            return
        self.setup = True
        self.last_retry = time.monotonic()
        self.start_portal()

    def close_setup(self):
        self.stop_portal()
        nmcli("con", "down", HOTSPOT)
        nmcli("con", "delete", HOTSPOT)
        DNS_CONF.unlink(missing_ok=True)
        self.setup = False

    def retry_saved(self, profiles):
        """Drop the setup network for a moment and try each saved Wi-Fi."""
        self.last_retry = time.monotonic()
        self.close_setup()
        for name in profiles:
            if nmcli("--wait", "30", "con", "up", "id", name, timeout=45)[0] == 0 and online()[0]:
                self.message = ""
                return
        self.open_setup()

    def connect(self):
        with self.lock:
            ssid, password = self.request
            self.request = None
        self.close_setup()
        args = ["--wait", "40", "dev", "wifi", "connect", ssid, "ifname", "wlan0"]
        if password:
            args += ["password", password]
        code, _, error = nmcli(*args, timeout=60)
        if code == 0 and online()[0]:
            self.message = ""
            return
        nmcli("con", "delete", "id", ssid)   # a wrong password leaves a profile that can never work
        self.message = f"Could not join {ssid}: check the password and try again"
        self.open_setup()

    # --- the setup page ------------------------------------------------------------

    def start_portal(self):
        keeper = self

        class Page(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                keeper.last_visit = time.monotonic()
                self.reply(keeper.page())

            def do_POST(self):
                keeper.last_visit = time.monotonic()
                length = min(int(self.headers.get("Content-Length") or 0), 4096)
                form = parse_qs(self.rfile.read(length).decode(errors="replace"))
                ssid = (form.get("ssid") or form.get("other") or [""])[0].strip()
                if form.get("other", [""])[0].strip():
                    ssid = form["other"][0].strip()
                password = (form.get("password") or [""])[0]
                if not ssid or len(ssid) > 32 or len(password) > 63:
                    self.reply(keeper.page("Pick a network (or type its name) and try again"))
                    return
                with keeper.lock:
                    keeper.request = (ssid, password)
                host = socket.gethostname().split(".")[0]
                self.reply(f"""<h1>Joining {html.escape(ssid)}…</h1>
<p>The setup network closes now. Put your phone back on <b>{html.escape(ssid)}</b>, then open
<a href="http://{host}.local:8081">http://{host}.local:8081</a>. The panel shows its address too.</p>
<p>If the password was wrong, <b>{HOTSPOT}</b> comes back in about a minute: join it and try again.</p>""")

            def reply(self, body):
                page = f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>RackTicker setup</title><style>body{{font:16px system-ui;max-width:480px;margin:24px auto;padding:0 16px;
background:#f6f1e7;color:#1d1b16}}button,input,select{{font:inherit;padding:10px;width:100%;box-sizing:border-box;
margin:6px 0}}button{{background:#e0561c;color:#fff;border:0;border-radius:6px}}</style>{body}"""
                data = page.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        class Server(http.server.ThreadingHTTPServer):
            allow_reuse_address = True

        for _ in range(10):   # the address appears a moment after the network comes up
            try:
                self.portal = Server((AP_IP, 80), Page)
                break
            except OSError:
                time.sleep(1)
        if self.portal:
            threading.Thread(target=self.portal.serve_forever, daemon=True).start()

    def stop_portal(self):
        if self.portal:
            self.portal.shutdown()
            self.portal.server_close()
            self.portal = None

    def page(self, note=""):
        note = note or self.message
        options = "".join(f'<option value="{html.escape(ssid)}">{html.escape(ssid)}'
                          f'{" 🔒" if secured else ""} ({signal}%)</option>' for ssid, signal, secured in self.networks)
        return f"""<h1>RackTicker Wi-Fi</h1>{f'<p style="color:#b3261e">{html.escape(note)}</p>' if note else ''}
<form method=post><label>Network<select name=ssid>{options}</select></label>
<label>Not listed? Type its name<input name=other maxlength=32 autocapitalize=off></label>
<label>Password<input name=password type=password maxlength=63 autocomplete=off></label>
<button>Connect</button></form>
<p>RackTicker remembers this network. If it is ever away for five minutes, this page comes back.</p>"""

    # --- the loop --------------------------------------------------------------------

    def publish(self, is_online, ssid, address):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        mode = "online" if is_online else "setup" if self.setup else "offline"
        body = {"mode": mode, "ssid": ssid, "address": address, "hotspot": HOTSPOT, "notice": self.notice,
                "portal": f"http://{AP_IP}", "message": self.message, "at": int(time.time())}
        temporary = STATE.with_suffix(".tmp")
        temporary.write_text(json.dumps(body))
        temporary.chmod(0o644)
        temporary.replace(STATE)

    def tick(self, act=True):
        now = time.monotonic()
        if act and not self.settled and now - self.started >= SETTLED:
            self.settled = True   # running a while: the next start is not a quick unplug
            try:
                boot = BOOT_ID.read_text().strip()
                QUICK_BOOTS.write_text(f"0 {boot}")
            except OSError:
                pass
        is_online, ssid, address = online()
        self.offline_since = None if is_online else (self.offline_since or now)
        profiles = saved()
        action = self.decide(now, is_online, profiles)
        if not act:
            return action, is_online, ssid, address, profiles
        if action == "open":
            self.open_setup()
        elif action == "close":
            self.close_setup()
        elif action == "retry":
            self.retry_saved(profiles or [])
        elif action == "connect":
            self.connect()
        self.publish(*online())
        return action, is_online, ssid, address, profiles


def main():
    keeper = Keeper()
    if "--check" in sys.argv:
        action, is_online, ssid, address, profiles = keeper.tick(act=False)
        print(json.dumps({"online": is_online, "ssid": ssid, "address": address, "saved": profiles,
                          "default_route": default_route(), "would": action,
                          "note": "stay means leave the network alone"}, indent=2))
        return
    if os.geteuid() != 0:
        sys.exit("run as root")
    keeper.notice = count_quick_boot()
    # Leftovers from a crash or power cut: never start with the setup network up.
    if any(name == HOTSPOT for _, name, _ in active() or []):
        keeper.close_setup()
    while True:
        try:
            keeper.tick()
        except Exception as exc:   # keep watching whatever happens
            print(f"network keeper: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(TICK)


if __name__ == "__main__":
    main()
