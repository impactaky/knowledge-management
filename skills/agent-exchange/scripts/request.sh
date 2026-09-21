#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
    printf 'usage: %s /absolute/path/to/repository < request.md\n' "${0##*/}" >&2
    printf '       %s --new-thread /absolute/path/to/repository < request.md\n' "${0##*/}" >&2
    printf '       %s --continue <thread-uuid-v4> < request.md\n' "${0##*/}" >&2
    exit 2
}

rollback_continuation_reservation() {
    local active_request

    [[ "${reservation:-}" == active ]] || return 0
    [[ -d "$thread_path" && ! -L "$thread_path" ]] || return 0
    active_request="$(agent_exchange_read_line "$thread_path/active" 2>/dev/null || true)"
    if [[ "$active_request" == "$request_id" ]]; then
        rm -f -- "$thread_path/active"
    fi
}

rollback_initial_thread() {
    local active_request initial_request thread_repository
    local -a entries=()

    [[ "${thread_published:-}" == 1 ]] || return 0
    [[ -d "$thread_path" && ! -L "$thread_path" ]] || return 0
    thread_repository="$(agent_exchange_read_line "$thread_path/repository" 2>/dev/null || true)"
    initial_request="$(agent_exchange_read_line "$thread_path/initial-request" 2>/dev/null || true)"
    active_request="$(agent_exchange_read_line "$thread_path/active" 2>/dev/null || true)"
    [[ "$thread_repository" == "$repository" &&
        "$initial_request" == "$request_id" &&
        "$active_request" == "$request_id" ]] || return 0
    mapfile -d '' -t entries < <(find "$thread_path" -mindepth 1 -maxdepth 1 -print0)
    [[ ${#entries[@]} -eq 3 ]] || return 0

    rm -f -- \
        "$thread_path/repository" \
        "$thread_path/initial-request" \
        "$thread_path/active"
    rmdir -- "$thread_path"
}

mode=fresh
repository=
thread_id=
case "${1:-}" in
    --new-thread)
        [[ $# -eq 2 ]] || usage
        mode=new-thread
        repository_arg="$2"
        ;;
    --continue)
        [[ $# -eq 2 ]] || usage
        mode='continue'
        thread_id="$2"
        agent_exchange_validate_id "$thread_id" || usage
        ;;
    --*)
        usage
        ;;
    *)
        [[ $# -eq 1 ]] || usage
        repository_arg="$1"
        ;;
esac

if [[ "$mode" != continue ]]; then
    repository="$(realpath -e -- "$repository_arg")" || {
        printf 'repository does not exist: %s\n' "$repository_arg" >&2
        exit 1
    }
    [[ "$repository" == /* && -d "$repository" ]] || {
        printf 'repository must be an absolute directory: %s\n' "$repository" >&2
        exit 1
    }

    repository="$(git -C "$repository" rev-parse --show-toplevel 2>/dev/null)" || {
        printf 'not a Git repository: %s\n' "$repository" >&2
        exit 1
    }
    repository="$(realpath -e -- "$repository")"
fi

root="$(agent_exchange_root)"
agent_exchange_ensure_layout "$root"

request_id="$(uuidgen | tr '[:upper:]' '[:lower:]')"
agent_exchange_validate_id "$request_id" || {
    printf 'uuidgen did not return a UUID v4\n' >&2
    exit 1
}
if [[ "$mode" == new-thread ]]; then
    thread_id="$(uuidgen | tr '[:upper:]' '[:lower:]')"
    agent_exchange_validate_id "$thread_id" || {
        printf 'uuidgen did not return a UUID v4\n' >&2
        exit 1
    }
    [[ "$thread_id" != "$request_id" ]] || {
        printf 'uuidgen returned duplicate Request and Thread IDs\n' >&2
        exit 1
    }
fi

staging="$root/staging/$request_id"
published="$root/requests/$request_id"
mkdir -- "$staging"
trap 'rm -rf -- "$staging"' EXIT

cat >"$staging/request.md"
[[ -s "$staging/request.md" ]] || {
    printf 'request must not be empty\n' >&2
    exit 1
}
case "$mode" in
    fresh)
        printf '%s\n' "$repository" >"$staging/repository"
        ;;
    new-thread)
        printf '%s\n' "$repository" >"$staging/repository"
        printf '%s\n' "$mode" >"$staging/mode"
        printf '%s\n' "$thread_id" >"$staging/thread"
        printf '%s\n' active >"$staging/reservation"
        ;;
    continue)
        printf '%s\n' "$mode" >"$staging/mode"
        printf '%s\n' "$thread_id" >"$staging/thread"
        ;;
esac
chmod 600 -- "$staging"/*

if [[ "$mode" == new-thread ]]; then
    thread_staging="$(mktemp -d --tmpdir="$root/threads" ".${thread_id}.XXXXXX.tmp")"
    thread_path="$root/threads/$thread_id"
    trap 'rm -rf -- "$staging" "$thread_staging"' EXIT
    chmod 700 -- "$thread_staging"
    agent_exchange_write_line "$thread_staging/repository" "$repository"
    agent_exchange_write_line "$thread_staging/initial-request" "$request_id"
    agent_exchange_write_line "$thread_staging/active" "$request_id"
    [[ ! -e "$thread_path" ]] || {
        printf 'generated Thread ID already exists: %s\n' "$thread_id" >&2
        exit 1
    }
    mv -T -- "$thread_staging" "$thread_path"
    thread_published=1
elif [[ "$mode" == continue ]]; then
    agent_exchange_thread_lock "$root" "$thread_id" || {
        printf 'refusing unsafe thread lock: %s\n' "$thread_id" >&2
        exit 1
    }
    thread_path="$root/threads/$thread_id"
    reservation=unavailable
    if [[ -d "$thread_path" && ! -L "$thread_path" ]]; then
        active_request="$(agent_exchange_read_line "$thread_path/active" 2>/dev/null || true)"
        if [[ -z "$active_request" && ! -e "$thread_path/active" ]]; then
            agent_exchange_write_line "$thread_path/active" "$request_id"
            reservation=active
        elif agent_exchange_validate_id "$active_request"; then
            reservation=busy
        fi
    fi
    printf '%s\n' "$reservation" >"$staging/reservation"
    chmod 600 -- "$staging/reservation"
fi

if ! mv -T -- "$staging" "$published"; then
    case "$mode" in
        new-thread)
            rollback_initial_thread
            ;;
        continue)
            rollback_continuation_reservation
            ;;
    esac
    exit 1
fi
trap - EXIT
if [[ "$mode" == fresh ]]; then
    printf '%s\n' "$request_id"
else
    printf '{"request_id":"%s","thread_id":"%s"}\n' "$request_id" "$thread_id"
fi
