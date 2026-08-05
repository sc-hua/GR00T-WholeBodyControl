#!/usr/bin/env bash
# install_inference.sh
# Sets up the .venv_inference venv for running VLA inference with
# Isaac-GR00T PolicyClient against a remote or local policy server.
#
# Installs gear_sonic[inference] which pulls in the Isaac-GR00T library,
# PyZMQ, msgpack, Pinocchio, and other inference dependencies.
#
# Usage:  bash install_scripts/install_inference.sh   (run from repo root)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── 0. System dependencies ────────────────────────────────────────────────────
ARCH="$(uname -m)"
echo "[OK] Architecture: $ARCH"

# ── 1. Ensure uv is installed and available ──────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "[INFO] uv not found – installing via official installer …"
    curl -LsSf https://astral.sh/uv/install.sh | sh

    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        source "$HOME/.local/bin/env"
    elif [ -f "$HOME/.cargo/env" ]; then
        # shellcheck disable=SC1091
        source "$HOME/.cargo/env"
    else
        export PATH="$HOME/.local/bin:$PATH"
    fi

    if ! command -v uv &>/dev/null; then
        echo "[ERROR] uv installation succeeded but binary not found on PATH."
        echo "        Please add ~/.local/bin (or ~/.cargo/bin) to your PATH and re-run."
        exit 1
    fi
fi
echo "[OK] uv $(uv --version)"

# ── 2. Install the Python version required by the current Isaac-GR00T ──────
# Keep this separate from the Python 3.10 teleop, simulation, and data-
# collection environments. Current Isaac-GR00T requires Python >=3.12,<3.13.
PYTHON_VERSION="3.12"
echo "[INFO] Installing uv-managed Python $PYTHON_VERSION (required by Isaac-GR00T) …"
uv python install "$PYTHON_VERSION"
MANAGED_PY="$(uv python find --no-project "$PYTHON_VERSION")"
echo "[OK] Using Python: $MANAGED_PY"

# ── 3. Clean previous venv (if any) ──────────────────────────────────────────
cd "$REPO_ROOT"
echo "[INFO] Removing old .venv_inference (if present) …"
rm -rf .venv_inference

# ── 4. Create venv & install inference extra ─────────────────────────────────
echo "[INFO] Creating .venv_inference with uv-managed Python $PYTHON_VERSION …"
uv venv .venv_inference --python "$MANAGED_PY" --prompt gear_sonic_inference
# shellcheck disable=SC1091
source .venv_inference/bin/activate
echo "[INFO] Installing gear_sonic[inference] (this may take a few minutes) …"

# Isaac-GR00T currently contains a platform-specific local wheel reference.
# uv supports that reference when Isaac-GR00T is installed as a local top-level
# project, but rejects it when the repository is a transitive Git dependency.
# Prefer an explicitly configured checkout, then the usual sibling checkout.
ISAAC_GROOT_DIR="${ISAAC_GROOT_PATH:-$REPO_ROOT/../Isaac-GR00T}"
if [ ! -f "$ISAAC_GROOT_DIR/pyproject.toml" ]; then
    echo "[ERROR] Isaac-GR00T checkout not found at: $ISAAC_GROOT_DIR"
    echo "        Clone it next to this repository or set ISAAC_GROOT_PATH."
    exit 1
fi
echo "[OK] Using local Isaac-GR00T checkout: $ISAAC_GROOT_DIR"
ISAAC_GROOT_URI="$(python -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve().as_uri())' "$ISAAC_GROOT_DIR")"
uv pip install \
    --overrides <(printf 'gr00t @ %s\n' "$ISAAC_GROOT_URI") \
    -e "$ISAAC_GROOT_DIR" \
    -e "gear_sonic[inference]"

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  Setup complete!  Activate the venv with:"
echo ""
echo "    source .venv_inference/bin/activate"
echo ""
echo "  You should see (gear_sonic_inference) in your prompt."
echo ""
echo "  Then run VLA inference with:"
echo "    python gear_sonic/scripts/run_vla_inference.py --help"
echo "══════════════════════════════════════════════════════════════"
