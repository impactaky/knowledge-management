#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

herdr_bin="$(agent_exchange_resolve_herdr)" || {
    printf 'herdr executable not found; set HERDR_BIN\n' >&2
    exit 1
}
export HERDR_SESSION="${HERDR_SESSION:-agent-exchange}"

command -v timeout >/dev/null 2>&1 || {
    printf 'timeout is required\n' >&2
    exit 1
}

# Only signal a child started by this wrapper. An existing server may belong
# to an SSH session and must survive stopping/restarting this service.
child_pid=
stop() {
    trap '' TERM INT
    if [[ -n "$child_pid" ]]; then
        kill -TERM "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    exit 0
}
trap stop TERM INT

server_ready() {
    timeout --kill-after=1s 5s "$herdr_bin" api snapshot >/dev/null 2>&1
}

observing=false
while true; do
    if server_ready; then
        if [[ "$observing" == false ]]; then
            printf 'Herdr session %s is already available; monitoring it\n' "$HERDR_SESSION"
            observing=true
        fi
        sleep 5 &
        child_pid=$!
        wait "$child_pid"
        child_pid=
        continue
    fi

    observing=false
    printf 'Starting Herdr session %s\n' "$HERDR_SESSION"
    status=0
    "$herdr_bin" server &
    child_pid=$!
    wait "$child_pid" || status=$?
    child_pid=

    # Another client may have started the server after our initial probe,
    # or replaced our server. Recheck availability before declaring failure.
    if server_ready; then
        continue
    fi
    printf 'Herdr session %s is unavailable after server exit (%s)\n' "$HERDR_SESSION" "$status" >&2
    # A clean server exit still needs recovery while the service is enabled.
    [[ "$status" -ne 0 ]] || status=1
    exit "$status"
done
