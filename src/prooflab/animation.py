"""Optional, fail-closed terminal presentation; no runner or evidence dependencies."""

from importlib.resources import files
import os
import re
import sys
import threading
import time

WIDTH, HEIGHT, FRAME_COUNT, FPS = 32, 36, 24, 12
LOOP_SECONDS = FRAME_COUNT / FPS
MIN_COLUMNS, MIN_LINES = WIDTH + 1, HEIGHT + 1
PREFIX = b"\x1b[?25l\x1b[H"
RESTORE = b"\x1b8"
SAVE = b"\x1b7"
SGR = re.compile(rb"\x1b\[[0-9;]*m")
CELL = re.compile(r"\x1b\[([0-9;]+)m([ ▀▄█])")
# Inclusive half-pixel row spans, derived from the accepted plan.json's fixed
# glass keys (SHA-256 998502a6ff1bec278f72647308784224faa2db49b6710469e2d5bb09422363c9).
# Enclose the cavity as well as the glass: monochrome's intentional black interior
# must survive. Rows 0..9 and 68..71 contain no vessel. No artwork is generated here.
VESSEL_SPANS = (
    ((11, 20), (9, 22), (7, 24), (6, 25), (5, 26), (5, 26), (5, 25), (6, 25))
    + ((7, 24),) * 44
    + ((8, 23), (8, 23), (9, 22), (10, 21), (11, 20), (12, 19))
)


def _exterior(x, half_y):
    if not 10 <= half_y < 68:
        return True
    left, right = VESSEL_SPANS[half_y - 10]
    return not left <= x <= right


def _sprite_cell(match, x, y, mode):
    """Unflatten only empty exterior halves; retain all protected cell bytes.

    The original source metadata establishes that #020B08 is solely the export
    matte, and palette black outside the vessel is absent monochrome artwork.
    The fixed vessel envelope prevents color-keying any internal dark detail.
    """
    parameters, glyph = match.groups()
    values = tuple(map(int, parameters.split(";")))
    if mode == "truecolor":
        if (len(values) != 10 or values[:2] != (38, 2) or values[5:7] != (48, 2)
                or any(not 0 <= v <= 255 for v in values[2:5] + values[7:])):
            raise ValueError("invalid animation colors")
        foreground, background = values[2:5], values[7:]
        matte = (2, 11, 8)
    else:
        if len(values) != 2 or values[0] not in (30, 97) or values[1] not in (40, 107):
            raise ValueError("invalid animation palette")
        foreground, background = values[0], values[1] - 10
        matte = 30
    upper = foreground if glyph in ("▀", "█") else background
    lower = foreground if glyph in ("▄", "█") else background
    clear_upper = upper == matte and _exterior(x, y * 2)
    clear_lower = lower == matte and _exterior(x, y * 2 + 1)
    if not clear_upper and not clear_lower:
        return match.group(0)
    if clear_upper and clear_lower:
        return "\x1b[49m "
    # An upper/lower half block lets the other half use the terminal's actual
    # default background; SGR 39 would incorrectly use the default foreground.
    color = lower if clear_upper else upper
    foreground_sgr = "38;2;" + ";".join(map(str, color)) if mode == "truecolor" else str(color)
    return "\x1b[" + foreground_sgr + ";49m" + ("▄" if clear_upper else "▀")


def load_frames(mode):
    """Read immutable exports and adapt inline placement and exterior transparency.

    The exported hide-cursor/home prefix is inappropriate for inline playback.
    Only empty exterior halves lose the export matte. Intentional half-pixel
    colors, the vessel interior, dimensions and frame order remain unchanged.
    """
    if mode not in ("truecolor", "monochrome"):
        raise ValueError("unsupported animation mode")
    root = files("prooflab").joinpath("animation_frames", mode)
    frames = []
    for number in range(1, FRAME_COUNT + 1):
        raw = root.joinpath(f"frame-{number:02d}.ans").read_bytes()
        if not raw.startswith(PREFIX) or not raw.endswith(b"\x1b[0m"):
            raise ValueError("invalid animation framing")
        body = raw[len(PREFIX):-len(b"\x1b[0m")]
        rows = body.split(b"\r\n")
        if len(rows) != HEIGHT:
            raise ValueError("invalid animation height")
        rendered = []
        for y, row in enumerate(rows):
            text = row.decode("utf-8")
            cells = list(CELL.finditer(text))
            if len(cells) != WIDTH or "".join(c.group(0) for c in cells) != text:
                raise ValueError("invalid animation cells")
            rendered.append("".join(_sprite_cell(cell, x, y, mode)
                                    for x, cell in enumerate(cells)).encode("utf-8"))
        frames.append(RESTORE + b"\x1b[1B\r".join(rendered) + b"\x1b[0m" + RESTORE)
    return tuple(frames)


