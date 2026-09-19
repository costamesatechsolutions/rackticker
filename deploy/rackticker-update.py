#!/usr/bin/python3
"""RackTicker's updater and self-healer. Runs as root under systemd, never from the web app.

    rackticker-update.py update   install the commit the web page asked for
    rackticker-update.py heal     check the installed files; repair a damaged install

The web app runs unprivileged and can only drop a request, {"commit": "<sha>"}, into
/var/lib/rackticker/update/. This script installs a commit only if it is on the
official repository's main branch, downloads it from GitHub over HTTPS, and hands
it to install-release.sh, which switches back to the previous release if the new
one does not come up healthy. Progress goes to status.json for the page to show.
Standard library only: it must work even when the app's own environment does not.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = "costamesatechsolutions/rackticker"
ROOT = Path("/opt/rackticker")
STATE = Path("/var/lib/rackticker/update")
REQUEST, STATUS = STATE / "request.json", STATE / "status.json"
AGENT = {"User-Agent": f"RackTicker updater (+https://github.com/{REPO})"}


def status(stage, message, error=False, **extra):
    STATE.mkdir(parents=True, exist_ok=True)
    body = {"stage": stage, "message": message, "error": error, "at": int(time.time()), **extra}
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(body))
    shutil.chown(temporary, "rackticker", "rackticker")
    temporary.replace(STATUS)
    print(f"[{stage}] {message}", flush=True)


def get(url, attempts=3):
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=60) as reply:
                return reply.read()
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(5 * (attempt + 1))


def on_main(commit):
    """True if the commit is main itself or an ancestor of it on the official repository."""
    compare = json.loads(get(f"https://api.github.com/repos/{REPO}/compare/{commit}...main"))
    return compare.get("status") in ("identical", "ahead")


def install(commit):
    with tempfile.TemporaryDirectory(prefix="rackticker-update-") as folder:
        archive = Path(folder) / "release.tar.gz"
        archive.write_bytes(get(f"https://codeload.github.com/{REPO}/tar.gz/{commit}"))
        # The installer of the release that is running now is known to work.
        script = ROOT / "current/deploy/install-release.sh"
        if not script.is_file():
            subprocess.run(["tar", "-xzf", str(archive), "-C", folder], check=True)
            script = next(Path(folder).glob("*/deploy/install-release.sh"))
        return subprocess.run(["bash", str(script), str(archive), commit]).returncode


def update():
    try:
        commit = str(json.loads(REQUEST.read_text()).get("commit", "")).lower()
    except (OSError, ValueError):
        return
    finally:
        REQUEST.unlink(missing_ok=True)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        status("error", "That is not a commit RackTicker can install", True)
        return
    current = (ROOT / "current/REVISION").read_text().strip() if (ROOT / "current/REVISION").exists() else ""
    try:
        status("checking", "Checking the update on GitHub", target=commit, current=current)
        if not on_main(commit):
            status("error", "Only releases from RackTicker's main branch can be installed", True)
            return
        status("installing", "Downloading and installing; the display restarts in a moment", target=commit)
        code = install(commit)
    except OSError as exc:
        status("error", f"Could not reach GitHub: {exc}", True)
        return
    if code == 0:
        status("done", "Updated", revision=commit)
    elif code == 2:
        status("error", "The update did not start, so RackTicker went back to the previous version", True)
    else:
        status("error", "The update could not be installed; nothing was changed", True)


def intact(folder):
    """Every file the install fingerprinted is present and unchanged."""
    manifest = folder / "MANIFEST"
    if not manifest.is_file():
        return None  # installed before fingerprints existed: nothing to compare
    for line in manifest.read_text().splitlines():
        digest, _, name = line.partition("  ")
        path = folder / name
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return False
        except OSError:
            return False
    return True


def heal():
    current = ROOT / "current"
    state = intact(current) if current.is_dir() else False
    if state is not False:
        print("installed files are intact" if state else "no fingerprints to check")
        return
    print("the installed files are damaged or missing")
    previous = ROOT / "previous"
    if previous.is_dir() and intact(previous):
        status("healing", "Damaged files found; switching back to the previous version")
        subprocess.run(["systemctl", "stop", "rackticker"])
        shutil.rmtree(ROOT / "failed", ignore_errors=True)
        if current.exists():
            current.rename(ROOT / "failed")
        previous.rename(current)
        subprocess.run(["systemctl", "start", "rackticker"])
        status("done", "Repaired: running the previous version", revision=(current / "REVISION").read_text().strip())
        return
    revision = (current / "REVISION").read_text().strip() if (current / "REVISION").exists() else ""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        # Nothing to reinstall from: fall back to the newest release on GitHub.
        revision = get(f"https://api.github.com/repos/{REPO}/commits/main").decode()
        revision = json.loads(revision)["sha"]
    status("healing", "Damaged files found; downloading a fresh copy")
    code = install(revision)
    status("done" if code == 0 else "error", "Repaired" if code == 0 else "Repair failed", code != 0)


def rollback():
    """The display keeps crashing: run the previous version instead, once."""
    if Path("/run/rackticker-install/busy").exists() or (ROOT / "staging").is_dir():
        print("an install is running: not a crash")
        return
    restarts = subprocess.run(["systemctl", "show", "rackticker", "-p", "NRestarts", "--value"],
                              capture_output=True, text=True).stdout.strip()
    if restarts.isdigit() and int(restarts) < 3:
        print(f"only {restarts} restarts: leaving it alone")
        return
    current, previous = ROOT / "current", ROOT / "previous"
    if not previous.is_dir() or intact(previous) is False:
        status("error", "RackTicker keeps stopping and there is no earlier version to go back to", True)
        return
    status("healing", "RackTicker kept stopping; going back to the previous version")
    subprocess.run(["systemctl", "stop", "rackticker"])
    shutil.rmtree(ROOT / "failed", ignore_errors=True)
    current.rename(ROOT / "failed")
    previous.rename(current)
    subprocess.run(["/opt/rackticker/venv/bin/python", "-m", "pip", "install", "-q", "--no-deps",
                    "--force-reinstall", str(current)])
    subprocess.run(["systemctl", "reset-failed", "rackticker"])
    subprocess.run(["systemctl", "start", "--no-block", "rackticker"])
    status("done", "Went back to the previous version after repeated crashes",
           revision=(current / "REVISION").read_text().strip() if (current / "REVISION").exists() else "")


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("run as root")
    {"update": update, "heal": heal, "rollback": rollback}[sys.argv[1] if len(sys.argv) > 1 else "update"]()
