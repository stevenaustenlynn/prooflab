"""Presentation-only qualification: exact exports, deterministic gates, real PTYs."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from prooflab import animation as a
from prooflab.cli import main
from prooflab.protocol import IntegrityState
from prooflab.verify import verify_package
from tests.test_runner import make_project

ROOT = Path(__file__).resolve().parents[1]
# Pinned to the accepted export's asset-identities.json; no authoring path dependency.
ASSET_HASHES = {
    "monochrome/frame-01.ans": "3195d365067873668d7643486adaa53ff13f5c1f80f126060aeeb674a5e8d7af",
    "monochrome/frame-02.ans": "4a0c2081603cdb12b2bec10e85ca8502c1a691d98053b95960cdc1bd5144c7c2",
    "monochrome/frame-03.ans": "b1460d7cac3c4e8ed9c3bdf21d09dc170c1059df92c5ba2f0f79a6f382d7581a",
    "monochrome/frame-04.ans": "97774d556acdf6838dcb038dcdb599886c5a354ebb5e1410af0ff5ea31028bec",
    "monochrome/frame-05.ans": "64f82276e6fd508acd354ee8b73410b2afdc31fb7c5f6957d64cd584003a8aff",
    "monochrome/frame-06.ans": "01e3efdc4301fe4151acbcbfbc3b586530b1b06fecfc118adbf045f68698a9d7",
    "monochrome/frame-07.ans": "3982f5d9f5cad85f7f721d3cc4959149dc00841ff71414d2a21cb025d37489c4",
    "monochrome/frame-08.ans": "c16d010e1863838d5030c29312e7f20fcb4832dbc91c3ce191b42686fd5ea426",
    "monochrome/frame-09.ans": "bc21491e41ac66df59014b46ffe9d434634c97ed9b7ec9434d91d8c111bfa544",
    "monochrome/frame-10.ans": "aaef9e2dd7f745f22e80a7ac2c1748da6bab06fa3c6566b390993426588f4a92",
    "monochrome/frame-11.ans": "7c5c467c4297bc6337ef811d06e37a764c9eea566d1e69d0ed308b391d81eb71",
    "monochrome/frame-12.ans": "9131a5947519497623e9fa35d57b81b2151fd5ba0f27bb3013c34a5556d4866f",
    "monochrome/frame-13.ans": "adf600b1a158d2b76167c146601118198ada38dd8c72f6eb69b9fc8bdecfee9b",
    "monochrome/frame-14.ans": "791bf69aa1b759239995bc92079e4524bd11878bee9e809a87b695edeb5ce2d7",
    "monochrome/frame-15.ans": "52f9f2f977a5bae8d1cd7df6d71c581aea7a5512d0fcc82d9312338762bb0171",
    "monochrome/frame-16.ans": "9dc7840abd864d8aba9f9d5034667a4aa55072e896183cb926e21201b018ccef",
    "monochrome/frame-17.ans": "8f95be787fc22cbf5e0ed59b83228d4e4b694749ead1323e34b642db55b18555",
    "monochrome/frame-18.ans": "566b4d5b828613f0aca36d8fc73710b1dc711a06ee14c04236c754847c2e2bb9",
    "monochrome/frame-19.ans": "2843b5391ff6693d6b7fdfdfce3b8afa9374d6ac97c3efb970b1d8c4873de5cf",
    "monochrome/frame-20.ans": "50ba5ae1d7024ec8d7f36ad3e207054b9faefe71cf41ff99a866361c918d48c5",
    "monochrome/frame-21.ans": "c638ffde518588bb7a6273eafbbcc0f76f0f028f0ee828131957b6db4c011830",
    "monochrome/frame-22.ans": "199754f122f2a7a0e886b00f6827f0c0de9293498df9ec88e21a6b5d99e13438",
    "monochrome/frame-23.ans": "367b721f43263f8fc099cca2818a0153bb6c9af7869ca12accadc6b6b80a9ac6",
    "monochrome/frame-24.ans": "42a6d70a9f5abef99823b479a429bb2e01295cb00475b4405cbf60654daac3f8",
    "truecolor/frame-01.ans": "e98d565970a35814749afe462edc18bb7a8656af9615a5008bb88d7cfbb9ca06",
    "truecolor/frame-02.ans": "e799b8e3bc38a062d1cbff24771516d7ae940dfe2b2bf8873861bb3913092921",
    "truecolor/frame-03.ans": "0af34fecdf71a1a47870bb54d5bce4976d10caa799935c38244c2da02f34fd6b",
    "truecolor/frame-04.ans": "0148e2696ef10623d383f527de09c4ed91afcec82d6dbba93611fc7047d47655",
    "truecolor/frame-05.ans": "0cda4850eb1e7dc1357042dbeec8c4b8bc8023d48d1e758fc411fb24016b8d7a",
    "truecolor/frame-06.ans": "b3c1d619ded25f6d8acbc4d1f488c0d9ed26c4f348a919a7600c786121ea916c",
    "truecolor/frame-07.ans": "2d44f7c870b2f6e7db0ee35a751a410bd91855763724c2d72d606d0bd509f359",
    "truecolor/frame-08.ans": "4d588cd49bac31b9a70d9300238b9b2bff87bc84b18e75084351401b987534f2",
    "truecolor/frame-09.ans": "38a8925c411471ee7d4f91bbab0ea2e4b6e56564d4b8f5c3bc5ffa43b01ce170",
    "truecolor/frame-10.ans": "149ca65a4b3f2833b4f3325f5a3b24369c3d56a6001dcd3efc5d79037dea5cc6",
    "truecolor/frame-11.ans": "f45e68b9c4c6a3d94d32297ddfcdb53ea011c0fe1e293a2c1a136a6ce49c298f",
    "truecolor/frame-12.ans": "39b56acc32e76ac807da042e6add67a3ac5602313540f5afce2e04486ba095ea",
    "truecolor/frame-13.ans": "81297f882fa15ecd9d386ce461a26528afdc8c382d003c417e45d7aa7b24aab1",
    "truecolor/frame-14.ans": "df734f85256dc0ffe77e33e76c81bacc077b2168153c1a88da8b3cdcb88efdd4",
    "truecolor/frame-15.ans": "ec273370dac41e557b04d83d402f331bcd9489d7c610e51d4e434c9c06eca60e",
    "truecolor/frame-16.ans": "a00951ca1568eafea9eac651c440e098ce7bece5d552a2b0748982522550640d",
    "truecolor/frame-17.ans": "341a0ce2d200a90a092c2087130c0e0c0817faaef3a0fd7f7f50195e6cb16f3f",
    "truecolor/frame-18.ans": "cab5051afec1b60b249897187750c9a4c39261cbee1945a08f26cae361668676",
    "truecolor/frame-19.ans": "52532112f706f9d41ffcde3d8acc163e237a66eba7c532a1df2bc381d0eee992",
    "truecolor/frame-20.ans": "e7578579807245722ad0d80800eae1682c86a6524a49c16979de364b547b3a50",
    "truecolor/frame-21.ans": "2a886a7229e8551b223dac3e5a2dbeb0497d11cbf6ae0bee537574321a9f90f2",
    "truecolor/frame-22.ans": "4e7c7b8d955a55462c6d6a314cae9eb246aaea4fa07f815afd7cae30185b966b",
    "truecolor/frame-23.ans": "60d093cb230a8f6c905814185d7a4fa9ad7699f5041b3c3bee89becc78c9919d",
    "truecolor/frame-24.ans": "345d306594549ab7ed26869c7d4dc325a30ba43b3531e1109960520d2d79b9ad"
}


class ResourceTests(unittest.TestCase):
    def test_exact_accepted_exports_and_both_modes(self):
        for mode in ('truecolor', 'monochrome'):
            frames = a.load_frames(mode)
            self.assertEqual(len(frames), 24)
            for i, frame in enumerate(frames, 1):
                name = f'{mode}/frame-{i:02d}.ans'
                raw = a.files('prooflab').joinpath('animation_frames', name).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), ASSET_HASHES[name])
                body = frame[len(a.RESTORE):-len(a.RESTORE)].replace(b'\x1b[1B\r', b'\r\n')
                self.assertNotEqual(a.PREFIX + body, raw)  # Exterior matte is presentation-only.
                rows = body.split(b'\r\n')
                self.assertEqual(len(rows), 36)
                self.assertTrue(all(len(a.SGR.sub(b'', row).decode()) == 32 for row in rows))

    def test_timing_metadata(self):
        self.assertEqual((a.WIDTH, a.HEIGHT, a.FRAME_COUNT, a.FPS, a.LOOP_SECONDS), (32, 36, 24, 12, 2))

    def test_invalid_mode(self):
        with self.assertRaises(ValueError):
            a.load_frames('compact')

    def test_corrupt_resource_fails_closed(self):
        with patch.object(a, 'files') as files:
            files.return_value.joinpath.return_value.joinpath.return_value.read_bytes.return_value = b'bad'
            with self.assertRaises(ValueError):
                a.load_frames('truecolor')


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.streams = [Mock(encoding='utf-8') for _ in range(3)]
        for stream in self.streams:
            stream.isatty.return_value = True
            stream.fileno.return_value = 2
        self.env = {'TERM': 'xterm-256color', 'COLORTERM': 'truecolor', 'LANG': 'C.UTF-8'}
        for name, value in [('fstat', Mock(st_rdev=1)), ('tcgetpgrp', os.getpgrp()),
                            ('get_terminal_size', os.terminal_size((80, 40)))]:
            p = patch.object(a.os, name, return_value=value)
            p.start(); self.addCleanup(p.stop)

    def mode(self, requested='truecolor'):
        return a.terminal_mode(requested, self.streams, self.env)

    def test_default_off(self):
        self.assertIsNone(self.mode('off'))

    def test_explicit_truecolor(self):
        self.assertEqual(self.mode(), 'truecolor')

    def test_explicit_monochrome(self):
        self.assertEqual(self.mode('monochrome'), 'monochrome')

    def test_truecolor_falls_back(self):
        self.env.pop('COLORTERM')
        self.assertEqual(self.mode(), 'monochrome')

    def test_non_tty_each_stream(self):
        for stream in self.streams:
            stream.isatty.return_value = False
            self.assertIsNone(self.mode())
            stream.isatty.return_value = True

    def test_ci_and_no_color(self):
        for key in ('CI', 'CONTINUOUS_INTEGRATION', 'BUILD_NUMBER', 'NO_COLOR'):
            self.env[key] = ''
            self.assertIsNone(self.mode())
            self.assertIsNone(self.mode('monochrome'))
            del self.env[key]

    def test_dumb_unknown_or_missing_term(self):
        for term in ('dumb', '', 'unknown'):
            self.env['TERM'] = term
            self.assertIsNone(self.mode())

    def test_size_boundary(self):
        for cols, rows, allowed in [(32, 37, False), (33, 36, False), (0, 0, False), (33, 37, True)]:
            with patch.object(a.os, 'get_terminal_size', return_value=os.terminal_size((cols, rows))):
                self.assertEqual(self.mode() is not None, allowed)

    def test_unsafe_encoding_or_ambiguous_locale(self):
        self.streams[2].encoding = 'ascii'
        self.assertIsNone(self.mode())
        self.streams[2].encoding = 'utf-8'
        self.env['LC_CTYPE'] = 'ja_JP.UTF-8'
        self.assertIsNone(self.mode())

    def test_background_process_suppressed(self):
        with patch.object(a.os, 'tcgetpgrp', return_value=-1):
            self.assertIsNone(self.mode())

    def test_different_terminals_suppressed(self):
        with patch.object(a.os, 'fstat', side_effect=[Mock(st_rdev=i) for i in (1, 1, 2)]):
            self.assertIsNone(self.mode())


class LifecycleTests(unittest.TestCase):
    def test_off_has_no_thread_or_terminal_access(self):
        with patch.object(a.threading, 'Thread') as thread, patch.object(a, 'terminal_mode') as gate:
            with a.TerminalAnimation():
                pass
            thread.assert_not_called(); gate.assert_not_called()

    def test_interruption_during_thread_start_joins_worker(self):
        real_start = threading.Thread.start
        def interrupted_start(thread):
            real_start(thread)
            raise KeyboardInterrupt
        with patch.object(a.threading.Thread, 'start', interrupted_start), patch.object(a, 'terminal_mode', return_value=None):
            with self.assertRaises(KeyboardInterrupt):
                with a.TerminalAnimation('monochrome'):
                    self.fail('interrupted entry must not execute body')
        self.assertFalse(any(t.name == 'prooflab-animation' for t in threading.enumerate()))

    def test_active_worker_is_joined_on_body_exception(self):
        entered = threading.Event()
        def rendering(renderer):
            entered.set()
            renderer._stop.wait(1)
        with patch.object(a.TerminalAnimation, '_render', rendering):
            with self.assertRaises(KeyboardInterrupt):
                with a.TerminalAnimation('truecolor') as renderer:
                    self.assertTrue(entered.wait(1))
                    raise KeyboardInterrupt
        self.assertTrue(renderer._done.is_set())
        self.assertFalse(renderer._thread.is_alive())

    def test_monotonic_schedule_wrap_and_wait(self):
        renderer = a.TerminalAnimation()
        now = [100.0]
        waits, emitted = [], []
        def wait(delay):
            waits.append(delay); now[0] += delay
            return len(waits) == 26
        renderer._stop = Mock()
        renderer._stop.is_set.return_value = False
        renderer._stop.wait.side_effect = wait
        renderer._write = lambda fd, data: emitted.append(data) or True
        size = os.terminal_size((80, 40))
        with patch.object(a.time, 'monotonic', side_effect=lambda: now[0]), patch.object(a.os, 'get_terminal_size', return_value=size):
            renderer._play(2, tuple(range(24)), size)
        self.assertEqual(emitted, list(range(24)) + [0, 1])
        self.assertTrue(all(abs(delay - 1/12) < 1e-10 for delay in waits))

    def test_resize_stops_before_next_frame(self):
        renderer = a.TerminalAnimation()
        renderer._write = Mock()
        with patch.object(a.os, 'get_terminal_size', return_value=os.terminal_size((20, 20))):
            renderer._play(2, (b'frame',), os.terminal_size((80, 40)))
        renderer._write.assert_not_called()

    def test_stalled_writer_is_bounded(self):
        renderer = a.TerminalAnimation()
        with patch.object(a.os, 'write', side_effect=BlockingIOError), patch.object(a.time, 'monotonic', side_effect=[0, 1]):
            self.assertFalse(renderer._write(2, b'data'))

    def test_partial_writes_finish(self):
        renderer = a.TerminalAnimation()
        with patch.object(a.os, 'write', side_effect=[2, 2]) as write:
            self.assertTrue(renderer._write(2, b'data'))
        self.assertEqual(write.call_count, 2)

    def test_worker_exception_never_changes_result(self):
        with patch.object(a, 'terminal_mode', side_effect=RuntimeError('broken terminal')):
            with a.TerminalAnimation('truecolor') as renderer:
                renderer._done.wait(1)
        self.assertFalse(renderer._thread.is_alive())

    def test_render_exception_cleanup_and_close(self):
        renderer = a.TerminalAnimation('monochrome')
        renderer._play = Mock(side_effect=RuntimeError('render failure'))
        renderer._write = Mock(return_value=True)
        with patch.object(a, 'terminal_mode', return_value='monochrome'), patch.object(a, 'load_frames', return_value=(b'frame',)), patch.object(a.os, 'ttyname', return_value='/dev/test'), patch.object(a.os, 'open', return_value=99), patch.object(a.os, 'get_terminal_size', return_value=os.terminal_size((80, 40))), patch.object(a.os, 'close') as close:
            renderer._render()
        close.assert_called_once_with(99)
        self.assertTrue(renderer._write.call_args.args[1].endswith(a.RESTORE))
        self.assertNotIn(b'?25', b''.join(call.args[1] for call in renderer._write.call_args_list))

    def test_cli_preserves_exit_codes_and_runner_arguments(self):
        for mode in ('off', 'truecolor', 'monochrome'):
            for code in (0, 3, 4):
                result = Mock(sealed=code != 4, run_id='run', variant='only', planned=1,
                              counts={'completed': int(code == 0)}, directory='package', reason=None, exit_code=code)
                with patch('prooflab.cli.run_experiment', return_value=result) as run, patch.object(a, 'terminal_mode', return_value=None), contextlib.redirect_stdout(io.StringIO()) as out:
                    args = ['run', 'experiment.toml'] + (['--animation', mode] if mode != 'off' else [])
                    self.assertEqual(main(args), code)
                run.assert_called_once_with('experiment.toml', None, project_root=None)
                self.assertNotIn('\x1b', out.getvalue())
        self.assertFalse(any(t.name == 'prooflab-animation' for t in threading.enumerate()))

    def test_configuration_error_and_keyboard_interrupt_propagation(self):
        for error in (ValueError(), KeyboardInterrupt()):
            with patch('prooflab.cli.run_experiment', side_effect=error), patch.object(a, 'terminal_mode', return_value=None), contextlib.redirect_stderr(io.StringIO()):
                if isinstance(error, KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        main(['run', 'missing', '--animation', 'monochrome'])
                else:
                    self.assertEqual(main(['run', 'missing', '--animation', 'monochrome']), 2)
        self.assertFalse(any(t.name == 'prooflab-animation' for t in threading.enumerate()))


@unittest.skipUnless(os.name == 'posix', 'PTY qualification requires POSIX')
class PlaybackTests(unittest.TestCase):
    def playback(self, mode, experiment='ok', interrupt=False, dimensions=(40, 80)):
        import fcntl
        import pty
        import select
        import struct
        import termios
        with tempfile.TemporaryDirectory(prefix='prooflab-animation-') as temporary:
            root = Path(temporary)
            path = make_project(root, cases=(('first', 1, experiment),), timeout=1 if experiment == 'timeout' and not interrupt else None)
            if experiment == 'ok':
                (root / 'program.py').write_text('import time\nprint("accepted output", flush=True)\ntime.sleep(2.2)\n')
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', *dimensions, 0, 0))
            before = termios.tcgetattr(slave)
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), TERM='xterm-256color', COLORTERM='truecolor', LC_ALL='C.UTF-8', PYTHONDONTWRITEBYTECODE='1')
            for key in ('CI', 'CONTINUOUS_INTEGRATION', 'BUILD_NUMBER', 'NO_COLOR'):
                env.pop(key, None)
            process = subprocess.Popen([sys.executable, '-B', '-m', 'prooflab', 'run', str(path), '--animation', mode], stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True, preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
            output = bytearray(); sent = False; started = time.monotonic()
            try:
                while process.poll() is None:
                    if time.monotonic() - started > 10:
                        self.fail('bounded playback timed out')
                    if select.select([master], [], [], .05)[0]:
                        output.extend(os.read(master, 65536))
                    if interrupt and not sent and b'\x1b8' in output and time.monotonic() - started > .4:
                        os.kill(process.pid, signal.SIGINT); sent = True
                while select.select([master], [], [], .05)[0]:
                    output.extend(os.read(master, 65536))
                self.assertEqual(termios.tcgetattr(slave), before)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)
                os.close(master); os.close(slave)
            expected = 3 if experiment != 'ok' or interrupt else 0
            self.assertEqual(process.returncode, expected, bytes(output[-2000:]))
            packages = list((root / '.prooflab/runs').iterdir())
            self.assertEqual(len(packages), 1)
            package = packages[0]
            self.assertEqual(verify_package(package).state, IntegrityState.VERIFIED)
            for file in package.rglob('*'):
                if file.is_file():
                    self.assertNotIn(b'\x1b', file.read_bytes(), str(file))
            run = json.loads((package / 'run.json').read_text())
            self.assertEqual(run['acceptance_state'], 'not_applicable')
            self.assertEqual(run['execution_state'], 'interrupted' if interrupt else {'ok': 'completed', 'fail': 'failed', 'timeout': 'timed_out'}[experiment])
            if dimensions == (40, 80):
                frames = a.load_frames(mode)
                self.assertIn(frames[0], output)
                self.assertNotIn(b'\x1b[?25', output)
                if experiment == 'ok':
                    for frame in frames:
                        self.assertIn(frame, output)
                    self.assertGreaterEqual(output.count(frames[0]), 2)
                summary = bytes(output).split(b'SEALED', 1)[1]
                self.assertNotIn(b'\x1b', summary)
                self.assertIn(a.RESTORE, bytes(output).split(b'SEALED', 1)[0][-10:])
            else:
                self.assertNotIn(b'\x1b', output)
            return bytes(output)

    def test_truecolor_actual_loop_and_evidence(self):
        self.playback('truecolor')

    def test_monochrome_actual_loop_and_evidence(self):
        self.playback('monochrome')

    def test_nonzero_exit_cleanup(self):
        self.playback('monochrome', 'fail')

    def test_timeout_cleanup(self):
        self.playback('truecolor', 'timeout')

    def test_ctrl_c_cleanup(self):
        self.playback('monochrome', 'timeout', interrupt=True)

    def test_small_terminal_no_escapes(self):
        self.playback('truecolor', 'fail', dimensions=(24, 80))


if __name__ == '__main__':
    unittest.main()
