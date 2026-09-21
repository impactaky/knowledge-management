#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd -- "$TEST_DIR/../scripts" && pwd)"

fail() {
    printf 'not ok - %s\n' "$1" >&2
    exit 1
}

assert_eq() {
    [[ "$1" == "$2" ]] || fail "${3:-values differ: $1 != $2}"
}

assert_file_contains() {
    grep -F -- "$2" "$1" >/dev/null || fail "$1 does not contain $2"
}

json_field() {
    jq -r ".$2" <<<"$1"
}

set_fake_uuids() {
    printf '%s\n' "$@" >"$FAKE_UUIDGEN_SEQUENCE"
    printf '1\n' >"$FAKE_UUIDGEN_INDEX"
}

# Invoked by request.sh subprocesses after export -f.
# shellcheck disable=SC2317
uuidgen() {
    local index

    index="$(<"$FAKE_UUIDGEN_INDEX")"
    sed -n "${index}p" "$FAKE_UUIDGEN_SEQUENCE"
    printf '%s\n' "$((index + 1))" >"$FAKE_UUIDGEN_INDEX"
}

test_root="$(mktemp -d)"
repository="$test_root/repository"
exchange_root="$test_root/exchange"
fake_bin="$test_root/bin"
fake_state="$test_root/fake-state"
fake_log="$test_root/herdr.log"
export FAKE_UUIDGEN_SEQUENCE="$test_root/fake-uuidgen-sequence"
export FAKE_UUIDGEN_INDEX="$test_root/fake-uuidgen-index"
legacy_worktree="$test_root/legacy-worktree"
thread_worktree="$test_root/thread-worktree"
mkdir -p -- "$fake_bin" "$fake_state"
ln -s /bin/true "$fake_bin/inotifywait"
trap 'git -C "$repository" worktree remove --force "$legacy_worktree" >/dev/null 2>&1 || true; git -C "$repository" worktree remove --force "$thread_worktree" >/dev/null 2>&1 || true; rm -rf -- "$test_root"' EXIT

git init -q "$repository"
git -C "$repository" config user.email test@example.invalid
git -C "$repository" config user.name 'Agent Exchange test'
printf 'fixture\n' >"$repository/fixture.txt"
git -C "$repository" add fixture.txt
git -C "$repository" commit -qm fixture
git -C "$repository" worktree add -q -b test-legacy "$legacy_worktree" >/dev/null 2>&1
git -C "$repository" worktree add -q -b test-thread "$thread_worktree" >/dev/null 2>&1

export AGENT_EXCHANGE_ROOT="$exchange_root"
export HERDR_BIN="$TEST_DIR/fake-herdr.sh"
export FAKE_HERDR_LOG="$fake_log"
export FAKE_HERDR_STATE="$fake_state"
export FAKE_REPOSITORY="$repository"
export FAKE_WORKSPACE=w-test
export PATH="$fake_bin:$PATH"
: >"$fake_log"

# The Launcher starts the kind and native args from its own config; this test
# uses a synthetic kind so no real agent product is assumed.
launcher_config="$test_root/launcher-config.toml"
cat >"$launcher_config" <<'EOF'
[implementation]
kind = "test-kind"
args = ["--model", "test-model", "--flag", "value"]
EOF
export AGENT_EXCHANGE_CONFIG="$launcher_config"

# Single-line metadata rejects multiline content and symlink indirection.
source "$SCRIPT_DIR/lib.sh"
mkdir -p "$test_root/line-test"
printf 'one\ntwo\n' >"$test_root/line-test/multiline"
if agent_exchange_read_line "$test_root/line-test/multiline" >/dev/null; then
    fail 'multiline metadata was accepted'
fi
ln -s "$test_root/line-test/multiline" "$test_root/line-test/link"
if agent_exchange_read_line "$test_root/line-test/link" >/dev/null; then
    fail 'symlink metadata was accepted'
fi

