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
revision="$3"
trap 'rm -f "$remote_archive" "$0" /tmp/rackticker-install-release.sh' EXIT
actual_sha="$(sha256sum "$remote_archive" | awk '{print $1}')"
if [[ "$actual_sha" != "$expected_sha" ]]; then
  echo "Archive checksum mismatch." >&2
  exit 1
fi
# The same installer the web page's updater uses: checks, switches, and rolls
# back if the new release does not come up healthy.
tar -xzf "$remote_archive" -O deploy/install-release.sh > /tmp/rackticker-install-release.sh
bash /tmp/rackticker-install-release.sh "$remote_archive" "$revision"
echo "RackTicker services are active."
# Feeders are independent of RackTicker (and idle while the SDR is unplugged).
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
ssh -tt "$pi_host" "sudo bash ${remote_script} ${remote_archive} ${archive_sha} ${revision}"
