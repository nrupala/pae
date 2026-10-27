#!/bin/sh
# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Install the PAE native bundle (the directory this script lives in)
# into a prefix and link the `pae` command into a bin directory.
#
#   ./install.sh [--prefix DIR] [--bin-dir DIR] [--systemd|--launchd]
#
# Defaults: prefix ~/.local/opt/pae, bin dir ~/.local/bin.
# Your data is NOT installed here — it lives in PAE_HOME
# (~/.local/share/pae) and survives upgrades. To upgrade, unpack the
# new bundle and re-run its install.sh; the new files replace the old
# in the prefix and your data is untouched.
#
# --systemd (Linux): also write a systemd *user* unit that runs
#   `pae serve` at login. You still enable it yourself (command is
#   printed) so nothing starts without your say-so.
# --launchd (macOS): also write a LaunchAgent plist for `pae serve`.
#   Load it with the printed launchctl command when you are ready.

set -eu

SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX="$HOME/.local/opt/pae"
BIN_DIR="$HOME/.local/bin"
WANT_SYSTEMD=0
WANT_LAUNCHD=0

while [ $# -gt 0 ]; do
    case $1 in
        --prefix)  PREFIX=$2; shift 2 ;;
        --bin-dir) BIN_DIR=$2; shift 2 ;;
        --systemd) WANT_SYSTEMD=1; shift ;;
        --launchd) WANT_LAUNCHD=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ ! -f "$SRC/VERSION" ] || [ ! -x "$SRC/pae" ]; then
    echo "This script must run from inside an unpacked PAE bundle" >&2
    echo "(expected VERSION and the pae launcher next to install.sh)." >&2
    exit 1
fi

# --- Copy the bundle into the prefix -----------------------------------
if [ "$SRC" != "$PREFIX" ]; then
    if [ -e "$PREFIX" ] && [ ! -f "$PREFIX/VERSION" ]; then
        echo "Refusing to overwrite $PREFIX — it exists and is not a" >&2
        echo "previous PAE install (no VERSION file). Move it away or" >&2
        echo "choose another --prefix." >&2
        exit 1
    fi
    echo "Installing PAE $(cat "$SRC/VERSION") to $PREFIX ..."
    mkdir -p "$PREFIX"
    # Replace the previous install wholesale; data lives elsewhere.
    find "$PREFIX" -mindepth 1 -delete
    cp -r "$SRC/." "$PREFIX/"
else
    echo "Bundle already lives in the prefix $PREFIX — skipping copy."
fi
chmod +x "$PREFIX/pae" "$PREFIX/bin/pae-engine" \
         "$PREFIX/bin/pae-seed" "$PREFIX/analytics/pae-analytics" 2>/dev/null || true

# --- Link the launcher ---------------------------------------------------
mkdir -p "$BIN_DIR"
ln -sf "$PREFIX/pae" "$BIN_DIR/pae"
echo "Linked $BIN_DIR/pae -> $PREFIX/pae"

# --- Optional service registration --------------------------------------
if [ "$WANT_SYSTEMD" -eq 1 ]; then
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/pae.service" <<EOF
[Unit]
Description=PAE - Personal Analytics Engine
After=network.target

[Service]
ExecStart=$PREFIX/pae serve
Restart=on-failure
Environment=PAE_HOME=$HOME/.local/share/pae

[Install]
WantedBy=default.target
EOF
    echo "Wrote $UNIT_DIR/pae.service"
    echo "To enable at login:"
    echo "  systemctl --user daemon-reload"
    echo "  systemctl --user enable --now pae"
fi

if [ "$WANT_LAUNCHD" -eq 1 ]; then
    AGENTS_DIR="$HOME/Library/LaunchAgents"
    mkdir -p "$AGENTS_DIR"
    cat > "$AGENTS_DIR/org.aimlds.pae.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>org.aimlds.pae</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PREFIX/pae</string>
        <string>serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PAE_HOME</key>
        <string>$HOME/.local/share/pae</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$HOME/.local/share/pae/logs/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.local/share/pae/logs/launchd.err.log</string>
</dict>
</plist>
EOF
    echo "Wrote $AGENTS_DIR/org.aimlds.pae.plist"
    echo "To load it:"
    echo "  launchctl load $AGENTS_DIR/org.aimlds.pae.plist"
fi

cat <<EOF

PAE installed. Next steps:
  1. Make sure $BIN_DIR is on your PATH (it usually is on Linux;
     on macOS add it in your shell profile if needed).
  2. Start PAE:        pae start
  3. Open the dashboard: http://localhost:3001
  4. First run? Load the fictional demo data: pae seed
  5. Stop anytime:      pae stop

Your data lives in ~/.local/share/pae (override with PAE_HOME) and
is not touched by upgrades.
EOF