# Legacy publication remains UUID-only and creates no Thread metadata.
legacy_id="$(printf 'legacy request\n' | "$SCRIPT_DIR/request.sh" "$repository")"
agent_exchange_validate_id "$legacy_id" || fail 'legacy request did not return UUID v4'
[[ -f "$exchange_root/requests/$legacy_id/repository" ]] || fail 'legacy repository metadata missing'
[[ ! -e "$exchange_root/requests/$legacy_id/mode" ]] || fail 'legacy request unexpectedly has mode'
export FAKE_WORKTREE="$legacy_worktree"
AGENT_EXCHANGE_RUN_ONCE=1 "$SCRIPT_DIR/launcher.sh"
[[ -d "$exchange_root/running/$legacy_id" ]] || fail 'legacy request was not claimed'
assert_file_contains "$fake_log" 'worktree create'
assert_file_contains "$fake_log" 'agent start'
printf 'legacy complete\n' | "$SCRIPT_DIR/respond.sh" "$legacy_id"
"$SCRIPT_DIR/cleanup.sh" "$legacy_id"
[[ ! -e "$exchange_root/running/$legacy_id" && ! -e "$exchange_root/responses/$legacy_id.md" ]] ||
    fail 'legacy cleanup regressed'

# A new Thread publishes distinct UUIDs and private durable metadata.
new_json="$(printf 'initial thread request\n' | "$SCRIPT_DIR/request.sh" --new-thread "$repository")"
initial_id="$(json_field "$new_json" request_id)"
thread_id="$(json_field "$new_json" thread_id)"
agent_exchange_validate_id "$initial_id" || fail 'new-thread request_id is invalid'
agent_exchange_validate_id "$thread_id" || fail 'new-thread thread_id is invalid'
[[ "$initial_id" != "$thread_id" ]] || fail 'Request ID and Thread ID are equal'
assert_eq "$(stat -c %a "$exchange_root/threads/$thread_id")" 700 'Thread directory permission'
assert_eq "$(stat -c %a "$exchange_root/threads/$thread_id/active")" 600 'Thread file permission'
assert_eq "$(agent_exchange_read_line "$exchange_root/threads/$thread_id/active")" "$initial_id" 'initial active Request'

export FAKE_WORKTREE="$thread_worktree"
AGENT_EXCHANGE_RUN_ONCE=1 "$SCRIPT_DIR/launcher.sh"
thread_path="$exchange_root/threads/$thread_id"
[[ -d "$thread_path/binding" && -f "$thread_path/ready" ]] || fail 'Thread binding was not published'
bound_agent="$(agent_exchange_read_line "$thread_path/binding/agent")"
assert_eq "$(agent_exchange_read_line "$thread_path/binding/worktree")" "$thread_worktree" 'bound worktree'
printf 'initial complete\n' | "$SCRIPT_DIR/respond.sh" "$initial_id"
"$SCRIPT_DIR/cleanup.sh" "$initial_id"
[[ ! -e "$thread_path/active" && -d "$thread_path/binding" ]] || fail 'initial cleanup did not leave an idle durable Thread'

# A failed final publish rolls back only the reservation owned by that Request.
failed_continue_id=11111111-1111-4111-8111-111111111111
failed_thread_request_id=22222222-2222-4222-8222-222222222222
failed_thread_id=33333333-3333-4333-8333-333333333333
foreign_attempt_id=44444444-4444-4444-8444-444444444444
foreign_owner_id=55555555-5555-4555-8555-555555555555
export -f uuidgen

set_fake_uuids "$failed_continue_id"
mkdir -- "$exchange_root/requests/$failed_continue_id"
printf 'collision\n' >"$exchange_root/requests/$failed_continue_id/marker"
if printf 'publish collision\n' | "$SCRIPT_DIR/request.sh" --continue "$thread_id" >/dev/null 2>&1; then
    fail 'Continuation publish collision unexpectedly succeeded'
fi
[[ ! -e "$thread_path/active" ]] || fail 'failed Continuation publish left its active reservation'
[[ ! -e "$exchange_root/staging/$failed_continue_id" ]] || fail 'failed Continuation publish left staging metadata'
rm -rf -- "$exchange_root/requests/$failed_continue_id"

