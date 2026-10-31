# Wi-Fi driver recovery

The panel can keep running while the Broadcom Wi-Fi driver is blocked in the
kernel. A saved IP address and default route can remain present, even though the
control page, SSH and data feeds are unreachable. The display watchdog and the
hardware watchdog do not detect this partial failure.

## Detection and recovery

`rackticker-wifi-guard.timer` runs an independent check every minute, starting
one minute after boot. It reads `/proc` without asking the radio or
NetworkManager to respond. A task must be in uninterruptible sleep (`D`), with
`brcmf_` in its kernel stack, across observations for at least three minutes.
Task IDs include the process start ticks, and observations reset at every boot.

When that condition persists, the guard saves the kernel stacks and requests a
normal system reboot. Recovery is limited to once per fifteen minutes, with the last
request saved across reboots. An installation in progress defers recovery.
DNS failures, an unavailable router, and lost internet access alone do not
trigger a reboot. This guard addresses a specific driver hang; it does not
detect every possible network failure or establish its underlying cause.

The reboot target has a two-minute job deadline. If blocked tasks prevent a clean
shutdown, systemd forces reboot after that deadline rather than waiting through
each service's stop timeout. This fallback can interrupt outstanding writes; the
guard durably saves evidence and requests journal sync before starting shutdown.
The last twelve incident files in `/var/lib/rackticker-wifi-guard/incidents/`
preserve kernel warnings, blocked stacks, SDIO configuration, temperature, memory,
and interrupts. Evidence is captured even while cooldown delays recovery, without
querying the radio. The latest reboot request remains in `last-failure.json`.

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
and timer. The original guard recovered from real recurrences; recovery with the
revised cooldown and shutdown deadline still needs field validation.
Retain uptime, kernel errors and recovery records during the soak before
claiming this resolves the incident. Unit tests are not a substitute for that
field validation.

On October 30–31 the same device failed again at 20:19, shortly after an automatic
reboot requested at 20:10. The six-hour cooldown delayed the next request until
02:10; blocked services delayed reboot until 02:23. This establishes that the
guard detects real failures, but its original cooldown prolonged the outage.
The fifteen-minute cooldown and two-minute shutdown deadline address those
observed delays, not the underlying SDIO failure.

After another recurrence on October 31, the revised guard and reboot deadline
were installed directly on that Pi with backups under
`/var/lib/rackticker-wifi-guard/pre-oct31-fix/`. These recovery changes ship in
RackTicker 1.10.1. All 267 repository tests passed; on-device unit
verification and a healthy guard check passed. The official Raspberry Pi kernel
packages were updated from 6.12.96 to 6.12.109, and a normal reboot confirmed the
new kernel, active display services, and HTTP 200 from the API and remote control
page. This is a stability trial, not evidence that the underlying hang is fixed.
Power saving was off, firmware integrity checks passed, and the earlier healthy
radio sample had a -52 dBm signal. A post-install temperature sample was 60.7 C
with soft-temperature-limit flags; earlier samples were 57.5 C with no throttle
flags. Those samples do not establish temperature or power as the cause of the
recorded hangs.
