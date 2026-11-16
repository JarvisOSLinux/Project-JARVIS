# Shared by the scripts in this directory. Source it, do not run it.
# Sets ROOT, VENV, PYTHON and BIN, and provides ensure_env, pm_install and
# ensure_tailscale.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
PYTHON="${PYTHON:-python3}"
BIN="$VENV/bin"

ensure_env() {
    if [ ! -x "$VENV/bin/python" ]; then
        echo "==> creating $VENV"
        if ! "$PYTHON" -m venv "$VENV" 2>/dev/null; then
            echo "==> python venv module missing, installing it"
            if command -v pacman >/dev/null; then
                sudo pacman -S --needed --noconfirm python
            elif command -v apt-get >/dev/null; then
                sudo apt-get install -y python3-venv
            elif command -v dnf >/dev/null; then
                sudo dnf install -y python3
            elif command -v zypper >/dev/null; then
                sudo zypper install -y python3
            fi
            "$PYTHON" -m venv "$VENV"
        fi
    fi
    if ! "$VENV/bin/python" -c "import pytest, black, isort, flake8, mypy, websockets" 2>/dev/null; then
        echo "==> installing project with dev and web extras"
        "$VENV/bin/python" -m pip install -q -e ".[dev,web]"
    fi
}

pm_install() {
    if command -v pacman >/dev/null; then
        sudo pacman -S --needed --noconfirm "$@"
    elif command -v apt-get >/dev/null; then
        sudo apt-get install -y "$@"
    elif command -v dnf >/dev/null; then
        sudo dnf install -y "$@"
    elif command -v zypper >/dev/null; then
        sudo zypper install -y "$@"
    else
        echo "no supported package manager to install: $*" >&2
        exit 1
    fi
}

# Install tailscale when missing and start tailscaled; joining is left to the caller.
ensure_tailscale() {
    if ! command -v tailscale >/dev/null; then
        echo "==> installing tailscale"
        pm_install tailscale
    fi
    # Only when it is not already up: otherwise every start asks for a password.
    if ! systemctl is-active --quiet tailscaled; then
        sudo systemctl enable --now tailscaled
    fi
}
