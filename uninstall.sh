#!/usr/bin/env bash
# Removes what install.sh created. Nothing amao installs ever touches system
# Python or anything outside this repo directory -- everything lives in
# .venv, so removing that is the entire uninstall.
#
# .env is left in place by default since it may hold real API keys -- pass
# --with-env to remove it too. Run `source uninstall.sh` if .venv is
# currently active in this shell, so it gets deactivated as part of the same
# command; running it as ./uninstall.sh still removes .venv fine, you'd just
# need to run `deactivate` yourself afterward (same shell-vs-subprocess rule
# install.sh explains).
set -uo pipefail

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    _amao_sourced=0
else
    _amao_sourced=1
fi

_amao_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

remove_env=0
for arg in "$@"; do
    case "$arg" in
        --with-env) remove_env=1 ;;
        *)
            echo "error: unknown option '$arg' (only --with-env is supported)" >&2
            unset _amao_sourced _amao_script_dir remove_env arg
            [ "${BASH_SOURCE[0]}" = "${0}" ] && exit 1 || return 1
            ;;
    esac
done
unset arg

cd "$_amao_script_dir" || exit 1

if [ -n "${VIRTUAL_ENV:-}" ] && [ "$_amao_sourced" -eq 1 ]; then
    deactivate 2>/dev/null || true
fi

if [ -d .venv ]; then
    rm -rf .venv
    echo "==> Removed .venv"
else
    echo "==> .venv not present, nothing to remove"
fi

if [ "$remove_env" -eq 1 ]; then
    if [ -f .env ]; then
        rm -f .env
        echo "==> Removed .env"
    fi
else
    if [ -f .env ]; then
        echo "==> Left .env in place (it may hold real API keys) -- pass --with-env to remove it too"
    fi
fi

echo "==> Nothing else to clean up -- amao was only ever installed inside .venv, never system-wide."

unset _amao_sourced _amao_script_dir remove_env
