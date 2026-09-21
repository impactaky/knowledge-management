#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

if [[ $# -ne 1 ]] || ! agent_exchange_validate_id "$1"; then
    printf 'usage: %s <request-uuid-v4> < response.md\n' "${0##*/}" >&2
    exit 2
fi

id="$1"
root="$(agent_exchange_root)"
agent_exchange_ensure_layout "$root"
[[ -d "$root/running/$id" && ! -L "$root/running/$id" ]] || {
    printf 'request is not running: %s\n' "$id" >&2
    exit 1
}

response="$root/responses/$id.md"
tmp="$(mktemp --tmpdir="$root/responses" ".${id}.XXXXXX.tmp")"
trap 'rm -f -- "$tmp"' EXIT
cat >"$tmp"
[[ -s "$tmp" ]] || {
    printf 'response must not be empty\n' >&2
    exit 1
}
chmod 600 -- "$tmp"

if ! ln -- "$tmp" "$response"; then
    printf 'response already exists: %s\n' "$id" >&2
    exit 1
fi
rm -f -- "$tmp"
trap - EXIT
