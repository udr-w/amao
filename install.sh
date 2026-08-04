#!/usr/bin/env bash
# Sets up a local amao environment: creates a virtual environment (falling
# back to `uv` if the stdlib `venv` module can't run), installs the package
# with dev extras, and seeds a .env file from .env.example. Safe to re-run --
# never overwrites an existing .venv install or an existing .env file.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PYTHON="${PYTHON:-python3}"
VENV_DIR=".venv"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: '$PYTHON' not found on PATH. Install Python 3.10+ first." >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "warning: git not found on PATH -- amao requires it at runtime (GitHelper shells out to it)." >&2
fi

if [ -d "$VENV_DIR" ]; then
    echo "==> $VENV_DIR already exists, reusing it (delete it first for a clean rebuild)"
else
    echo "==> Creating virtual environment at $VENV_DIR"
    venv_log="$(mktemp)"
    trap 'rm -f "$venv_log"' EXIT

    if "$PYTHON" -m venv "$VENV_DIR" >"$venv_log" 2>&1; then
        :
    elif grep -q "ensurepip is not available" "$venv_log"; then
        echo "==> '$PYTHON -m venv' failed: the python3-venv system package isn't installed."
        if command -v uv >/dev/null 2>&1; then
            echo "==> Falling back to uv instead (no sudo needed)"
            rm -rf "$VENV_DIR"
            uv venv "$VENV_DIR"
        else
            cat >&2 <<EOF
error: python3-venv isn't installed and uv isn't available either. Fix one of these, then re-run:

  sudo apt install python3-venv     # or python3.<minor>-venv to match \`$PYTHON --version\`
  curl -LsSf https://astral.sh/uv/install.sh | sh   # installs uv, no sudo needed
EOF
            rm -rf "$VENV_DIR"
            exit 1
        fi
    else
        cat "$venv_log" >&2
        rm -rf "$VENV_DIR"
        exit 1
    fi
fi

echo "==> Installing amao with dev extras"
if [ -x "$VENV_DIR/bin/pip" ]; then
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -e ".[dev]"
elif command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV_DIR/bin/python" -e ".[dev]"
else
    echo "error: no pip in $VENV_DIR and uv isn't available -- can't install." >&2
    exit 1
fi

if [ ! -f .env ]; then
    echo "==> Seeding .env from .env.example -- fill in your real key(s) before running amao"
    cp .env.example .env
else
    echo "==> .env already exists, leaving it untouched"
fi

cat <<EOF

Done. Next steps:
  1. source $VENV_DIR/bin/activate
  2. Edit .env and set at least one provider's API key (see "Rewiring the agents" in README.md)
  3. amao run --dir ./my-idea --goal "..."

amao reads .env automatically from the directory you run it in -- no need to \`export\` anything
by hand unless you'd rather set real environment variables directly.
EOF
