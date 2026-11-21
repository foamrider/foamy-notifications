"""Exercise feature delivery with real D-Bus traffic and a private PipeWire server."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import testhost
from testhost import hostenv

if "--desktop" in sys.argv:
    testhost.desktop_opt_in(__file__)
testhost.session(__file__, 60, *sys.argv[1:])
source = Path(__file__).resolve().parents[1]
base = Path(tempfile.mkdtemp(prefix="foamy-features-runtime-"))
app = base / "app"
app.mkdir()
home = base / "home"
home.mkdir()
runtime = base / "runtime"
runtime.mkdir(mode=0o700)
host = hostenv.root()
for name in ["Commons", "Ui"]:
    (app / name).symlink_to(host / "shell" / name, target_is_directory=True)
shutil.copytree(source, app / "plugin", ignore=shutil.ignore_patterns(".git", "__pycache__"))
service = (source / "Service.qml").read_text()
# The copied code is cleared after 60 s; 300 ms here, so the test sees the clear.
service = service.replace(
    "    id: clipboardExpiry\n    interval: 60000\n", "    id: clipboardExpiry\n    interval: 300\n", 1
)
assert "    id: clipboardExpiry\n    interval: 300\n" in service, "the clipboard expiry timer moved"
desktop = "--desktop" in sys.argv
if desktop:
    # Retain the real layer surfaces and capture the sharing prompt after detection.
    service = service.replace("  id: service", "  id: service\n  property var sharingTestCard: null", 1)
    service = service.replace(
        "Component.onCompleted: service.sharingPromptHeight = implicitHeight",
        "Component.onCompleted: { service.sharingPromptHeight = implicitHeight; service.sharingTestCard = sharingCard }",
        1,
    )
else:
    service = service[: service.index("  Variants {")] + "}\n"
(app / "plugin/Service.qml").write_text(service)
(app / "shell.qml").write_text("""import QtQuick
import Quickshell
import Quickshell.Io
import "plugin" as Plugin
ShellRoot {
 Plugin.Service { id: notifications }
 IpcHandler { target: "feature-test"
  function snooze(): string { return notifications.snoozeSource("app:Chat", 30) }
  function offer(kind: string, value: string): void { notifications.featureAction(notifications.displayGroups[0], kind, value) }
  function state(): string { return JSON.stringify({sharing:notifications.sharingActive,offer:notifications.sharingOfferPending,clipboard:notifications.clipboardSecret,entries:notifications.popupModel.count}) }
  function capture(): void { notifications.sharingTestCard.grabToImage(function(result) { result.saveToFile(Qt.resolvedUrl("sharing.png").toString().replace("file://", "")) }) }
  function shortSnooze(): void { notifications.snoozes = {"app:Chat": Date.now()+500}; notifications.quietNow = Date.now() }
 }
}""")
config = home / ".config/omarchy"
config.mkdir(parents=True)
(config / "shell.json").write_text(json.dumps({"plugins": [{"id": "foamy.notifications", "dndSilenceCritical": True}]}))
cli = base / "bin"
cli.mkdir()
# The clipboard script uses actual stdin and exit statuses; these stand-ins
# cannot touch the real clipboard. Each wl-copy call is logged after it is done,
# so the test reads a whole copy once its log line exists.
(cli / "wl-copy").write_text(
    "#!/bin/bash\n"
    "case ${1:-} in\n"
    '  --sensitive) value=$(cat) && printf %s "$value" > "$HOME/copied" && echo "copy $value" >> "$HOME/clipboard.log" ;;\n'
    '  --clear) : > "$HOME/copied" && echo clear >> "$HOME/clipboard.log" ;;\n'
    "  *) exit 2 ;;\n"
    "esac\n"
)
(cli / "wl-paste").write_text('#!/bin/bash\ncat "$HOME/copied"\n')
for name in ("wl-copy", "wl-paste"):
    (cli / name).chmod(0o755)
env = dict(
    os.environ,
    HOME=str(home),
    XDG_CONFIG_HOME=str(home / ".config"),
    XDG_STATE_HOME=str(home / ".local/state"),
    XDG_RUNTIME_DIR=str(runtime),
    QT_QPA_PLATFORM="offscreen",
    QT_QUICK_BACKEND="software",
    QT_QPA_PLATFORMTHEME="basic",
)
for key in ["DISPLAY", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE"]:
    env.pop(key, None)
if desktop:
    env["QT_QPA_PLATFORM"] = "wayland"
    env["QT_QUICK_BACKEND"] = "rhi"
    env["WAYLAND_DISPLAY"] = str(Path(os.environ["XDG_RUNTIME_DIR"]) / os.environ["WAYLAND_DISPLAY"])
pw_config = base / "pipewire.conf"
pw_config.write_text("""context.properties = { core.daemon = true core.name = pipewire-0 }
context.spa-libs = { support.* = support/libspa-support }
context.modules = [
 { name = libpipewire-module-protocol-native }
 { name = libpipewire-module-spa-node-factory }
 { name = libpipewire-module-client-node }
 { name = libpipewire-module-metadata }
 { name = libpipewire-module-access args = { access.socket = { pipewire-0 = unrestricted } } }
]
""")
# Host QML/data only: OMARCHY_PATH exposes shell/ and default/; omarchy-* are stubs.
testhost.make(base, app, env, session_bus=os.environ["DBUS_SESSION_BUS_ADDRESS"], display=desktop)
# make() names a dead PipeWire socket; this test runs its own server.
env["PIPEWIRE_REMOTE"] = str(runtime / "pipewire-0")
env["PATH"] = str(cli) + ":" + env["PATH"]
log = (base / "runtime.log").open("w")
pw = hostenv.ProcessGroup(["pipewire", "-c", str(pw_config)], env=env, stdout=log, stderr=log)
proc = None


def start():
    return hostenv.ProcessGroup(["qs", "-p", str(app), "--no-color"], env=env, stdout=log, stderr=log)


def ipc(target, *args):
    return subprocess.check_output(
        ["qs", "ipc", "-n", "-p", str(app), "call", target, *args],
        env=env,
        text=True,
        timeout=10,
        stderr=subprocess.DEVNULL,
    ).strip()


def state():
    return json.loads(ipc("foamy.notifications", "state"))


def wait(fn, seconds=5, label="condition", detail=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if fn():
                return
        except (ValueError, subprocess.SubprocessError):
            pass
        time.sleep(0.05)
    # Name the failed check and what it saw before the long runtime log.
    seen = "\n" + detail() if detail else ""
    raise AssertionError("timed out: " + label + seen + "\n" + (base / "runtime.log").read_text())


def send(app, title, body="Message"):
    subprocess.run(["notify-send", "-a", app, "-u", "critical", title, body], env=env, check=True, timeout=10)


def files():
    return list([p for p in (home / ".local/state/foamy-notifications").glob("*/*.json")])


try:
    wait(lambda: (runtime / "pipewire-0").exists())
    proc = start()
    wait(lambda: ipc("notifications", "ping") == "ok", 30)
    send("Chat", "Verification code", "Use code 482913")
    wait(lambda: state()["popups"] == 1 and not state()["busy"])
    assert not any("482913" in p.read_text() for p in files()), "code leaked to persisted state"
    # A code the card does not offer is never copied; the offered one is, and
    # the expiry clears it from the clipboard again.
    ipc("feature-test", "offer", "code", "111111")
    ipc("feature-test", "offer", "code", "482913")
    clipboard = home / "clipboard.log"
    wait(
        lambda: clipboard.exists() and clipboard.read_text().splitlines() == ["copy 482913", "clear"],
        label="offered code copied once and cleared",
        detail=lambda: "clipboard.log: " + (clipboard.read_text() if clipboard.exists() else "missing"),
    )
    wait(
        lambda: json.loads(ipc("feature-test", "state"))["clipboard"] == "", label="card shows the clipboard as cleared"
    )
    ipc("feature-test", "snooze")
    wait(lambda: state()["popups"] == 0 and not state()["busy"])
    send("Chat", "Held during snooze")
    wait(lambda: any("Held during snooze" in p.read_text() for p in files()) and not state()["busy"])
    assert state()["popups"] == 0
    send("Mail", "Other sender")
    wait(lambda: state()["popups"] == 1 and not state()["busy"])
    ipc("notifications", "dismissAll")
    wait(lambda: state()["popups"] == 0 and not state()["busy"])
    # Do not disturb and the snooze are saved and come back after a restart.
    ipc("notifications", "setDnd", "on")
    saved = home / ".local/state/foamy-notifications/state.json"
    wait(
        lambda: (
            "app:Chat" in json.loads(saved.read_text()).get("snoozes", {})
            and json.loads(saved.read_text()).get("dnd") is True
        ),
        label="DND and snooze saved to state.json",
        detail=lambda: "state.json: " + (saved.read_text() if saved.exists() else "missing"),
    )
    proc.stop()
    proc = start()
    wait(
        lambda: "app:Chat" in state()["snoozes"] and state()["doNotDisturb"],
        30,
        label="DND and snooze restored after restart",
    )
    ipc("notifications", "setDnd", "off")
    wait(lambda: not state()["doNotDisturb"], label="DND turned off")
    ipc("feature-test", "shortSnooze")
    wait(lambda: not state()["snoozes"])
    send("Chat", "After snooze")
    wait(lambda: state()["popups"] == 1)
    # A protocol-native node exercises the actual Quickshell PipeWire model and tracker.
    subprocess.run(
        [
            "pw-cli",
            "create-node",
            "spa-node-factory",
            "{ factory.name = support.null-audio-sink node.name = foamy-test media.class = Video/Source media.name = xdph-streaming-foamy-test object.linger = true }",
        ],
        env=env,
        check=True,
        timeout=10,
        stdout=subprocess.DEVNULL,
    )
    wait(lambda: json.loads(ipc("feature-test", "state"))["sharing"])
    assert json.loads(ipc("feature-test", "state"))["offer"]
    assert not state()["doNotDisturb"] and state()["popups"] == 1, "sharing must not silently mute or enter history"
    if desktop:
        time.sleep(0.3)
        ipc("feature-test", "capture")
        wait(lambda: (app / "sharing.png").exists())
    ipc("notifications", "snoozeAll", "30")
    wait(lambda: not json.loads(ipc("feature-test", "state"))["offer"])
    send("notify-send", "Critical alert during snooze")
    wait(lambda: any("Critical alert during snooze" in p.read_text() for p in files()) and not state()["busy"])
    assert state()["popups"] == 0
    print(
        "PASS code copy through stdin, 60 s clipboard clear and disk redaction; sender/global snooze, expiry and restart; DND across restart; private PipeWire sharing detection and opt-in prompt"
    )
finally:
    for child in [proc, pw]:
        if child:
            child.stop()
    log.close()
    print("Evidence:", base)
    hostenv.check(env, (base / "runtime.log").read_text(), testhost.calls(base))
