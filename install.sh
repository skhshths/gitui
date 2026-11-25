#!/usr/bin/env bash
# gitui installer — sets up an isolated Python environment and puts a
# `gitui` command on your PATH. Safe to re-run (also used for updating).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gitui"
VENV="$DATA_DIR/venv"
BIN_DIR="$HOME/.local/bin"

c_ok="\033[1;32m"; c_info="\033[1;35m"; c_warn="\033[1;33m"; c_err="\033[1;31m"; c_off="\033[0m"
say()  { printf "${c_info}◆${c_off} %s\n" "$*"; }
ok()   { printf "${c_ok}✔${c_off} %s\n" "$*"; }
warn() { printf "${c_warn}!${c_off} %s\n" "$*"; }
die()  { printf "${c_err}✘${c_off} %s\n" "$*" >&2; exit 1; }

say "Installing gitui from $REPO_DIR"

# ---------------------------------------------------------------- prerequisites
need_apt=()
command -v git >/dev/null 2>&1 || need_apt+=(git)
command -v python3 >/dev/null 2>&1 || need_apt+=(python3)
if command -v python3 >/dev/null 2>&1 && ! python3 -c "import venv, ensurepip" >/dev/null 2>&1; then
  need_apt+=(python3-venv)
fi
if ((${#need_apt[@]})); then
  if command -v apt-get >/dev/null 2>&1; then
    say "Installing system packages: ${need_apt[*]} (needs sudo)"
    sudo apt-get update -qq
    sudo apt-get install -y "${need_apt[@]}"
  else
    die "Please install: ${need_apt[*]}"
  fi
fi

python3 - <<'PY' || die "gitui needs Python 3.9 or newer."
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
ok "Python $(python3 -c 'import platform; print(platform.python_version())') and $(git --version)"

# ---------------------------------------------------------------- venv
mkdir -p "$DATA_DIR" "$BIN_DIR"
if [[ ! -x "$VENV/bin/python" ]]; then
  say "Creating virtual environment in $VENV"
  python3 -m venv "$VENV"
fi
say "Installing Python dependencies (textual, httpx, qrcode)…"
LOG="$DATA_DIR/install.log"
if ! { "$VENV/bin/python" -m pip install --upgrade pip &&
       "$VENV/bin/python" -m pip install --upgrade -e "$REPO_DIR"; } >"$LOG" 2>&1; then
  tail -n 30 "$LOG" >&2
  die "pip install failed (full log: $LOG). Check your internet connection and retry."
fi
ok "Dependencies installed"

# ---------------------------------------------------------------- launcher
ln -sf "$VENV/bin/gitui" "$BIN_DIR/gitui"
ok "Linked $BIN_DIR/gitui"

# ---------------------------------------------------------------- PATH
path_line='export PATH="$HOME/.local/bin:$PATH"'
added=0
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  [[ -f "$rc" || "$rc" == "$HOME/.bashrc" ]] || continue
  if ! grep -qs '\.local/bin' "$rc"; then
    printf '\n# added by gitui installer\n%s\n' "$path_line" >> "$rc"
    ok "Added ~/.local/bin to PATH in $(basename "$rc")"
    added=1
  fi
done

# Warn if a different `gitui` would shadow ours.
other="$(PATH="$BIN_DIR:$PATH" command -v -a gitui 2>/dev/null | grep -v "^$BIN_DIR/gitui$" | head -n1 || true)"
if [[ -n "$other" ]]; then
  warn "Another program called gitui exists at $other."
  warn "Ours is in $BIN_DIR, which the installer puts first in PATH."
fi

echo
ok "gitui is installed!"
case ":$PATH:" in
  *":$BIN_DIR:"*) printf "   Run it with:  ${c_ok}gitui${c_off}\n" ;;
  *) printf "   Open a new terminal (or run ${c_ok}source ~/.bashrc${c_off}), then type ${c_ok}gitui${c_off}\n" ;;
esac
