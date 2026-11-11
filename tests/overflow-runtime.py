"""The overflow queue with real D-Bus traffic on a private session bus: order,
timers that start on show, replacements, CloseNotification, center removal,
DND, restart survival, screen-space overflow, the hover rule and the hold of
new arrivals while hovered (frame by frame, its maximum, animations off), on the real
service, stack, card delegate and fitCards (an offscreen window stands in for
the layer-shell surface)."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import testhost
from testhost import hostenv

SOURCE = Path(__file__).resolve().parents[1]
testhost.session(__file__, 300)
base = Path(tempfile.mkdtemp(prefix="foamy-notifications-overflow-"))
app = base / "app"
app.mkdir()
for name in ("Commons", "Ui"):
    (app / name).symlink_to(hostenv.root() / "shell" / name, target_is_directory=True)
shutil.copytree(SOURCE, app / "plugin", ignore=shutil.ignore_patterns(".git", "__pycache__"))
full = (SOURCE / "Service.qml").read_text()
# Keep the real service and server; the layer-shell windows are replaced below.
(app / "plugin/Service.qml").write_text(
    full[: full.index("  Variants {")].replace("Quickshell.screens.map(function (s) {\n    return s.name\n  })", '["TEST-1"]')
    + "}\n"
)
# The shipped delegate, fitCards and stack handlers, in a window of a chosen height.
windows = full[full.index("  Variants {") :]


def extract(start, end, after=0):
    first = windows.index(start, after)
    return windows[first : windows.index(end, first)]


delegate = extract("NotificationCard {", "\n      }\n    }\n  }", windows.index("delegate: NotificationCard {"))
fit = extract("      function fitCards() {", "      Item {")
handlers = extract("        onImplicitHeightChanged: Qt.callLater(popupWindow.fitCards)", "        delegate:")


def local(text):
    return (
        text.replace("NotificationCard {", "Cards.NotificationCard {")
        .replace("service.", "notifications.")
        .replace("popupWindow.screen.name", '"TEST-1"')
    )


(app / "shell.qml").write_text(
    """import QtQuick
