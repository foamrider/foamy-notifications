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


DESKTOP_OPT_IN = "FOAMY_TEST_DESKTOP"


def desktop_opt_in(script):
    """Stop a run that draws real surfaces on the live compositor (layer-shell
    popups on the user's screen) unless FOAMY_TEST_DESKTOP=1 asks for it.
    Call it before the test creates anything."""
    if os.environ.get(DESKTOP_OPT_IN) != "1":
        raise SystemExit(
            f"{Path(script).name}: refused: it draws real surfaces on the live desktop; "
            f"set {DESKTOP_OPT_IN}=1 to run it on purpose"
        )


def make(base, app, env, session_bus=None, display=False):
    """`env` (changed in place and returned) for a qs or client the test starts:
    hostenv.isolate() (no host bin dirs or AppImage libraries, dead audio,
    system-bus and session-bus addresses, no display) and hostenv.make() (a
    private OMARCHY_PATH whose omarchy-* log to <base>/omarchy-calls.log;
    omarchy-shell reaches this test's shell only), with the focus-app double
    above. A test inside session() passes its private bus as `session_bus`
    (`os.environ["DBUS_SESSION_BUS_ADDRESS"]`); one that runs on the live
    compositor on purpose passes `display=True`."""
    base = Path(base)
    hostenv.isolate(env, base, session_bus=session_bus, display=display)
    stubs = {"omarchy-hyprland-focus-app": FOCUS_APP.format(log=base / hostenv.CALLS_LOG)}
    env.update(hostenv.make(base, app, env=env, stubs=stubs, names=("omarchy-notification-send",)))
    if not env.get("LD_LIBRARY_PATH"):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def run(argv, env, log, timeout):
    """Run `argv` (a qs the test starts) as its own process group until it
    exits or `timeout` seconds pass, then stop the whole group, so the
    scripts its Processes started go too (hostenv.ProcessGroup). Returns the
    exit status (negative: the signal that stopped it) and the output, which
    goes to the file `log`."""
    log = Path(log)
    with log.open("w") as out, hostenv.ProcessGroup(argv, env=env, stdout=out, stderr=subprocess.STDOUT) as shell:
        shell.wait(timeout)
    return shell.returncode, log.read_text()


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
    inner = hostenv.ProcessGroup(["dbus-run-session", "--", sys.executable, str(script), "--inside", *args])
    try:
        code = inner.wait(timeout)
        if code is None:
            print(f"{Path(script).name}: no result within {timeout} s, stopping it", file=sys.stderr, flush=True)
            code = 124
    finally:
        # The whole group, also what the run left behind when it ended.
        inner.stop(grace=10)
    raise SystemExit(code)
