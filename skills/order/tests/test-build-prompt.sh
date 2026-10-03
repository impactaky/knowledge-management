#!/usr/bin/env bash
# Contract tests for the first worker prompt builder. Uses only temporary files
# and synthetic repositories; no Herdr session or agent is involved.
set -Eeuo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd -- "$TEST_DIR/../scripts" && pwd)"
BUILDER="$SCRIPT_DIR/build-prompt.py"
WORKER_PROMPT="$TEST_DIR/../worker-prompt.md"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

# Never read a developer's real order config here: --config wins where used and
# the ORDER_CONFIG fallback points at a file that does not exist.
export ORDER_CONFIG="$test_root/absent-default.toml"
export HOME="$test_root/home"
unset XDG_CONFIG_HOME
mkdir -p "$HOME"

fail() {
    printf 'not ok - %s\n' "$1" >&2
    exit 1
}

assert_eq() {
    [[ "$1" == "$2" ]] || fail "${3:-values differ: $1 != $2}"
}

write_config() {
    local path="$1"
    shift
    printf '%s\n' "$@" >"$path"
}

run_builder() {
    set +e
    python3 "$BUILDER" "$@" >"$test_root/stdout" 2>"$test_root/stderr"
    builder_status=$?
    set -e
}

repo="$test_root/repos/example-repo"
mkdir -p "$repo"
rules_dir="$test_root/rules"
mkdir -p "$rules_dir"
write_config "$rules_dir/example-repo.md" '# Synthetic project rules' 'Do the synthetic thing.'

order_dir="$test_root/orders"
mkdir -p "$order_dir"
order="$order_dir/order.md"
printf '# Synthetic order\n\nImplement the synthetic example.\n' >"$order"

context_config="$test_root/context.toml"
write_config "$context_config" \
    '[implementation]' 'kind = "k"' 'args = []' \
    '[context]' "project_rules = \"$rules_dir/{repo}.md\""

worker_body="$(cat "$WORKER_PROMPT")"
rules_body="$(cat "$rules_dir/example-repo.md")"
order_body="$(cat "$order")"

# With rules: prefix, then # Project rules, then # Order, in that order, and the
# saved prompt.md is byte-identical to stdout.
run_builder --order "$order" --repo "$repo" --config "$context_config"
[[ "$builder_status" -eq 0 ]] || fail "rules build failed: $(cat "$test_root/stderr")"
assert_eq "$(cat "$test_root/stdout")" "$worker_body

# Project rules

$rules_body

# Order

$order_body" 'built prompt content and order'
cmp -s "$test_root/stdout" "$order_dir/prompt.md" || fail 'saved prompt.md differs from stdout'

# {repo} expands to the basename of the real path even through a symlink, so a
# symlinked --repo resolves the rules file under the real repository name.
ln -s "$repo" "$test_root/example-repo-link"
run_builder --order "$order" --repo "$test_root/example-repo-link" --config "$context_config"
[[ "$builder_status" -eq 0 ]] || fail "symlink build failed: $(cat "$test_root/stderr")"
assert_eq "$(cat "$test_root/stdout")" "$worker_body

# Project rules

$rules_body

# Order

$order_body" '{repo} did not expand to the real basename'

# A missing rules file succeeds without the section and reports a notice.
missing_config="$test_root/missing-rules.toml"
write_config "$missing_config" \
    '[implementation]' 'kind = "k"' 'args = []' \
    '[context]' "project_rules = \"$test_root/missing/{repo}.md\""
run_builder --order "$order" --repo "$repo" --config "$missing_config"
[[ "$builder_status" -eq 0 ]] || fail "missing rules should succeed: $(cat "$test_root/stderr")"
assert_eq "$(cat "$test_root/stdout")" "$worker_body

# Order

$order_body" 'missing rules should omit the section'
grep -qF 'project rules file not found' "$test_root/stderr" ||
    fail 'missing rules notice is absent'
cmp -s "$test_root/stdout" "$order_dir/prompt.md" || fail 'missing-rules prompt was not saved'

# No [context] section succeeds without rules and reports a notice.
no_context_config="$test_root/no-context.toml"
write_config "$no_context_config" '[implementation]' 'kind = "k"' 'args = []'
run_builder --order "$order" --repo "$repo" --config "$no_context_config"
[[ "$builder_status" -eq 0 ]] || fail "missing [context] should succeed: $(cat "$test_root/stderr")"
if grep -qF '# Project rules' "$test_root/stdout"; then
    fail '[context]-less build added a rules section'
fi
grep -qF 'no [context] project_rules configured' "$test_root/stderr" ||
    fail '[context] notice is absent'

# A config file that does not exist (explicit or via the ORDER_CONFIG fallback)
# succeeds without rules and reports a notice.
run_builder --order "$order" --repo "$repo" --config "$test_root/absent.toml"
[[ "$builder_status" -eq 0 ]] || fail "absent config should succeed: $(cat "$test_root/stderr")"
if grep -qF '# Project rules' "$test_root/stdout"; then
    fail 'absent config added a rules section'
