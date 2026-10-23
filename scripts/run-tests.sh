#!/usr/bin/env bash
# Run the CI baseline locally: black, isort, flake8, mypy, pytest.
# Creates .venv and installs ".[dev]" first if anything is missing.
#
#   scripts/run-tests.sh            full CI baseline
#   scripts/run-tests.sh --web      jarvis web gateway tests only (fast)
#   scripts/run-tests.sh --tests    pytest only, skip format/lint/types
#   scripts/run-tests.sh -- <args>  extra args passed to pytest
set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"

MODE=full
PYTEST_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --web) MODE=web ;;
        --tests) MODE=tests ;;
        -h | --help)
            sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        --)
            shift
            PYTEST_ARGS=("$@")
            break
            ;;
        *)
            echo "unknown option: $1" >&2
            exit 2
            ;;
    esac
    shift
done

step() {
    echo
    echo "==> $*"
}

FAILED=()
run() {
    local name="$1"
    shift
    step "$name"
    if "$@"; then
        return 0
    fi
    FAILED+=("$name")
}

ensure_env
PYTEST_MARKS=(-m "not integration and not manual and not slow" -k "not voice_activation")

case "$MODE" in
    web)
        run "pytest (web gateway)" "$BIN/pytest" tests/test_web_gateway.py "${PYTEST_ARGS[@]}"
        ;;
    tests)
        run "pytest" "$BIN/pytest" "${PYTEST_MARKS[@]}" "${PYTEST_ARGS[@]}"
        ;;
    full)
        run "black" "$BIN/black" --check .
        run "isort" "$BIN/isort" --check-only .
        run "flake8" "$BIN/flake8" . --count --select=E9,F63,F7,F82 --show-source --statistics --exclude .venv
        run "mypy" "$BIN/mypy" --config-file /dev/null --follow-imports=skip --ignore-missing-imports jarvis/tui/slash_commands_doc.py
        run "pytest" "$BIN/pytest" "${PYTEST_MARKS[@]}" "${PYTEST_ARGS[@]}"
        ;;
esac

echo
if [ ${#FAILED[@]} -gt 0 ]; then
    echo "FAILED: ${FAILED[*]}"
    exit 1
fi
echo "all green"
