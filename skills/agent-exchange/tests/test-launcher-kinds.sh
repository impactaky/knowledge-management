#!/usr/bin/env bash
# Contract tests for configured kind preparation in the Agent Exchange Launcher.
# Uses a temporary repository, exchange directory and fake Herdr; no real agent
# or config is touched.
set -Eeuo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd -- "$TEST_DIR/../scripts" && pwd)"
test_root="$(mktemp -d)"
repository="$test_root/repository"
exchange_root="$test_root/exchange with spaces"
fake_bin="$test_root/bin"
fake_state="$test_root/fake-state"
fake_log="$test_root/herdr.log"
trap 'rm -rf -- "$test_root"' EXIT

# Do not inherit a real deployment from the host environment.
unset AGENT_EXCHANGE_CONFIG AGENT_EXCHANGE_ROOT HERDR_BIN HERDR_SESSION \
    OPENCODE_CONFIG_CONTENT 2>/dev/null || true
export AGENT_EXCHANGE_ROOT="$exchange_root"

fail() {
    printf 'not ok - %s\n' "$1" >&2
    exit 1
}

assert_kind() {
    local expected="$1" actual
    actual="$(awk 'p {print; exit} $0 == "--kind" {p = 1}' "$fake_state/agent-start" 2>/dev/null || true)"
    [[ "$actual" == "$expected" ]] || fail "kind: got [$actual] want [$expected]"
}

agent_tail() {
    awk 'seen {print} $0 == "--" {seen = 1}' "$fake_state/agent-start" 2>/dev/null || true
}

assert_agent_tail() {
    printf '%s\n' "$@" >"$test_root/expected"
    agent_tail >"$test_root/actual"
    if ! diff -u "$test_root/expected" "$test_root/actual" >"$test_root/tail.diff"; then
        cat "$test_root/tail.diff" >&2
        fail 'final agent args differ'
    fi
}

run_launcher() {
    local config="$1"
    shift
    env AGENT_EXCHANGE_CONFIG="$config" AGENT_EXCHANGE_ROOT="$exchange_root" \
        HERDR_BIN="$TEST_DIR/fake-herdr.sh" HERDR_SESSION=agent-exchange \
        FAKE_HERDR_LOG="$fake_log" FAKE_HERDR_STATE="$fake_state" \
        FAKE_REPOSITORY="$repository" FAKE_WORKSPACE=w-kind FAKE_WORKTREE="$test_root/worktree" \
        PATH="$fake_bin:$PATH" AGENT_EXCHANGE_RUN_ONCE=1 "$@" \
        bash "$SCRIPT_DIR/launcher.sh"
}

publish_request() {
    printf 'kind preparation request\n' | "$SCRIPT_DIR/request.sh" "$repository"
}

mkdir -p "$fake_bin" "$fake_state"
ln -s /bin/true "$fake_bin/inotifywait"
git init -q "$repository"
git -C "$repository" config user.email test@example.invalid
git -C "$repository" config user.name 'Agent Exchange kind test'
printf 'fixture\n' >"$repository/fixture.txt"
git -C "$repository" add fixture.txt
git -C "$repository" commit -qm fixture
: >"$fake_log"

# codex keeps --add-dir after the ordered user args.
codex_config="$test_root/codex.toml"
printf '[implementation]\nkind = "codex"\nargs = ["--model", "cx"]\n' >"$codex_config"
publish_request >/dev/null
run_launcher "$codex_config"
assert_kind codex
assert_agent_tail --model cx --add-dir "$exchange_root"

# Claude keeps the selected model and permission mode, and adds exchange access.
# A user-supplied additional directory must remain an independent argv element.
claude_config="$test_root/claude.toml"
printf '[implementation]\nkind = "claude"\nargs = ["--model", "cl", "--permission-mode", "acceptEdits", "--add-dir", "/tmp/extra context"]\n' >"$claude_config"
publish_request >/dev/null
run_launcher "$claude_config"
assert_kind claude
assert_agent_tail --model cl --permission-mode acceptEdits --add-dir '/tmp/extra context' --add-dir "$exchange_root"

# The equals form of a non-bypass permission mode is also passed through.
printf '[implementation]\nkind = "claude"\nargs = ["--permission-mode=auto"]\n' >"$claude_config"
publish_request >/dev/null
run_launcher "$claude_config"
assert_agent_tail --permission-mode=auto --add-dir "$exchange_root"

# Cursor adds exchange access and explicitly enables its sandbox without force.
cursor_config="$test_root/cursor.toml"
printf '[implementation]\nkind = "cursor"\nargs = ["--model", "cu", "--auto-review"]\n' >"$cursor_config"
publish_request >/dev/null
run_launcher "$cursor_config"
assert_kind cursor
assert_agent_tail --model cu --auto-review --add-dir "$exchange_root" --sandbox enabled

