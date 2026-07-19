#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY_SCRIPT="$SCRIPT_DIR/../codex_provider_local_launcher.py"
COMMAND="${1:-interactive}"
CODEX_HOME_PATH="${2:-${CODEX_HOME:-$HOME/.codex}}"

pause_on_exit() {
  printf "\n按回车键关闭窗口..."
  IFS= read -r _ || true
}

trap pause_on_exit EXIT

if [ ! -f "$PY_SCRIPT" ]; then
  echo "[ERROR] Missing Python launcher:"
  echo "  $PY_SCRIPT"
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
else
  echo "[ERROR] Missing Python. Please install Python 3.10+."
  exit 1
fi

echo "============================================"
echo " Codex Session Recovery / Provider Sync"
echo "============================================"
echo

"$PYTHON_BIN" "$PY_SCRIPT" "$COMMAND" "$CODEX_HOME_PATH"
