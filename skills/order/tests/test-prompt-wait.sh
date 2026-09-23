#!/usr/bin/env bash
# Contract tests for the settled-state prompt wrapper. A fake herdr replays
# scripted responses, so no Herdr session or agent is needed.
set -Eeuo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd -- "$TEST_DIR/../scripts" && pwd)"
WRAPPER="$SCRIPT_DIR/prompt-wait.py"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

fail() {
    printf 'not ok - %s\n' "$1" >&2
    exit 1
}

assert_eq() {
    [[ "$1" == "$2" ]] || fail "${3:-values differ: $1 != $2}"
}

# The fake herdr appends its arguments to calls.log (each call starts a line
# with --session; a multi-line prompt continues on the following lines) and
# answers each call with the next "<status>|<json>" line of responses.txt.
fake="$test_root/herdr"
cat >"$fake" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
dir="$(dirname -- "$0")"
printf '%s\n' "$*" >>"$dir/calls.log"
line="$(head -n 1 "$dir/responses.txt")"
tail -n +2 "$dir/responses.txt" >"$dir/responses.next"
mv "$dir/responses.next" "$dir/responses.txt"
printf '%s\n' "${line#*|}"
exit "${line%%|*}"
EOF
chmod +x "$fake"

agent_json() {
    printf '0|{"result":{"agent":{"name":"worker","agent_status":"%s"},"type":"%s"}}' "$1" "$2"
}

responses() {
    printf '%s\n' "$@" >"$test_root/responses.txt"
    : >"$test_root/calls.log"
}

run_wrapper() {
    set +e
    wrapper_out="$(HERDR="$fake" python3 "$WRAPPER" --session s1 worker --settle-ms 0 "$@" 2>"$test_root/stderr")"
    wrapper_status=$?
    set -e
}

calls() {
    grep '^--session ' "$test_root/calls.log" | cut -d' ' -f3-4 | paste -sd, - || true
}

printf 'line one\nline "two" $HOME\n' >"$test_root/prompt.md"

# A settled result that holds is confirmed once and returned.
responses "$(agent_json done agent_prompted)" "$(agent_json done agent_info)"
run_wrapper --prompt-file "$test_root/prompt.md"
assert_eq "$wrapper_status" 0 "stable result should succeed"
assert_eq "$(calls)" "agent prompt,agent get" "stable result should not wait again"
assert_eq "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["type"])' <<<"$wrapper_out")" \
    agent_info "stdout should be the confirming agent get"

# The prompt reaches herdr as one unmodified argument.
python3 - "$test_root/calls.log" <<'EOF' || fail "prompt should be passed verbatim"
import sys
log = open(sys.argv[1]).read()
assert 'agent prompt worker line one\nline "two" $HOME\n --wait' in log, log
EOF

# A transient settled state is followed by another wait until it holds.
responses "$(agent_json done agent_prompted)" "$(agent_json working agent_info)" \
    "$(agent_json done agent_waited)" "$(agent_json working agent_info)" \
    "$(agent_json idle agent_waited)" "$(agent_json idle agent_info)"
run_wrapper --prompt-file "$test_root/prompt.md"
assert_eq "$wrapper_status" 0 "re-waited result should succeed"
assert_eq "$(calls)" "agent prompt,agent get,agent wait,agent get,agent wait,agent get" \
    "working after a settled return should wait again"
grep -q 'waited again 2 time' "$test_root/stderr" || fail "re-waits should be reported on stderr"

# Herdr errors pass through with their status and no re-wait.
responses '1|{"error":{"code":"agent_prompt_stalled","message":"stalled"}}'
run_wrapper --prompt-file "$test_root/prompt.md"
assert_eq "$wrapper_status" 1 "herdr error status should pass through"
assert_eq "$wrapper_out" '{"error":{"code":"agent_prompt_stalled","message":"stalled"}}' "herdr error should pass through"
assert_eq "$(calls)" "agent prompt" "an error should not be followed by other calls"

# A blocked agent is returned for inspection rather than waited on.
responses "$(agent_json blocked agent_prompted)" "$(agent_json blocked agent_info)"
run_wrapper --prompt-file "$test_root/prompt.md"
assert_eq "$wrapper_status" 0 "blocked should be returned"
assert_eq "$(calls)" "agent prompt,agent get" "blocked should not be waited on again"

# --wait-only sends no prompt; stdin is the default prompt source.
responses "$(agent_json done agent_waited)" "$(agent_json done agent_info)"
run_wrapper --wait-only </dev/null
assert_eq "$(calls)" "agent wait,agent get" "wait-only should not prompt"
responses "$(agent_json done agent_prompted)" "$(agent_json done agent_info)"
run_wrapper <<<"from stdin"
grep -q '^--session s1 agent prompt worker from stdin' "$test_root/calls.log" || fail "stdin should be the prompt"

# --timeout is forwarded as a remaining budget.
responses "$(agent_json done agent_prompted)" "$(agent_json done agent_info)"
run_wrapper --prompt-file "$test_root/prompt.md" --timeout 60000
grep -Eq -- '--wait --timeout [0-9]+$' "$test_root/calls.log" || fail "timeout should be forwarded"

# An empty prompt is rejected before herdr is called.
responses "$(agent_json done agent_prompted)"
run_wrapper </dev/null
assert_eq "$wrapper_status" 2 "empty prompt should be a usage error"
assert_eq "$(calls)" "" "empty prompt should not call herdr"

printf 'ok - prompt-wait contract\n'
