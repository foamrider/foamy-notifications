"""Flood real private-bus notifications while the actual persistence worker is stalled."""

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import testhost
from testhost import hostenv

from gi.repository import Gio, GLib

SOURCE = Path(__file__).resolve().parents[1]
testhost.session(__file__, 300)
base = Path(tempfile.mkdtemp(prefix="foamy-popup-queue-"))
app = base / "app"
app.mkdir()
for name in ("Commons", "Ui"):
    (app / name).symlink_to(hostenv.root() / "shell" / name, target_is_directory=True)
shutil.copytree(SOURCE, app / "plugin", ignore=shutil.ignore_patterns(".git", "__pycache__"))
service = (SOURCE / "Service.qml").read_text()
# Keep the real service/server/queue; presentation has separate desktop coverage.
(app / "plugin/Service.qml").write_text(
    (service[: service.index("  Variants {")] + "}\n").replace(
        "tracked: Object.keys(service.liveRefs).length",
        "nativeTracked: server.trackedNotifications.values.length, tracked: Object.keys(service.liveRefs).length",
    )
)
(app / "shell.qml").write_text('import Quickshell\nimport "plugin" as Plugin\nShellRoot { Plugin.Service {} }\n')
# Wrap the real native worker: selected operations stall while a flag file exists.
helper = app / "plugin/bin" / platform.machine() / "foamy-notifications-helper"
helper.rename(helper.with_name("foamy-notifications-helper-real"))
stall = base / "stall"
started = base / "started"
context_stall = base / "context-stall"
context_started = base / "context-started"
# Holds how many more serve jobs the worker dies on, without a reply.
crash = base / "crash"
# While it exists, every context query fails at once.
context_fail = base / "context-fail"
# One "start <time>" or "crash <time>" line per event; CLOCK_MONOTONIC is
# system-wide, so the times of different worker processes compare.
serve_events = base / "serve-events"
# Stall selected writes/context queries, not startup restoration. The timeout also protects failed tests.
helper.write_text(
    "#!"
    + sys.executable
    + """
import json, os, subprocess, sys, time
from pathlib import Path
real = str(Path(__file__).with_name('foamy-notifications-helper-real'))
def stall(flag, started, seconds):
    if not Path(flag).exists():
        return True
    Path(started).touch()
    deadline = time.monotonic() + seconds
    while Path(flag).exists() and time.monotonic() < deadline:
        time.sleep(.02)
    return not Path(flag).exists()
def event(kind):
    with open(SERVE_EVENTS, 'a') as stream:
        stream.write(kind + ' ' + repr(time.monotonic()) + '\\n')
def crash_now():
    try:
        left = int(Path(CRASH).read_text())
    except (OSError, ValueError):
        return False
    if left <= 0:
        return False
    Path(CRASH).write_text(str(left - 1))
    return True
if sys.argv[1:] == ['serve']:
    event('start')
    worker = subprocess.Popen([real, 'serve'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    for line in sys.stdin.buffer:
        # Die like a crashed helper: no reply, no output; the real worker sees EOF and exits.
        if crash_now():
            event('crash')
            os._exit(1)
        try:
            job = json.loads(line)
        except ValueError:
            job = {}
        if isinstance(job, dict) and job.get('op') == 'write' and not stall(STALL, STARTED, 30):
            reply = {'id': job.get('id'), 'ok': False, 'out': '', 'error': 'test worker stall timed out'}
            sys.stdout.write(json.dumps(reply) + '\\n'); sys.stdout.flush()
            continue
        worker.stdin.write(line); worker.stdin.flush()
        sys.stdout.buffer.write(worker.stdout.readline()); sys.stdout.flush()
    worker.stdin.close()
    sys.exit(worker.wait())
if sys.argv[1:] == ['context'] and Path(CONTEXT_FAIL).exists():
    sys.exit(1)
if sys.argv[1:] == ['context'] and not stall(CONTEXT_STALL, CONTEXT_STARTED, 15):
    print('foamy.notifications: test worker stall timed out', file=sys.stderr); sys.exit(1)
os.execv(real, [real] + sys.argv[1:])
""".replace("CONTEXT_FAIL", repr(str(context_fail)))
    .replace("SERVE_EVENTS", repr(str(serve_events)))
    .replace("CRASH", repr(str(crash)))
    .replace("CONTEXT_STALL", repr(str(context_stall)))
    .replace("CONTEXT_STARTED", repr(str(context_started)))
    .replace("STALL", repr(str(stall)))
    .replace("STARTED", repr(str(started)))
)
helper.chmod(0o755)
home = base / "home"
home.mkdir()
config = home / ".config/omarchy"
config.mkdir(parents=True)
(config / "shell.json").write_text(
    json.dumps(
        {
            "bar": {"layout": {"right": [{"id": "foamy.notification-center"}]}},
            "plugins": [{"id": "foamy.notifications", "maxVisible": 3}],
        }
    )
)
runtime = base / "runtime"
runtime.mkdir(mode=0o700)
cli = base / "bin"
cli.mkdir()
(cli / "hyprctl").write_text('#!/bin/sh\nprintf "[]\\n"\n')
(cli / "hyprctl").chmod(0o755)
env = dict(
    os.environ,
    HOME=str(home),
    XDG_CONFIG_HOME=str(home / ".config"),
    XDG_STATE_HOME=str(base / "state"),
    XDG_RUNTIME_DIR=str(runtime),
    PATH=str(cli) + ":" + os.environ["PATH"],
    QT_QPA_PLATFORM="offscreen",
    QT_QUICK_BACKEND="software",
    QT_QPA_PLATFORMTHEME="basic",
    QT_STYLE_OVERRIDE="Fusion",
)
for key in ["DISPLAY", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE"]:
    env.pop(key, None)
cli_path = env["PATH"].split(":")[0]
testhost.make(base, app, env, session_bus=os.environ["DBUS_SESSION_BUS_ADDRESS"])
env["PATH"] = cli_path + ":" + env["PATH"]
log = (base / "runtime.log").open("w")
process = hostenv.ProcessGroup(["quickshell", "-p", str(app), "--no-color"], env=env, stdout=log, stderr=log)


def ipc(target, method, *args):
    return subprocess.check_output(
        ["qs", "ipc", "-n", "-p", str(app), "call", target, method, *map(str, args)],
        env=env,
        text=True,
        stderr=subprocess.DEVNULL,
        timeout=10,
    ).strip()


def state():
    return json.loads(ipc("foamy.notifications", "state"))


def wait(check, label, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (ValueError, subprocess.SubprocessError):
            pass
        if process.exit_status() is not None:
            raise AssertionError("Shell exited: " + label)
        time.sleep(0.04)
    raise AssertionError(label + " " + json.dumps(state()))


def send(title, icon="foot", replace=0):
    result = subprocess.check_output(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.freedesktop.Notifications",
            "--object-path",
            "/org/freedesktop/Notifications",
            "--method",
            "org.freedesktop.Notifications.Notify",
            "Foamy queue test",
            str(replace),
            icon,
            title,
            "Working text",
            "[]",
            "{'urgency': <byte 2>}",
            "0",
        ],
        env=env,
        text=True,
        timeout=10,
    )
    return int(result.split("uint32 ")[1].split(",")[0])


# A persistent sender delivers valid 4 MiB pixel buffers without command-line size limits.
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
raw = GLib.Variant("(iiibiiay)", (1024, 1024, 4096, True, 8, 4, bytes([40, 80, 120, 255]) * (1024 * 1024)))


def send_buffer(title, alias="image-data", replace=0, file_hint=""):
    hints = {"urgency": GLib.Variant("y", 2)}
    if alias:
        hints[alias] = raw
    if file_hint:
        hints["image-path"] = GLib.Variant("s", file_hint)
    arguments = GLib.Variant(
        "(susssasa{sv}i)",
        ("Foamy queue test", replace, "foot", title, "Working text", ["default", "Open", "reply", "Reply"], hints, 0),
    )
    return bus.call_sync(
        "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications",
        "Notify",
        arguments,
        GLib.VariantType.new("(u)"),
        Gio.DBusCallFlags.NONE,
        3000,
        None,
    ).unpack()[0]


def notify_then_close(ident, title, icon, hints):
    """Send an in-place update and its CloseNotification without waiting for the first reply."""
    replies = []
    loop = GLib.MainLoop()

    def finished(connection, result, name):
        try:
            connection.call_finish(result)
            replies.append(name)
        except GLib.Error as error:
            replies.append(name + ": " + error.message)
        if len(replies) == 2:
            loop.quit()

    update = GLib.Variant(
        "(susssasa{sv}i)",
        ("Foamy queue test", ident, icon, title, "Working text", [], dict(hints, urgency=GLib.Variant("y", 2)), 0),
    )
    path, interface = "/org/freedesktop/Notifications", "org.freedesktop.Notifications"
    bus.call(
        interface, path, interface, "Notify", update, GLib.VariantType.new("(u)"), 0, 3000, None, finished, "notify"
    )
    bus.call(
        interface,
        path,
        interface,
        "CloseNotification",
        GLib.Variant("(u)", (ident,)),
        None,
        0,
        3000,
        None,
        finished,
        "close",
    )
    loop.run()
    assert replies == ["notify", "close"], replies


def serve_events_after_first_crash():
    events = [line.split() for line in serve_events.read_text().splitlines()] if serve_events.exists() else []
    kinds = [kind for kind, _ in events]
    return [(kind, float(at)) for kind, at in events[kinds.index("crash") :]] if "crash" in kinds else []


def rss():
    return int(
        next(
            line.split()[1]
            for line in Path("/proc/" + str(process.pid) + "/status").read_text().splitlines()
            if line.startswith("VmRSS:")
        )
    )


try:
    wait(lambda: ipc("notifications", "ping") == "ok" and not state()["busy"], "startup", 30)
    ipc("notifications", "setDnd", "on")
    stall.touch()
    send("Stalled DND write")
    wait(started.exists, "worker is stalled")
    for i in range(180):
        send("DND flood " + str(i), "/tmp/" + "x" * 4080)
    first = state()
    first_rss = rss()
    for i in range(180, 360):
        send("DND flood " + str(i), "/tmp/" + "x" * 4080)
    second = state()
    second_rss = rss()
    assert first["queuedJobs"] == second["queuedJobs"] == 64, (first, second)
    assert second["queuedBytes"] <= 786432 and second["tracked"] <= 65, second
    assert "queue is full" in second["lastError"], second
    # Clearing cancels pending DND callbacks; the one running write precedes durable clearing.
    ipc("notifications", "clear")
    wait(lambda: state()["queuedJobs"] == 1 and state()["tracked"] <= 1, "cancelled DND callbacks release")
    stall.unlink()
    wait(lambda: not state()["busy"] and state()["tracked"] == 0, "clear drains without catch-up retries")
    assert not list((base / "state/foamy-notifications/history").glob("*.json"))
    ipc("notifications", "setDnd", "off")
    send("Oversized icon", "x" * 5000)
    wait(lambda: "sender limits" in state()["lastError"], "oversized metadata rejection")
    assert state()["popups"] == state()["tracked"] == state()["queuedJobs"] == 0
    ident = send("Before oversized replacement")
    wait(lambda: not state()["busy"] and state()["popups"] == 1, "valid popup")
    send("Oversized replacement", "x" * 5000, ident)
    wait(lambda: state()["tracked"] == 0, "oversized replacement releases live source")
    assert state()["queuedJobs"] == 0
    # The card stays, and the oversized icon is never stored, not even cut.
    # The refused update is not applied in part either: its new title arrives
    # before its icon, and the saved card keeps the old title.
    wait(lambda: not state()["busy"], "oversized replacement settles")
    refused = state()
    assert refused["popups"] == 1 and refused["lastError"] == "Notification update exceeds sender limits", refused
    saved = [json.loads(p.read_text()) for p in (base / "state/foamy-notifications/popups").glob("*-*.json")]
    assert [(entry["summary"], entry["appIcon"]) for entry in saved] == [("Before oversized replacement", "foot")], (
        saved
    )
    ipc("notifications", "dismissAll")
    wait(lambda: not state()["busy"] and state()["popups"] == 0, "old popup cleanup")
    # A sender may withdraw a notification right after updating it, without
    # waiting for the reply, so both calls reach the shell in one turn of its
    # event loop. The card keeps a valid update, still refuses an oversized
    # one as a whole, and tells the sender once that it closed.
    closed = []
    subscription = bus.signal_subscribe(
        None,
        "org.freedesktop.Notifications",
        "NotificationClosed",
        "/org/freedesktop/Notifications",
        None,
        Gio.DBusSignalFlags.NONE,
        lambda _connection, _sender, _path, _interface, _member, parameters: closed.append(parameters.unpack()),
    )
    for title, icon, hints, kept in [
        ("Updated before close", "foot", {}, ("Updated before close", "foot")),
        ("Oversized before close", "x" * 5000, {}, ("Before close", "foot")),
    ]:
        ident = send("Before close")
        wait(lambda: not state()["busy"] and state()["popups"] == 1, "card before close")
        notify_then_close(ident, title, icon, hints)
        wait(
            lambda: state()["nativeTracked"] == state()["tracked"] == 0 and not state()["busy"],
            "withdrawn card settles",
        )
        saved = [json.loads(p.read_text()) for p in (base / "state/foamy-notifications/popups").glob("*-*.json")]
        assert [(entry["summary"], entry["appIcon"]) for entry in saved] == [kept], saved
        while GLib.MainContext.default().iteration(False):
            pass
        assert [reason for closed_id, reason in closed if closed_id == ident] == [3], closed
        ipc("notifications", "dismissAll")
        wait(lambda: not state()["busy"] and state()["popups"] == 0, "withdrawn card cleanup")
    bus.signal_unsubscribe(subscription)
    started.unlink()
    stall.touch()
    send("Stalled popup write")
    wait(started.exists, "popup worker is stalled")
    for i in range(160):
        send("Popup flood " + str(i))
    popup_first = state()
    for i in range(160, 320):
        send("Popup flood " + str(i))
    popup_second = state()
    for i in range(320, 480):
        send("Popup flood " + str(i))
    popup_third = state()
    # Overflow waits in the queue (at most 100 notifications behind the 3 shown);
    # past that the oldest waiting ones go to history, whose archives share the job bound.
    assert popup_first["queuedJobs"] <= popup_second["queuedJobs"] == popup_third["queuedJobs"] <= 128, (
        popup_first,
        popup_second,
        popup_third,
    )
    assert (
        popup_third["queuedBytes"] <= 1048576 and popup_third["popups"] <= 103 and len(popup_third["queue"]) <= 100
    ), popup_third
    assert popup_third["tracked"] <= 103, popup_third
    popup_second = popup_third
    stall.unlink()
    wait(lambda: not state()["busy"], "bounded popup writes and archives drain")
    ipc("notifications", "dismissAll")
    wait(lambda: not state()["busy"] and state()["tracked"] == 0, "post-flood cleanup")
    send("Normal delivery after overload")
    wait(lambda: not state()["busy"] and state()["popups"] == 1, "normal delivery recovers")
    ipc("notifications", "dismissAll")
    ipc("notifications", "clear")
    wait(lambda: not state()["busy"] and state()["nativeTracked"] == 0, "native cleanup")
    assert state()["centerEnabled"]
    # Small provider URLs must not hide native pixels behind either callback pool.
    for alias in ["image-data", "image_data", "icon_data"]:
        send_buffer("Raw callback omitted " + alias, alias, file_hint="/tmp/missing-fallback.png")
        wait(lambda: not state()["busy"] and state()["nativeTracked"] == 0, "raw release " + alias)
        assert state()["tracked"] == state()["retainedActions"] == 0 and state()["groups"][0]["actions"] == 0, state()
    ipc("notifications", "dismissAll")
    ipc("notifications", "clear")
    wait(lambda: not state()["busy"], "raw cleanup")
    for mode in ["visible", "history", "silenced"]:
        ipc("notifications", "setDnd", "on" if mode == "silenced" else "off")
        identifier = send_buffer("File-only callback " + mode, alias="")
        wait(lambda: state()["tracked"] == 1 and not state()["busy"], "file-only callback " + mode)
        if mode == "history":
            ipc("notifications", "dismissAll")
            wait(lambda: state()["retainedActions"] == 1, "file-only history callback")
        if mode == "silenced":
            assert state()["retainedActions"] == 1
        send_buffer("Raw replacement " + mode, replace=identifier)
        wait(
            lambda: (
                state()["nativeTracked"] == state()["tracked"] == state()["retainedActions"] == 0
                and not state()["busy"]
            ),
            "raw replacement releases " + mode,
        )
        files = (
            list((base / "state/foamy-notifications/popups").glob("*.json"))
            if mode == "visible"
            else list((base / "state/foamy-notifications/history").glob("*.json"))
        )
        assert any(json.loads(p.read_text())["summary"] == "Raw replacement " + mode for p in files), files
        if mode == "visible":
            assert state()["popups"] == 1 and state()["groups"][0]["actions"] == 0, state()
        ipc("notifications", "dismissAll")
        ipc("notifications", "clear")
        wait(lambda: not state()["busy"], "replacement cleanup")
    # Even a blocked first write must release native replacements before it finishes.
    ipc("notifications", "setDnd", "on")
    started.unlink()
    stall.touch()
    identifier = send_buffer("Awaiting first history write", alias="")
    wait(started.exists, "raw test worker stalled")
    send_buffer("Raw during first write", replace=identifier)
    wait(lambda: state()["nativeTracked"] == state()["tracked"] == 0, "release while worker stalled")
    for i in range(50):
        send_buffer("Large raw flood " + str(i))
    wait(lambda: state()["nativeTracked"] == 0, "first raw flood releases")
    raw_first = rss()
    for i in range(50, 100):
        send_buffer("Large raw flood " + str(i))
    wait(lambda: state()["nativeTracked"] == 0, "second raw flood releases")
    raw_second = rss()
    raw_state = state()
    assert raw_state["tracked"] == raw_state["retainedActions"] == 0 and raw_state["queuedJobs"] <= 64, raw_state
    assert raw_second - raw_first < 20 * 1024, (raw_first, raw_second)
    stall.unlink()
    wait(lambda: not state()["busy"] and state()["nativeTracked"] == 0, "raw writes drain")
    assert any(
        json.loads(p.read_text())["summary"] == "Raw during first write"
        for p in (base / "state/foamy-notifications/history").glob("*.json")
    )
    print(
        "Raw 4 MiB buffer plateau:",
        json.dumps(
            {
                "rssKiB": [raw_first, raw_second],
                "nativeTracked": raw_state["nativeTracked"],
                "queuedJobs": raw_state["queuedJobs"],
            }
        ),
    )
    ipc("notifications", "dismissAll")
    ipc("notifications", "clear")
    ipc("notifications", "setDnd", "off")
    wait(lambda: not state()["busy"], "context test cleanup")
    context_stall.touch()
    cfg = json.loads((config / "shell.json").read_text())
    cfg["plugins"][0]["suppressFullscreen"] = True
    (config / "shell.json").write_text(json.dumps(cfg))
    wait(lambda: state()["settings"]["suppressFullscreen"] and context_started.exists(), "context worker stalled")
    identifier = send_buffer("Awaiting compositor context", alias="")
    wait(lambda: state()["pending"] == 1, "native context backlog")
    send_buffer("Raw replacement awaiting context", replace=identifier)
    send_buffer("Raw initial awaiting context")
    wait(lambda: state()["pending"] == 2 and state()["nativeTracked"] == 0, "context retains primitives only")
    context_stall.unlink()
    wait(lambda: state()["pending"] == 0 and not state()["busy"], "detached context delivery")
    files = list((base / "state/foamy-notifications/popups").glob("*.json"))
    assert {json.loads(p.read_text())["summary"] for p in files} == {
        "Raw replacement awaiting context",
        "Raw initial awaiting context",
    }, files
    # A context query that fails still delivers the card and reports the failure.
    context_fail.touch()
    send("Context failure delivers")
    wait(
        lambda: (
            state()["pending"] == 0
            and not state()["busy"]
            and state()["lastError"] == "Cannot query monitor state; notifications use the last known monitor"
            and "Context failure delivers"
            in {
                json.loads(p.read_text())["summary"] for p in (base / "state/foamy-notifications/popups").glob("*.json")
            }
        ),
        "a failed context query delivers the card",
    )
    context_fail.unlink()
    # A helper that really dies: the job in flight fails once and is not
    # replayed, restarts back off (1 s, then 2 s, never a 1 Hz loop), and the
    # cards still show while persistence is down.
    ipc("notifications", "dismissAll")
    ipc("notifications", "clear")
    cfg["plugins"][0]["suppressFullscreen"] = False
    (config / "shell.json").write_text(json.dumps(cfg))
    wait(
        lambda: not state()["settings"]["suppressFullscreen"] and not state()["busy"] and state()["popups"] == 0,
        "crash test setup",
    )
    crash.write_text("2")
    # The history read is the job in flight when the helper dies first; the
    # first popup write after the restart kills it again.
    ipc("notifications", "showHistory")
    for i in (1, 2, 3):
        send("Crash " + str(i))
    wait(
        lambda: len(serve_events_after_first_crash()) == 4 and not state()["busy"] and state()["popups"] == 3,
        "the helper restarts after two crashes",
        15,
    )
    events = serve_events_after_first_crash()
    assert [kind for kind, _ in events] == ["crash", "start", "crash", "start"], events
    assert events[1][1] - events[0][1] >= 0.9, ("restarted too soon after the first crash", events)
    assert events[3][1] - events[2][1] >= 1.8, ("the restart delay did not double", events)
    # Failed once, no replay: only the three new cards are on screen.
    assert state()["popups"] == 3, state()
    log.flush()
    read_failures = (base / "runtime.log").read_text().count("foamy.notifications: Could not read notification history")
    assert read_failures == 1, read_failures
    send("After crash")
    wait(
        lambda: (
            "After crash"
            in {
                json.loads(p.read_text())["summary"] for p in (base / "state/foamy-notifications/popups").glob("*.json")
            }
        ),
        "persistence works again after the restart",
        12,
    )
    ipc("notifications", "dismissAll")
    ipc("notifications", "clear")
    wait(lambda: not state()["busy"], "crash test cleanup")
    assert process.exit_status() is None
    log.flush()
    output = (base / "runtime.log").read_text()
    # The private XDG_RUNTIME_DIR has no PipeWire socket; the screen-share prompt's
    # PipeWire import logs that as an ERROR line, which is expected here.
    lines = [line for line in output.splitlines() if "Failed to connect pipewire context" not in line]
    for unexpected in ["Invalid notification image result", "callback failed", "ReferenceError", "TypeError", "ERROR"]:
        assert not any(unexpected in line for line in lines), output
    print(
        "PASS: real stalled-worker DND/popup floods, byte/count plateaus, cancellation, oversized intake/replacement rejection, update then close, cleanup, raw-pixel release/retention replacements, primitive context backlog and recovery, failed context query, helper crash with restart backoff"
    )
    print(
        "DND plateau:",
        json.dumps(
            {
                "jobs": second["queuedJobs"],
                "bytes": second["queuedBytes"],
                "tracked": second["tracked"],
                "rssKiB": [first_rss, second_rss],
            }
        ),
    )
    print(
        "Popup plateau:",
        json.dumps(
            {"jobs": popup_second["queuedJobs"], "bytes": popup_second["queuedBytes"], "popups": popup_second["popups"]}
        ),
    )
finally:
    stall.unlink(missing_ok=True)
    context_stall.unlink(missing_ok=True)
    crash.unlink(missing_ok=True)
    context_fail.unlink(missing_ok=True)
    process.stop()
    log.close()
    hostenv.check(env, (base / "runtime.log").read_text(), testhost.calls(base))
    print("Runtime log:", base / "runtime.log")