set_fake_uuids "$failed_thread_request_id" "$failed_thread_id"
mkdir -- "$exchange_root/requests/$failed_thread_request_id"
printf 'collision\n' >"$exchange_root/requests/$failed_thread_request_id/marker"
if printf 'new Thread publish collision\n' | "$SCRIPT_DIR/request.sh" --new-thread "$repository" >/dev/null 2>&1; then
    fail 'new-thread publish collision unexpectedly succeeded'
fi
[[ ! -e "$exchange_root/threads/$failed_thread_id" ]] || fail 'failed new-thread publish left orphan Thread metadata'
[[ ! -e "$exchange_root/staging/$failed_thread_request_id" ]] || fail 'failed new-thread publish left staging metadata'
rm -rf -- "$exchange_root/requests/$failed_thread_request_id"

# If ownership changes before a failed publish, rollback preserves the foreign owner.
# Invoked by request.sh subprocesses after export -f.
# shellcheck disable=SC2317
mv() {
    local source destination

    source="${*: -2:1}"
    destination="${*: -1}"
    if [[ "$source" == "$FAKE_MV_FAIL_SOURCE" && "$destination" == "$FAKE_MV_FAIL_DESTINATION" ]]; then
        printf '%s\n' "$foreign_owner_id" >"$thread_path/active"
        chmod 600 -- "$thread_path/active"
        return 1
    fi
    command mv "$@"
}
export -f mv
export foreign_owner_id thread_path
set_fake_uuids "$foreign_attempt_id"
export FAKE_MV_FAIL_SOURCE="$exchange_root/staging/$foreign_attempt_id"
export FAKE_MV_FAIL_DESTINATION="$exchange_root/requests/$foreign_attempt_id"
if printf 'foreign owner race\n' | "$SCRIPT_DIR/request.sh" --continue "$thread_id" >/dev/null 2>&1; then
    fail 'forced Continuation publish failure unexpectedly succeeded'
fi
assert_eq "$(agent_exchange_read_line "$thread_path/active")" "$foreign_owner_id" 'publish rollback released foreign active Request'
rm -f -- "$thread_path/active"
unset -f mv uuidgen
unset FAKE_MV_FAIL_SOURCE FAKE_MV_FAIL_DESTINATION
export -n foreign_owner_id thread_path

# Continuation reuses the same agent and never creates a worktree or starts an agent.
before_create="$(grep -c '^worktree create ' "$fake_log" || true)"
before_start="$(grep -c '^agent start ' "$fake_log" || true)"
continue_json="$(printf 'continuation delta\n' | "$SCRIPT_DIR/request.sh" --continue "$thread_id")"
continue_id="$(json_field "$continue_json" request_id)"
assert_eq "$(agent_exchange_read_line "$exchange_root/requests/$continue_id/reservation")" active 'Continuation reservation'
AGENT_EXCHANGE_RUN_ONCE=1 "$SCRIPT_DIR/launcher.sh"
assert_eq "$(grep -c '^worktree create ' "$fake_log" || true)" "$before_create" 'Continuation created worktree'
assert_eq "$(grep -c '^agent start ' "$fake_log" || true)" "$before_start" 'Continuation started agent'
assert_file_contains "$fake_log" "agent prompt $bound_agent"
assert_file_contains "$fake_log" "Continuation Request $continue_id"
printf 'continuation complete\n' | "$SCRIPT_DIR/respond.sh" "$continue_id"
"$SCRIPT_DIR/cleanup.sh" "$continue_id"
[[ ! -e "$thread_path/active" ]] || fail 'Continuation cleanup did not return Thread to idle'

# A busy Request gets a failure Response, and its stale cleanup cannot release the owner.
active_json="$(printf 'active request\n' | "$SCRIPT_DIR/request.sh" --continue "$thread_id")"
active_id="$(json_field "$active_json" request_id)"
busy_json="$(printf 'busy request\n' | "$SCRIPT_DIR/request.sh" --continue "$thread_id")"
busy_id="$(json_field "$busy_json" request_id)"
assert_eq "$(agent_exchange_read_line "$exchange_root/requests/$busy_id/reservation")" busy 'busy reservation'
AGENT_EXCHANGE_RUN_ONCE=1 "$SCRIPT_DIR/launcher.sh"
assert_file_contains "$exchange_root/responses/$busy_id.md" 'Code: thread-busy'
"$SCRIPT_DIR/cleanup.sh" "$busy_id"
assert_eq "$(agent_exchange_read_line "$thread_path/active")" "$active_id" 'stale cleanup released active Request'
printf 'active complete\n' | "$SCRIPT_DIR/respond.sh" "$active_id"
"$SCRIPT_DIR/cleanup.sh" "$active_id"
[[ ! -e "$thread_path/active" ]] || fail 'owner cleanup did not release Thread'

