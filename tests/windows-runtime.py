"""The shipped popup windows, one per output, on a private session bus.

The other runtime tests replace the windows with an Item. Here the whole
Service.qml runs with its Variants of windows on two offscreen outputs; only
the layer-shell PanelWindow becomes a FloatingWindow (offscreen Qt has no
layer shell) and its layer-shell settings go. Destroying a card once logged
"TypeError: Cannot read property 'width' of null" from each snooze button.
This test sends real notifications, lets them expire, and checks what each
window shows and that the log has no such warning."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import testhost
from testhost import hostenv

SOURCE = Path(__file__).resolve().parents[1]
testhost.session(__file__, 180)
base = Path(tempfile.mkdtemp(prefix="foamy-notifications-windows-"))
app = base / "app"
app.mkdir()
for name in ("Commons", "Ui"):
    (app / name).symlink_to(hostenv.root() / "shell" / name, target_is_directory=True)
shutil.copytree(SOURCE, app / "plugin", ignore=shutil.ignore_patterns(".git", "__pycache__"))
service = (SOURCE / "Service.qml").read_text()
assert service.count("PanelWindow {") == 2, "Service.qml no longer has its two layer-shell windows"
service = service.replace("PanelWindow {", "FloatingWindow {")
service = re.sub(r"\n\s*(WlrLayershell\.\w+|exclusionMode):[^\n]*", "", service)
# The popup window fills its output; the sharing prompt (not shown here)
# loses its place at the top right.
for anchors, size in (
    (
        "      anchors {\n        top: true\n        bottom: true\n        left: true\n        right: true\n      }",
        "      implicitWidth: 1920\n      implicitHeight: 1080",
    ),
    (
        "      anchors {\n        top: true\n        right: true\n      }\n      margins {\n"
        "        top: service.barClearance + Style.space(8)\n        right: Style.space(8)\n      }\n",
        "",
    ),
):
    assert anchors in service, "Service.qml no longer places its windows this way"
    service = service.replace(anchors, size)
(app / "plugin/Service.qml").write_text(service)
(app / "shell.qml").write_text("""import QtQuick
import Quickshell
import Quickshell.Io
import "plugin" as Plugin
ShellRoot {
 Plugin.Service { id: notifications }
 IpcHandler {
  target: "windows-test"
  // Each popup window: its output, whether it shows, and its stack's cards.
  function windows(): string {
   var variants = notifications.resources.find(function (item) {
    return item.instances !== undefined && item.instances.length && item.instances[0].selected !== undefined
   })
   return JSON.stringify(variants.instances.map(function (window) {
    var stack = window.contentItem.children.find(function (item) { return item.groups !== undefined && item.waiting !== undefined })
    return { name: window.screen ? window.screen.name : "", visible: window.visible, groups: stack.groups.map(function (group) { return group.key }), cards: stack.count, waiting: stack.waiting }
   }).sort(function (a, b) { return a.name < b.name ? -1 : 1 }))
  }
 }
}
""")
home = base / "home"
home.mkdir()
runtime = base / "runtime"
runtime.mkdir(mode=0o700)
config = home / ".config/omarchy"
config.mkdir(parents=True)
(config / "shell.json").write_text(
    json.dumps({"plugins": [{"id": "foamy.notifications", "maxVisible": 2, "normalTimeoutSec": 2}]})
)
bin_dir = base / "bin"
bin_dir.mkdir()
outputs = [("TEST-1", 0), ("TEST-2", 1920)]
context = base / "context.json"
context.write_text(
    json.dumps(
        {
            "clients": [],
            "monitors": [
                {
                    "name": name,
                    "x": x,
                    "y": 0,
                    "width": 1920,
                    "height": 1080,
                    "scale": 1,
                    "focused": x == 0,
                    "activeWorkspace": {"id": 1},
                }
                for name, x in outputs
            ],
            "cursorpos": {"x": 100, "y": 100},
        }
    )
)
(bin_dir / "hyprctl").write_text(
    "#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n"
    f"data=json.loads(Path({str(context)!r}).read_text())\n"
    "command=next((arg for arg in sys.argv[1:] if arg in data),None)\n"
    "print(json.dumps(data[command]) if command else 'ok')\n"
)
(bin_dir / "hyprctl").chmod(0o755)
screens = base / "screens.json"
screens.write_text(
    json.dumps(
        {
            "screens": [
                {
                    "name": name,
                    "x": x,
                    "y": 0,
                    "width": 1920,
                    "height": 1080,
                    "logicalDpi": 96,
                    "logicalBaseDpi": 96,
                    "dpr": 1,
                }
                for name, x in outputs
            ]
        }
    )
)
env = dict(
    os.environ,
    HOME=str(home),
    XDG_CONFIG_HOME=str(home / ".config"),
    XDG_STATE_HOME=str(home / ".local/state"),
    XDG_RUNTIME_DIR=str(runtime),
    QT_QPA_PLATFORM=f"offscreen:configfile={screens}",
    QT_QUICK_BACKEND="software",
    QT_QPA_PLATFORMTHEME="basic",
    QT_STYLE_OVERRIDE="Fusion",
)
testhost.make(base, app, env, session_bus=os.environ["DBUS_SESSION_BUS_ADDRESS"])
env["PATH"] = str(bin_dir) + ":" + env["PATH"]
log_path = base / "runtime.log"
log = log_path.open("w")
process = hostenv.ProcessGroup(["quickshell", "-p", str(app), "--no-color"], env=env, stdout=log, stderr=log)


def ipc(target, *args):
    result = subprocess.run(
        ["qs", "ipc", "-n", "-p", str(app), "call", target, *args], env=env, capture_output=True, text=True, timeout=10
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def windows():
    return json.loads(ipc("windows-test", "windows"))


def wait_for(predicate, message, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if predicate():
                return
        except (ValueError, RuntimeError, subprocess.TimeoutExpired):
            pass
        time.sleep(0.1)
    raise AssertionError(message + "\n" + log_path.read_text())


def state():
    return json.loads(ipc("foamy.notifications", "state"))


def send(summary):
    sent = subprocess.run(
        ["notify-send", "-p", "-a", "Foamy Test", summary, "Sample body"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert sent.returncode == 0 and sent.stdout.strip(), "notify-send failed: " + sent.stderr
    return sent.stdout.strip()


try:
    wait_for(lambda: [w["name"] for w in windows()] == ["TEST-1", "TEST-2"], "a popup window for each output")
    assert all(not w["visible"] and w["cards"] == 0 for w in windows()), windows()
    for summary in ("First", "Second", "Third"):
        send(summary)
        time.sleep(0.3)
    # The focused output shows the service's cards, in its order, and the
    # count of the one that waits (maxVisible 2); the other shows nothing.
    wait_for(lambda: len(state()["groups"]) == 2 and len(state()["queue"]) == 1, "two cards show and one waits")
    shown = [group["key"] for group in state()["groups"]]
    wait_for(
        lambda: (
            windows()
            == [
                {"name": "TEST-1", "visible": True, "groups": shown, "cards": 2, "waiting": 1},
                {"name": "TEST-2", "visible": False, "groups": [], "cards": 0, "waiting": 1},
            ]
        ),
        "TEST-1 shows " + repr(shown) + " with one waiting, TEST-2 nothing",
    )
    # They expire (normalTimeoutSec 2), the waiting one shows and expires too:
    # every card is destroyed and the window unmaps.
    wait_for(
        lambda: (
            windows()
            == [
                {"name": "TEST-1", "visible": False, "groups": [], "cards": 0, "waiting": 0},
                {"name": "TEST-2", "visible": False, "groups": [], "cards": 0, "waiting": 0},
            ]
        ),
        "every card expired and the windows unmapped",
        timeout=20,
    )
    warnings = [line for line in log_path.read_text().splitlines() if "TypeError" in line]
    assert not warnings, "the popup windows logged:\n" + "\n".join(warnings[:6])
    print("PASS: each output's window shows its cards and the waiting count, maps and unmaps with no TypeError")
finally:
    process.stop()
    log.close()
    print("Runtime log:", log_path)
    hostenv.check(env, log_path.read_text(), testhost.calls(base))
testhost.passed(base)
