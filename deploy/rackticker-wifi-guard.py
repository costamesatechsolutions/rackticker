#!/usr/bin/python3
"""Recover a wedged Broadcom Wi-Fi driver without probing the broken radio.

The display and PID 1 can stay healthy while brcmfmac is stuck in SDIO and
NetworkManager still reports the old IP. Neither existing watchdog sees that.
Only a persistently blocked kernel stack inside brcmfmac triggers recovery;
DNS failures, unreachable routers and missing Wi-Fi never do.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

STATE = Path('/var/lib/rackticker-wifi-guard/state.json')
HANG_SECONDS = 180
COOLDOWN_SECONDS = 15 * 60
INCIDENT_LIMIT = 12


def blocked_wifi(proc=Path('/proc')):
    found = {}
    for process in proc.glob('[0-9]*'):
        for task in (process / 'task').glob('[0-9]*'):
            try:
                stat = (task / 'stat').read_text()
                fields = stat[stat.rfind(')') + 2:].split()
                if fields[0] != 'D':
                    continue
                stack = (task / 'stack').read_text()
                if 'brcmf_' in stack:
                    # Include start ticks to distinguish reused task IDs.
                    found[f'{process.name}/{task.name}/{fields[19]}'] = stack
            except (OSError, IndexError):
                continue
    return found


def decide(previous, blocked, boot, now, wall):
    same_boot = previous.get('boot') == boot
    old = previous.get('blocked', {}) if same_boot else {}
    since = {key: old.get(key, now) for key in blocked}
    last = previous.get('last_reboot')
    state = {'boot': boot, 'blocked': since, 'last_reboot': last}
    persistent = any(now - start >= HANG_SECONDS for start in since.values())
    # A backwards clock jump also suppresses recovery until the cooldown passes.
    cooled = last is None or wall - last >= COOLDOWN_SECONDS
    return state, persistent and cooled


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def diagnostics():
    """Capture local evidence without querying the potentially wedged radio."""
    result = {}
    for name, path in {
        'kernel': '/proc/version', 'cmdline': '/proc/cmdline',
        'memory': '/proc/meminfo', 'interrupts': '/proc/interrupts',
        'softirqs': '/proc/softirqs', 'load': '/proc/loadavg',
        'sdio': '/sys/kernel/debug/mmc1/ios',
        'temperature': '/sys/class/thermal/thermal_zone0/temp',
    }.items():
        try:
            result[name] = Path(path).read_text()[:32768]
        except OSError as exc:
            result[name] = str(exc)
    try:
        log = subprocess.run(['journalctl', '-b', '0', '-k',
                              '--grep=mmc|brcmf|voltage|thrott|thermal|blocked for|Out of memory',
                              '-n', '150', '--no-pager'], capture_output=True,
                             text=True, timeout=10, check=False)
        result['kernel_warnings'] = log.stdout[-65536:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['kernel_warnings'] = str(exc)
    try:
        power = subprocess.run(['/usr/bin/vcgencmd', 'get_throttled'],
                               capture_output=True, text=True, timeout=3, check=False)
        result['power_throttling'] = power.stdout.strip() or power.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['power_throttling'] = str(exc)
    return result


def record_incident(boot, blocked, wall):
    # One file per boot preserves earlier incidents when last-failure is replaced.
    directory = STATE.parent / 'incidents'
    save(directory / f'{boot}.json', {'time': wall, 'boot': boot,
                                     'stacks': blocked, 'diagnostics': diagnostics()})
    for old in sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime,
                      reverse=True)[INCIDENT_LIMIT:]:
        old.unlink()


def main():
    blocked = blocked_wifi()
    if '--check' in sys.argv:
        print(json.dumps({'blocked_wifi_tasks': blocked, 'read_only': True}, indent=2))
        return
    try:
        previous = json.loads(STATE.read_text())
    except FileNotFoundError:
        previous = {}
    # Invalid state must fail closed, never discard a reboot cooldown.
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    state, reboot = decide(previous, blocked, boot, time.monotonic(), time.time())
    persistent = any(time.monotonic() - start >= HANG_SECONDS
                     for start in state['blocked'].values())
    if persistent and not (STATE.parent / 'incidents' / f'{boot}.json').exists():
        record_incident(boot, blocked, time.time())
        print('Persistent Broadcom Wi-Fi hang; diagnostic evidence saved', flush=True)
    if persistent and not reboot:
        remaining = max(0, COOLDOWN_SECONDS - (time.time() - state['last_reboot']))
        print(f'Wi-Fi recovery cooldown: {remaining:.0f} seconds remaining', flush=True)
    if reboot and not Path('/run/rackticker-install/busy').exists():
        state['last_reboot'] = time.time()
        save(STATE.parent / 'last-failure.json', {'time': state['last_reboot'],
                                                'boot': boot, 'stacks': blocked})
        save(STATE, state)  # durable before requesting a restart
        print('Broadcom Wi-Fi driver blocked for 3 minutes; requesting reboot', flush=True)
        try:
            subprocess.run(['journalctl', '--sync'], timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f'journal sync failed: {exc}; failure stacks are saved', flush=True)
        subprocess.run(['systemctl', 'reboot', '--no-block'], timeout=15, check=True)
    elif state != previous:
        save(STATE, state)


if __name__ == '__main__':
    main()
