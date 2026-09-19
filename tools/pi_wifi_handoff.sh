#!/bin/bash
# NetworkManager 1.42+ on Raspberry Pi OS. No password in shell history or argv.
set -euo pipefail
if [ "$EUID" -ne 0 ]; then exec sudo -- "$0" "$@"; fi
umask 077
rt_marker=/run/rackticker-wifi-checkpoint
rt_nm=org.freedesktop.NetworkManager
rt_nm_path=/org/freedesktop/NetworkManager

if [ "${1:-}" = confirm ]; then
    [ -f "$rt_marker" ] || { echo 'No pending RackTicker Wi-Fi checkpoint.'; exit 1; }
    read -r rt_checkpoint rt_uuid < "$rt_marker"
    [ "$(nmcli -g GENERAL.CON-UUID device show wlan0)" = "$rt_uuid" ] || {
        echo 'Taylor handoff is not active; leaving rollback in place.'; exit 1;
    }
    # Checkpoint must still exist: never report success after an expired rollback.
    busctl introspect "$rt_nm" "$rt_checkpoint" >/dev/null
    nmcli connection modify uuid "$rt_uuid" connection.autoconnect yes connection.autoconnect-priority 20
    busctl call "$rt_nm" "$rt_nm_path" "$rt_nm" CheckpointDestroy o "$rt_checkpoint"
    rm -f "$rt_marker"
    echo 'New Wi-Fi confirmed. The previous Wi-Fi profile remains as fallback.'
    exit 0
fi

rt_ssid=${1:?Usage: pi_wifi_handoff.sh SSID | confirm}
[ ! -e "$rt_marker" ] || { echo 'A previous handoff marker exists; inspect its checkpoint before retrying.'; exit 1; }
rt_name=rackticker-migration-wifi
if nmcli -g connection.uuid connection show "$rt_name" >/dev/null 2>&1; then
    echo 'Migration profile already exists; inspect it before retrying.'; exit 1
fi
printf 'Joining SSID: %s\n' "$rt_ssid"
read -r -s -p 'Wi-Fi password (hidden): ' rt_password
printf '\n'
if [ "${#rt_password}" -lt 8 ] || [ "${#rt_password}" -gt 64 ]; then
    unset rt_password
    echo 'Expected a WPA personal password of 8–63 characters, or a 64-digit hex key.'; exit 1
fi
if [ "${#rt_password}" -eq 64 ] && [[ ! "$rt_password" =~ ^[0-9A-Fa-f]{64}$ ]]; then
    unset rt_password
    echo 'A 64-character key must be hexadecimal.'; exit 1
fi
rt_secret=$(mktemp /run/rackticker-wifi-secret.XXXXXX)
trap 'rm -f "$rt_secret"' EXIT
printf '802-11-wireless-security.psk:%s\n' "$rt_password" > "$rt_secret"
unset rt_password
nmcli connection add type wifi ifname wlan0 con-name "$rt_name" ssid "$rt_ssid" \
    connection.autoconnect no wifi-sec.key-mgmt wpa-psk
rt_uuid=$(nmcli -g connection.uuid connection show "$rt_name")
rt_device=$(nmcli -g GENERAL.DBUS-PATH device show wlan0)
rt_checkpoint=$(busctl call "$rt_nm" "$rt_nm_path" "$rt_nm" CheckpointCreate aouu 1 "$rt_device" 300 0 | cut -d '"' -f 2)
[[ "$rt_checkpoint" == /org/freedesktop/NetworkManager/Checkpoint/* ]] || {
    echo 'Could not create rollback checkpoint. Wi-Fi has not been switched.'; exit 1;
}
printf '%s %s\n' "$rt_checkpoint" "$rt_uuid" > "$rt_marker"
echo 'Rollback is armed for 5 minutes. SSH may disconnect during the switch.'
echo 'Reconnect to the Pi, verify connectivity, then run this script with: confirm'
if ! nmcli --wait 45 connection up uuid "$rt_uuid" passwd-file "$rt_secret"; then
    busctl call "$rt_nm" "$rt_nm_path" "$rt_nm" CheckpointRollback o "$rt_checkpoint" || true
    rm -f "$rt_marker"
    echo 'Connection failed; rollback requested. Old profile is preserved.'
    exit 1
fi
echo 'Connected. Confirm from a working SSH connection before the checkpoint expires.'
