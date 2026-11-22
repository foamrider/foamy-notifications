"""testhost.session(), started the way the runtime tests start: neither its
private bus nor the test it runs gets what points at the live desktop
session, and the bus starts no service, unless a desktop test opts in.
testhost.passed() at the end of a visual test that passed: only its PNG
captures stay, and what the run's links point to is never touched."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import testhost

TESTS = Path(__file__).resolve().parent
# Starts like a runtime test (testhost.session at the top). Inside, it saves
# its environment and asks its bus to start a service that only the
# caller's own data directory defines.
PROBE = """import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, TESTS)
import testhost
if "--desktop" in sys.argv:
    testhost.session(__file__, 30, "--desktop", display=True)
else:
    testhost.session(__file__, 30)
start = subprocess.run(
    ["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus",
     "org.freedesktop.DBus.StartServiceByName", "string:org.foamy.SessionProbe", "uint32:0"],
    capture_output=True, text=True, timeout=10)
Path(OUT).write_text(json.dumps({"env": dict(os.environ), "start": start.returncode}))
"""


class SessionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="foamy-notifications-session-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.out = self.root / "out.json"
        self.started = self.root / "started"
        probe = self.root / "probe.py"
        probe.write_text(PROBE.replace("TESTS", repr(str(TESTS))).replace("OUT", repr(str(self.out))))
        self.probe = probe
        # Made-up values in this test's own directory, never the real session's.
        live = self.root / "live"
        (live / "runtime").mkdir(parents=True)
        self.desktop = {
            "WAYLAND_DISPLAY": "wayland-probe",
            "DISPLAY": ":4242",
            "XAUTHORITY": str(live / "Xauthority"),
            "HYPRLAND_INSTANCE_SIGNATURE": "probe",
            "XDG_RUNTIME_DIR": str(live / "runtime"),
        }
        self.session = {
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(live / "bus"),
            "XDG_SESSION_ID": "4242",
            "XDG_SEAT": "seat-probe",
            "SSH_AUTH_SOCK": str(live / "ssh-agent"),
            "KRB5CCNAME": "FILE:" + str(live / "krb5cc"),
            "GNOME_KEYRING_CONTROL": str(live / "keyring"),
            "PULSE_SERVER": "unix:" + str(live / "pulse"),
            "PIPEWIRE_REMOTE": str(live / "pipewire-0"),
            "TMUX": str(live / "tmux") + ",1,0",
            "ALACRITTY_SOCKET": str(live / "alacritty.sock"),
            "NOTIFY_SOCKET": str(live / "notify"),
        }
        # A session bus that reads the caller's data directories would start
        # this service, which saves the environment it was given. It exits
        # 1, so that the bus answers the start at once.
        services = self.root / "data/dbus-1/services"
        services.mkdir(parents=True)
        service = self.root / "service"
        service.write_text(f"#!/bin/sh\nenv > '{self.started}'\nexit 1\n")
        service.chmod(0o755)
        (services / "org.foamy.SessionProbe.service").write_text(
            f"[D-BUS Service]\nName=org.foamy.SessionProbe\nExec={service}\n"
        )

    def run_probe(self, *args, opt_in=False):
        env = dict(os.environ, **self.desktop, **self.session, XDG_DATA_HOME=str(self.root / "data"))
        env.pop("FOAMY_TEST_DESKTOP", None)
        if opt_in:
            env["FOAMY_TEST_DESKTOP"] = "1"
        return subprocess.run(
            [sys.executable, str(self.probe), *args], env=env, capture_output=True, text=True, timeout=60
        )

    def child(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(self.out.read_text())

    def assertNoService(self, started):
        self.assertFalse(self.started.exists(), "the private bus started a service of the caller's")
        self.assertNotEqual(started, 0, "the private bus accepted StartServiceByName")

    def assertNotLive(self, env, names):
        live = {**self.desktop, **self.session}
        self.assertEqual(
            [name for name in names if env.get(name) == live[name]], [], "live session variables reached the child"
        )

    def test_the_test_and_its_bus_get_nothing_of_the_live_session(self):
        data = self.child(self.run_probe())
        self.assertNotLive(data["env"], [*self.desktop, *self.session])
        self.assertTrue(data["env"].get("XDG_RUNTIME_DIR"), "the child has no runtime directory of its own")
        self.assertNoService(data["start"])

    def test_a_desktop_test_that_opts_in_keeps_only_the_desktop(self):
        data = self.child(self.run_probe("--desktop", opt_in=True))
        self.assertEqual({name: data["env"].get(name) for name in self.desktop}, self.desktop)
        self.assertNotLive(data["env"], list(self.session))
        self.assertNoService(data["start"])

    def test_a_desktop_test_without_the_opt_in_is_refused(self):
        result = self.run_probe("--desktop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FOAMY_TEST_DESKTOP=1", result.stderr)
        self.assertFalse(self.out.exists(), "the test ran without the opt-in")
        self.assertFalse(self.started.exists())


class PassedTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="foamy-notifications-passed-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        # The user's files that a run links to: the plugin checkout and the host shell.
        self.outside = {
            "checkout/preview.png": b"README picture",
            "checkout/components/NotificationCard.qml": b"Item {}",
            "shell/Commons/Color.qml": b"QtObject {}",
        }
        for name, data in self.outside.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_bytes(data)
        # A run directory as the visual tests leave it. Captures lie in the
        # app folder beside the links (render, browser-ui, motion,
        # images-ui) or in a copy of the plugin beside its own PNG (layer).
        self.base = Path(tempfile.mkdtemp(prefix="foamy-notifications-run-", dir=self.root))
        self.app = self.base / "app"
        files = {
            "qs.log": b"log",
            "runtime/bus": b"",
            "stub-bin/hyprctl": b"#!/bin/sh\n",
            "home/.local/state/foamy-notifications/images/1-image.png": b"converted image, not a capture",
            "app/shell.qml": b"ShellRoot {}",
            "app/avatar.svg": b"<svg/>",
            "app/capture-0.png": b"first capture",
            "app/capture-1.png": b"second capture",
            "app/copy/Service.qml": b"Item {}",
            "app/copy/preview.png": b"README picture",
            "app/copy/entrance.png": b"entrance capture",
        }
        for name, data in files.items():
            (self.base / name).parent.mkdir(parents=True, exist_ok=True)
            (self.base / name).write_bytes(data)
        (self.app / "plugin").symlink_to(self.root / "checkout", target_is_directory=True)
        (self.app / "Commons").symlink_to(self.root / "shell/Commons", target_is_directory=True)

    def passed(self, captures):
        with contextlib.redirect_stdout(io.StringIO()):
            testhost.passed(self.base, captures)

    def left(self):
        """Every entry still in the run directory; links are listed, not followed."""
        found = []
        for folder, dirs, files in os.walk(self.base):
            found += [str(Path(folder, name).relative_to(self.base)) for name in dirs + files]
        return sorted(found)

    def assertOutsideUntouched(self):
        self.assertEqual(
            {name: (self.root / name).read_bytes() for name in self.outside if (self.root / name).exists()},
            self.outside,
            "removing the run reached the files its links point to",
        )

    def test_a_pass_keeps_only_the_png_captures(self):
        self.passed([*self.app.glob("*.png"), self.app / "copy/entrance.png"])
        self.assertOutsideUntouched()
        self.assertEqual(
            self.left(),
            ["app", "app/capture-0.png", "app/capture-1.png", "app/copy", "app/copy/entrance.png"],
            "a passing run keeps its PNG captures and nothing else",
        )
        self.assertEqual((self.app / "capture-1.png").read_bytes(), b"second capture")
        self.assertEqual((self.app / "copy/entrance.png").read_bytes(), b"entrance capture")

    def test_a_pass_without_its_captures_leaves_nothing(self):
        # layer.py names its captures, but a run can pass without taking them.
        self.passed([self.app / "copy/exit.png"])
        self.assertFalse(self.base.exists(), "a passing run without captures left its directory")
        self.assertOutsideUntouched()


if __name__ == "__main__":
    unittest.main()
