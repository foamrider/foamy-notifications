"""Runs the notification helper as a process, against fakes only (black-box).

FOAMY_HELPER is the helper's argv prefix as a JSON list of absolute paths, e.g.
  FOAMY_HELPER='["/path/to/bin/aarch64/foamy-notifications-helper"]'
The default is this machine's bundled binary, bin/<machine>/foamy-notifications-helper. Each Sandbox has its own HOME and
XDG directories and a PATH that holds only a fake `hyprctl` (plus `magick` and
`file` when a test asks for them), so nothing reaches the live Hyprland session
or the user's notification state.
"""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]


def prefix():
    if os.environ.get('FOAMY_HELPER'):
        return json.loads(os.environ['FOAMY_HELPER'])
    return [str(ROOT / 'bin' / platform.machine() / 'foamy-notifications-helper')]


# Answers `hyprctl -j <what>` from <what>.json beside it and logs every call.
# `dispatch` fails when dispatch-fail-lua (hl.dsp.*) or dispatch-fail-legacy
# (focuswindow) exists.
HYPRCTL = '''#!PYTHON
import json, os, sys
here = os.path.dirname(os.path.abspath(sys.argv[0]))
args = sys.argv[1:]
with open(os.path.join(here, 'hyprctl.log'), 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[:1] == ['-j'] and len(args) == 2:
    path = os.path.join(here, args[1] + '.json')
    sys.stdout.write(open(path).read() if os.path.exists(path) else '[]')
elif args[:1] == ['dispatch'] and len(args) > 1:
    if args[1].startswith('hl.dsp.') and os.path.exists(os.path.join(here, 'dispatch-fail-lua')):
        sys.exit(1)
    if args[1] == 'focuswindow' and os.path.exists(os.path.join(here, 'dispatch-fail-legacy')):
        sys.exit(1)
else:
    sys.exit(2)
'''


def emulated():
    """Whether the helper is an ELF for another CPU (run through qemu-user,
    which ignores RLIMIT_AS for the programs it emulates)."""
    machines = {'x86_64': 62, 'aarch64': 183}
    try:
        with open(prefix()[0], 'rb') as binary:
            header = binary.read(20)
    except OSError:
        return False
    if header[:4] != b'\x7fELF':
        return False
    return int.from_bytes(header[18:20], 'little') != machines.get(platform.machine())


def from_uri(uri):
    return Path(unquote(urlparse(uri).path))


class Sandbox:
    def __init__(self, tools=False):
        self.temp = tempfile.TemporaryDirectory(prefix='foamy-notifications-helper-')
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.state = self.root / 'state'
        self.bin = self.root / 'bin'
        self.tmp = self.root / 'tmp'
        for folder in (self.home, self.state, self.bin, self.tmp):
            folder.mkdir()
        (self.bin / 'hyprctl').write_text(HYPRCTL.replace('PYTHON', sys.executable))
        (self.bin / 'hyprctl').chmod(0o755)
        self.path = [str(self.bin)]
        if tools:
            self.tools()
        self.extra = {}

    def tools(self):
        """Puts the real `file` and `magick` on PATH (image conversion)."""
        folder = self.root / 'tools'
        folder.mkdir(exist_ok=True)
        for name in ('file', 'magick'):
            real = shutil.which(name)
            if real and not (folder / name).exists():
                (folder / name).symlink_to(real)
        self.path.append(str(folder))

    def cleanup(self):
        self.temp.cleanup()

    @property
    def env(self):
        return {'PATH': ':'.join(self.path), 'HOME': str(self.home), 'XDG_STATE_HOME': str(self.state),
                'XDG_CONFIG_HOME': str(self.root / 'config'), 'XDG_CACHE_HOME': str(self.root / 'cache'),
                'TMPDIR': str(self.tmp), **self.extra}

    @property
    def base(self):
        return self.state / 'foamy-notifications'

    @property
    def popups(self):
        return self.base / 'popups'

    @property
    def history(self):
        return self.base / 'history'

    @property
    def images(self):
        return self.base / 'images'

    def hypr(self, what, value):
        (self.bin / (what + '.json')).write_text(json.dumps(value))

    def calls(self):
        log = self.bin / 'hyprctl.log'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def dispatches(self):
        return [call[1:] for call in self.calls() if call[:1] == ['dispatch']]

    def clear_calls(self):
        (self.bin / 'hyprctl.log').unlink(missing_ok=True)

    def run(self, op, data=None, raw=None, timeout=10, args=None):
        """One-shot call: `<helper> <op>` with `data` as JSON on stdin."""
        stdin = raw if raw is not None else json.dumps({} if data is None else data).encode()
        return subprocess.run(prefix() + (args if args is not None else [op]), input=stdin, capture_output=True,
                              env=self.env, timeout=timeout, cwd=self.root)

    def ok(self, op, data=None, **kwargs):
        result = self.run(op, data, **kwargs)
        assert result.returncode == 0, (op, result.returncode, result.stderr)
        return result.stdout.decode()

    def serve(self, jobs, raw=None, timeout=20):
        """`<helper> serve` with one JSON line per job; returns the parsed replies."""
        stdin = raw if raw is not None else ''.join(json.dumps(job) + '\n' for job in jobs).encode()
        result = subprocess.run(prefix() + ['serve'], input=stdin, capture_output=True, env=self.env,
                                timeout=timeout, cwd=self.root)
        assert result.returncode == 0, (result.returncode, result.stderr)
        return [json.loads(line) for line in result.stdout.decode().splitlines()]

    def job(self, op, data=None):
        """One serve job; returns its reply."""
        replies = self.serve([{'id': 1, 'op': op, 'data': data or {}}])
        assert len(replies) == 1, replies
        return replies[0]

    def write(self, entry, **data):
        return self.ok('write', dict(data, entry=entry))

    def saved(self, key, history=False):
        return json.loads(((self.history if history else self.popups) / (key + '.json')).read_text())
