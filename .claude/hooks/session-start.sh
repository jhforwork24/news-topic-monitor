#!/bin/bash
set -euo pipefail

# Only bootstrap in Claude Code on the web (remote) sessions -- local CLI
# users already manage their own .venv per README.md.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# pyproject.toml pins requires-python = ">=3.12"; pip install fails closed
# under an older interpreter, so make sure the venv is actually built on 3.12
# rather than whatever `python3` happens to resolve to.
PYTHON_BIN=""
for candidate in python3.12 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v "$candidate")"
    if [ "$candidate" = "python3.12" ]; then
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "session-start: no python interpreter found on PATH; skipping dependency install" >&2
  exit 0
fi

VENV_PYTHON_VERSION=""
if [ -x .venv/bin/python ]; then
  VENV_PYTHON_VERSION="$(.venv/bin/python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true)"
fi

if [ "$VENV_PYTHON_VERSION" != "3.12" ]; then
  rm -rf .venv
  "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e '.[dev]'

echo "export PATH=\"$CLAUDE_PROJECT_DIR/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
