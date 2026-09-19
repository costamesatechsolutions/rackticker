#!/usr/bin/env bash
# Install RackTicker on a fresh Raspberry Pi OS Lite (Bookworm), from the Pi itself:
#
#   curl -fsSL https://raw.githubusercontent.com/costamesatechsolutions/rackticker/main/tools/install.sh | sudo bash
#
# It prepares the panel driver (tools/bootstrap_matrix_pi.sh), reboots once, then
# installs the newest release from GitHub on the next boot. After that the panel
# shows its address, and updates come from Settings → Software on the web page.
set -euo pipefail
REPO=costamesatechsolutions/rackticker
RAW="https://raw.githubusercontent.com/$REPO/main"
SELF=/usr/local/sbin/rackticker-install

[[ $EUID -eq 0 ]] || { echo "Run it with sudo (see the first lines of this script)." >&2; exit 1; }
stage="${1:-prepare}"

if [[ "$stage" == prepare ]]; then
  echo "== Preparing the Pi for the LED panels (this takes a few minutes)"
  curl -fsSL "$RAW/tools/bootstrap_matrix_pi.sh" | bash
  curl -fsSL "$RAW/tools/install.sh" -o "$SELF"
  chmod 0755 "$SELF"
  cat > /etc/systemd/system/rackticker-firstboot.service <<EOF
[Unit]
Description=Install RackTicker after the panel driver's reboot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$SELF release
TimeoutStartSec=30min

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable -q rackticker-firstboot
  echo "== Rebooting. RackTicker installs itself when the Pi is back (about five minutes);"
  echo "   the panel then shows the address of its control page: http://$(hostname).local:8081/"
  sleep 3
  reboot
  exit 0
fi

if [[ "$stage" == release ]]; then
  for attempt in 1 2 3 4 5 6; do   # the network can take a moment after boot
    sha="$(curl -fsSL -H 'Accept: application/vnd.github.sha' "https://api.github.com/repos/$REPO/commits/main" || true)"
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] && break
    sleep 20
  done
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "Could not reach GitHub" >&2; exit 1; }
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/$sha" -o "$work/release.tar.gz"
  tar -xzf "$work/release.tar.gz" -C "$work" --wildcards '*/deploy/install-release.sh'
  bash "$work"/*/deploy/install-release.sh "$work/release.tar.gz" "$sha"
  systemctl disable -q rackticker-firstboot || true
  rm -f /etc/systemd/system/rackticker-firstboot.service
  systemctl daemon-reload
  # Said here as well as on the panel: this runs unattended, but the log is read later.
  echo "== RackTicker is running. Control page: http://$(hostname).local:8081/"
  exit 0
fi

echo "usage: install.sh [prepare|release]" >&2
exit 2
