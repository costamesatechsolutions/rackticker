#!/usr/bin/env bash
# Install a RackTicker release on the Pi, as root:
#
#   install-release.sh RELEASE.tar.gz REVISION
#
# Used by tools/deploy_pi.sh (from a computer) and by the updater (from the web
# page). The release is unpacked beside the running one, checked, and switched in;
# if the display or the web page does not come back healthy within a minute, the
# previous release is switched back in and the script fails. /opt/rackticker keeps
# `current` and `previous`; configuration and installed plugins live in
# /var/lib/rackticker and are never touched.
set -euo pipefail

archive="$1"
revision="$2"
root=/opt/rackticker
venv="$root/venv/bin/python"
say() { echo "[install] $*"; }

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ "$revision" =~ ^[0-9a-f]{7,40}$ ]] || { echo "bad revision: $revision" >&2; exit 1; }

say "unpacking $revision"
rm -rf "$root/staging"
mkdir -p "$root/staging"
tar --warning=no-unknown-keyword -xzf "$archive" -C "$root/staging"
# GitHub's archives wrap everything in one top-level folder; git archive does not.
entries=("$root"/staging/*)
if [[ ${#entries[@]} -eq 1 && -d "${entries[0]}" && ! -f "$root/staging/pyproject.toml" ]]; then
  inner="${entries[0]}"
  shopt -s dotglob
  mv "$inner"/* "$root/staging/"
  shopt -u dotglob
  rmdir "$inner"
fi
[[ -f "$root/staging/pyproject.toml" && -f "$root/staging/app/__main__.py" ]] || { echo "not a RackTicker release" >&2; exit 1; }
echo "$revision" > "$root/staging/REVISION"
chown -R root:root "$root/staging"

# A Pi 3A+ has 512 MB. Compiling and installing while everything else runs has run it
# out of memory, taking the web page and ssh with it. Make room first, and be gentle.
swap_kb="$(awk '/SwapTotal/ {print $2}' /proc/meminfo)"
if [[ "${swap_kb:-0}" -lt 200000 && -f /etc/dphys-swapfile ]]; then
  say "giving the Pi 512 MB of swap"
  sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' /etc/dphys-swapfile
  dphys-swapfile setup >/dev/null 2>&1 && dphys-swapfile swapon >/dev/null 2>&1 || true
fi
say "pausing the display while the new version is prepared"
systemctl stop rackticker || true

say "checking the code compiles"
"$venv" -m compileall -q "$root/staging/app" "$root/staging/rackticker" "$root/staging/plugins" >/dev/null

# Python dependencies change rarely; install them only when requirements.txt did.
if ! cmp -s "$root/staging/requirements.txt" "$root/current/requirements.txt" 2>/dev/null; then
  say "installing Python dependencies"
  "$venv" -m pip install -q -r "$root/staging/requirements.txt"
fi

# The panel companion is rebuilt only when its source changed (or is missing).
if [[ ! -x /usr/local/bin/rackticker-hub75d ]] || \
   ! cmp -s "$root/staging/deploy/hub75-daemon.cpp" "$root/current/deploy/hub75-daemon.cpp" 2>/dev/null; then
  [[ -f /opt/rpi-rgb-led-matrix/lib/librgbmatrix.a ]] || { echo "rpi-rgb-led-matrix is missing" >&2; exit 1; }
  say "building the panel companion"
  nice -n 19 ionice -c3 g++ -O2 -std=c++17 --param ggc-min-expand=20 -I/opt/rpi-rgb-led-matrix/include "$root/staging/deploy/hub75-daemon.cpp" \
    -o /usr/local/bin/rackticker-hub75d.new -L/opt/rpi-rgb-led-matrix/lib -lrgbmatrix -lrt -lm -lpthread
  matrix_changed=1
fi

# Fingerprints of every file, so self-healing can tell a damaged install from a good one.
( cd "$root/staging" && find . -type f ! -path '*/__pycache__/*' ! -name MANIFEST -print0 | sort -z \
    | xargs -0 sha256sum > MANIFEST )

"$venv" -m pip install -q --no-deps --force-reinstall "$root/staging"
install -d -o rackticker -g rackticker -m 0700 /var/lib/rackticker/plugins /var/lib/rackticker/plugin-data \
  /var/lib/rackticker/update
if [[ ! -f /var/lib/rackticker/config.json ]]; then
  install -o rackticker -g rackticker -m 0600 "$root/staging/config/config.pi.example.json" /var/lib/rackticker/config.json
fi
# Keep the system log small: a Pi running for years should not wear out its SD card.
if [[ ! -f /etc/systemd/journald.conf.d/rackticker.conf ]]; then
  install -D -m 0644 /dev/stdin /etc/systemd/journald.conf.d/rackticker.conf <<'JOURNAL'
[Journal]
SystemMaxUse=48M
RuntimeMaxUse=16M
MaxRetentionSec=2week
JOURNAL
  systemctl restart systemd-journald || true
fi
# Panel tuning belongs to the owner: installed once, never overwritten.
if [[ ! -f /etc/rackticker/matrix.conf ]]; then
  install -D -m 0644 "$root/staging/deploy/matrix.conf" /etc/rackticker/matrix.conf
fi

healthy() {
  for _ in $(seq 1 30); do
    if systemctl is-active --quiet rackticker rackticker-matrix &&
       curl -fs -m 3 -o /dev/null http://127.0.0.1:8081/api/state 2>/dev/null; then
      return 0
    fi
    sleep 2
  done
  return 1
}

switch_in() {  # $1: release folder to make current; units come from it
  for unit in "$1"/deploy/*.service "$1"/deploy/*.path "$1"/deploy/*.timer; do
    [[ -f "$unit" ]] && install -m 0644 "$unit" /etc/systemd/system/
  done
  systemctl daemon-reload
  systemctl enable -q rackticker-matrix rackticker rackticker-update.path rackticker-selfheal.service rackticker-network \
    rackticker-selfheal.timer 2>/dev/null || true
  systemctl start rackticker-update.path rackticker-selfheal.timer 2>/dev/null || true
  systemctl restart rackticker-network 2>/dev/null || true   # pick up its new code
}

say "switching to $revision"
rm -rf "$root/previous"
[[ -d "$root/current" ]] && mv "$root/current" "$root/previous"
mv "$root/staging" "$root/current"
switch_in "$root/current"
if [[ -n "${matrix_changed:-}" ]]; then
  cp /usr/local/bin/rackticker-hub75d /usr/local/bin/rackticker-hub75d.previous 2>/dev/null || true
  mv /usr/local/bin/rackticker-hub75d.new /usr/local/bin/rackticker-hub75d
fi
# Always restart the panel companion: it recreates its socket with the permissions
# the display needs, whatever happened to /run since.
systemctl restart rackticker-matrix
systemctl start rackticker

if healthy; then
  rm -rf "$root/failed" /usr/local/bin/rackticker-hub75d.previous
  say "RackTicker $revision is running"
  exit 0
fi

say "not healthy after a minute: switching back"
systemctl stop rackticker || true
rm -rf "$root/failed"
mv "$root/current" "$root/failed"
mv "$root/previous" "$root/current"
if [[ -n "${matrix_changed:-}" && -x /usr/local/bin/rackticker-hub75d.previous ]]; then
  mv /usr/local/bin/rackticker-hub75d.previous /usr/local/bin/rackticker-hub75d
  systemctl restart rackticker-matrix
fi
"$venv" -m pip install -q --no-deps --force-reinstall "$root/current" || true
switch_in "$root/current"
systemctl start rackticker
echo "the new release did not start; the previous one is running again" >&2
exit 2
