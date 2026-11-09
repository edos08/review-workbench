#!/bin/sh
# Start as root only long enough to make the data folder writable, then drop privileges.
#   PUID / PGID  user and group id the app runs as (default 1000). Set them to your own host
#                user (`id -u`, `id -g`) and the files in the data folder belong to you.
set -e
DATA="${DATA_DIR:-/data}"
if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-1000}"
  PGID="${PGID:-1000}"
  case "$PUID$PGID" in
    *[!0-9]*) echo "PUID and PGID must be numbers (got PUID='$PUID' PGID='$PGID')" >&2; exit 1 ;;
  esac
  mkdir -p "$DATA"
  if [ "$(stat -c %u:%g "$DATA")" != "$PUID:$PGID" ]; then
    chown -R "$PUID:$PGID" "$DATA"
  fi
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi
exec "$@"
