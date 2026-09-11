#!/usr/bin/env bash
# Local CI — the same checks GitHub Actions runs (workflows/ci.yml calls this
# script too, so the two can never drift).
#
#   ./ci.sh              lint + import + pack validation (no API keys needed)
#   ./ci.sh --with-api   plus the live smoke tests (uses .env, makes real API calls)
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
RUFF=.venv/bin/ruff

if [ ! -x "$PY" ]; then
  echo "▸ no .venv found — creating one with uv"
  uv venv
  uv pip install -q -e ".[dev]"
fi

echo "▸ ruff check"
"$RUFF" check .

echo "▸ ruff format --check"
"$RUFF" format --check .

echo "▸ imports (keyless)"
"$PY" -c "import autodebate.cli, autodebate.tui, autodebate.web"

echo "▸ persona packs"
"$PY" tests/test_packs.py

echo "▸ moderator parser"
"$PY" tests/test_moderator.py

if [ "${1:-}" = "--with-api" ]; then
  echo "▸ live smoke: TUI (real API calls)"
  "$PY" tests/test_tui.py
  echo "▸ live smoke: web (real API calls)"
  "$PY" tests/test_web.py
fi

echo "✓ local CI green"
