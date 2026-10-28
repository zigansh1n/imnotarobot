#!/bin/sh
# Builds "ImNotARobot.app" (shown as «Я не робот» on a Russian Mac) into dist/ and copies it to ~/Applications (older copies go to the Trash).
# IMNOTAROBOT_NO_INSTALL=1 with IMNOTAROBOT_DISTPATH=... builds into a directory without installing;
# IMNOTAROBOT_SIGN_IDENTITY=name signs with that identity instead of the one make_cert.sh created.
set -e
cd "$(dirname "$0")"
APP="ImNotARobot.app"
DIST="${IMNOTAROBOT_DISTPATH:-../dist}"
IDENTITY="${IMNOTAROBOT_SIGN_IDENTITY:-}"

../.venv/bin/pyinstaller --noconfirm --distpath "$DIST" --workpath ../build ImNotARobot.spec

# PyInstaller signs ad hoc, and macOS treats every ad hoc build as a new program: the "Allow in the
# Background" and notification permissions are lost after each build. A stable identity fixes that;
# find-identity runs without -v because an untrusted self-signed identity is only listed that way.
if [ -z "$IDENTITY" ] && security find-identity -p codesigning | grep -q '"ImNotARobot Local"'; then
  IDENTITY="ImNotARobot Local"
fi
if [ -n "$IDENTITY" ]; then
  codesign --force --deep --sign "$IDENTITY" "$DIST/$APP"
  echo "signed with: $IDENTITY"
else
  echo "warning: no signing identity, the build stays ad hoc — after every build macOS asks again"
  echo "         for the background and notification permissions. Run packaging/make_cert.sh once."
fi
codesign --verify --deep --strict "$DIST/$APP"

"$DIST/$APP/Contents/MacOS/ImNotARobot" --selftest

if [ -n "$IMNOTAROBOT_NO_INSTALL" ]; then
  echo "built (not installed): $DIST/$APP"
  exit 0
fi
mkdir -p "$HOME/Applications"
for old in "$HOME/Applications/$APP" "$HOME/Applications/Я не робот.app" "$HOME/Applications/Captcha Stack.app"; do
  if [ -e "$old" ]; then trash "$old"; fi
done
ditto "$DIST/$APP" "$HOME/Applications/$APP"
echo "installed: $HOME/Applications/$APP"
