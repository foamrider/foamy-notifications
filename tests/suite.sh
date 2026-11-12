#!/bin/bash
# foamy-notifications' own tests; tests/validate.sh runs this after the
# linters.
#   --ci    no Quickshell, no Omarchy host: the node tests and the host-free
#           Python tests (the helper runs as a process from bin/x86_64; the
#           image tests skip themselves without ImageMagick 7 and file)
#   --fast  skip the Quickshell runtime tests
# Without --ci the helper suites also run the aarch64 build when binfmt has
# qemu-aarch64. images-ui and layer put real layer-shell popups on the live
# desktop, so they run only when FOAMY_TEST_DESKTOP=1 asks for them (and a
# Wayland session is there); the scripts refuse to start without it too.
set -uo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." || exit 1
ci=0 fast=0
for arg in "$@"; do
  case $arg in
    --ci) ci=1 ;;
    --fast) fast=1 ;;
    *)
      echo "suite.sh: unknown argument $arg" >&2
      exit 2
      ;;
  esac
done
export FOAMY_KIT=${FOAMY_KIT:-$HOME/GitHub/Personal/foamy-kit}
status=0
run() {
  echo "== $*"
  "$@" || status=1
}
skip() { echo "SKIP $*"; }

run node --test tests/*.test.cjs
run python3 -m unittest discover -s tests -p '*_test.py'

helper=bin/aarch64/foamy-notifications-helper
if ((ci)); then
  skip "the aarch64 helper: host test (qemu-aarch64 through binfmt)"
elif [[ -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]]; then
  FOAMY_HELPER="[\"$PWD/$helper\"]" run python3 -m unittest discover -s tests -p 'helper_test.py'
  FOAMY_HELPER="[\"$PWD/$helper\"]" run python3 -m unittest discover -s tests -p 'favicon_test.py'
else
  skip "the aarch64 helper: no qemu-aarch64 binfmt entry"
fi

if ((ci || fast)); then
  skip "the Quickshell runtime tests (render, ui, browser-ui, motion, i18n, preferences-, features-, runtime, overflow- and queue-runtime, images-ui, layer)"
  exit $status
fi
# Quickshell must not load an AppImage's bundled libraries (an editor's).
unset LD_LIBRARY_PATH
run python3 tests/render.py
run python3 tests/ui.py
run python3 tests/browser-ui.py
run python3 tests/motion.py
run python3 "$FOAMY_KIT/preview/i18n_runtime.py" .
run python3 tests/i18n-runtime.py
run python3 tests/preferences-runtime.py
run python3 tests/features-runtime.py
run python3 tests/runtime.py
run python3 tests/overflow-runtime.py
run python3 tests/queue-runtime.py
if [[ ${FOAMY_TEST_DESKTOP:-} != 1 ]]; then
  skip "images-ui, layer: real layer-shell surfaces on the live desktop; set FOAMY_TEST_DESKTOP=1"
elif [[ -z ${WAYLAND_DISPLAY:-} ]]; then
  skip "images-ui, layer: no Wayland session"
else
  run python3 tests/images-ui.py
  run python3 tests/layer.py
fi
exit $status
