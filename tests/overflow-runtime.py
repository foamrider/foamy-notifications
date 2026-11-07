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
import hostenv

SOURCE = Path(__file__).resolve().parents[1]
if '--inside' not in sys.argv:
    raise SystemExit(subprocess.run(['dbus-run-session', '--', sys.executable, __file__, '--inside'], timeout=90).returncode)
base = Path(tempfile.mkdtemp(prefix='foamy-notifications-overflow-'))
app = base/'app'; app.mkdir()
(app/'Commons').symlink_to(hostenv.root()/'shell/Commons', target_is_directory=True)
shutil.copytree(SOURCE, app/'plugin', ignore=shutil.ignore_patterns('.git', '__pycache__'))
full = (SOURCE/'Service.qml').read_text()
# Keep the real service and server; the layer-shell windows are replaced below.
(app/'plugin/Service.qml').write_text(full[:full.index('  Variants {')].replace('Quickshell.screens.map(function(s) { return s.name })', '["TEST-1"]') + '}\n')
# The shipped delegate, fitCards and stack handlers, in a window of a chosen height.
windows = full[full.index('  Variants {'):]
def extract(start, end):
    first = windows.index(start); return windows[first:windows.index(end, first)]
delegate = extract('          NotificationCard {', '\n      }\n    }\n  }')
fit = extract('      function fitCards() {', '      Item {')
handlers = extract('        onImplicitHeightChanged: Qt.callLater(popupWindow.fitCards)', '        delegate:')
def local(text):
    return text.replace('NotificationCard {', 'Cards.NotificationCard {').replace('service.', 'notifications.').replace('popupWindow.screen.name', '"TEST-1"')
(app/'shell.qml').write_text('''import QtQuick
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
'''.replace('FIT', local(fit)).replace('HANDLERS', local(handlers)).replace('DELEGATE', local(delegate)))
home = base/'home'; home.mkdir()
config = home/'.config/omarchy'; config.mkdir(parents=True)
settings = {'id': 'foamy.notifications', 'maxVisible': 2, 'normalTimeoutSec': 2, 'criticalTimeoutSec': 0}
def configure(**values):
    settings.update(values)
    (config/'shell.json').write_text(json.dumps({'plugins': [settings]}))
configure()
runtime = base/'runtime'; runtime.mkdir(mode=0o700)
cli = base/'bin'; cli.mkdir()
(cli/'hyprctl').write_text('#!/bin/sh\nprintf "[]\\n"\n'); (cli/'hyprctl').chmod(0o755)
env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home/'.config'), XDG_STATE_HOME=str(home/'.local/state'),
           XDG_RUNTIME_DIR=str(runtime), QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
           QT_QPA_PLATFORMTHEME='basic', QT_STYLE_OVERRIDE='Fusion')
for key in ['DISPLAY', 'WAYLAND_DISPLAY', 'HYPRLAND_INSTANCE_SIGNATURE']: env.pop(key, None)
env.update(hostenv.make(base, app))
env['PATH'] = str(cli) + ':' + env['PATH']
state_dir = home/'.local/state/foamy-notifications'
log = (base/'runtime.log').open('w')
def start():
    return subprocess.Popen(['quickshell', '-p', str(app), '--no-color'], env=env, stdout=log, stderr=log, start_new_session=True)
process = start()

def ipc(target, *args):
    result = subprocess.run(['qs', 'ipc', '-n', '-p', str(app), 'call', target, *map(str, args)], env=env, capture_output=True, text=True, timeout=3)
    if result.returncode: raise RuntimeError(result.stderr)
    return result.stdout.strip()
