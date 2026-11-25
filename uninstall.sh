#!/usr/bin/env bash
# Removes the gitui launcher and its virtual environment.
# Pass --purge to also delete your saved sign-in and settings.
set -euo pipefail
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gitui"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/gitui"
rm -f "$HOME/.local/bin/gitui"
rm -rf "$DATA_DIR"
echo "✔ Removed gitui launcher and environment."
if [[ "${1:-}" == "--purge" ]]; then
  rm -rf "$CONF_DIR"
  echo "✔ Removed saved sign-in and settings ($CONF_DIR)."
else
  echo "  Your sign-in/settings are kept in $CONF_DIR (use --purge to remove)."
fi
echo "  Cloned repositories were not touched."
