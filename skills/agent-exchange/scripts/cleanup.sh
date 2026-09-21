#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

if [[ $# -ne 1 ]] || ! agent_exchange_validate_id "$1"; then
    printf 'usage: %s <request-uuid-v4>\n' "${0##*/}" >&2
    exit 2
fi

id="$1"
root="$(agent_exchange_root)"
agent_exchange_ensure_layout "$root"
running="$root/running/$id"
response="$root/responses/$id.md"

[[ -f "$response" && ! -L "$response" ]] || {
    printf 'response has not been published: %s\n' "$id" >&2
    exit 1
}
[[ ! -e "$running" || ( -d "$running" && ! -L "$running" ) ]] || {
    printf 'refusing unexpected running path: %s\n' "$running" >&2
    exit 1
}

if [[ -d "$running" && ! -L "$running" && -e "$running/thread" ]]; then
    thread_id="$(agent_exchange_read_line "$running/thread" 2>/dev/null || true)"
    agent_exchange_validate_id "$thread_id" || {
        printf 'refusing invalid thread metadata for request: %s\n' "$id" >&2
        exit 1
    }
    agent_exchange_thread_lock "$root" "$thread_id" || {
        printf 'refusing unsafe thread lock: %s\n' "$thread_id" >&2
        exit 1
    }
    thread_path="$root/threads/$thread_id"
    if [[ -d "$thread_path" && ! -L "$thread_path" ]]; then
        active_request="$(agent_exchange_read_line "$thread_path/active" 2>/dev/null || true)"
        if [[ "$active_request" == "$id" ]]; then
            rm -f -- "$thread_path/active"
        fi
    fi
fi

rm -rf -- "$running"
rm -f -- "$response"