import QtTest
import Quickshell
import Quickshell.Io
import qs.Commons
import "plugin" as Plugin
import "plugin/components" as Cards
import "plugin/kit"
ShellRoot {
 Plugin.Service { id: notifications }
 FloatingWindow { id: testWindow; visible: true; implicitWidth: 500; implicitHeight: 900; color: "#17171c"
 // Stands in for the layer-shell window: its height is the screen's.
 Item { id: popupWindow; width: 500; height: 900
  readonly property bool selected: true
FIT
  Cards.NotificationStack { id: popupColumn; x: 20; y: 20; width: 420; spacing: 8
   groups: notifications.displayGroups
   waiting: notifications.waitingGroups.length
HANDLERS
   delegate: DELEGATE
  }
  TestCase { id: input; name: "OverflowInput"; when: false }
 }
 }
 QtObject { id: held; property var card: null }
 // Service-side times (Date.now() in this shell), so durations don't include
 // how late a polling IPC call saw a change: when each notification id last
 // entered and left the stack (shown) and the queue (queued), and when the
 // first arrival was held and the held cards were let go.
 QtObject { id: clock; property var shown: ({}); property var queued: ({}); property real heldAt: 0; property real releasedAt: 0
  function track(book, keys) {
   var now = Date.now(), on = {}
   for (var i = 0; i < keys.length; i++) {
    var id = String(keys[i]).split("-")[1]; on[id] = true
    if (!book[id] || book[id].left) book[id] = {entered: now, left: 0}
   }
   for (var known in book) if (!on[known] && !book[known].left) book[known].left = now
  }
 }
 Connections { target: notifications
  function onDisplayGroupsChanged() { clock.track(clock.shown, notifications.displayGroups.map(function(g) { return g.key })) }
  function onWaitingGroupsChanged() { clock.track(clock.queued, notifications.waitingGroups) }
  function onHeldCountChanged() {
   if (notifications.heldCount > 0 && !clock.heldAt) clock.heldAt = Date.now()
   else if (notifications.heldCount === 0 && clock.heldAt && !clock.releasedAt) clock.releasedAt = Date.now()
  }
 }
 // Every frame: did the held card move in the window, and is a card mid-entrance?
 QtObject { id: frames; property bool watching: false; property real pinnedY: 0; property int moves: 0; property int entering: 0; property int count: 0 }
 FrameAnimation { running: true; onTriggered: {
  if (!frames.watching) return
  frames.count++
  if (held.card && Math.abs(held.card.mapToItem(null, 0, 0).y - frames.pinnedY) > 0.5) frames.moves++
  for (var i = 0; i < popupColumn.count; i++) {
   var card = popupColumn.itemAtIndex(i)
   if (card && !card.retired && (card.opacity > 0 && card.opacity < 1 || card.entranceOffset > 0 && card.entranceOffset < Style.space(8))) { frames.entering++; break }
  }
 } }
 IpcHandler {
  target: "overflow-test"
  function setHeight(height: int): void { popupWindow.height = height }
  function hover(index: int): void { held.card = popupColumn.itemAtIndex(index); input.mouseMove(held.card, 100, 20) }
  function leave(): void { input.mouseMove(testWindow.contentItem, 490, 890) }
  // Start counting frames from the held card's window position now.
  function watch(): void { frames.pinnedY = held.card.mapToItem(null, 0, 0).y; frames.moves = 0; frames.entering = 0; frames.count = 0; frames.watching = true }
  function frameCounts(): string { return JSON.stringify({moves: frames.moves, entering: frames.entering, frames: frames.count, pinnedY: frames.pinnedY}) }
  function setHoldMax(ms: int): void { notifications.holdMaxMs = ms }
  function times(): string { return JSON.stringify({shown: clock.shown, queued: clock.queued, heldAt: clock.heldAt, releasedAt: clock.releasedAt}) }
  function resetHoldClock(): void { clock.heldAt = 0; clock.releasedAt = 0 }
  function setReduceMotion(on: bool): void { Style.reduceMotion = on }
  function stack(): string {
   var cards = []
   for (var i = 0; i < popupColumn.count; i++) {
    var card = popupColumn.itemAtIndex(i)
    cards.push(card ? {summary: card.summary, y: card.mapToItem(popupColumn, 0, 0).y, settled: card.opacity === 1 && card.entranceOffset === 0} : null)
   }
   return JSON.stringify({cards: cards, held: popupColumn.holdPositions, heldY: held.card ? held.card.mapToItem(popupColumn, 0, 0).y : -1,
    heldSummary: held.card ? held.card.summary : "", hovered: held.card ? held.card.hovered : false,
    note: popupColumn.footerItem ? popupColumn.footerItem.height : -1,
    noteText: popupColumn.footerItem && popupColumn.footerItem.visible ? input.findChild(popupColumn.footerItem, "waitingNote").children[0].text : "",
    room: popupWindow.height - popupColumn.y - Style.space(8), settledHeight: popupColumn.settledHeight()})
  }
 }
}
""".replace("FIT", local(fit))
    .replace("HANDLERS", local(handlers))
    .replace("DELEGATE", local(delegate))
)
home = base / "home"
home.mkdir()
config = home / ".config/omarchy"
config.mkdir(parents=True)
# Normal cards stay until the timer test, so the first scenarios do not race their expiry.
settings = {"id": "foamy.notifications", "maxVisible": 2, "normalTimeoutSec": 30, "criticalTimeoutSec": 0}


def configure(**values):
    settings.update(values)
    (config / "shell.json").write_text(json.dumps({"plugins": [settings]}))


configure()
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
    XDG_STATE_HOME=str(home / ".local/state"),
    XDG_RUNTIME_DIR=str(runtime),
    QT_QPA_PLATFORM="offscreen",
    QT_QUICK_BACKEND="software",
    QT_QPA_PLATFORMTHEME="basic",
    QT_STYLE_OVERRIDE="Fusion",
)
for key in ["DISPLAY", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE"]:
    env.pop(key, None)
testhost.make(base, app, env)
env["PATH"] = str(cli) + ":" + env["PATH"]
state_dir = home / ".local/state/foamy-notifications"
log = (base / "runtime.log").open("w")


def start():
    return subprocess.Popen(
        ["quickshell", "-p", str(app), "--no-color"], env=env, stdout=log, stderr=log, start_new_session=True
    )


process = start()


def ipc(target, *args):
    result = subprocess.run(
        ["qs", "ipc", "-n", "-p", str(app), "call", target, *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        timeout=3,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def state():
    return json.loads(ipc("foamy.notifications", "state"))


def stack():
    return json.loads(ipc("overflow-test", "stack"))


def wait(check, label, seconds=6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
            pass
        time.sleep(0.05)
    raise AssertionError(
        label
        + "\n"
        + ipc("foamy.notifications", "state")
        + "\n"
        + ipc("overflow-test", "stack")
        + "\n"
        + (base / "runtime.log").read_text()[-4000:]
    )


def send(summary, urgency="normal", extra=(), body="Sample body"):
    sent = subprocess.run(
        ["notify-send", "-p", "-a", "Foamy overflow test", "-u", urgency, *extra, summary, body],
        env=env,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert sent.returncode == 0 and sent.stdout.strip(), "notify-send failed: " + sent.stderr
    return sent.stdout.strip()


def ids(keys):
    return [key.split("-")[1] for key in keys]


def shown():
    return ids(group["key"] for group in state()["groups"])


def queue():
    return ids(state()["queue"])


def times():
    return json.loads(ipc("overflow-test", "times"))


def files(folder):
    return {path.stem.split("-")[1]: json.loads(path.read_text()) for path in (state_dir / folder).glob("*.json")}


def settled():
    return not state()["busy"]


def reset():
    ipc("overflow-test", "leave")
    ipc("notifications", "dismissAll")
    wait(lambda: state()["popups"] == 0 and settled(), "cleanup")
    for path in (state_dir / "history").glob("*.json"):
        path.unlink()


try:
    wait(lambda: ipc("notifications", "ping") == "ok" and state()["storeReady"], "startup", 30)

    # Queue order: criticals newest first, then the normal cards in arrival order.
    # One of the two slots stays for a normal card: C takes it from A, the
    # oldest critical, and E, the newest critical, takes B's.
    a = send("A", "critical")
    b = send("B", "critical")
    c = send("C")
    d = send("D")
    e = send("E", "critical")
    wait(
        lambda: shown() == [e, c] and queue() == [b, a, d] and settled(),
        "the newest critical and a normal card show, the rest waits in order",
    )
    assert [card["summary"] for card in stack()["cards"]] == ["E", "C"], stack()
    popups = files("popups")
    assert (
        all(popups[key].get("queued") is True for key in (b, a, d))
        and "queued" not in popups[e]
        and "queued" not in popups[c]
    ), "waiting cards are not marked queued on disk"
    assert not files("history"), "a waiting card went to history"
    assert stack()["noteText"] == "+3 waiting", stack()
    # A dismissed card's slot goes to the head of the queue, at the bottom of the stack.
    ipc("notifications", "dismissOne")
    wait(lambda: shown() == [c, b] and queue() == [a, d] and settled(), "the waiting critical joins the bottom")
    assert [card["summary"] for card in stack()["cards"]] == ["C", "B"], stack()
    # The normal slot goes to the next normal card, not to the waiting critical.
    ipc("notifications", "dismissOne")
    wait(lambda: shown() == [b, d] and queue() == [a] and settled(), "the next normal card takes the reserved slot")
    reset()
    print(
        "PASS: queue order (criticals newest first, then arrival), one slot for normal cards, queued flag on disk, +N waiting note"
    )

    # Criticals that never expire (criticalTimeoutSec 0) cannot starve a new alert:
    # it rotates the oldest critical on screen back into the queue.
    assert state()["settings"]["criticalTimeoutSec"] == 0
    first = send("Flood 1", "critical")
    second = send("Flood 2", "critical")
    wait(lambda: shown() == [second, first] and not queue() and settled(), "two criticals fill the stack")
    genuine = send("Genuine alert", "critical")
    wait(
        lambda: shown() == [genuine, second] and queue() == [first] and settled(),
        "the new critical shows; the oldest waits",
    )
    assert [card["summary"] for card in stack()["cards"]] == ["Genuine alert", "Flood 2"] and stack()[
        "noteText"
    ] == "+1 waiting", stack()
    assert files("popups")[first].get("queued") is True and first not in files("history"), (
        "the displaced critical did not go back to the queue"
    )
    time.sleep(0.5)
    assert shown() == [genuine, second] and queue() == [first], "the rotation did not hold"
    # It shows again, at the bottom, when a slot frees.
    ipc("notifications", "dismissOne")
    wait(lambda: shown() == [second, first] and not queue() and settled(), "the displaced critical shows again")
    wait(lambda: "queued" not in files("popups")[first], "the re-shown critical is still marked queued", 1)
    reset()
    print("PASS: a new critical rotates the oldest non-expiring critical back into the queue")

    # Nor can they starve normal notifications: one slot stays for them.
    first = send("Flood 1", "critical")
    second = send("Flood 2", "critical")
    wait(lambda: shown() == [second, first] and not queue() and settled(), "two criticals fill the stack")
    normal = send("Normal")
    wait(
        lambda: shown() == [normal, second] and queue() == [first] and settled(),
        "the normal card shows; the older critical waits",
    )
    assert [card["summary"] for card in stack()["cards"]] == ["Normal", "Flood 2"] and stack()[
        "noteText"
    ] == "+1 waiting", stack()
    assert files("popups")[first].get("queued") is True and first not in files("history"), (
        "the displaced critical did not go back to the queue"
    )
    time.sleep(0.5)
    assert shown() == [normal, second] and queue() == [first], "the reserved slot did not hold"
    # With no normal card left, the critical takes the slot again, at the bottom.
    ipc("notifications", "dismissOne")
    wait(
        lambda: shown() == [second, first] and not queue() and settled(),
        "the critical shows again once no normal card needs the slot",
    )
    reset()
    print("PASS: non-expiring criticals leave one slot for a normal notification")

    # Nor can a flood of normal cards keep a later one waiting for the reserved
    # slot: a normal card that waits past maxWaitSec (3 s here) goes to history,
    # on its own timer, with nothing else happening.
    configure(maxWaitSec=3)
    wait(lambda: state()["settings"]["maxWaitSec"] == 3, "maxWaitSec 3")
    first = send("Flood 1", "critical")
    second = send("Flood 2", "critical")
    long = send("Long normal")
    wait(
        lambda: shown() == [long, second] and queue() == [first] and settled(),
        "criticals and a long normal card fill the stack",
    )
    flood = [send("Flood normal " + str(i)) for i in range(4)]
    queued_at = time.monotonic()
    wait(lambda: queue() == [first] + flood and settled(), "the normal flood waits")
    assert all(files("popups")[key].get("waitingSince", 0) > 0 for key in flood), "the waiting clock is not saved"
    wait(
        lambda: queue() == [first] and set(flood) <= set(files("history")),
        "normal cards that waited too long go to history",
        6,
    )
    waited = time.monotonic() - queued_at
    assert 2.5 <= waited <= 5, "aged after %.2f s" % waited
    assert first in queue() and shown() == [long, second], "aging touched a critical or a card on screen"
    # A later normal card reaches the screen when the normal slot frees, well within its maximum wait.
    later = send("Later normal")
    wait(lambda: queue() == [first, later] and settled(), "the later normal card waits")
    ipc("notifications", "dismissOne")
    wait(
        lambda: shown() == [second, later] and queue() == [first] and settled(),
        "the later normal card takes the normal slot",
    )
    clocks = times()["shown"]
    assert clocks[later]["entered"] - clocks[long]["left"] < 3000, (
        "the later normal card did not show within its maximum wait: " + repr(clocks)
    )
    reset()
    # The waiting clock survives a restart: a card that waited 2 s before it ages 1 s after.
    first = send("Flood 1", "critical")
    long = send("Long normal")
    waiting = send("Waits over a restart")
    wait(lambda: shown() == [long, first] and queue() == [waiting] and settled(), "restart aging fixture")
    clock = files("popups")[waiting]["waitingSince"]
    time.sleep(2)
    os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=3)
    process = start()
    wait(
        lambda: ipc("notifications", "ping") == "ok" and state()["storeReady"] and waiting in files("history"),
        "the waiting card ages after the restart",
        30,
    )
    restored = times()["queued"].get(waiting)
    # With its clock reset it would wait the full 3 s after the restore. A
    # restart slower than the 1 s it had left ages it at the restore, unqueued.
    assert restored is None or restored["left"] - restored["entered"] < 2900, (
        "the restart reset the waiting clock: " + repr(restored)
    )
    assert json.loads(next((state_dir / "history").glob("*-" + waiting + ".json")).read_text()).get("waitingSince") in (
        None,
        clock,
    )
    wait(lambda: set(shown()) == {long, first} and not queue() and settled(), "the cards on screen are restored")
    reset()
    print(
        "PASS: normal cards that wait past maxWaitSec go to history on their own timer, also across a restart; a later card still shows"
    )

    # The rest runs with one slot, where criticals take it, and 2 s normal timeouts.
    configure(maxVisible=1, normalTimeoutSec=2, maxWaitSec=300)
    wait(
        lambda: (
            state()["settings"]["maxVisible"] == 1
            and state()["settings"]["normalTimeoutSec"] == 2
            and state()["settings"]["maxWaitSec"] == 300
        ),
        "maxVisible 1",
    )

    # A waiting card's timer starts when it shows: C waited past its 2 s timeout.
    a = send("A", "critical")
    c = send("C")
    d = send("D")
    wait(lambda: shown() == [a] and queue() == [c, d] and settled(), "timer fixture")
    time.sleep(2.3)
    assert queue() == [c, d], "a waiting card expired before it showed"
    ipc("notifications", "dismissOne")
    wait(lambda: shown() == [c], "C shows")
    wait(
        lambda: files("popups")[c].get("deadline", 0) > 0 and "queued" not in files("popups")[c],
        "a card that showed after waiting is saved with its own deadline",
        1,
    )
    wait(lambda: c not in shown() and d in shown(), "C expires, D shows", 4)
    on_screen = times()["shown"][c]
    assert on_screen["left"] - on_screen["entered"] >= 1900, "C did not get its full time on screen: " + repr(on_screen)
    assert c in files("history"), "an expired card did not go to history"
    reset()
    print("PASS: the timer of a waiting card starts on show; expiry still archives")

    # An update that stops a card expiring drops the deadline it got when it showed after waiting.
    # The update must arrive before that deadline, so it gets 4 s here, and the check
    # waits until just past the saved deadline rather than a fixed time.
    configure(normalTimeoutSec=4)
    wait(lambda: state()["settings"]["normalTimeoutSec"] == 4, "normalTimeoutSec 4")
    a = send("A", "critical")
    c = send("Promoted")
    wait(lambda: queue() == [c] and settled(), "promotion fixture")
    ipc("notifications", "dismissOne")
    wait(
        lambda: c in shown() and files("popups")[c].get("deadline", 0) > 0, "the promoted card is saved with a deadline"
    )
    deadline = files("popups")[c]["deadline"] / 1000
    send("Promoted, now critical", "critical", extra=("-r", c))
    wait(
        lambda: (
            files("popups")[c]["summary"] == "Promoted, now critical"
            and "deadline" not in files("popups")[c]
            and settled()
        ),
        "an update to a non-expiring card drops its saved deadline",
        2,
    )
    time.sleep(max(0, deadline - time.time()) + 0.3)
    assert c in shown(), "the card updated to critical expired"
    reset()
    configure(normalTimeoutSec=2)
    wait(lambda: state()["settings"]["normalTimeoutSec"] == 2, "normalTimeoutSec 2")
    print("PASS: an update that disables expiry drops the saved deadline")

    # Replacement and duplicates update a waiting card in place.
    a = send("A", "critical")
    c = send("Waiting")
    d = send("Behind")
    wait(lambda: queue() == [c, d] and settled(), "replacement fixture")
    key = state()["queue"][0]
    send("Waiting, edited", extra=("-r", c))
    wait(
        lambda: state()["queue"][0] == key and files("popups")[c]["summary"] == "Waiting, edited" and settled(),
        "the waiting card is updated in place",
    )
    assert files("popups")[c].get("queued") is True, "the replacement lost the queued flag"
    send("Behind")
    wait(lambda: len(state()["queue"]) == 2 and state()["popups"] == 4, "a duplicate merges into its waiting group")
    assert queue()[0] == c, "the duplicate changed the queue order"
    print("PASS: replacement and duplicate merge update waiting cards in place")

    # CloseNotification withdraws a waiting card: it never shows, and goes to history.
    subprocess.run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.freedesktop.Notifications",
            "--object-path",
            "/org/freedesktop/Notifications",
            "--method",
            "org.freedesktop.Notifications.CloseNotification",
            c,
        ],
        env=env,
        check=True,
        capture_output=True,
        timeout=3,
    )
    wait(lambda: c not in queue() and c in files("history") and settled(), "CloseNotification removes the waiting card")
    assert shown() == [a], "closing a waiting card changed the stack"
    # The center removes a waiting card through releaseHistory.
    behind = [
        path.stem
        for path in (state_dir / "popups").glob("*.json")
        if json.loads(path.read_text())["summary"] == "Behind"
    ]
    assert len(behind) == 2 and ipc("foamy.notifications", "releaseHistory", ",".join(behind)) == "ok"
    wait(lambda: not state()["queue"] and settled(), "the center removes a waiting card")
    assert not set(ids(behind)) & (set(files("history")) | set(files("popups"))), "a card the center removed was kept"
    print("PASS: CloseNotification and the center remove waiting cards")

    # DND: cards still waiting go to history, as new arrivals do; cards on screen stay.
    c = send("Silenced while waiting")
    wait(lambda: queue() == [c] and settled(), "DND fixture")
    ipc("notifications", "setDnd", "on")
    wait(lambda: not queue() and c in files("history") and settled(), "DND archives the waiting card")
    assert shown() == [a], "DND hid a visible card"
    ipc("notifications", "setDnd", "off")
    reset()
    print("PASS: DND sends waiting cards to history and keeps the visible ones")

    # Restart: the queue survives, in order, and nothing shows twice. Y waited past its timeout.
    x = send("X", "critical")
    w = send("W", "critical")
    y = send("Y")
    z = send("Z")
    wait(lambda: shown() == [w] and queue() == [x, y, z] and settled(), "restart fixture")
    keys = {group["key"] for group in state()["groups"]} | set(state()["queue"])
    clocks = {key: files("popups")[key].get("waitingSince") for key in (x, y, z)}
    assert all(clocks.values()), "a waiting card was saved without its waiting clock"
    time.sleep(2.2)
    os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=3)
    process = start()
    wait(
        lambda: ipc("notifications", "ping") == "ok" and state()["storeReady"] and state()["popups"] == 4, "restart", 30
    )
    wait(lambda: shown() == [w] and queue() == [x, y, z], "the queue is restored waiting, in order")
    assert {group["key"] for group in state()["groups"]} | set(state()["queue"]) == keys, "restart changed identities"
    assert len(files("popups")) == 4 and not files("history"), "restart duplicated or archived a card"
    assert {key: files("popups")[key].get("waitingSince") for key in (x, y, z)} == clocks, (
        "the restart reset a waiting clock"
    )
    ipc("notifications", "dismissOne")
    ipc("notifications", "dismissOne")
    wait(
        lambda: shown() == [y] and queue() == [z] and state()["popups"] == 2 and settled(),
        "restored waiting cards show once",
    )
    assert set(files("popups")) == {y, z} and set(files("history")) == {w, x}
    assert "queued" not in files("popups")[y] and files("popups")[y].get("deadline", 0) > 0, (
        "the shown card kept the queued flag"
    )
    assert files("popups")[z].get("queued") is True, "the waiting card lost the queued flag"
    reset()
    print("PASS: the queue survives a shell restart without duplicates or early expiry")

    # A keyboard-selected card keeps its slot; giving the keyboard back lets a waiting critical take it.
    n = send("Selected")
    wait(lambda: shown() == [n] and settled(), "keyboard fixture")
    assert ipc("notifications", "keyboard") == "on"
    c = send("Urgent", "critical")
    wait(
        lambda: shown() == [n] and queue() == [c] and settled(), "the critical waits behind the keyboard-selected card"
    )
    assert ipc("notifications", "keyboard") == "off"
    wait(
        lambda: shown() == [c] and queue() == [n] and settled(),
        "giving the keyboard back lets the waiting critical show",
        2,
    )
    reset()
    print("PASS: releasing the keyboard selection frees its slot for a waiting critical")

    # Screen-space overflow waits too: a short window fits fewer cards than maxVisible.
    configure(maxVisible=10)
    wait(lambda: state()["settings"]["maxVisible"] == 10, "maxVisible 10")
    ipc("overflow-test", "setHeight", 330)
    sent = [send("Tall card " + str(i), "critical", body="Line one\nLine two\nLine three") for i in range(6)]
    wait(
        lambda: (
            len(state()["groups"]) + len(state()["queue"]) == 6
            and state()["queue"]
            and settled()
            and stack()["cards"]
            and all(card and card["settled"] for card in stack()["cards"])
        ),
        "screen space queues cards",
    )
    view = stack()
    assert view["settledHeight"] <= view["room"], view
    assert not files("history"), "screen-space overflow went to history"
    fitted = len(state()["groups"])
    # The newest criticals show; the older ones wait, the next to show first.
    assert shown() == sent[::-1][:fitted] and queue() == sent[::-1][fitted:], "screen-space waiting order"
    assert 2 <= fitted < 6, fitted
    ipc("notifications", "dismissOne")
    wait(
        lambda: len(state()["groups"]) == fitted and len(state()["queue"]) == 6 - fitted - 1,
        "a freed slot shows the next waiting card",
    )
    ipc("overflow-test", "setHeight", 900)
    wait(lambda: len(state()["groups"]) == 5 and not state()["queue"], "a taller screen shows every waiting card")
    # The hover fixture below must be in place before its cards expire, so they get 4 s.
    configure(maxVisible=3, normalTimeoutSec=4)
    wait(
        lambda: state()["settings"]["maxVisible"] == 3 and state()["settings"]["normalTimeoutSec"] == 4, "maxVisible 3"
    )
    reset()
    print("PASS: cards that do not fit the screen wait, and show when room frees")

    # Hover: the card under the pointer stays put while cards above and below it
    # expire and waiting cards appear; they join below it.
    for title in ["Bottom expires", "Hold this card", "Top expires", "Waits one", "Waits two"]:
        send(title)
    wait(
        lambda: (
            len(state()["groups"]) == 3
            and len(state()["queue"]) == 2
            and all(card and card["settled"] for card in stack()["cards"])
        ),
        "hover fixture",
    )
    ipc("overflow-test", "hover", 1)
    wait(
        lambda: stack()["held"] and stack()["hovered"] and stack()["heldSummary"] == "Hold this card",
        "pointer holds the middle card",
    )
    held_y = stack()["heldY"]
    assert held_y > 0
    samples = []
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        view = stack()
        samples.append(view["heldY"])
        if [card["summary"] for card in view["cards"] if card] == ["Hold this card", "Waits one", "Waits two"] and all(
            card["settled"] for card in view["cards"]
        ):
            break
        time.sleep(0.03)
    view = stack()
    assert [card["summary"] for card in view["cards"]] == ["Hold this card", "Waits one", "Waits two"], view
    assert all(y == held_y for y in samples), "the hovered card moved: " + repr(sorted(set(samples)))
    assert view["hovered"] and not state()["queue"] and view["note"] == 0, view
    assert view["cards"][1]["y"] > held_y, "a waiting card appeared above the hovered card"
    ipc("overflow-test", "leave")
    wait(lambda: not stack()["held"] and stack()["cards"][0]["y"] == 0, "leaving closes the gap")
    print("PASS: the hovered card stays under the pointer while others expire and waiting cards join below it")
    reset()

    # Hold while hovered: new arrivals, a critical too, wait while the pointer
    # is over the stack, even with room; nothing moves, frame by frame.
    configure(maxVisible=5, normalTimeoutSec=30)
    wait(
        lambda: state()["settings"]["maxVisible"] == 5 and state()["settings"]["normalTimeoutSec"] == 30,
        "hold settings",
    )

    def summaries():
        return [card["summary"] if card else None for card in stack()["cards"]]

    def fixture():
        below = send("Below")
        top = send("Hold this card")
        wait(
            lambda: (
                summaries() == ["Hold this card", "Below"]
                and all(card["settled"] for card in stack()["cards"])
                and settled()
            ),
            "hold fixture",
        )
        ipc("overflow-test", "hover", 0)
        wait(
            lambda: stack()["held"] and stack()["hovered"] and state()["pointerHolds"],
            "the pointer is over the top card",
        )
        ipc("overflow-test", "watch")
        return below, top

    def counts():
        return json.loads(ipc("overflow-test", "frameCounts"))

    below, top = fixture()
    normal = send("New normal")
    alert = send("New critical", "critical")
    wait(
        lambda: (
            queue() == [alert, normal]
            and sorted(ids(state()["held"])) == sorted([normal, alert])
            and stack()["noteText"] == "+2 waiting"
            and settled()
        ),
        "arrivals are held",
    )
    assert shown() == [top, below], "a held arrival entered the stack"
    assert files("popups")[normal].get("queued") is True and files("popups")[alert].get("queued") is True, (
        "held cards are not saved as waiting"
    )
    # A replacement of the hovered card updates it in place.
    send("Hold this card, edited", extra=("-r", top))
    wait(lambda: summaries() == ["Hold this card, edited", "Below"], "the hovered card is replaced in place")
    time.sleep(0.5)
    view, frame = stack(), counts()
    assert frame["frames"] > 10 and frame["moves"] == 0, "the hovered card moved in the window: " + repr(frame)
    assert view["hovered"] and view["heldY"] == 0 and len(state()["held"]) == 2, view
    # The pointer leaves: they show as the new arrivals they are, newest on top, with the entrance motion.
    released_at = time.time() * 1000
    ipc("overflow-test", "leave")
    wait(
        lambda: (
            summaries() == ["New critical", "New normal", "Hold this card, edited", "Below"]
            and all(card["settled"] for card in stack()["cards"])
            and settled()
        ),
        "held arrivals show when the pointer leaves",
    )
    assert not state()["queue"] and not state()["held"] and stack()["note"] == 0, state()
    assert counts()["entering"] > 0, "the released cards did not animate in: " + repr(counts())
    # Their timer started when they showed.
    wait(
        lambda: (
            "queued" not in files("popups")[normal]
            and files("popups")[normal].get("deadline", 0) >= released_at + 29000
        ),
        "a held card is saved with a deadline from when it showed",
        2,
    )
    reset()
    print(
        "PASS: while hovered, a new normal and a new critical wait, the hovered card never moves; they show on top when the pointer leaves"
    )

    # The hold's maximum (stock: 30 s; shortened here) lets them go while the pointer stays.
    ipc("overflow-test", "setHoldMax", 1500)
    below, top = fixture()
    ipc("overflow-test", "resetHoldClock")
    send("Held normal")
    send("Held critical", "critical")
    wait(lambda: len(state()["held"]) == 2, "arrivals are held")
    assert counts()["moves"] == 0
    wait(lambda: summaries()[:2] == ["Held critical", "Held normal"], "the maximum releases the held cards", 4)
    hold = times()
    assert hold["releasedAt"] - hold["heldAt"] >= 1400, "released before the maximum: " + repr(hold)
    assert stack()["held"] and state()["pointerHolds"], "the pointer left the stack"
    # As in stock, the released cards go on top, so this time the hovered card moves (and the frame check sees it).
    wait(lambda: counts()["moves"] > 0 and stack()["heldY"] > 0, "the released cards push the hovered card down")
    assert not state()["held"] and not state()["queue"], state()
    # Arrivals after the release are held again.
    later = send("After the release")
    wait(lambda: ids(state()["held"]) == [later] and later not in shown(), "a later arrival is held again")
    ipc("overflow-test", "leave")
    wait(lambda: shown()[0] == later, "it shows when the pointer leaves")
    ipc("overflow-test", "setHoldMax", 30000)
    reset()
    print("PASS: the hold maximum releases held cards while the pointer stays, and later arrivals are held again")

    # Animations off: held cards appear at once when released.
    ipc("overflow-test", "setReduceMotion", "true")
    below, top = fixture()
    send("Instant normal")
    send("Instant critical", "critical")
    wait(lambda: len(state()["held"]) == 2 and settled(), "arrivals are held")
    time.sleep(0.3)
    assert counts()["moves"] == 0, counts()
    ipc("overflow-test", "leave")
    wait(
        lambda: (
            summaries()[:2] == ["Instant critical", "Instant normal"]
            and all(card["settled"] for card in stack()["cards"])
        ),
        "held cards show when the pointer leaves",
    )
    time.sleep(0.2)
    assert counts()["entering"] == 0, "with animations off, released cards must appear at once: " + repr(counts())
    ipc("overflow-test", "setReduceMotion", "false")
    reset()
    print("PASS: with animations off, released cards appear at once")

    # Restart: a held card is a waiting card; it comes back once, with its full time.
    below, top = fixture()
    survivor = send("Held over a restart")
    wait(
        lambda: ids(state()["held"]) == [survivor] and files("popups")[survivor].get("queued") is True and settled(),
        "held before the restart",
    )
    os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=3)
    restarted_at = time.time() * 1000
    process = start()
    wait(
        lambda: ipc("notifications", "ping") == "ok" and state()["storeReady"] and state()["popups"] == 3,
        "restart with a held card",
        30,
    )
    wait(lambda: survivor in shown() and not state()["queue"] and settled(), "the held card shows after the restart")
    assert len(files("popups")) == 3 and not files("history"), "restart duplicated or archived a card"
    wait(
        lambda: files("popups")[survivor].get("deadline", 0) >= restarted_at + 29000,
        "the held card got its full time after the restart",
        2,
    )
    reset()
    print("PASS: a held card survives a restart as a waiting card, once, without early expiry")
finally:
    os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=3)
    log.close()
    print("Runtime log:", base / "runtime.log")
    hostenv.check(env, (base / "runtime.log").read_text(), testhost.calls(base))
