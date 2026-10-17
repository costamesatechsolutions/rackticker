# Wi-Fi driver recovery

The panel can keep running while the Broadcom Wi-Fi driver is blocked in the
kernel. A saved IP address and default route can remain present, even though the
control page, SSH and data feeds are unreachable. The display watchdog and the
hardware watchdog do not detect this partial failure.

## Detection and recovery

`rackticker-wifi-guard.timer` runs an independent check every minute, starting
five minutes after boot. It reads `/proc` without asking the radio or
NetworkManager to respond. A task must be in uninterruptible sleep (`D`), with
`brcmf_` in its kernel stack, across observations for at least three minutes.
Task IDs include the process start ticks, and observations reset at every boot.

When that condition persists, the guard saves the kernel stacks and requests a
normal system reboot. Recovery is limited to once per six hours, with the last
request saved across reboots. An installation in progress defers recovery.
DNS failures, an unavailable router, and lost internet access alone do not
trigger a reboot. This guard addresses a specific driver hang; it does not
detect every possible network failure or establish its underlying cause.

## Support checks

On the device:

```sh
sudo python3 /usr/local/lib/rackticker/wifi-guard.py --check
systemctl list-timers rackticker-wifi-guard.timer
journalctl -u rackticker-wifi-guard.service
sudo cat /var/lib/rackticker-wifi-guard/last-failure.json
journalctl -b -1 -k
```

`--check` is read-only. `last-failure.json` exists only after an automatic
recovery request. Kernel logs are retained separately by journald.
To disable automatic recovery, run
`sudo systemctl disable --now rackticker-wifi-guard.timer`.

## Validation and release status

The October 17, 2026 incident on a Pi 3A+ running RackTicker 1.10.0 and kernel
6.12.96 showed an `mmc1` hardware interrupt timeout followed by blocked
`brcmf_sdio_dataworker` and NetworkManager tasks. Earlier recoverable SDIO errors
were also present. Wi-Fi power saving was disabled. These observations do not
establish a faulty board or rule out workload, firmware, kernel, power or thermal
contributions.

The guard is being evaluated on that device. Automated tests cover the blocked
stack filter, persistence threshold, task/boot changes, cooldown and durable
evidence before reboot. A healthy-device check verifies the installed service
and timer. Recovery from a real recurrence has not yet been demonstrated.
Retain uptime, kernel errors and recovery records during the soak before
claiming this resolves the incident. Unit tests are not a substitute for that
field validation.
