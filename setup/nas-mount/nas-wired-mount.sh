#!/bin/bash
#
# nas-wired-mount.sh
# Mount an SMB NAS share ONLY when a wired (Ethernet) connection is the active
# default route. On Wi-Fi it does nothing and leaves any existing mount
# untouched. Useful on a laptop that roams between Ethernet and Wi-Fi.
#
# Triggered by a LaunchAgent on every network change (and at login) — see
# nas-wired-mount.plist.example and README.md. Safe to run repeatedly / by hand.
#
# Credentials come from the login Keychain (seeded once in Finder), so no
# password ever lives in this file.

# ---- Config -----------------------------------------------------------------
# NAS_HOST / SHARE / SMB_USER come from nas-mount.conf, kept next to this script
# (copy nas-mount.conf.example -> nas-mount.conf and fill it in). nas-mount.conf
# is gitignored so your personal network details never get committed.
CONF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
[ -f "$CONF_DIR/nas-mount.conf" ] && . "$CONF_DIR/nas-mount.conf"

NAS_HOST="${NAS_HOST:?set NAS_HOST in nas-mount.conf (copy nas-mount.conf.example)}"
SHARE="${SHARE:?set SHARE in nas-mount.conf}"
SMB_USER="${SMB_USER:?set SMB_USER in nas-mount.conf}"
MOUNTPOINT="/Volumes/${SHARE}"
LOG="${HOME}/Library/Logs/nas-wired-mount.log"
# -----------------------------------------------------------------------------

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG"; }

# Which interface is the Wi-Fi device (e.g. en0)?
WIFI_DEV="$(networksetup -listallhardwareports \
  | awk '/Hardware Port: Wi-Fi/{getline; print $2}')"

# Which interface currently carries the default route?
DEF_IF="$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')"

# No default route at all -> offline, nothing to do.
if [ -z "$DEF_IF" ]; then
  log "no default route; skip"
  exit 0
fi

# Active route is Wi-Fi -> we only mount on wired. Leave any existing mount.
if [ "$DEF_IF" = "$WIFI_DEV" ]; then
  log "active interface ${DEF_IF} is Wi-Fi; skip (leaving any existing mount)"
  exit 0
fi

# Already mounted? Idempotent — nothing to do.
if mount | grep -q " on ${MOUNTPOINT} "; then
  log "already mounted at ${MOUNTPOINT}; skip"
  exit 0
fi

# NAS reachable on this wired link?
if ! /sbin/ping -c1 -t2 "$NAS_HOST" >/dev/null 2>&1; then
  log "wired (${DEF_IF}) but ${NAS_HOST} not reachable; skip"
  exit 0
fi

# Mount using Keychain-stored credentials. The system mounts it under
# /Volumes/<share> just like Finder's Connect to Server.
if /usr/bin/osascript -e "mount volume \"smb://${SMB_USER}@${NAS_HOST}/${SHARE}\"" >/dev/null 2>&1; then
  log "mounted smb://${SMB_USER}@${NAS_HOST}/${SHARE} on ${MOUNTPOINT} (wired: ${DEF_IF})"
else
  log "mount FAILED for smb://${SMB_USER}@${NAS_HOST}/${SHARE} (wired: ${DEF_IF}) -- check Keychain credential"
fi

exit 0
