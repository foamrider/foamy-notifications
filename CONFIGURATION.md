# Foamy Notifications configuration

## Settings

Configure the `foamy.notifications` entry in `~/.config/omarchy/shell.json`.
Changes are watched; invalid settings report an error and retain the last valid
configuration. The companion Notification Center exposes common popup settings
under **Settings → Foamy Notifications**; all settings remain in this entry.

```json
{
  "id": "foamy.notifications",
  "lowTimeoutSec": 5,
  "normalTimeoutSec": 8,
  "criticalTimeoutSec": 0,
  "maxVisible": 5,
  "groupDuplicates": true,
  "closeButtonRemovesHistory": false,
  "dndSilenceCritical": false,
  "offerSnoozeWhenSharing": true,
  "useBrowserFavicons": true,
  "browserGrouping": "browser",
  "monitor": "focused",
  "monitorName": "",
  "suppressFullscreen": false,
  "fullscreenCritical": true,
  "width": 390,
  "restoreMaxAgeSec": 300,
  "compact": true,
  "showTimeoutIndicator": true,
  "showImages": true,
  "imageSize": 56,
  "browserMappings": []
}
```

| Setting | Behavior |
| --- | --- |
| `lowTimeoutSec`, `normalTimeoutSec` | 1–120 seconds. Longer sender-requested reading times are honored up to 120 seconds. |
| `criticalTimeoutSec` | 0 keeps critical alerts until dismissed; 1–120 sets an expiry. |
| `maxVisible` | 1–10 cards (groups) on screen; 5 by default. Screen space may reduce this further. Cards past either limit wait in the [overflow queue](#overflow-queue) instead of going to history. |
| `groupDuplicates` | Groups exact duplicates. Different actions or sending applications remain separate. |
| `closeButtonRemovesHistory` | `false` (default) keeps notifications in history when × closes a popup. `true` removes the popup's whole duplicate group from history without invoking any action. Right-click, expiry, and keyboard/IPC dismissal still keep history. Failed removal leaves the popup available for retry. |
| `dndSilenceCritical` | `false` (default) preserves trusted critical alerts through Do Not Disturb. `true` sends all new critical alerts, including Omarchy crash notifications, directly to history during DND. Transient alerts are not stored. Normal Omarchy action feedback and delivery outside DND are unchanged. |
| `offerSnoozeWhenSharing` | `true` (default) shows a prompt when screen sharing starts, offering to snooze all notifications for 30 minutes or keep them. It is not shown during DND or an active snooze. `false` never shows it; sharing never mutes notifications by itself. |
| `useBrowserFavicons` | `true` (default) uses the identified website’s locally cached favicon in the header. Missing, unsupported or failed favicons fall back to the app icon, then the glyph. Independent of `showImages`. |
| `browserGrouping` | `browser` (default) and `hostname` preserve exact-duplicate grouping; the leading origin already keeps different websites separate. `none` disables duplicate grouping for browser notifications. Native duplicates still follow `groupDuplicates`. |
| `monitor` | `focused`, `pointer`, `named`, or `all`. Missing named outputs fall back to the focused output. Pointer placement updates when a notification arrives. |
| `monitorName` | Output name used with `named`, for example `DP-1`. |
| `suppressFullscreen` | Sends notifications directly to history when the target output is fullscreen. Existing popups are also archived when all their target outputs become fullscreen. |
| `fullscreenCritical` | Allows critical alerts through fullscreen suppression. DND remains a separate control. |
| `width` | 280–600 shell spacing units; constrained to the screen. |
| `restoreMaxAgeSec` | 0–3600. Older popups go to history after a shell restart. |
| `maxWaitSec` | 1–3600; 300 by default. Not in the center's settings. A normal notification that waits longer than this in the [overflow queue](#overflow-queue) goes to history. Critical alerts are not affected. |
| `compact` | `true` (default) uses a slim header and tighter content spacing; `false` adds room around the header and message. Text and sender-image sizes stay unchanged. |
| `showTimeoutIndicator` | `true` (default) shows a subtle 2 px straight line inset from the rounded bottom corners for timed popups. It shrinks smoothly with the shared frame-based expiry clock, pauses on hover, expanded actions or an action in progress, and is hidden for persistent notifications. `false` hides it without changing timeout behavior. |
| `showImages` | Shows the sender image beside the message. Defaults to `true`. |
| `imageSize` | 40–72 shell spacing units; defaults to 56. Images fit without stretching or cropping. |

On `all` monitors, fullscreen outputs hide normal popups while other outputs can
still show them. Group expiry has one shared clock, so it does not run faster on
multiple monitors. Hovering any copy pauses the group.

### Overflow queue

When `maxVisible` cards are on screen, or no further card fits the screen
height, a new card waits in a queue instead of going to history. Nothing is
lost for lack of room:

- **Order.** Critical alerts go first, with one slot kept for the rest. A
  new critical alert takes the slot of the non-critical card shown last; that
  card waits again, ahead of the other waiting non-critical cards.
- **One slot for normal notifications.** When the limit (`maxVisible`, or
  what fits the screen) is 2 or more and a non-critical notification is
  shown or waiting, critical alerts take at most all slots but one. The last
  one goes to the non-critical card shown longest, else to the oldest
  waiting one. So a stack of critical alerts that never expire
  (`criticalTimeoutSec` 0) cannot keep everything else waiting; it does not
  change how long a critical alert stays. With a limit of 1, or with no
  non-critical card, critical alerts may take every slot. For example, at a
  limit of 3, one normal notification followed by three critical alerts
  shows the normal one and the two newest criticals; the first critical
  waits and shows when a slot frees.
- **Rotation.** Among critical alerts the newest win: when the critical
  alerts' slots are full, a newer critical rotates the oldest one back into
  the queue (not to history), and it shows again when a slot frees. So
  alerts that never expire cannot hold every slot and keep later alerts
  waiting. Waiting cards show one per freed slot (when a card is dismissed,
  expires, or is handled by a click or an action) in queue order: critical
  alerts newest first, then the other cards in arrival order, oldest first,
  except that a freed normal slot goes to the next normal card. A hovered
  card, a card with an action in progress and the keyboard-selected card
  always keep their slot and count toward the limit (a pinned normal card
  fills the normal slot); a newer critical takes the slot of the oldest
  critical that is free to go.
- **Timers.** A waiting card's timer does not run. It starts when the card
  shows, so the card gets its full time on screen. A card that waits again
  starts over when it shows again.
- **Maximum wait.** A normal (non-critical) notification that waits more
  than 5 minutes (`maxWaitSec`, 300 by default) goes to history without
  showing, as if it expired; the Notification Center still lists it. The
  wait counts from when the card started waiting: when it arrived, or when
  it was sent back to the queue. Held cards count as waiting too (the
  hold's 30 seconds end first). A timer runs only while normal cards wait,
  and sends them to history on time even when nothing else happens. So a
  flood of notifications, from one app or many, cannot keep a later
  notification off the screen: it shows, or reaches history, within about
  5 minutes plus one card's time on screen. Critical alerts never age out:
  a new critical rotates in at once.
- **Placement.** A card from the queue joins the bottom of the stack. Cards
  on screen never move for it, and the hovered card stays under the pointer.
  New arrivals that fit appear on top, unless the pointer is over the stack
  (see [hold while hovered](#hold-while-hovered)). A small "+N waiting" note
  under the last card counts the waiting cards, held ones included; it
  disappears with the queue.
- **Screen space.** When the cards do not fit between the bar and the bottom
  of the screen, the non-critical card shown last waits again (unless it holds
  the normal slot; then the oldest critical alert waits), and the limit drops
  to what fits; the normal slot applies to that limit too. Each card that leaves lets one more try; a change of screen height
  (or of the settings) resets the limit to `maxVisible`. While the pointer is in
  the stack nothing is sent back; the check runs when it leaves.
- **Updates.** Replacements (`replaces_id`) and duplicates update a waiting
  card in place: it keeps its turn. A sender that closes a waiting
  notification (D-Bus `CloseNotification`) withdraws it: it never shows and
  goes to history. A card on screen stays when its sender closes it, without
  its actions, as before. Notification Center removals (`releaseHistory`,
  `releaseHistoryBefore`) and its clicks (`invokeDefault`) reach waiting
  cards too.
- **DND, snoozes and fullscreen.** A waiting card has not been seen, so it is
  treated like a new arrival: turning on DND sends it to history, unless it
  bypasses DND (trusted critical alerts, see `dndSilenceCritical`). Cards on
  screen stay, as before. A snooze hides its sender's cards, waiting or
  shown, and fullscreen suppression archives both alike, as before.
  `dismissAll` dismisses the waiting cards too; all of them go to history.
- **Restart.** Waiting cards keep their popup file, marked `"queued": true`,
  so a shell restart or reload restores them waiting, in order, once each.
  The file also keeps when the card started waiting (`waitingSince`), so a
  restart does not reset its maximum wait. Their expiry is not checked at
  restore (their timer has not started), but `restoreMaxAgeSec` applies, as
  to any popup. A card that showed after
  waiting is saved with its own `deadline`, so a restart judges it by the
  time it has been on screen. At most 200 popup files are restored.
- **Bound.** At most 100 notifications wait. Past that, the oldest waiting
  non-critical notification goes to history (if every waiting notification
  is critical, the oldest of those). Waiting notifications keep their sender
  callbacks, within the 256 live sources the service tracks.
- **Replays.** `showHistory` shows what fits and never queues: replayed
  entries are already in history.

`omarchy-shell foamy.notifications state` lists the waiting cards' keys in
queue order (`queue`), the held ones (`held`), whether the pointer holds a
stack (`pointerHolds`) and the current `visibleLimit`; `probe` counts the
waiting cards (`waiting`).

### Hold while hovered

As in stock Omarchy 4.5, whose notification deck holds new cards while the
pointer is over it, nothing enters the stack while the pointer is over it, so
nothing moves under the pointer:

- **New arrivals wait.** A new notification waits in the overflow queue, even
  when there is room, and the "+N waiting" note counts it. Cards that were
  waiting before the pointer arrived keep their turn and still join the
  bottom of the stack as cards leave.
- **Critical alerts wait too**, as in stock: on top, a critical would push
  down the card being read. The hold is short, and the alert shows the moment
  the pointer leaves.
- **Release.** When the pointer leaves the stack, or 30 seconds after the
  first card was held (stock's maximum, so a pointer resting on the stack
  cannot silence the desktop), the held cards show as the new arrivals they
  are: on top, newest first, with the usual entrance (at once with Omarchy's
  animations off). Critical alerts follow the queue's priority: one takes the
  slot of the normal card shown last, unless that is the normal slot, else of
  the oldest critical. Held cards
  that do not fit wait like any other. After a release at the maximum the
  pointer may still be on the stack, and the released cards push the hovered
  card down, as in stock; arrivals after that are held again.
- **Not held:** updates to a card on screen (`replaces_id` or a duplicate),
  which change it in place without moving it; `showHistory` replays and the
  empty-history message, which show now or not at all. A replacement of a
  held card stays held.
- **Slots.** The hovered card, a card with an action in progress and the
  keyboard-selected card keep their slots, as before. Unlike stock, keyboard
  mode alone does not hold arrivals: the selection follows its card.
- **Timers and restart.** A held card is a waiting card: its timer starts
  when it shows, its popup file is marked `"queued": true`, its time held
  counts toward the queue's maximum wait, and after a restart it comes back
  once, waiting, with its full time on screen.

### Browser apps

Favicons are read asynchronously from standard Linux Chromium-family profile
caches (Vivaldi, Chrome, Chromium, Brave, Edge and Opera), with no website
requests or extra dependencies. Firefox and custom profile locations fall back
to the app icon. Only a browser’s leading website origin identifies the site;
message links and sender avatars are never used as favicons. Results use a
private shared cache under `$XDG_CACHE_HOME/foamy/browser-favicons` (normally
`~/.cache/foamy/browser-favicons`), limited to 256 PNGs of at most 64 KiB and
256 × 256 pixels. Successful lookups refresh after one day; missing icons can
retry after five minutes when the display model next changes. Only the first
128 website groups in a display model are looked up.

Browser databases are opened read-only. If a rollback database is locked, the
helper can read an in-memory copy of at most 32 MiB. It rejects active
journals, WAL sidecars, changed files and copies that fail SQLite's integrity
check. Copying and querying share a two-second deadline. No database copies are
written to disk; the helper never writes to the browser database or removes its
locks. A cache that cannot be read safely keeps the app icon and reports
`cache-unavailable`.

Notification Center has the same `useBrowserFavicons` and `browserGrouping`
settings on its bar-widget entry. Its `hostname` mode creates separate website
stacks; `none` leaves browser messages unstacked. These settings are independent
between the plugins. Missing origins remain under the browser. Grouping does
not change notification click targets or identify browser profiles/accounts.

The plugin uses a browser notification's leading website origin to select its
web-app window. It accepts a leading HTTP(S) URL or link, and a bare hostname
on its own first line, as supplied by Vivaldi. It does not treat links inside
the message as app identity. This lookup is shared with Notification Center.

For a Chromium-family browser (Vivaldi, Chrome, Chromium, Brave, Edge), a click
on a notification that names its website focuses that website's app window
(`vivaldi-outlook.office.com__-Profile_1` and similar) instead of sending the
click to the browser. The shell's notification server gives the browser no
activation token, so the browser cannot raise an app window itself and sites
such as Outlook open a new tab instead. Focusing brings the app forward; it
does not open the specific message. Only when the website has no app window
does the click go to the browser, as for any other sender. This applies to
popup clicks and to clicks in Notification Center.

When multiple profiles have the same website open, the notification does not
say which profile sent it. An exact mapping names the window to use:

```json
"browserMappings": [
  {"origin": "teams.microsoft.com", "windowClass": "vivaldi-teams.microsoft.com__-Profile_1"}
]
```

Without a mapping (or when the mapped window is gone), Omarchy's
`omarchy-hyprland-focus-app` chooses among that website's app windows and takes
the one focused last. Mappings select one window class per origin. They cannot
recover account identity that the sender did not supply. When no window can be
focused, the popup shows an error rather than selecting a random browser window. Failed actions keep
the message and its history available for retry. Successful actions mark it handled
and remove it from the center. Structured shell commands are marked handled before
launch because they may restart the shell.

Up to two non-settings actions appear below the message. Settings (identified by
the action ID `settings`) and remaining actions are available under ⋯. Expanding
that list pauses expiry until it closes. Labels come from the sender; a Reply
action does not imply an inline text field. Critical alerts have a red edge that
follows and tapers through both left corners.

Sender images, such as Teams avatars and screenshot previews, share one rounded
slot to the left of the message. The app icon or website favicon stays in the header, with a glyph
fallback. Missing, failed, or disabled images leave no empty slot. Clicking the
image invokes the same default action as clicking the message. No browser-specific
image classification is needed.

Native actions work while their sender remains live. Restored notifications do
not show dead action buttons. Omarchy's structured `omarchy-exec-argv` action
continues to work; raw shell-command strings are not interpreted.

Popups enter with a 180 ms fade and an 8 px upward movement, then fade out over
120 ms. Stack gaps close over 140 ms after the pointer leaves the stack. Expired
cards stop accepting input immediately; the final popup surface closes when its
fade finishes. Replacements and duplicate updates keep the existing card.

## Caveat: raw `image-data` senders lose live actions

Apps that send avatars as raw `image-data` pixels (many GTK/libnotify apps,
Telegram, Thunderbird, …) lose their sender buttons (Reply, Mark read) and
in-place updates in Foamy: Quickshell keeps the raw buffer for as long as the
native notification is tracked, so Foamy releases those notifications at once
instead of letting the buffers accumulate in popups and retained history. Stock
4.5 keeps both. Text, Omarchy `--action`/`--exec` commands, favicons, theme
icons and app-focus clicks are unaffected, as are notifications with file images.

## State and privacy

Foamy keeps its own state in `$XDG_STATE_HOME/foamy-notifications/`
(normally `~/.local/state/foamy-notifications/`, mode 0700): `popups/` (one
file per visible or waiting popup), `history/` (newest 100), `images/` (normalized PNGs),
`state.json` (DND and snoozes) and `handled` (keys of handled notifications,
newest 1,000, read by Notification Center). Omarchy 4.5 owns
`~/.local/state/omarchy/notifications`; on its first start this version moves
Foamy's old files out of there once (see README, "Omarchy 4.5"). All writes go
through one long-lived helper process (`foamy-notifications-helper serve`).

Notification Center is optional and never touches these files: it reads them
(paths from `foamy.notifications storeInfo`) and asks Foamy over IPC to drop
entries (`releaseHistory`, `releaseHistoryBefore`) or change settings
(`setPreference`). Foamy tells the center about handled clicks with
`foamy.notification-center.store handled`.

DND bypass trusts `app_name` `omarchy-action` and critical `notify-send`, as
stock does. Any session-bus client can claim those names, so DND is a courtesy
filter, not a security boundary.

Notification content reaches persistence and window-matching helpers through
standard input, not process arguments. Adjacent queued snapshots for the same notification are coalesced, while reads,
actions, and deletes keep their ordering. Normalized PNGs use content-addressed filenames. Text-only updates reuse the
existing file; picture replacements get a new URL so Qt cannot reuse old pixels.

New state files use private permissions
and atomic replacement. Text and restore reads are bounded. Regular local image
files up to 5 MiB pass through the bounded external decoder described in the
README before the popup or restored history sees them. Progressive JPEG, PNG,
GIF, WebP, BMP, ICO and self-contained SVG images remain supported, including
transparency. SVG linked resources are unavailable. Failed, oversized or missing
file images retain text and actions and use the icon fallback; no original-image
fallback is used. Raw `image-data` provider URLs are never rendered because
Quickshell does not expose the buffer for plugin-side validation. Raw-image native
notifications are released before tracking or queuing; raw replacements also
release existing popup/history callbacks. Their bounded text snapshots remain,
but sender actions and further in-place updates are unavailable. Clicks use app
focus instead. A local `image-path` is converted if it survives native closure.
File-only and text-only notifications retain callbacks. Body markup follows stock 4.5: only `b i u s em strong br` render; other tags
stay literal text, and links are offered as footer buttons, never as clickable text. App names, titles, action
labels and their tooltips are plain text. No network image retrieval is added.

Critical alerts remain persistent by default, but restored popups are subject to
the configured maximum age. No message content appears in the diagnostic IPC:

```sh
omarchy-shell notifications ping
omarchy-shell foamy.notifications state
```

## Validation

The helper is Rust in `native/` (its contract is the comment at the top of
`native/src/main.rs`), committed as static binaries in `bin/x86_64/` and
`bin/aarch64/`. After changing it, rebuild and check with the build tool in `kit/native/`:

```sh
kit/native/build-native.sh . --test    # fmt, clippy, cargo tests
kit/native/build-native.sh .           # both arches, native/native.pin
kit/native/build-native.sh . --verify  # byte-identical clean rebuild
bash tests/kit-check.sh
# The black-box tests run any build (aarch64 through qemu-user binfmt):
FOAMY_HELPER="[\"$PWD/bin/aarch64/foamy-notifications-helper\"]" python3 -m unittest discover -s tests -p '*_test.py'
```

The tests need Python 3; the plugin does not.

```sh
omarchy plugin validate .
node --test tests/*.test.cjs
python3 -m unittest discover -s tests -p '*_test.py' -v
python3 tests/render.py
# In a Wayland desktop, verify GPU-rendered rounded image masks:
python3 tests/render.py --desktop
python3 tests/ui.py
python3 tests/browser-ui.py
# Real popup image intake/rendering on a private D-Bus session:
python3 tests/images-ui.py
python3 tests/motion.py
# In a Wayland desktop, verify animation frames on a real surface:
python3 tests/motion.py --desktop
# Requires Wayland; checks first-popup mapping and final-popup unmapping, and
# that cards which do not fit the real screen wait in the queue:
python3 tests/layer.py
python3 tests/runtime.py
# The overflow queue: order, timers, replacements, CloseNotification, DND,
# restart survival, screen space, the hover rule and the hold while hovered:
python3 tests/overflow-runtime.py
# Stalled-worker floods, raw 4 MiB buffers, replacements and context backlog:
python3 tests/queue-runtime.py
# Center preference saves through the host API (shell.updateEntryInline):
python3 tests/preferences-runtime.py
# Also exercise the center, if its checkout is available:
FOAMY_CENTER_SOURCE=/path/to/foamy-notification-center python3 tests/runtime.py
```

The render test uses the real card with synthetic data at wide and narrow widths.
The runtime and queue tests use the installed PyGObject/Gio bindings to send raw
pixel buffers without command-line size limits. They exercise actual notification
D-Bus traffic on a private bus and temporary home directory. Qt's offscreen platform cannot create layer-shell surfaces, so
that test omits only the popup windows. Complete Wayland placement, hover, and
fullscreen behavior must also be checked in a desktop session.

### Omarchy 4.5 additions

`offerSnoozeWhenSharing` (boolean, default `true`) controls the screen-sharing
snooze prompt. It is also available in Notification Center's settings. Snoozes
are bounded to 128 source entries and persist beside DND in `state.json`.
The service keeps its per-file format in its own
directory; it never writes stock Omarchy's `live.json`, `history.json` or `quiet.json`.

Verification-code detection, redaction, validated link buttons and the sender
snooze control are built in. Copying needs the standard `wl-clipboard` tools;
missing tools report a copy error without affecting notifications.

Additional checks:

```sh
python3 tests/features-runtime.py
python3 tests/clipboard_test.py
```

The feature runtime test starts a private D-Bus session and private PipeWire
server, uses a fake clipboard, and keeps all state in temporary directories.
The tests load the installed Omarchy 4.5 shell (`/usr/share/omarchy`; only its
QML and data, never its scripts). Set `OMARCHY_TEST_ROOT` to another Omarchy
tree, or `FOAMY_HOST_45` to its `shell/` directory, to exercise its Commons/Ui
components with `runtime.py`, `features-runtime.py`, `render.py`, and `ui.py`,
without installing that release. Omarchy 4.0 checks are opt-in through
`FOAMY_HOST_40`. This does not test a complete OS upgrade.