fi
grep -qF 'config not found' "$test_root/stderr" || fail 'absent config notice is absent'
run_builder --order "$order" --repo "$repo"
[[ "$builder_status" -eq 0 ]] || fail "ORDER_CONFIG fallback should succeed: $(cat "$test_root/stderr")"
grep -qF 'config not found' "$test_root/stderr" || fail 'fallback config notice is absent'

# An invalid config is an error that writes nothing and saves nothing.
for bad_case in \
    'context-not-table|context = "x"\n[implementation]\nkind = "k"\nargs = []' \
    'context-relative|[implementation]\nkind = "k"\nargs = []\n[context]\nproject_rules = "relative/{repo}.md"' \
    'context-placeholder|[implementation]\nkind = "k"\nargs = []\n[context]\nproject_rules = "/absolute/{other}.md"' \
    'context-number|[implementation]\nkind = "k"\nargs = []\n[context]\nproject_rules = 5' \
    'context-empty|[implementation]\nkind = "k"\nargs = []\n[context]\nproject_rules = ""'; do
    name="${bad_case%%|*}"
    body="${bad_case#*|}"
    bad_order_dir="$test_root/bad-orders/$name"
    mkdir -p "$bad_order_dir"
    bad_order="$bad_order_dir/order.md"
    printf '# Synthetic order\n' >"$bad_order"
    bad_config="$test_root/bad-$name.toml"
    printf '%b\n' "$body" >"$bad_config"
    run_builder --order "$bad_order" --repo "$repo" --config "$bad_config"
    [[ "$builder_status" -ne 0 ]] || fail "invalid config was accepted: $name"
    [[ ! -s "$test_root/stdout" ]] || fail "invalid config wrote to stdout: $name"
    [[ ! -e "$bad_order_dir/prompt.md" ]] || fail "invalid config saved prompt.md: $name"
    if grep -F 'Traceback' "$test_root/stderr" >/dev/null; then
        fail "invalid config produced a traceback: $name"
    fi
done

# [env] is validated by the shared loader but does not change the prompt: a
# valid table still builds and saves, an invalid one writes nothing.
env_config="$test_root/env.toml"
write_config "$env_config" \
    '[implementation]' 'kind = "k"' 'args = []' \
    '[context]' "project_rules = \"$rules_dir/{repo}.md\"" \
    '[env]' 'inherit = ["CLAUDE_CONFIG_DIR", "ORDER_TEST_VAR"]'
run_builder --order "$order" --repo "$repo" --config "$env_config"
[[ "$builder_status" -eq 0 ]] || fail "[env] build failed: $(cat "$test_root/stderr")"
assert_eq "$(cat "$test_root/stdout")" "$worker_body

# Project rules

$rules_body

# Order

$order_body" '[env] should not change the prompt'
cmp -s "$test_root/stdout" "$order_dir/prompt.md" || fail '[env] prompt was not saved'

bad_env_dir="$test_root/bad-env-order"
mkdir -p "$bad_env_dir"
bad_env_order="$bad_env_dir/order.md"
printf '# Synthetic order\n' >"$bad_env_order"
bad_env_config="$test_root/bad-env.toml"
write_config "$bad_env_config" \
    '[implementation]' 'kind = "k"' 'args = []' \
    '[env]' 'inherit = "CLAUDE_CONFIG_DIR"'
run_builder --order "$bad_env_order" --repo "$repo" --config "$bad_env_config"
[[ "$builder_status" -ne 0 ]] || fail 'an invalid [env] was accepted'
[[ ! -s "$test_root/stdout" ]] || fail 'an invalid [env] wrote to stdout'
[[ ! -e "$bad_env_dir/prompt.md" ]] || fail 'an invalid [env] saved prompt.md'
grep -F 'inherit' "$test_root/stderr" >/dev/null ||
    fail 'an invalid [env] reason is unclear'
if grep -F 'Traceback' "$test_root/stderr" >/dev/null; then
    fail 'an invalid [env] produced a traceback'
fi

# An unwritable prompt.md (a directory in its place) fails with non-zero status
# and writes nothing to stdout.
unwritable_dir="$test_root/unwritable"
mkdir -p "$unwritable_dir/prompt.md"
unwritable_order="$unwritable_dir/order.md"
printf '# Synthetic order\n' >"$unwritable_order"
run_builder --order "$unwritable_order" --repo "$repo" --config "$context_config"
[[ "$builder_status" -ne 0 ]] || fail 'an unwritable prompt.md was accepted'
[[ ! -s "$test_root/stdout" ]] || fail 'a failed save wrote to stdout'
grep -qF 'cannot save' "$test_root/stderr" || fail 'failed save reason is absent'

# A --repo that is not a directory is rejected before any output.
printf 'not a directory\n' >"$test_root/not-a-dir"
run_builder --order "$order" --repo "$test_root/not-a-dir" --config "$context_config"
[[ "$builder_status" -ne 0 ]] || fail 'a non-directory --repo was accepted'
[[ ! -s "$test_root/stdout" ]] || fail 'a non-directory --repo wrote to stdout'

printf 'ok - build-prompt contract\n'