# An already-enabled sandbox is compatible in either native option form.
for args in '["--sandbox", "enabled"]' '["--sandbox=enabled"]'; do
    printf '[implementation]\nkind = "cursor"\nargs = %s\n' "$args" >"$cursor_config"
    publish_request >/dev/null
    run_launcher "$cursor_config"
    mapfile -t expected_args < <(jq -r '.[]' <<<"$args")
    assert_agent_tail "${expected_args[@]}" --add-dir "$exchange_root" --sandbox enabled
done

# agy appends the required sandbox arguments after the ordered user args.
agy_config="$test_root/agy.toml"
printf '[implementation]\nkind = "agy"\nargs = ["--user-flag"]\n' >"$agy_config"
publish_request >/dev/null
run_launcher "$agy_config"
assert_kind agy
assert_agent_tail --user-flag --add-dir "$exchange_root" --mode accept-edits --sandbox

# opencode receives a session-only external_directory permission in its pane and
# no extra argv.
opencode_config="$test_root/opencode.toml"
printf '[implementation]\nkind = "opencode"\nargs = ["--model", "oc"]\n' >"$opencode_config"
publish_request >/dev/null
run_launcher "$opencode_config" OPENCODE_CONFIG_CONTENT='{"theme":"dark"}'
assert_kind opencode
assert_agent_tail --model oc
[[ -f "$fake_state/pane-run" ]] || fail 'opencode pane was not prepared'
{
    cat "$fake_state/pane-run"
    printf '\nprintf "%%s" "$OPENCODE_CONFIG_CONTENT"\n'
} >"$test_root/pane-eval"
merged="$(bash "$test_root/pane-eval")"
[[ -n "$merged" ]] || fail 'opencode pane did not export OPENCODE_CONFIG_CONTENT'
[[ "$(jq -r '.theme' <<<"$merged")" == dark ]] || fail 'existing inline config was not merged'
[[ "$(jq -r --arg p "$exchange_root" '.permission.external_directory[$p]' <<<"$merged")" == allow ]] ||
    fail 'Exchange Directory permission missing'
[[ "$(jq -r --arg p "$exchange_root/**" '.permission.external_directory[$p]' <<<"$merged")" == allow ]] ||
    fail 'Exchange Directory descendant permission missing'

# Unknown kinds pass through unchanged and receive no pane preparation.
unknown_config="$test_root/unknown.toml"
printf '[implementation]\nkind = "unknown-kind-xyz"\nargs = ["--x", "y"]\n' >"$unknown_config"
publish_request >/dev/null
rm -f "$fake_state/pane-run"
run_launcher "$unknown_config"
assert_kind unknown-kind-xyz
assert_agent_tail --x y
[[ ! -e "$fake_state/pane-run" ]] || fail 'unknown kind received pane preparation'

# Permission-bypass and conflicting safety args stop the Launcher before start.
for bad_case in \
    'codex|["--dangerously-skip-permissions"]' \
    'claude|["--dangerously-skip-permissions"]' \
    'claude|["--dangerously-skip-permissions=true"]' \
    'claude|["--allow-dangerously-skip-permissions"]' \
    'claude|["--permission-mode", "bypassPermissions"]' \
    'claude|["--permission-mode=bypassPermissions"]' \
    'cursor|["--force"]' \
    'cursor|["-f"]' \
    'cursor|["--yolo"]' \
    'cursor|["--force=true"]' \
    'cursor|["--yolo=true"]' \
    'cursor|["--sandbox", "disabled"]' \
    'cursor|["--sandbox=disabled"]' \
    'cursor|["--sandbox"]' \
    'cursor|["--sandbox", "invalid"]' \
    'agy|["--mode", "plan"]' \
    'agy|["--dangerously-skip-permissions"]' \
    'opencode|["--auto"]'; do
    kind="${bad_case%%|*}"
    args="${bad_case#*|}"
    bad_config="$test_root/bad-$kind.toml"
    printf '[implementation]\nkind = "%s"\nargs = %s\n' "$kind" "$args" >"$bad_config"
    rm -f "$fake_state/agent-start"
    if run_launcher "$bad_config" >/dev/null 2>&1; then
        fail "unsafe args accepted for $kind: $args"
    fi
    [[ ! -e "$fake_state/agent-start" ]] || fail "agent started despite unsafe args for $kind"
done

printf 'ok - Agent Exchange Launcher kind preparation tests passed\n'
