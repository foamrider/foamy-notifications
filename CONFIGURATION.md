# Foamy Notifications configuration

## Settings

Configure the `foamy.notifications` entry in `~/.config/omarchy/shell.json`.
Changes are watched; invalid settings report an error and retain the last valid
configuration. Settings are file-based, with no extra settings panel.

```json
{
  "id": "foamy.notifications",
  "lowTimeoutSec": 5,
  "normalTimeoutSec": 8,
  "criticalTimeoutSec": 0,
  "maxVisible": 3,
  "groupDuplicates": true,
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
| `maxVisible` | 1–10 groups. Screen space may reduce this further. Older groups go to history. |
| `groupDuplicates` | Groups exact duplicates. Different actions or sending applications remain separate. |
| `useBrowserFavicons` | `true` (default) uses the identified website’s locally cached favicon in the header. Missing, unsupported or failed favicons fall back to the app icon, then the glyph. Independent of `showImages`. |
| `browserGrouping` | `browser` (default) and `hostname` preserve exact-duplicate grouping; the leading origin already keeps different websites separate. `none` disables duplicate grouping for browser notifications. Native duplicates still follow `groupDuplicates`. |
| `monitor` | `focused`, `pointer`, `named`, or `all`. Missing named outputs fall back to the focused output. Pointer placement updates when a notification arrives. |
| `monitorName` | Output name used with `named`, for example `DP-1`. |
| `suppressFullscreen` | Sends notifications directly to history when the target output is fullscreen. Existing popups are also archived when all their target outputs become fullscreen. |
| `fullscreenCritical` | Allows critical alerts through fullscreen suppression. DND remains a separate control. |
| `width` | 280–600 shell spacing units; constrained to the screen. |
| `restoreMaxAgeSec` | 0–3600. Older popups go to history after a shell restart. |
| `compact` | `true` (default) uses a slim header and tighter content spacing; `false` adds room around the header and message. Text and sender-image sizes stay unchanged. |
| `showTimeoutIndicator` | `true` (default) shows a subtle 2 px straight line inset from the rounded bottom corners for timed popups. It shrinks smoothly with the shared frame-based expiry clock, pauses on hover, expanded actions or an action in progress, and is hidden for persistent notifications. `false` hides it without changing timeout behavior. |
| `showImages` | Shows the sender image beside the message. Defaults to `true`. |
| `imageSize` | 40–72 shell spacing units; defaults to 56. Images fit without stretching or cropping. |

On `all` monitors, fullscreen outputs hide normal popups while other outputs can
still show them. Group expiry has one shared clock, so it does not run faster on
multiple monitors. Hovering any copy pauses the group.

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

Notification Center has the same `useBrowserFavicons` and `browserGrouping`
settings on its bar-widget entry. Its `hostname` mode creates separate website
stacks; `none` leaves browser messages unstacked. These settings are independent
between the plugins. Missing origins remain under the browser. Grouping does
not change notification click targets or identify browser profiles/accounts.

The plugin uses a browser notification's leading website origin to select its
web-app window. It accepts a leading HTTP(S) URL or link, and a bare hostname
on its own first line, as supplied by Vivaldi. It does not treat links inside
the message as app identity. This lookup is shared with Notification Center
and is used by popups when no live default action is available. Live popup
actions still go to the sender so it can open the specific conversation.
When multiple profiles have the same website open, the notification may not
contain enough information to choose one. An exact mapping resolves that case:

```json
"browserMappings": [
  {"origin": "teams.microsoft.com", "windowClass": "vivaldi-teams.microsoft.com__-Profile_1"}
]
```

Mappings select one window class per origin. They cannot recover account identity
that the sender did not supply. Ambiguous or unavailable targets display an error
on the popup rather than selecting a random browser window. Failed actions keep
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

## State and privacy

Foamy Notification Center is optional. Its public
`foamy.notification-center.store handled` IPC method is required for immediate
unread-badge updates after a handled click. The helper commits removal first;
this acknowledgement updates the UI without rewriting the archive again.

The plugin shares Omarchy's notification state under
`$XDG_STATE_HOME/omarchy/notifications` (normally `~/.local/state/omarchy/notifications`).
DND is stored in the adjacent `notifications.json` file. Foamy Notification Center
continues to manage its own archive and retention.

Notification content reaches persistence and window-matching helpers through
standard input, not process arguments. Adjacent queued snapshots for the same notification are coalesced, while reads,
actions, and deletes keep their ordering. Unchanged local image copies retain
their inode and modification time so the center can reuse its thumbnails.

New state files use private permissions
and atomic replacement. Text and restore reads are bounded; optional image copies
are limited to regular local files up to 5 MiB. Sender image tags are filtered
before rendering. No network image retrieval is added.

Critical alerts remain persistent by default, but restored popups are subject to
the configured maximum age. No message content appears in the diagnostic IPC:

```sh
omarchy-shell notifications ping
omarchy-shell foamy.notifications state
```

## Validation

```sh
omarchy plugin validate .
node --test tests/*.test.cjs
python3 -m unittest discover -s tests -p '*_test.py' -v
python3 tests/render.py
# In a Wayland desktop, verify GPU-rendered rounded image masks:
python3 tests/render.py --desktop
python3 tests/ui.py
python3 tests/browser-ui.py
python3 tests/motion.py
# In a Wayland desktop, verify animation frames on a real surface:
python3 tests/motion.py --desktop
# Requires Wayland; checks first-popup mapping and final-popup unmapping:
python3 tests/layer.py
python3 tests/runtime.py
# Also exercise the center, if its checkout is available:
FOAMY_CENTER_SOURCE=/path/to/foamy-notification-center python3 tests/runtime.py
```

The render test uses the real card with synthetic data at wide and narrow widths.
The runtime test exercises actual notification D-Bus traffic on a private bus and
home directory. Qt's offscreen platform cannot create layer-shell surfaces, so
that test omits only the popup windows. Complete Wayland placement, hover, and
fullscreen behavior must also be checked in a desktop session.

