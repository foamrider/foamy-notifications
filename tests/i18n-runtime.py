"""Check live Omarchy translation bindings, fallback and literal parameters in isolation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

source = Path(__file__).resolve().parents[1]
omarchy = Path(os.environ.get('OMARCHY_PATH', '/usr/share/omarchy'))
native = (omarchy / 'shell/Commons/Translations.qml').exists()
catalog = json.loads((source / 'i18n/no.json').read_text())
key = next(k for k, v in catalog.items() if k != v and '%' not in k and '{' not in k)
with tempfile.TemporaryDirectory(prefix='foamy-language-test-') as directory:
    base = Path(directory)
    app = base / 'app'
    app.mkdir()
    plugin = app / 'plugin'
    plugin.mkdir()
    shutil.copytree(source / 'kit', plugin / 'kit')
    shutil.copytree(source / 'i18n', plugin / 'i18n')
    shutil.copytree(source / 'components', plugin / 'components')
    for name in ('NotificationLogic.js', 'Markup.js', 'BrowserIdentity.js'): shutil.copy(source / name, plugin)
    # The added catalog entry exists only in this fixture, never in a release.
    en = json.loads((plugin / 'i18n/en.json').read_text())
    en['English only'] = 'English only'
    (plugin / 'i18n/en.json').write_text(json.dumps(en))
    (app / 'Commons').symlink_to(omarchy / 'shell/Commons', target_is_directory=True)
    language = base / 'language'
    language.write_text('no')
    runtime = base / 'runtime'
    runtime.mkdir(mode=0o700)
    qml = '''import QtQuick
import Quickshell
import Quickshell.Io
import "plugin/kit" as Plugin
import "plugin/components"
ShellRoot {
  property var translator: Plugin.FoamyI18n
  property string rendered: translator.t(KEY)
  function child(item, name) {
    if (item.objectName === name) return item
    for (var nested of item.children || []) {
      var match = child(nested, name)
      if (match) return match
    }
    return null
  }
  FloatingWindow {
    visible: true; implicitWidth: 420; implicitHeight: 350
    NotificationCard {
      id: card
      x: 10; y: 10; width: 280
      app: "Preview"; summary: "Sign-in"; body: "Sample notification"
      timestamp: 1790930400000; count: 12
      snoozeAvailable: true
      offers: ({codes:"482913 731208", link:"https://example.com", meeting: stepper.step % 2 === 1})
      copiedCode: "482913"
      deeds: [{kind:"code", value:"482913", text:""}, {kind:"code", value:"731208", text:""}, {kind:"link", value:"https://example.com", text:"", meeting: stepper.step % 2 === 1}]
    }
  }
  FileView { id: choice; path: LANGUAGE_FILE }
  Timer {
    id: stepper
    property int step: 0
    interval: 250; running: true; repeat: true
    onTriggered: {
      var expected = NATIVE && (step === 0 || step === 4) ? TRANSLATED : KEY
      if (rendered !== expected) { console.error("Wrong translation", step, rendered, expected); Qt.exit(1); return }
      if (translator.t("English only") !== "English only" || translator.t("Unknown label") !== "Unknown label") { Qt.exit(2); return }
      if (translator.t("Name %1 {name}", ["$& %2"]) !== "Name $& %2 {name}") { Qt.exit(3); return }
      if (translator.t("Name {name}", {name: "$& {name}"}) !== "Name $& {name}") { Qt.exit(4); return }
      var copy = child(card, "notificationCopyCode"), link = child(card, "notificationOpenLink")
      if (copy.text !== translator.t("Copied %1", "482913") || link.text !== translator.t(step % 2 === 1 ? "Join meeting" : "Open link")) { console.error("Stale footer translation", step, copy.text, link.text); Qt.exit(5); return }
      var snooze = child(card, "notificationSnooze"), time = child(card, "notificationTime")
      if (snooze.Accessible.name !== translator.t("Snooze this sender")) { Qt.exit(6); return }
      var rtl = NATIVE && step === 3
      if (translator.rightToLeft !== rtl || (snooze.mapToItem(card,0,0).x > time.mapToItem(card,0,0).x) !== rtl) { console.error("Wrong header direction", step); Qt.exit(7); return }
      var buttons = Array.from(child(card,"notificationFooter").children).filter(function(item) { return item.entry !== undefined })
      if ((buttons[0].x > buttons[1].x) !== rtl) { console.error("Wrong footer direction", step); Qt.exit(8); return }
      for (var button of buttons) {
        if (button.mapToItem(card,0,0).x < 0 || button.mapToItem(card,button.width,0).x > card.width) { Qt.exit(9); return }
      }
      var decimal = NATIVE && (step === 0 || step === 1 || step === 4) ? "13,1" : "13.1"
      if (translator.number(13.14, 1) !== decimal) { console.error("Wrong number format", step); Qt.exit(10); return }
      if (step === 0) choice.setText("da")
      else if (step === 1) choice.setText("en")
      else if (step === 2) choice.setText("ar")
      else if (step === 3) choice.setText("no")
      else { console.log("I18N_RUNTIME_PASS", NATIVE ? "4.5 live no/da/en/ar/no with card bindings and RTL" : "4.0 English fallback"); Qt.quit() }
      step++
    }
  }
}
'''
    qml = qml.replace('LANGUAGE_FILE', json.dumps(str(language))).replace('TRANSLATED', json.dumps(catalog[key])).replace('KEY', json.dumps(key)).replace('NATIVE', str(native).lower())
    (app / 'shell.qml').write_text(qml)
    env = dict(os.environ, OMARCHY_PATH=str(omarchy), OMARCHY_LANGUAGE_FILE=str(language),
               XDG_RUNTIME_DIR=str(runtime), QT_QPA_PLATFORM='offscreen',
               QT_QPA_PLATFORMTHEME='generic', QT_QUICK_BACKEND='software')
    env.pop('WAYLAND_DISPLAY', None)
    result = subprocess.run(['quickshell', '-p', str(app), '--no-color'], env=env,
                            capture_output=True, text=True, timeout=10)
    output = result.stdout + result.stderr
    if result.returncode or 'I18N_RUNTIME_PASS' not in output or any(e in output for e in ['ReferenceError', 'TypeError', 'Unable to assign']):
        raise AssertionError(output)
    print(source.name, '4.5 live no/da/en/ar/no with card bindings and RTL' if native else '4.0 English fallback', 'passed')