# Missing agent validation fails closed with a machine-readable reason and no fresh fallback.
missing_json="$(printf 'missing agent request\n' | "$SCRIPT_DIR/request.sh" --continue "$thread_id")"
missing_id="$(json_field "$missing_json" request_id)"
touch "$fake_state/missing-agent"
before_create="$(grep -c '^worktree create ' "$fake_log" || true)"
before_start="$(grep -c '^agent start ' "$fake_log" || true)"
AGENT_EXCHANGE_RUN_ONCE=1 "$SCRIPT_DIR/launcher.sh"
assert_file_contains "$exchange_root/responses/$missing_id.md" 'Code: thread-agent-missing'
assert_eq "$(grep -c '^worktree create ' "$fake_log" || true)" "$before_create" 'missing agent triggered fresh worktree'
assert_eq "$(grep -c '^agent start ' "$fake_log" || true)" "$before_start" 'missing agent triggered fresh agent'
"$SCRIPT_DIR/cleanup.sh" "$missing_id"
rm -f "$fake_state/missing-agent"

# Workspace and worktree loss have distinct fail-closed codes.
workspace_json="$(printf 'missing workspace request\n' | "$SCRIPT_DIR/request.sh" --continue "$thread_id")"
workspace_missing_id="$(json_field "$workspace_json" request_id)"
touch "$fake_state/missing-workspace"
AGENT_EXCHANGE_RUN_ONCE=1 "$SCRIPT_DIR/launcher.sh"
assert_file_contains "$exchange_root/responses/$workspace_missing_id.md" 'Code: thread-workspace-missing'
"$SCRIPT_DIR/cleanup.sh" "$workspace_missing_id"
rm -f "$fake_state/missing-workspace"

worktree_json="$(printf 'missing worktree request\n' | "$SCRIPT_DIR/request.sh" --continue "$thread_id")"
worktree_missing_id="$(json_field "$worktree_json" request_id)"
git -C "$repository" worktree remove --force "$thread_worktree"
AGENT_EXCHANGE_RUN_ONCE=1 "$SCRIPT_DIR/launcher.sh"
assert_file_contains "$exchange_root/responses/$worktree_missing_id.md" 'Code: thread-worktree-missing'
"$SCRIPT_DIR/cleanup.sh" "$worktree_missing_id"

# Unknown Threads are published as Requests and receive a fail-closed Response.
unknown_thread="$(uuidgen | tr '[:upper:]' '[:lower:]')"
unknown_json="$(printf 'unknown thread request\n' | "$SCRIPT_DIR/request.sh" --continue "$unknown_thread")"
unknown_id="$(json_field "$unknown_json" request_id)"
AGENT_EXCHANGE_RUN_ONCE=1 "$SCRIPT_DIR/launcher.sh"
assert_file_contains "$exchange_root/responses/$unknown_id.md" 'Code: thread-missing'
"$SCRIPT_DIR/cleanup.sh" "$unknown_id"

# Traversal-shaped Thread IDs are rejected before any path is formed.
request_count="$(find "$exchange_root/requests" -mindepth 1 -maxdepth 1 -type d | wc -l)"
if printf 'unsafe\n' | "$SCRIPT_DIR/request.sh" --continue '../unsafe' >/dev/null 2>&1; then
    fail 'path traversal Thread ID was accepted'
fi
assert_eq "$(find "$exchange_root/requests" -mindepth 1 -maxdepth 1 -type d | wc -l)" "$request_count" 'unsafe request was published'

printf 'ok - Agent Exchange file protocol and fake Herdr launcher tests passed\n'
