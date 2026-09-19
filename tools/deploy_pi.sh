#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pi_host="${RACKTICKER_PI_HOST:-pi@rackticker.local}"

cd "$repo_root"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Commit or stash local changes before deploying." >&2
  exit 1
fi

revision="$(git rev-parse --verify HEAD)"
archive="$(mktemp -t rackticker-deploy.XXXXXX.tgz)"
remote_script_local="$(mktemp -t rackticker-remote.XXXXXX.sh)"
remote_archive="/tmp/rackticker-${revision}.tgz"
remote_script="/tmp/rackticker-deploy-${revision}.sh"
trap 'rm -f "$archive" "$remote_script_local"' EXIT

git archive --format=tar.gz --output="$archive" HEAD
archive_sha="$(shasum -a 256 "$archive" | awk '{print $1}')"

echo "Uploading RackTicker ${revision} to ${pi_host}"
scp "$archive" "${pi_host}:${remote_archive}"

cat >"$remote_script_local" <<'REMOTE'
#!/usr/bin/env bash
set -euo pipefail
remote_archive="$1"
expected_sha="$2"
trap 'rm -f "$remote_archive" "$0"' EXIT
actual_sha="$(sha256sum "$remote_archive" | awk '{print $1}')"

if [[ "$actual_sha" != "$expected_sha" ]]; then
  echo "Archive checksum mismatch." >&2
  exit 1
fi

sudo rm -rf /opt/rackticker/staging
sudo mkdir -p /opt/rackticker/staging
sudo tar --warning=no-unknown-keyword -xzf "$remote_archive" -C /opt/rackticker/staging
sudo chown -R root:root /opt/rackticker/staging

if [[ ! -f /opt/rpi-rgb-led-matrix/lib/librgbmatrix.a ]]; then
  echo "The official rpi-rgb-led-matrix library is missing." >&2
  exit 1
fi
sudo g++ -O3 -std=c++17 \
  -I/opt/rpi-rgb-led-matrix/include \
  /opt/rackticker/staging/deploy/hub75-daemon.cpp \
  -o /usr/local/bin/rackticker-hub75d \
  -L/opt/rpi-rgb-led-matrix/lib -lrgbmatrix -lrt -lm -lpthread
sudo install -m 0644 /opt/rackticker/staging/deploy/rackticker-matrix.service \
  /etc/systemd/system/rackticker-matrix.service
# Panel tuning belongs to the owner: install the defaults once, never overwrite.
if [[ ! -f /etc/rackticker/matrix.conf ]]; then
  sudo install -D -m 0644 /opt/rackticker/staging/deploy/matrix.conf /etc/rackticker/matrix.conf
fi
sudo install -m 0644 /opt/rackticker/staging/deploy/rackticker.service \
  /etc/systemd/system/rackticker.service

sudo /opt/rackticker/venv/bin/python -m pip install --no-deps --force-reinstall /opt/rackticker/staging
# Bundled plugins now load straight from /opt/rackticker/current/plugins and are
# switched on in Settings. Drop the per-plugin packages earlier releases installed.
sudo /opt/rackticker/venv/bin/python -m pip uninstall -y -q \
  rackticker-arcade rackticker-finance rackticker-f1-schedule rackticker-free-sports \
  rackticker-news rackticker-prediction-markets rackticker-pixel-town \
  rackticker-local-adsb rackticker-ticker-wall rackticker-weather rackticker-traffic \
  rackticker-sportsbook 2>/dev/null || true
# Installed community plugins and their data live here, outside the release.
sudo install -d -o rackticker -g rackticker -m 0700 /var/lib/rackticker/plugins /var/lib/rackticker/plugin-data

if [[ ! -f /var/lib/rackticker/config.json ]]; then
  sudo install -o rackticker -g rackticker -m 0600 \
    /opt/rackticker/staging/config/config.pi.example.json \
    /var/lib/rackticker/config.json
fi

sudo systemctl stop rackticker
sudo rm -rf /opt/rackticker/previous
sudo mv /opt/rackticker/current /opt/rackticker/previous
sudo mv /opt/rackticker/staging /opt/rackticker/current
sudo systemctl daemon-reload
sudo systemctl enable rackticker-matrix rackticker
# `enable --now` does not restart an already-running matrix daemon after its
# binary is replaced. Restart it explicitly so hardware fixes take effect in
# the same deployment, then start the Python service against the fresh socket.
sudo systemctl restart rackticker-matrix
sudo systemctl start rackticker

systemctl is-active --quiet rackticker rackticker-matrix
echo "RackTicker services are active."
# Feeders are independent of RackTicker (and idle while the SDR is unplugged);
# report them without failing the deployment.
for feeder in dump1090-fa piaware fr24feed; do
  if systemctl list-unit-files "${feeder}.service" >/dev/null 2>&1; then
    echo "  ${feeder}: $(systemctl is-active "$feeder" || true)"
  fi
done
REMOTE

# Uploading the program separately keeps SSH's pseudo-terminal available only
# for login and sudo prompts. Feeding a script through `ssh -tt ... bash -s`
# can leave interactive shells waiting for EOF on some Raspberry Pi releases.
scp "$remote_script_local" "${pi_host}:${remote_script}"
ssh -tt "$pi_host" "sudo bash ${remote_script} ${remote_archive} ${archive_sha}"
