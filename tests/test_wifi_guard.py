import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('wifi_guard', Path(__file__).resolve().parents[1] / 'deploy/rackticker-wifi-guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class WifiGuardTest(unittest.TestCase):
    def test_only_blocked_broadcom_stacks_count(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder)
            for tid, status, stack in [('10', 'D', 'wait_for_completion\nbrcmf_sdio_dataworker'),
                                       ('11', 'S', 'brcmf_sdio_dataworker'),
                                       ('12', 'D', 'ext4_read')]:
                task = proc / tid / 'task' / tid
                task.mkdir(parents=True)
                (task / 'stat').write_text(f'{tid} (worker with spaces) {status} ' + ' '.join(['0'] * 18 + ['123']))
                (task / 'stack').write_text(stack)
            self.assertEqual(list(guard.blocked_wifi(proc)), ['10/10/123'])

    def test_persistent_same_task_reboots_after_three_minutes(self):
        state, reboot = guard.decide({}, {'task': 'stack'}, 'boot', 100, 1000)
        self.assertFalse(reboot)
        self.assertFalse(guard.decide(state, {'task': 'stack'}, 'boot', 279, 1179)[1])
        self.assertTrue(guard.decide(state, {'task': 'stack'}, 'boot', 280, 1180)[1])

    def test_recovery_new_task_or_new_boot_resets_observation(self):
        state, _ = guard.decide({}, {'task': 'stack'}, 'boot', 100, 1000)
        for tasks, boot in [({}, 'boot'), ({'new-task': 'stack'}, 'boot'), ({'task': 'stack'}, 'new-boot')]:
            self.assertFalse(guard.decide(state, tasks, boot, 400, 1300)[1])
        clear, _ = guard.decide(state, {}, 'boot', 400, 1300)
        self.assertFalse(guard.decide(clear, {'task': 'stack'}, 'boot', 500, 1400)[1])

    def test_cooldown_survives_boot_and_backwards_clock(self):
        previous = {'boot': 'old', 'last_reboot': 10000}
        state, _ = guard.decide(previous, {'task': 'stack'}, 'new', 100, 10100)
        self.assertEqual(state['last_reboot'], 10000)
        for wall in [9000, 10500, 10000 + guard.COOLDOWN_SECONDS - 1]:
            self.assertFalse(guard.decide(state, {'task': 'stack'}, 'new', 500, wall)[1])
        self.assertTrue(guard.decide(state, {'task': 'stack'}, 'new', 500, 10000 + guard.COOLDOWN_SECONDS)[1])

    def test_no_blocked_driver_never_reboots(self):
        self.assertFalse(guard.decide({}, {}, 'boot', 999999, 999999)[1])

    def test_repeat_hang_does_not_wait_six_hours(self):
        previous = {'boot': 'old', 'last_reboot': 10000}
        state, _ = guard.decide(previous, {'task': 'stack'}, 'new', 60, 10120)
        self.assertFalse(guard.decide(state, {'task': 'stack'}, 'new', 240, 10300)[1])
        self.assertTrue(guard.decide(state, {'task': 'stack'}, 'new', 840, 10900)[1])

    @unittest.skipIf(os.name == 'nt', 'Linux guard uses POSIX directory fsync')
    def test_incidents_keep_bounded_history_and_diagnostics(self):
        import json
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(guard, 'STATE', Path(folder) / 'state.json'), \
             mock.patch.object(guard, 'diagnostics', return_value={'kernel': 'test'}):
            for index in range(guard.INCIDENT_LIMIT + 2):
                guard.record_incident(f'boot-{index:02}', {'task': 'stack'}, index)
                os.utime(Path(folder) / 'incidents' / f'boot-{index:02}.json',
                         (1000 + index, 1000 + index))
            files = list((Path(folder) / 'incidents').glob('*.json'))
            self.assertEqual(len(files), guard.INCIDENT_LIMIT)
            self.assertEqual(json.loads((Path(folder) / 'incidents' / 'boot-13.json').read_text())
                             ['diagnostics'], {'kernel': 'test'})
            self.assertFalse((Path(folder) / 'incidents' / 'boot-00.json').exists())

    @unittest.skipIf(os.name == 'nt', 'Linux guard uses POSIX directory fsync')
    def test_cooldown_still_records_incident_once_without_reboot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'boot').write_text('boot')
            state_path = root / 'state.json'
            guard.save(state_path, {'boot': 'boot', 'blocked': {'task': 100},
                                    'last_reboot': 10000})
            real_path = Path

            def paths(path):
                if path == '/proc/sys/kernel/random/boot_id':
                    return root / 'boot'
                if path == '/run/rackticker-install/busy':
                    return root / 'busy'
                return real_path(path)

            with mock.patch.object(guard, 'STATE', state_path), \
                 mock.patch.object(guard, 'Path', paths), \
                 mock.patch.object(guard, 'blocked_wifi', return_value={'task': 'stack'}), \
                 mock.patch.object(guard, 'diagnostics', return_value={}) as capture, \
                 mock.patch.object(guard.time, 'monotonic', return_value=300), \
                 mock.patch.object(guard.time, 'time', return_value=10300), \
                 mock.patch.object(guard.sys, 'argv', ['guard']), \
                 mock.patch.object(guard.subprocess, 'run') as commands:
                guard.main()
                guard.main()
            capture.assert_called_once()
            commands.assert_not_called()
            self.assertTrue((root / 'incidents' / 'boot.json').exists())

    def test_diagnostics_failures_do_not_prevent_recovery(self):
        import subprocess
        with mock.patch.object(guard, 'Path') as path, \
             mock.patch.object(guard.subprocess, 'run', side_effect=[
                 subprocess.TimeoutExpired('journalctl', 10), FileNotFoundError('vcgencmd')
             ]) as commands:
            path.return_value.read_text.side_effect = PermissionError('unavailable')
            evidence = guard.diagnostics()
        self.assertIn('timed out', evidence['kernel_warnings'])
        self.assertIn('vcgencmd', evidence['power_throttling'])
        self.assertTrue(all(call.kwargs['timeout'] <= 10 for call in commands.call_args_list))

    @unittest.skipIf(os.name == 'nt', 'Linux guard uses POSIX directory fsync')
    def test_reboot_saves_evidence_first_even_if_journal_sync_fails(self):
        import json
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'boot').write_text('boot')
            state_path = root / 'state.json'
            guard.save(state_path, {'boot': 'boot', 'blocked': {'task': 100}, 'last_reboot': None})

            def run(args, **kwargs):
                self.assertIsNotNone(json.loads(state_path.read_text())['last_reboot'])
                self.assertEqual(json.loads((root / 'last-failure.json').read_text())['stacks'], {'task': 'brcmf_sdio'})
                if args[0] == 'journalctl':
                    raise subprocess.TimeoutExpired(args, 15)

            with mock.patch.object(guard, 'STATE', state_path), \
                 mock.patch.object(guard, 'Path', lambda path: root / ('boot' if path.startswith('/proc') else 'busy')), \
                 mock.patch.object(guard, 'blocked_wifi', return_value={'task': 'brcmf_sdio'}), \
                 mock.patch.object(guard, 'record_incident'), \
                 mock.patch.object(guard.time, 'monotonic', return_value=300), \
                 mock.patch.object(guard.sys, 'argv', ['guard']), \
                 mock.patch.object(guard.subprocess, 'run', side_effect=run) as commands:
                guard.main()
            self.assertEqual(commands.call_args_list[-1].args[0], ['systemctl', 'reboot', '--no-block'])


if __name__ == '__main__':
    unittest.main()
