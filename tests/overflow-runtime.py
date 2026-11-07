"""The overflow queue with real D-Bus traffic on a private session bus: order,
timers that start on show, replacements, CloseNotification, center removal,
DND, restart survival, screen-space overflow and the hover rule, on the real
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
 IpcHandler {
  target: "overflow-test"
  function setHeight(height: int): void { popupWindow.height = height }
  function hover(index: int): void { held.card = popupColumn.itemAtIndex(index); input.mouseMove(held.card, 100, 20) }
  function leave(): void { input.mouseMove(testWindow.contentItem, 490, 890) }
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
finally:
    os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=3); log.close()
    print('Runtime log:', base/'runtime.log')
    hostenv.check(env, (base/'runtime.log').read_text(), (base/'omarchy-calls.log').read_text() if (base/'omarchy-calls.log').exists() else '')
