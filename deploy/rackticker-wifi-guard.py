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
COOLDOWN_SECONDS = 6 * 3600


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