def terminal_mode(requested, streams, env):
    """Conservative POSIX/UTF-8 ANSI gate; never query or consume terminal input."""
    if requested == "off" or os.name != "posix":
        return None
    if requested not in ("truecolor", "monochrome"):
        return None
    if any(key in env for key in ("CI", "CONTINUOUS_INTEGRATION", "BUILD_NUMBER", "NO_COLOR")):
        return None
    term = env.get("TERM", "")
    if not (term == "xterm" or term.startswith(("xterm-", "screen", "tmux", "rxvt-unicode"))):
        return None
    if not all(stream.isatty() for stream in streams):
        return None
    descriptors = [stream.fileno() for stream in streams]
    if len({os.fstat(fd).st_rdev for fd in descriptors}) != 1:
        return None
    if os.tcgetpgrp(descriptors[2]) != os.getpgrp():
        return None
    encoding = (streams[2].encoding or "").lower().replace("-", "")
    locale = env.get("LC_ALL") or env.get("LC_CTYPE") or env.get("LANG", "")
    if encoding != "utf8" or locale.lower().startswith(("ja", "ko", "zh")):
        return None
    size = os.get_terminal_size(descriptors[2])
    if size.columns < MIN_COLUMNS or size.lines < MIN_LINES:
        return None
    if requested == "truecolor" and not (
        env.get("COLORTERM", "").lower() in ("truecolor", "24bit") or term.endswith("-direct")
    ):
        return "monochrome"
    return requested


class TerminalAnimation:
    """A bounded nonblocking writer, joined before CLI summaries or diagnostics.

    No signal handlers, terminal input modes, alternate screen, or cursor visibility
    changes. An independently opened tty avoids setting O_NONBLOCK on CLI streams.
    All optional setup/loading/I/O happens on the worker, outside the runner path.
    """

    def __init__(self, mode="off"):
        self.mode = mode
        self._stop = threading.Event()
        self._thread = None
        self._done = threading.Event()

    def __enter__(self):
        if self.mode != "off":
            try:
                self._thread = threading.Thread(target=self._run, name="prooflab-animation")
                self._thread.start()
            except BaseException as exc:
                self._stop.set()
                # Thread.start can itself be interrupted after creating the
                # worker. Join an already-started worker before propagating it.
                if self._thread is not None and self._thread.ident is not None:
                    self.__exit__(type(exc), exc, exc.__traceback__)
                if not isinstance(exc, Exception):
                    raise
                self._thread = None
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread is not None:
            # A second Ctrl-C must not strand the writer while the CLI exits.
            interrupted = False
            while not self._done.is_set():
                try:
                    self._done.wait(0.02)
                except KeyboardInterrupt:
                    interrupted = True
            self._thread.join()
            if interrupted and exc_type is None:
                raise KeyboardInterrupt
        return False

    def _write(self, fd, data):
        """Finish each escape stream, including on stop, but abandon a stalled tty."""
        deadline = time.monotonic() + 0.15
        view = memoryview(data)
        while view:
            try:
                count = os.write(fd, view)
                if count <= 0:
                    return False
                view = view[count:]
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return False
                # An independent wait avoids spinning when the stop event is set.
                threading.Event().wait(0.005)
            if time.monotonic() >= deadline and view:
                return False
        return True

    def _play(self, fd, frames, size):
        start = time.monotonic()
        tick = 0
        while not self._stop.is_set():
            if os.get_terminal_size(fd) != size:
                break
            if not self._write(fd, frames[tick % FRAME_COUNT]):
                break
            tick += 1
            # Skip missed deadlines, never replay a burst of overdue frames.
            tick = max(tick, int((time.monotonic() - start) * FPS) + 1)
            if self._stop.wait(max(0, start + tick / FPS - time.monotonic())):
                break

    def _run(self):
        try:
            self._render()
        finally:
            self._done.set()

    def _render(self):
        fd = None
        reserved = False
        size = None
        try:
            streams = (sys.stdin, sys.stdout, sys.stderr)
            mode = terminal_mode(self.mode, streams, os.environ)
            if mode is None or self._stop.is_set():
                return
            frames = load_frames(mode)
            fd = os.open(os.ttyname(streams[2].fileno()), os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY)
            size = os.get_terminal_size(fd)
            if size.columns < MIN_COLUMNS or size.lines < MIN_LINES or self._stop.is_set():
                return
            # Reserve a private inline block, retaining one row below it. Existing
            # scrollback can move during reservation; frame playback never scrolls.
            if not self._write(fd, b"\r\n" * HEIGHT + f"\x1b[{HEIGHT}A\r".encode() + SAVE):
                return
            reserved = True
            self._play(fd, frames, size)
        except Exception:
            # Presentation failure has no scientific or operational result.
            pass
        finally:
            if fd is not None:
                try:
                    if reserved:
                        cleanup = RESTORE
                        if os.get_terminal_size(fd) == size:
                            cleanup += b"\x1b[49m" + b"\x1b[1B\r".join([b" " * WIDTH] * HEIGHT) + RESTORE
                        self._write(fd, cleanup)
                except Exception:
                    pass
                finally:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
