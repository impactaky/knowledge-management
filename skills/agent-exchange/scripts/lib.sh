#!/usr/bin/env bash

agent_exchange_root() {
    printf '%s\n' "${AGENT_EXCHANGE_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/agent-exchange}"
}

agent_exchange_ensure_layout() {
    local root="$1"
    umask 077
    mkdir -p -- \
        "$root/staging" \
        "$root/requests" \
        "$root/running" \
        "$root/responses" \
        "$root/threads" \
        "$root/thread-locks"
    chmod 700 -- \
        "$root" \
        "$root/staging" \
        "$root/requests" \
        "$root/running" \
        "$root/responses" \
        "$root/threads" \
        "$root/thread-locks"
}

agent_exchange_validate_id() {
    [[ "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]
}

agent_exchange_resolve_herdr() {
    local candidate

    if [[ -n "${HERDR_BIN:-}" ]]; then
        [[ -x "$HERDR_BIN" ]] || return 1
        printf '%s\n' "$HERDR_BIN"
        return
    fi

    candidate="$(type -P herdr 2>/dev/null || true)"
    if [[ -n "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return
    fi

    candidate="$(type -P mise 2>/dev/null || true)"
    if [[ -n "$candidate" ]]; then
        "$candidate" which herdr 2>/dev/null
        return
    fi

    return 1
}

agent_exchange_write_line() {
    local path="$1"
    local value="$2"
    local directory basename tmp

    directory="$(dirname -- "$path")"
    basename="$(basename -- "$path")"
    tmp="$(mktemp --tmpdir="$directory" ".${basename}.XXXXXX.tmp")"

    printf '%s\n' "$value" >"$tmp"
    chmod 600 -- "$tmp"
    mv -T -- "$tmp" "$path"
}

agent_exchange_read_line() {
    local path="$1"
    local -a lines

    [[ -f "$path" && ! -L "$path" ]] || return 1
    mapfile -t lines <"$path"
    [[ ${#lines[@]} -eq 1 && -n "${lines[0]}" ]] || return 1
    printf '%s\n' "${lines[0]}"
}

agent_exchange_thread_lock() {
    local root="$1"
    local thread_id="$2"
    local lock_path="$root/thread-locks/$thread_id.lock"

    [[ ! -L "$lock_path" ]] || return 1
    exec {AGENT_EXCHANGE_THREAD_LOCK_FD}>"$lock_path"
    chmod 600 -- "$lock_path"
    flock "$AGENT_EXCHANGE_THREAD_LOCK_FD"
}
