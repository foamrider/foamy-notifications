"""Save center preferences through the host API (shell.updateEntryInline), offscreen.

The fake shell stores exactly what the host's updateEntryInline would: {id, ...settings}
replaces the plugins[] entry, and an unchanged entry returns false without writing.
XDG_CONFIG_HOME points at a decoy, because the host always reads ~/.config.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import testhost
from testhost import hostenv

testhost.session(__file__, 60)
source = Path(__file__).resolve().parents[1]
base = Path(tempfile.mkdtemp(prefix="foamy-preferences-runtime-"))
app = base / "app"
app.mkdir()
home = base / "home"
home.mkdir()
runtime = base / "runtime"
runtime.mkdir(mode=0o700)
for name in ["Commons", "Ui"]:
    (app / name).symlink_to(hostenv.root() / "shell" / name, target_is_directory=True)
shutil.copytree(source, app / "plugin", ignore=shutil.ignore_patterns(".git", "__pycache__"))
service = (source / "Service.qml").read_text()
(app / "plugin/Service.qml").write_text(service[: service.index("  Variants {")] + "}\n")
(app / "shell.qml").write_text("""import QtQuick
import Quickshell
import Quickshell.Io
import "plugin" as Plugin
ShellRoot {
 FileView { id: hostConfig; path: Quickshell.env("HOME") + "/.config/omarchy/shell.json"; watchChanges: true; onFileChanged: reload() }
 QtObject {
  id: fakeShell
  property bool refuse: false
  property int writes: 0
  function updateEntryInline(id, settings) {
   if (refuse || id !== "foamy.notifications") return false
   var config = JSON.parse(hostConfig.text()), dirty = false
   config.plugins.forEach(function(entry, index) {
    if (entry.id !== id) return
    var next = {id: id}
    for (var key in settings) if (key !== "id") next[key] = settings[key]
    if (JSON.stringify(entry) !== JSON.stringify(next)) { config.plugins[index] = next; dirty = true }
   })
   if (!dirty) return false
   writes++
   hostConfig.setText(JSON.stringify(config, null, 2) + "\\n")
   return true
  }
 }
 Plugin.Service { id: notifications; shell: fakeShell }
 IpcHandler { target: "preferences-test"
  function refuse(on: bool): void { fakeShell.refuse = on }
  function detach(): void { notifications.shell = null }
  function writes(): int { return fakeShell.writes }
  // omarchy-shell calls the service's peer IPC still has in flight (its "running" count).
  function peerCalls(): int {
   for (var i = 0; i < notifications.resources.length; i++) {
    var o = notifications.resources[i]
    if (o && typeof o.call === "function" && o.limit !== undefined) return o.running
   }
   return -1
  }
 }
}""")
config = home / ".config/omarchy"
config.mkdir(parents=True)
stored = {
    "version": 1,
    "bar": {"layout": {"right": [{"id": "foamy.notification-center", "compact": False}]}},
    "plugins": [
        {"id": "other.service", "value": 7},
        {"id": "foamy.notifications", "browserGrouping": "none", "custom": "preserve"},
    ],
}
(config / "shell.json").write_text(json.dumps(stored))
decoy = base / "xdg/omarchy"
decoy.mkdir(parents=True)
(decoy / "shell.json").write_text(
    json.dumps({"plugins": [{"id": "foamy.notifications", "compact": False, "normalTimeoutSec": 99}]})
)
env = dict(
    os.environ,
    HOME=str(home),
    XDG_CONFIG_HOME=str(base / "xdg"),
    XDG_STATE_HOME=str(home / ".local/state"),
    XDG_RUNTIME_DIR=str(runtime),
    QT_QPA_PLATFORM="offscreen",
    QT_QUICK_BACKEND="software",
    QT_QPA_PLATFORMTHEME="basic",
)
for key in ["DISPLAY", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE"]:
    env.pop(key, None)
# Host QML/data only: OMARCHY_PATH exposes shell/ and default/; omarchy-* are stubs.
testhost.make(base, app, env, session_bus=os.environ["DBUS_SESSION_BUS_ADDRESS"])
log = (base / "runtime.log").open("w")
proc = None


def ipc(target, *args):
    return subprocess.check_output(
        ["qs", "ipc", "-n", "-p", str(app), "call", target, *args],
        env=env,
        text=True,
        timeout=10,
        stderr=subprocess.DEVNULL,
    ).strip()


def preferences():
    return json.loads(ipc("foamy.notifications", "preferences"))


def entry():
    return [p for p in json.loads((config / "shell.json").read_text())["plugins"] if p["id"] == "foamy.notifications"][
        0
    ]


def wait(fn, seconds=5, label="condition"):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if fn():
                return
        except (ValueError, subprocess.SubprocessError):
            pass
        time.sleep(0.05)
    # Name the failed check before the long runtime log.
    raise AssertionError("timed out: " + label + "\n" + (base / "runtime.log").read_text())


def center_pokes():
    # The center is told through omarchy-shell, which the test host logs.
    return sum(
        line.endswith(" foamy.notification-center.store peerChanged preferences") for line in hostenv.calls(base)
    )


def refused_on(changed, loaded, expected):
    # The service must have read the new file before the save, or it would judge the old one.
    (config / "shell.json").write_text(json.dumps(changed))
    wait(lambda: loaded(preferences()["settings"]), label="the service read the changed shell.json")
    before, writes = (config / "shell.json").read_bytes(), int(ipc("preferences-test", "writes"))
    assert save("showImages", False) == expected, preferences()
    assert (config / "shell.json").read_bytes() == before and int(ipc("preferences-test", "writes")) == writes, (
        "a refused save changed shell.json"
    )


def save(key, value):
    # Leading space: JSON stays out of the IPC CLI's option parser, as the center sends it.
    assert ipc("foamy.notifications", "setPreference", key, " " + json.dumps(value)) == "saving"
    wait(lambda: not preferences()["saving"])
    return preferences()["saveError"]


try:
    proc = hostenv.ProcessGroup(["qs", "-p", str(app), "--no-color"], env=env, stdout=log, stderr=log)
    wait(lambda: ipc("notifications", "ping") == "ok", 30)
    wait(lambda: preferences()["settings"]["normalTimeoutSec"] == 8)
    assert preferences()["settings"]["compact"] is True, "read the XDG_CONFIG_HOME decoy"
    # The full raw entry is written: unknown keys stay, defaults are not filled in.
    assert save("compact", False) == ""
    assert entry() == {
        "id": "foamy.notifications",
        "browserGrouping": "none",
        "custom": "preserve",
        "compact": False,
    }, entry()
    assert json.loads((config / "shell.json").read_text())["plugins"][0] == stored["plugins"][0]
    wait(lambda: preferences()["settings"]["compact"] is False)
    # Enabling duplicate grouping lifts the legacy browser override.
    assert save("groupDuplicates", True) == ""
    assert entry()["groupDuplicates"] is True and entry()["browserGrouping"] == "browser", entry()
    # The service reloads the written file and pokes the center for it; wait for
    # that reload, so its poke cannot land in the refused-save count below.
    wait(
        lambda: preferences()["settings"]["browserGrouping"] == "browser",
        label="the service reloaded the grouping save",
    )
    # An unchanged value is stored already: the host reports no change, the save succeeds.
    writes = int(ipc("preferences-test", "writes"))
    assert save("compact", False) == ""
    assert int(ipc("preferences-test", "writes")) == writes
    assert ipc("foamy.notifications", "setPreference", "normalTimeoutSec", " 0") == "error: invalid value"
    assert ipc("foamy.notifications", "setPreference", "browserGrouping", ' "none"') == "error: unknown setting"
    before = (config / "shell.json").read_text()
    # The center shows "saving" until it is told the result, so it is told about
    # a refused save too, once (nothing is written, so no reload tells it either).
    wait(lambda: int(ipc("preferences-test", "peerCalls")) == 0, label="no center call in flight")
    pokes = center_pokes()
    ipc("preferences-test", "refuse", "true")
    assert save("showImages", False) == "Could not save the setting"
    ipc("preferences-test", "refuse", "false")
    assert (config / "shell.json").read_text() == before
    wait(
        lambda: center_pokes() > pokes and int(ipc("preferences-test", "peerCalls")) == 0,
        label="the center heard the refused save",
    )
    assert center_pokes() == pokes + 1, hostenv.calls(base)
    # Saving never adds a missing entry.
    (config / "shell.json").write_text(json.dumps({**stored, "plugins": [stored["plugins"][0]]}))
    wait(lambda: preferences()["settings"]["compact"] is True)
    assert save("showImages", False) == "Foamy Notifications is not enabled"
    # Saving never enables a disabled plugin and never picks one of two entries.
    ours = {"id": "foamy.notifications", "lowTimeoutSec": 6}
    refused_on(
        {**stored, "disabledPlugins": ["foamy.notifications"], "plugins": [stored["plugins"][0], ours]},
        lambda settings: settings["lowTimeoutSec"] == 6,
        "Foamy Notifications is not enabled",
    )
    refused_on(
        {**stored, "plugins": [{**ours, "lowTimeoutSec": 7}, {**ours, "showImages": True}]},
        lambda settings: settings["lowTimeoutSec"] == 7,
        "Duplicate Foamy Notifications entries",
    )
    ipc("preferences-test", "detach")
    assert save("showImages", False) == "Omarchy cannot save plugin settings"
    print(
        "PASS center preferences save the full raw entry through shell.updateEntryInline; unchanged, refused, missing, disabled, duplicate and detached saves; the center hears a refused save"
    )
finally:
    if proc:
        proc.stop()
    log.close()
    print("Evidence:", base)
    hostenv.check(env, (base / "runtime.log").read_text(), testhost.calls(base))
