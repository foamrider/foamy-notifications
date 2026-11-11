"""foamy-kit's shared test helpers, plus this plugin's host doubles.

The runtime tests start their own `qs`, `notify-send` and `gdbus` instead of
going through harness.Run, so they take the kit's preview/hostenv.py from the
foamy-kit checkout (FOAMY_KIT, default ~/GitHub/Personal/foamy-kit) like the
harness, and apply its isolation and private Omarchy tree through make().
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

KIT = Path(os.environ.get("FOAMY_KIT") or Path.home() / "GitHub/Personal/foamy-kit")
sys.path.insert(0, str(KIT / "preview"))
import hostenv  # noqa: E402

# Test double of Omarchy's omarchy-hyprland-focus-app (same contract): focus the
# newest window whose class matches, through the test's own hyprctl stub.
FOCUS_APP = (
    '#!/bin/bash\nprintf "%s\\n" "$0 $*" >> "{log}"\n'
    'address=$(hyprctl clients -j 2>/dev/null | jq -r --arg p "$1" \'def m($v): ($v // "") | test($p; "i"); '
    'sort_by(.focusHistoryID) | first((.[] | select(m(.class))), (.[] | select(.initialClass == "org.omarchy.agent" and m(.initialTitle)))).address // empty\')\n'
    "[[ -n $address ]] || exit 1\n"
    'hyprctl dispatch "hl.dsp.focus({{ window = \\"address:$address\\" }})"\n'
)


def make(base, app, env):
    """`env` (changed in place and returned) for a qs or client the test starts:
    hostenv.isolate() (no host bin dirs or AppImage libraries, dead audio and
    system-bus addresses) and hostenv.make() (a private OMARCHY_PATH whose
    omarchy-* log to <base>/omarchy-calls.log; omarchy-shell reaches this
    test's shell only), with the focus-app double above."""
    base = Path(base)
    hostenv.isolate(env, base)
    stubs = {"omarchy-hyprland-focus-app": FOCUS_APP.format(log=base / hostenv.CALLS_LOG)}
    env.update(hostenv.make(base, app, env=env, stubs=stubs, names=("omarchy-notification-send",)))
    if not env.get("LD_LIBRARY_PATH"):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def calls(base):
    """The stubs' log as one string, for hostenv.check()."""
    return "\n".join(hostenv.calls(base))


def session(script, timeout, *args):
    """Run the test on a private session bus. Outside (no `--inside`): re-runs
    `script --inside *args` under dbus-run-session and exits with its status;
    after `timeout` seconds it stops the whole run with SIGTERM (SIGKILL 10 s
    later). Inside: SIGTERM raises SystemExit, so the test's `finally` stops
    the qs it started; a killed wrapper used to leave it running."""
    if "--inside" in sys.argv:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
        return
    inner = subprocess.Popen(
        ["dbus-run-session", "--", sys.executable, str(script), "--inside", *args], start_new_session=True
    )
    try:
        code = inner.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"{Path(script).name}: no result within {timeout} s, stopping it", file=sys.stderr, flush=True)
        code = 124
    finally:
        if inner.poll() is None:
            os.killpg(inner.pid, signal.SIGTERM)
            try:
                inner.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(inner.pid, signal.SIGKILL)
                inner.wait()
    raise SystemExit(code)
