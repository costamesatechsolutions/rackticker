#!/usr/bin/env bash
set -euo pipefail

matrix_revision="51d3231e370593b60952b2c3b18d2e3802329f18"
model="$(tr -d '\0' </proc/device-tree/model)"
if [[ "$model" != *"Pi 3 Model A Plus"* && "$model" != *"Pi Zero 2 W"* ]]; then
  echo "Expected a Raspberry Pi 3 A+ or Zero 2 W; found: $model" >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y build-essential git python3-venv python3-pil python3-aiohttp

if ! getent passwd rackticker >/dev/null; then
  sudo useradd --system --home-dir /var/lib/rackticker --shell /usr/sbin/nologin rackticker
fi
sudo install -d -o root -g root -m 0755 /opt/rackticker/current
sudo install -d -o rackticker -g rackticker -m 0700 /var/lib/rackticker
if [[ ! -x /opt/rackticker/venv/bin/python ]]; then
  sudo python3 -m venv --system-site-packages /opt/rackticker/venv
fi

if [[ ! -d /opt/rpi-rgb-led-matrix/.git ]]; then
  sudo git clone https://github.com/hzeller/rpi-rgb-led-matrix.git /opt/rpi-rgb-led-matrix
fi
sudo git -C /opt/rpi-rgb-led-matrix fetch --depth 1 origin "$matrix_revision"
sudo git -C /opt/rpi-rgb-led-matrix checkout --detach "$matrix_revision"
sudo nice -n 10 ionice -c3 make -C /opt/rpi-rgb-led-matrix/lib -j1

boot_dir=/boot/firmware
[[ -f "$boot_dir/config.txt" ]] || boot_dir=/boot
sudo cp "$boot_dir/config.txt" "$boot_dir/config.txt.rackticker-before-matrix"
sudo cp "$boot_dir/cmdline.txt" "$boot_dir/cmdline.txt.rackticker-before-matrix"
if grep -q '^dtparam=audio=' "$boot_dir/config.txt"; then
  sudo sed -i 's/^dtparam=audio=.*/dtparam=audio=off/' "$boot_dir/config.txt"
else
  echo 'dtparam=audio=off' | sudo tee -a "$boot_dir/config.txt" >/dev/null
fi
echo 'blacklist snd_bcm2835' | sudo tee /etc/modprobe.d/rackticker-no-onboard-audio.conf >/dev/null
grep -q 'isolcpus=3' "$boot_dir/cmdline.txt" || sudo sed -i 's/$/ isolcpus=3/' "$boot_dir/cmdline.txt"
sudo update-initramfs -u

echo "Matrix Pi base prepared. Reboot before deploying RackTicker."