def state(): return json.loads(ipc('foamy.notifications', 'state'))
def stack(): return json.loads(ipc('overflow-test', 'stack'))
def wait(check, label, seconds=6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check(): return
        except (ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError): pass
        time.sleep(.05)
    raise AssertionError(label + '\n' + ipc('foamy.notifications', 'state') + '\n' + ipc('overflow-test', 'stack') + '\n' + (base/'runtime.log').read_text()[-4000:])
def send(summary, urgency='normal', extra=(), body='Sample body'):
    sent = subprocess.run(['notify-send', '-p', '-a', 'Foamy overflow test', '-u', urgency, *extra, summary, body], env=env, capture_output=True, text=True, timeout=3)
    assert sent.returncode == 0 and sent.stdout.strip(), 'notify-send failed: ' + sent.stderr
    return sent.stdout.strip()
def ids(keys): return [key.split('-')[1] for key in keys]
def shown(): return ids(group['key'] for group in state()['groups'])
def queue(): return ids(state()['queue'])
def files(folder): return {path.stem.split('-')[1]: json.loads(path.read_text()) for path in (state_dir/folder).glob('*.json')}
def settled(): return not state()['busy']
def reset():
    ipc('overflow-test', 'leave')
    ipc('notifications', 'dismissAll')
    wait(lambda: state()['popups'] == 0 and settled(), 'cleanup')
    for path in (state_dir/'history').glob('*.json'): path.unlink()

try:
    wait(lambda: ipc('notifications', 'ping') == 'ok' and state()['storeReady'], 'startup')

    # Queue order: arrival order, a critical ahead of the waiting normal cards.
    a = send('A', 'critical'); b = send('B', 'critical'); c = send('C'); d = send('D'); e = send('E', 'critical')
    wait(lambda: shown() == [b, a] and queue() == [e, c, d] and settled(), 'criticals show, the rest waits in order')
    assert files('popups')[c].get('queued') is True and 'queued' not in files('popups')[a], 'waiting cards are not marked queued on disk'
    assert not files('history'), 'a waiting card went to history'
    assert stack()['noteText'] == '+3 waiting', stack()
    # A dismissed card's slot goes to the head of the queue, at the bottom of the stack.
    ipc('notifications', 'dismissOne')
    wait(lambda: shown() == [a, e] and queue() == [c, d], 'the waiting critical joins the bottom')
    print('PASS: queue order (criticals first, then arrival), queued flag on disk, +N waiting note')

    # A waiting card's timer starts when it shows: C waited past its 2 s timeout.
    time.sleep(2.3)
    assert queue() == [c, d], 'a waiting card expired before it showed'
    ipc('notifications', 'dismissOne')
    wait(lambda: shown() == [e, c], 'C shows')
    shown_at = time.monotonic()
    wait(lambda: files('popups')[c].get('deadline', 0) > 0 and 'queued' not in files('popups')[c], 'a card that showed after waiting is saved with its own deadline', 1)
    time.sleep(1.4)
    assert c in shown(), 'C did not get its full time on screen'
    wait(lambda: c not in shown() and d in shown(), 'C expires, D shows', 4)
    assert time.monotonic() - shown_at >= 1.9, 'C expired early'
    assert c in files('history'), 'an expired card did not go to history'
    reset()
    print('PASS: the timer of a waiting card starts on show; expiry still archives')

    # An update that stops a card expiring drops the deadline it got when it showed after waiting.
    a = send('A', 'critical'); b = send('B', 'critical'); c = send('Promoted')
    wait(lambda: queue() == [c] and settled(), 'promotion fixture')
    ipc('notifications', 'dismissOne')
    wait(lambda: c in shown() and files('popups')[c].get('deadline', 0) > 0, 'the promoted card is saved with a deadline')
    send('Promoted, now critical', 'critical', extra=('-r', c))
    wait(lambda: files('popups')[c]['summary'] == 'Promoted, now critical' and 'deadline' not in files('popups')[c] and settled(),
         'an update to a non-expiring card drops its saved deadline', 2)
    time.sleep(2.3)
    assert c in shown(), 'the card updated to critical expired'
    reset()
    print('PASS: an update that disables expiry drops the saved deadline')

    # Replacement and duplicates update a waiting card in place.
    a = send('A', 'critical'); b = send('B', 'critical'); c = send('Waiting'); d = send('Behind')
    wait(lambda: queue() == [c, d] and settled(), 'replacement fixture')
    key = state()['queue'][0]
    send('Waiting, edited', extra=('-r', c))
    wait(lambda: state()['queue'][0] == key and files('popups')[c]['summary'] == 'Waiting, edited' and settled(), 'the waiting card is updated in place')
    assert files('popups')[c].get('queued') is True, 'the replacement lost the queued flag'
    send('Behind')
    wait(lambda: len(state()['queue']) == 2 and state()['popups'] == 5, 'a duplicate merges into its waiting group')
    assert queue()[0] == c, 'the duplicate changed the queue order'
    print('PASS: replacement and duplicate merge update waiting cards in place')

    # CloseNotification withdraws a waiting card: it never shows, and goes to history.
    subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.Notifications', '--object-path', '/org/freedesktop/Notifications',
                    '--method', 'org.freedesktop.Notifications.CloseNotification', c], env=env, check=True, capture_output=True, timeout=3)
    wait(lambda: c not in queue() and c in files('history') and settled(), 'CloseNotification removes the waiting card')
    assert shown() == [b, a], 'closing a waiting card changed the stack'
    # The center removes a waiting card through releaseHistory.
    behind = [path.stem for path in (state_dir/'popups').glob('*.json') if json.loads(path.read_text())['summary'] == 'Behind']
    assert len(behind) == 2 and ipc('foamy.notifications', 'releaseHistory', ','.join(behind)) == 'ok'
    wait(lambda: not state()['queue'] and settled(), 'the center removes a waiting card')
    assert not set(ids(behind)) & (set(files('history')) | set(files('popups'))), 'a card the center removed was kept'
    print('PASS: CloseNotification and the center remove waiting cards')

    # DND: cards still waiting go to history, as new arrivals do; cards on screen stay.
    c = send('Silenced while waiting')
    wait(lambda: queue() == [c] and settled(), 'DND fixture')
    ipc('notifications', 'setDnd', 'on')
    wait(lambda: not queue() and c in files('history') and settled(), 'DND archives the waiting card')
    assert shown() == [b, a], 'DND hid a visible card'
    ipc('notifications', 'setDnd', 'off')
    reset()
    print('PASS: DND sends waiting cards to history and keeps the visible ones')

    # Restart: the queue survives, in order, and nothing shows twice. Y waited past its timeout.
    x = send('X', 'critical'); w = send('W', 'critical'); y = send('Y'); z = send('Z')
    wait(lambda: shown() == [w, x] and queue() == [y, z] and settled(), 'restart fixture')
    keys = {group['key'] for group in state()['groups']} | set(state()['queue'])
    time.sleep(2.2)
    os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=3)
    process = start()
    wait(lambda: ipc('notifications', 'ping') == 'ok' and state()['storeReady'] and state()['popups'] == 4, 'restart')
    wait(lambda: set(shown()) == {w, x} and queue() == [y, z], 'the queue is restored waiting, in order')
    assert {group['key'] for group in state()['groups']} | set(state()['queue']) == keys, 'restart changed identities'
    assert len(files('popups')) == 4 and not files('history'), 'restart duplicated or archived a card'
    ipc('notifications', 'dismissOne'); ipc('notifications', 'dismissOne')
    wait(lambda: shown() == [y, z] and not state()['queue'] and state()['popups'] == 2 and settled(), 'restored waiting cards show once')
    assert set(files('popups')) == {y, z} and set(files('history')) == {w, x}
    assert all('queued' not in entry and entry.get('deadline', 0) > 0 for entry in files('popups').values()), 'shown cards kept the queued flag'
    reset()
    print('PASS: the queue survives a shell restart without duplicates or early expiry')

    # Screen-space overflow waits too: a short window fits fewer cards than maxVisible.
    configure(maxVisible=10)
    wait(lambda: state()['settings']['maxVisible'] == 10, 'maxVisible 10')
    ipc('overflow-test', 'setHeight', 330)
    sent = [send('Tall card ' + str(i), 'critical', body='Line one\nLine two\nLine three') for i in range(6)]
    wait(lambda: len(state()['groups']) + len(state()['queue']) == 6 and state()['queue'] and settled() and stack()['cards'] and all(card and card['settled'] for card in stack()['cards']), 'screen space queues cards')
    view = stack()
    assert view['settledHeight'] <= view['room'], view
    assert not files('history'), 'screen-space overflow went to history'
    assert queue() == sent[len(state()['groups']):], 'screen-space waiting order'
    fitted = len(state()['groups'])
    assert 2 <= fitted < 6, fitted
    ipc('notifications', 'dismissOne')
    wait(lambda: len(state()['groups']) == fitted and len(state()['queue']) == 6 - fitted - 1, 'a freed slot shows the next waiting card')
    ipc('overflow-test', 'setHeight', 900)
    wait(lambda: len(state()['groups']) == 5 and not state()['queue'], 'a taller screen shows every waiting card')
    configure(maxVisible=3)
    wait(lambda: state()['settings']['maxVisible'] == 3, 'maxVisible 3')
    reset()
    print('PASS: cards that do not fit the screen wait, and show when room frees')

    # Hover: the card under the pointer stays put while cards above and below it
    # expire and waiting cards appear; they join below it.
    for title in ['Bottom expires', 'Hold this card', 'Top expires', 'Waits one', 'Waits two']: send(title)
    wait(lambda: len(state()['groups']) == 3 and len(state()['queue']) == 2 and all(card and card['settled'] for card in stack()['cards']), 'hover fixture')
    ipc('overflow-test', 'hover', 1)
    wait(lambda: stack()['held'] and stack()['hovered'] and stack()['heldSummary'] == 'Hold this card', 'pointer holds the middle card')
    held_y = stack()['heldY']; assert held_y > 0
    samples = []
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        view = stack(); samples.append(view['heldY'])
        if [card['summary'] for card in view['cards'] if card] == ['Hold this card', 'Waits one', 'Waits two'] and all(card['settled'] for card in view['cards']): break
        time.sleep(.03)
    view = stack()
    assert [card['summary'] for card in view['cards']] == ['Hold this card', 'Waits one', 'Waits two'], view
    assert all(y == held_y for y in samples), 'the hovered card moved: ' + repr(sorted(set(samples)))
    assert view['hovered'] and not state()['queue'] and view['note'] == 0, view
    assert view['cards'][1]['y'] > held_y, 'a waiting card appeared above the hovered card'
    ipc('overflow-test', 'leave')
    wait(lambda: not stack()['held'] and stack()['cards'][0]['y'] == 0, 'leaving closes the gap')
    print('PASS: the hovered card stays under the pointer while others expire and waiting cards join below it')
    reset()

    # Hold while hovered: new arrivals, a critical too, wait while the pointer
    # is over the stack, even with room; nothing moves, frame by frame.
    configure(maxVisible=5, normalTimeoutSec=30)
    wait(lambda: state()['settings']['maxVisible'] == 5 and state()['settings']['normalTimeoutSec'] == 30, 'hold settings')
    def summaries(): return [card['summary'] if card else None for card in stack()['cards']]
    def fixture():
        below = send('Below'); top = send('Hold this card')
        wait(lambda: summaries() == ['Hold this card', 'Below'] and all(card['settled'] for card in stack()['cards']) and settled(), 'hold fixture')
        ipc('overflow-test', 'hover', 0)
        wait(lambda: stack()['held'] and stack()['hovered'] and state()['pointerHolds'], 'the pointer is over the top card')
        ipc('overflow-test', 'watch')
        return below, top
    def counts(): return json.loads(ipc('overflow-test', 'frameCounts'))
    below, top = fixture()
    normal = send('New normal'); alert = send('New critical', 'critical')
    wait(lambda: queue() == [alert, normal] and sorted(ids(state()['held'])) == sorted([normal, alert]) and stack()['noteText'] == '+2 waiting' and settled(), 'arrivals are held')
    assert shown() == [top, below], 'a held arrival entered the stack'
    assert files('popups')[normal].get('queued') is True and files('popups')[alert].get('queued') is True, 'held cards are not saved as waiting'
    # A replacement of the hovered card updates it in place.
    send('Hold this card, edited', extra=('-r', top))
    wait(lambda: summaries() == ['Hold this card, edited', 'Below'], 'the hovered card is replaced in place')
    time.sleep(.5)
    view, frame = stack(), counts()
    assert frame['frames'] > 10 and frame['moves'] == 0, 'the hovered card moved in the window: ' + repr(frame)
    assert view['hovered'] and view['heldY'] == 0 and len(state()['held']) == 2, view
    # The pointer leaves: they show as the new arrivals they are, newest on top, with the entrance motion.
    released_at = time.time() * 1000
    ipc('overflow-test', 'leave')
    wait(lambda: summaries() == ['New critical', 'New normal', 'Hold this card, edited', 'Below'] and all(card['settled'] for card in stack()['cards']) and settled(), 'held arrivals show when the pointer leaves')
    assert not state()['queue'] and not state()['held'] and stack()['note'] == 0, state()
    assert counts()['entering'] > 0, 'the released cards did not animate in: ' + repr(counts())
    # Their timer started when they showed.
    wait(lambda: 'queued' not in files('popups')[normal] and files('popups')[normal].get('deadline', 0) >= released_at + 29000, 'a held card is saved with a deadline from when it showed', 2)
    reset()
    print('PASS: while hovered, a new normal and a new critical wait, the hovered card never moves; they show on top when the pointer leaves')

    # The hold's maximum (stock: 30 s; shortened here) lets them go while the pointer stays.
    ipc('overflow-test', 'setHoldMax', 1500)
    below, top = fixture()
    send('Held normal'); send('Held critical', 'critical')
    wait(lambda: len(state()['held']) == 2, 'arrivals are held')
    held_at = time.monotonic()
    assert counts()['moves'] == 0
    wait(lambda: summaries()[:2] == ['Held critical', 'Held normal'], 'the maximum releases the held cards', 4)
    assert time.monotonic() - held_at >= 1.3, 'released before the maximum'
    assert stack()['held'] and state()['pointerHolds'], 'the pointer left the stack'
    # As in stock, the released cards go on top, so this time the hovered card moves (and the frame check sees it).
    wait(lambda: counts()['moves'] > 0 and stack()['heldY'] > 0, 'the released cards push the hovered card down')
    assert not state()['held'] and not state()['queue'], state()
    # Arrivals after the release are held again.
    later = send('After the release')
    wait(lambda: ids(state()['held']) == [later] and later not in shown(), 'a later arrival is held again')
    ipc('overflow-test', 'leave')
    wait(lambda: shown()[0] == later, 'it shows when the pointer leaves')
    ipc('overflow-test', 'setHoldMax', 30000)
    reset()
    print('PASS: the hold maximum releases held cards while the pointer stays, and later arrivals are held again')

    # Animations off: held cards appear at once when released.
    ipc('overflow-test', 'setReduceMotion', 'true')
    below, top = fixture()
    send('Instant normal'); send('Instant critical', 'critical')
    wait(lambda: len(state()['held']) == 2 and settled(), 'arrivals are held')
    time.sleep(.3)
    assert counts()['moves'] == 0, counts()
    ipc('overflow-test', 'leave')
    wait(lambda: summaries()[:2] == ['Instant critical', 'Instant normal'] and all(card['settled'] for card in stack()['cards']), 'held cards show when the pointer leaves')
    time.sleep(.2)
    assert counts()['entering'] == 0, 'with animations off, released cards must appear at once: ' + repr(counts())
    ipc('overflow-test', 'setReduceMotion', 'false')
    reset()
    print('PASS: with animations off, released cards appear at once')

    # Restart: a held card is a waiting card; it comes back once, with its full time.
    below, top = fixture()
    survivor = send('Held over a restart')
    wait(lambda: ids(state()['held']) == [survivor] and files('popups')[survivor].get('queued') is True and settled(), 'held before the restart')
    os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=3)
    restarted_at = time.time() * 1000
    process = start()
    wait(lambda: ipc('notifications', 'ping') == 'ok' and state()['storeReady'] and state()['popups'] == 3, 'restart with a held card')
    wait(lambda: survivor in shown() and not state()['queue'] and settled(), 'the held card shows after the restart')
    assert len(files('popups')) == 3 and not files('history'), 'restart duplicated or archived a card'
    wait(lambda: files('popups')[survivor].get('deadline', 0) >= restarted_at + 29000, 'the held card got its full time after the restart', 2)
    reset()
    print('PASS: a held card survives a restart as a waiting card, once, without early expiry')
finally:
    os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=3); log.close()
    print('Runtime log:', base/'runtime.log')
    hostenv.check(env, (base/'runtime.log').read_text(), (base/'omarchy-calls.log').read_text() if (base/'omarchy-calls.log').exists() else '')
