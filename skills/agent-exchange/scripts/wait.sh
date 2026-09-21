#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

if [[ $# -ne 1 ]] || ! agent_exchange_validate_id "$1"; then
    printf 'usage: %s <request-uuid-v4>\n' "${0##*/}" >&2
    exit 2
fi
command -v inotifywait >/dev/null 2>&1 || {
    printf 'inotifywait is required\n' >&2
    exit 1
}

id="$1"
root="$(agent_exchange_root)"
agent_exchange_ensure_layout "$root"
response="$root/responses/$id.md"

while [[ ! -f "$response" ]]; do
    inotifywait -q -t 60 -e moved_to,close_write,create -- "$root/responses" >/dev/null 2>&1 || true
done

cat -- "$response"
