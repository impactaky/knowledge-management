#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd -- "$TEST_DIR/../scripts" && pwd)"
test_root="$(mktemp -d)"
wrapper_pid=
external_pid=

cleanup() {
    if [[ -n "$wrapper_pid" ]]; then
        kill -TERM "$wrapper_pid" 2>/dev/null || true
        wait "$wrapper_pid" 2>/dev/null || true
    fi
    if [[ -n "$external_pid" ]]; then
        kill -TERM "$external_pid" 2>/dev/null || true
        wait "$external_pid" 2>/dev/null || true
    fi
    rm -rf -- "$test_root"
}
trap cleanup EXIT

fail() {
    printf 'not ok - %s\n' "$1" >&2
    cat "$test_root/wrapper.log" >&2
    exit 1
}

wait_file() {
    local path="$1" attempt
    for ((attempt = 0; attempt < 200; attempt++)); do
        [[ ! -f "$path" ]] || return 0
        sleep 0.05
    done
    fail "timed out waiting for $path"
}

cat >"$test_root/herdr" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
case "$1 ${2:-}" in
    'api snapshot')
        touch "$FAKE_STATE/probed"
        if [[ -f "$FAKE_STATE/hang-probe" ]]; then
            rm "$FAKE_STATE/hang-probe"
            exec sleep 60
        fi
        [[ -f "$FAKE_STATE/available" ]]
        ;;
    'server ')
        printf 'start\n' >>"$FAKE_STATE/starts"
        if [[ -f "$FAKE_STATE/race" ]]; then
            touch "$FAKE_STATE/available"
            exit 1
        fi
        [[ ! -f "$FAKE_STATE/fail-start" ]] || exit 7
        printf '%s\n' "$$" >"$FAKE_STATE/server-pid"
        trap 'rm -f "$FAKE_STATE/available"; touch "$FAKE_STATE/stopped"; exit 0' TERM INT
        touch "$FAKE_STATE/available"
        while [[ ! -f "$FAKE_STATE/exit-clean" ]]; do sleep 0.05; done
        rm -f "$FAKE_STATE/available"
        ;;
    *) exit 2 ;;
esac
EOF
chmod +x "$test_root/herdr"
export HERDR_BIN="$test_root/herdr"
export HERDR_SESSION=agent-exchange-service-test
export FAKE_STATE="$test_root/state"

reset_state() {
    rm -rf "$FAKE_STATE"
    mkdir "$FAKE_STATE"
    : >"$test_root/wrapper.log"
}

start_wrapper() {
    "$SCRIPT_DIR/herdr-server.sh" >"$test_root/wrapper.log" 2>&1 &
    wrapper_pid=$!
}

stop_wrapper() {
    kill -TERM "$wrapper_pid"
    wait "$wrapper_pid" || fail 'wrapper did not stop cleanly'
    wrapper_pid=
}

# A healthy external server is reused and survives stopping the wrapper.
reset_state
sleep 120 &
external_pid=$!
touch "$FAKE_STATE/available"
start_wrapper
wait_file "$FAKE_STATE/probed"
sleep 0.1
[[ ! -f "$FAKE_STATE/starts" ]] || fail 'started a duplicate external server'
stop_wrapper
kill -0 "$external_pid" || fail 'external server was stopped'
[[ -f "$FAKE_STATE/available" ]] || fail 'external server state was changed'
printf 'ok - reuse and preserve an external server\n'

# If that server disappears, monitoring starts a replacement exactly once.
reset_state
touch "$FAKE_STATE/available"
start_wrapper
wait_file "$FAKE_STATE/probed"
sleep 0.1
rm "$FAKE_STATE/available"
wait_file "$FAKE_STATE/server-pid"
[[ "$(wc -l <"$FAKE_STATE/starts")" -eq 1 ]] || fail 'replacement started repeatedly'
stop_wrapper
[[ -f "$FAKE_STATE/stopped" ]] || fail 'owned server did not receive TERM'
printf 'ok - recover after external server disappears and stop owned child\n'

# A client wins the start race: a failed start with a healthy API is accepted.
reset_state
touch "$FAKE_STATE/race"
start_wrapper
wait_file "$FAKE_STATE/available"
sleep 0.2
kill -0 "$wrapper_pid" || fail 'wrapper exited after a harmless start race'
[[ "$(wc -l <"$FAKE_STATE/starts")" -eq 1 ]] || fail 'start race caused a retry loop'
stop_wrapper
[[ -f "$FAKE_STATE/available" ]] || fail 'race winner was stopped'
printf 'ok - recheck API after losing the start race\n'

# A real startup failure remains visible to systemd for restart.
reset_state
touch "$FAKE_STATE/fail-start"
start_wrapper
status=0
wait "$wrapper_pid" || status=$?
wrapper_pid=
[[ "$status" -eq 7 ]] || fail 'startup failure exit status was lost'
printf 'ok - propagate a genuine startup failure\n'

# Even a clean owned-server exit must not silently disable recovery.
reset_state
touch "$FAKE_STATE/exit-clean"
start_wrapper
status=0
wait "$wrapper_pid" || status=$?
wrapper_pid=
[[ "$status" -eq 1 ]] || fail 'clean server exit bypassed service recovery'
printf 'ok - request recovery after a clean server exit\n'

# A stuck API must not leave the service stuck forever before startup.
reset_state
touch "$FAKE_STATE/hang-probe"
start_wrapper
wait_file "$FAKE_STATE/server-pid"
stop_wrapper
[[ -f "$FAKE_STATE/stopped" ]] || fail 'owned server survived shutdown after probe timeout'
printf 'ok - bound a hung API probe\n'
