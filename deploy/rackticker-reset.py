#!/usr/bin/python3
"""The deeper resets: the ones that need root, and the one that prepares a unit to ship.

    rackticker-reset.py network     forget every saved Wi-Fi network, then open setup mode
    rackticker-reset.py everything  settings, plugins, password and Wi-Fi: a Pi as it came
    rackticker-reset.py identity    regenerate what must be unique per device (first boot)
    rackticker-reset.py ship        everything, plus wipe identity and logs, then power off
    rackticker-reset.py request     carry out what the control page asked for (systemd path)

Settings and plugins alone are reset by the web app itself; it runs unprivileged and
cannot touch Wi-Fi, so it leaves {"scope": "..."} in /var/lib/rackticker/reset/ and
this script, started by rackticker-reset.path, does the rest.

`ship` is for making a card to sell or to clone: it removes the owner's Wi-Fi
password, the control page's password, plugin logins, shell history and the system
log, and clears the machine id, host keys and hostname so the next boot invents new
ones. Without that last part every cloned card answers to the same name, offers the
same ssh host key, and can be handed the same DHCP address as its twin.

Standard library only: this has to work when nothing else does.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

DATA = Path("/var/lib/rackticker")
STATE = DATA / "reset"
REQUEST, STATUS = STATE / "request.json", STATE / "status.json"
HOME = Path("/home/rackticker")               # the service account plugins keep logins in
IDENTITY_WANTED = DATA / "new-identity"        # left behind by `ship`, read on the next boot
HOTSPOT = "RackTicker-Setup"
SCOPES = ("network", "everything", "ship")


def status(stage, message, error=False, **extra):
    STATE.mkdir(parents=True, exist_ok=True)
    body = {"stage": stage, "message": message, "error": error, "at": int(time.time()), **extra}
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(body))
    try:
        shutil.chown(temporary, "rackticker", "rackticker")
    except (LookupError, PermissionError, OSError):
        pass
    temporary.replace(STATUS)
    print(f"[{stage}] {message}", flush=True)


def run(*args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=kwargs.pop("timeout", 60), **kwargs)


# ---------------------------------------------------------------- Wi-Fi

def wifi_profiles():
    """Saved Wi-Fi connections, the setup network itself excluded."""
    done = run("nmcli", "-t", "-e", "yes", "-f", "TYPE,NAME", "con", "show")
    if done.returncode != 0:
        return []
    names = []
    for line in done.stdout.splitlines():
        kind, _, name = line.partition(":")
        name = name.replace("\\:", ":")
        if kind == "802-11-wireless" and name != HOTSPOT:
            names.append(name)
    return names


def forget_wifi():
    """Delete every saved Wi-Fi network. The keeper then opens setup mode by itself."""
    gone = []
    for name in wifi_profiles():
        if run("nmcli", "con", "delete", "id", name).returncode == 0:
            gone.append(name)
    return gone


# ---------------------------------------------------------------- settings and logins

def forget_settings():
    """Everything the owner chose: settings, installed plugins, plugin logins, password."""
    removed = []
    for path in (DATA / "config.json", DATA / "access.json", DATA / "quick-boots", DATA / "timezone"):
        if path.exists():
            path.unlink(missing_ok=True)
            removed.append(path.name)
    for folder in (DATA / "plugins", DATA / "plugin-data"):
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
            removed.append(folder.name)
    for leftover in DATA.glob("config.json.before-reset-*"):
        leftover.unlink(missing_ok=True)
    # Plugin logins a plugin kept in the service account's home (a Spotify token, say).
    for dotfile in list(HOME.glob(".*-spotify.json")) + list(HOME.glob(".rackticker-*.json")):
        dotfile.unlink(missing_ok=True)
        removed.append(dotfile.name)
    for owner in (DATA, DATA / "plugins", DATA / "plugin-data"):
        try:
            shutil.chown(owner, "rackticker", "rackticker")
        except (LookupError, OSError):
            pass
    return removed


# ---------------------------------------------------------------- identity

def cpu_serial():
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("Serial"):
                return line.split(":")[-1].strip()
    except OSError:
        pass
    return ""


def device_name():
    """A name of this device's own: rackticker-1a2b, from the Pi's serial number.

    Two cards cloned from one image must not both answer to `rackticker.local`."""
    serial = cpu_serial()
    if not serial or set(serial) == {"0"}:
        serial = os.urandom(8).hex()
    return f"rackticker-{serial[-4:].lower()}"


def clear_identity():
    """Blank what must differ between two devices. The next boot fills it back in."""
    Path("/etc/machine-id").write_text("")        # systemd regenerates an empty file at boot
    Path("/var/lib/dbus/machine-id").unlink(missing_ok=True)
    for key in Path("/etc/ssh").glob("ssh_host_*"):
        key.unlink(missing_ok=True)
    shutil.rmtree("/var/lib/NetworkManager", ignore_errors=True)   # DHCP leases and client ids
    IDENTITY_WANTED.write_text("pending\n")


def new_identity():
    """Give this device a machine id, host keys and a name of its own. Idempotent."""
    made = []
    machine_id = Path("/etc/machine-id")
    if not (machine_id.exists() and machine_id.read_text().strip()):
        run("systemd-machine-id-setup")
        made.append("machine id")
    if not any(Path("/etc/ssh").glob("ssh_host_*_key")):
        run("ssh-keygen", "-A", timeout=180)
        made.append("ssh host keys")
    wanted = device_name()
    current = Path("/etc/hostname").read_text().strip() if Path("/etc/hostname").exists() else ""
    # A name the owner chose is theirs. Only a card being started as a new device,
    # or one still called what the imager called it, gets named after its own Pi.
    if current != wanted and (IDENTITY_WANTED.exists() or current in ("", "raspberrypi")):
        run("hostnamectl", "set-hostname", wanted)
        hosts = Path("/etc/hosts")
        if hosts.exists():   # keep 127.0.1.1 pointing at whatever we are called now
            lines = [line for line in hosts.read_text().splitlines() if not line.startswith("127.0.1.1")]
            hosts.write_text("\n".join(lines + [f"127.0.1.1\t{wanted}"]) + "\n")
        made.append(f"name {wanted}")
    IDENTITY_WANTED.unlink(missing_ok=True)
    return made


# ---------------------------------------------------------------- traces

def wipe_traces():
    """Logs, shell history and other records of the person who set this card up."""
    run("journalctl", "--rotate", timeout=120)
    run("journalctl", "--vacuum-time=1s", timeout=120)
    for pattern in ("/root/.bash_history", "/home/*/.bash_history", "/root/.ssh/known_hosts",
                    "/home/*/.ssh/known_hosts", "/var/log/wtmp", "/var/log/btmp", "/var/log/lastlog"):
        for path in Path("/").glob(pattern.lstrip("/")):
            try:
                path.unlink()
            except OSError:
                pass
    shutil.rmtree("/var/tmp/rackticker", ignore_errors=True)
    for folder in (Path("/opt/rackticker/failed"), Path("/opt/rackticker/staging")):
        shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------- the scopes

def reset(scope):
    if scope not in SCOPES:
        status("error", f"There is no reset called {scope!r}", True)
        return 1
    if scope == "network":
        gone = forget_wifi()
        status("done", "Wi-Fi forgotten; RackTicker is opening its setup network", networks=len(gone))
        return 0
    status("resetting", "Clearing settings, plugins and the password")
    removed = forget_settings()
    gone = forget_wifi()
    if scope == "everything":
        status("done", "Reset. RackTicker is opening its setup network so it can be set up again",
               cleared=removed, networks=len(gone))
        subprocess.run(["systemctl", "restart", "--no-block", "rackticker"])
        return 0
    status("resetting", "Clearing this device's identity and its logs")
    clear_identity()
    wipe_traces()
    status("done", "Ready to ship: powering off. The next boot makes a new identity.",
           cleared=removed, networks=len(gone))
    subprocess.run(["systemctl", "poweroff", "--no-block"])
    return 0


def request():
    """Carry out what the control page left for us."""
    try:
        scope = str(json.loads(REQUEST.read_text()).get("scope", "")).lower()
    except (OSError, ValueError):
        return 0
    finally:
        REQUEST.unlink(missing_ok=True)
    if not re.fullmatch(r"[a-z]{1,20}", scope):
        status("error", "That is not a reset RackTicker knows", True)
        return 1
    return reset(scope)


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "request"
    if action == "--check":
        print(json.dumps({"saved_wifi": wifi_profiles(), "would_be_named": device_name(),
                          "hostname": Path("/etc/hostname").read_text().strip(),
                          "identity_pending": IDENTITY_WANTED.exists()}, indent=2))
        return 0
    if os.geteuid() != 0:
        sys.exit("run as root")
    if action == "identity":
        made = new_identity()
        print("identity: " + (", ".join(made) if made else "already this device's own"))
        return 0
    if action == "request":
        return request()
    return reset(action)


if __name__ == "__main__":
    sys.exit(main())
