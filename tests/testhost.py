"""foamy-kit's shared test helpers, plus this plugin's host doubles.

The runtime tests start their own `qs`, `notify-send` and `gdbus` instead of
going through harness.Run, so they take the kit's preview/hostenv.py from the
foamy-kit checkout (FOAMY_KIT, default ~/GitHub/Personal/foamy-kit) like the
harness, and apply its isolation and private Omarchy tree through make().
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
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
# What points at the live desktop session. A test that runs on the live
# desktop on purpose keeps DESKTOP: the display, the compositor and the
# session's runtime directory, which holds the Wayland socket. No test keeps
# the rest: the session's bus, logind's names for the session and its seat,
# the user's agents and credentials, the audio servers, the terminal and
# multiplexer around the caller, and the service manager that started it.
DESKTOP = (
    "WAYLAND_DISPLAY",
    "WAYLAND_SOCKET",
    "DISPLAY",
    "XAUTHORITY",
    "HYPRLAND_INSTANCE_SIGNATURE",
    "XDG_RUNTIME_DIR",
)
LIVE_SESSION = DESKTOP + (
    "DBUS_SESSION_BUS_ADDRESS",
    "DBUS_STARTER_ADDRESS",
    "DBUS_STARTER_BUS_TYPE",
    "XDG_SESSION_ID",
    "XDG_SESSION_PATH",
    "XDG_SEAT",
    "XDG_SEAT_PATH",
    "XDG_VTNR",
    "XDG_ACTIVATION_TOKEN",
    "DESKTOP_STARTUP_ID",
    "WINDOWID",
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "GPG_AGENT_INFO",
    "GNOME_KEYRING_CONTROL",
    "KRB5CCNAME",
    "PULSE_SERVER",
    "PULSE_RUNTIME_PATH",
    "PULSE_COOKIE",
    "PIPEWIRE_REMOTE",
    "PIPEWIRE_RUNTIME_DIR",
    "TMUX",
    "TMUX_PANE",
    "ALACRITTY_SOCKET",
    "NOTIFY_SOCKET",
    "LISTEN_FDS",
    "LISTEN_FDNAMES",
    "LISTEN_PID",
    "JOURNAL_STREAM",
    "INVOCATION_ID",
    "MANAGERPID",
    "MANAGERPIDFDID",
    "SYSTEMD_EXEC_PID",
)
# session()'s bus has no service directories, as the kit harness's private
# bus: it starts no host service (desktop portals, keyring, input method)
# that would run with the user's home and reach the live session.
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:dir={folder}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


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


def passed(base, captures=()):
    """The end of a run that passed: remove the run's own directory `base`
    (made by tempfile.mkdtemp), so that passing runs leave little in /tmp,
    whose quota the tests of all agents share. A visual test names its PNG
    `captures`, because they are the evidence of the run: the ones it took
    stay where the test printed them, and everything else goes. A failed
    run never gets here and keeps its directory for diagnosis. The links in
    `base` (to the plugin and the host shell) are removed, never followed."""
    base = Path(base)
    keep = {Path(capture) for capture in captures if Path(capture).is_file()}
    if not keep:
        shutil.rmtree(base)
        print("Passed; removed", base)
        return
    prune(base, keep)
    print("Passed; kept", len(keep), "PNG captures and removed the rest of", base)


def prune(folder, keep):
    """Remove everything in `folder` but the files in `keep`. Only a real
    directory that holds one of them is entered; a link is removed itself."""
    for path in folder.iterdir():
        if path in keep:
            continue
        if path.is_symlink() or not path.is_dir():
            path.unlink()
        elif any(path in capture.parents for capture in keep):
            prune(path, keep)
        else:
            shutil.rmtree(path)


def session(script, timeout, *args, display=False):
    """Run the test on a private session bus. Outside (no `--inside`): re-runs
    `script --inside *args` under dbus-run-session and exits with its status;
    after `timeout` seconds it stops the whole run with SIGTERM (SIGKILL 10 s
    later). The bus starts no service (BUS_CONFIG), and neither it nor the
    test gets LIVE_SESSION; the test gets a private XDG_RUNTIME_DIR instead.
    A test that runs on the live desktop on purpose passes `display=True`
    and keeps DESKTOP; that needs FOAMY_TEST_DESKTOP=1. Inside: SIGTERM raises
    SystemExit, so the test's `finally` stops the qs it started; a killed
    wrapper used to leave it running."""
    if "--inside" in sys.argv:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
        return
    if display:
        desktop_opt_in(script)
    with tempfile.TemporaryDirectory(prefix="foamy-notifications-session-") as folder:
        env = {
            name: value
            for name, value in os.environ.items()
            if name not in LIVE_SESSION or (display and name in DESKTOP)
        }
        if not display:
            runtime = Path(folder) / "runtime"
            runtime.mkdir(mode=0o700)
            env["XDG_RUNTIME_DIR"] = str(runtime)
        config = Path(folder) / "bus.conf"
        config.write_text(BUS_CONFIG.format(folder=folder))
        command = ["dbus-run-session", f"--config-file={config}", "--", sys.executable, str(script), "--inside", *args]
        inner = hostenv.ProcessGroup(command, env=env)
        try:
            code = inner.wait(timeout)
            if code is None:
                print(f"{Path(script).name}: no result within {timeout} s, stopping it", file=sys.stderr, flush=True)
                code = 124
        finally:
            # The whole group, also what the run left behind when it ended.
            inner.stop(grace=10)
    raise SystemExit(code)
