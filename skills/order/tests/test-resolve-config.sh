#!/usr/bin/env bash
# Contract tests for the order config resolver. Uses only temporary files and
# no implementation repository, agent or Herdr session.
set -Eeuo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd -- "$TEST_DIR/../scripts" && pwd)"
RESOLVER="$SCRIPT_DIR/resolve-config.py"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

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

run_resolver() {
    # run_resolver <explicit> <order_config> <xdg> <home>
    local explicit="$1" order_config="$2" xdg="$3" home="$4"
    local -a extra=()
    [[ -z "$explicit" ]] || extra=(--config "$explicit")
    set +e
    resolver_out="$(
        ORDER_CONFIG="$order_config" XDG_CONFIG_HOME="$xdg" HOME="$home" \
            python3 "$RESOLVER" "${extra[@]}" 2>"$test_root/stderr"
    )"
    resolver_status=$?
    set -e
}

mkdir -p "$test_root/xdg/knowledge-management" "$test_root/homedir/.config/knowledge-management"

explicit="$test_root/explicit.toml"
write_config "$explicit" '[implementation]' 'kind = "explicit-kind"' 'args = ["--one"]'
order_config="$test_root/order-config.toml"
write_config "$order_config" '[implementation]' 'kind = "order-env-kind"' 'args = ["--two"]'
xdg_config="$test_root/xdg/knowledge-management/order.toml"
write_config "$xdg_config" '[implementation]' 'kind = "xdg-kind"' 'args = ["--three"]'
home_config="$test_root/homedir/.config/knowledge-management/order.toml"
write_config "$home_config" '[implementation]' 'kind = "home-kind"' 'args = ["--four"]'

# Explicit argument wins over ORDER_CONFIG, XDG and HOME.
run_resolver "$explicit" "$order_config" "$test_root/xdg" "$test_root/homedir"
[[ "$resolver_status" -eq 0 ]] || fail "explicit resolver failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r .kind <<<"$resolver_out")" explicit-kind 'explicit config did not win'
assert_eq "$(jq -r .source <<<"$resolver_out")" config 'source'

# ORDER_CONFIG wins over XDG and HOME.
run_resolver '' "$order_config" "$test_root/xdg" "$test_root/homedir"
[[ "$resolver_status" -eq 0 ]] || fail "ORDER_CONFIG resolver failed"
assert_eq "$(jq -r .kind <<<"$resolver_out")" order-env-kind 'ORDER_CONFIG did not win'

# XDG wins over HOME when ORDER_CONFIG is empty.
run_resolver '' '' "$test_root/xdg" "$test_root/homedir"
[[ "$resolver_status" -eq 0 ]] || fail "XDG resolver failed"
assert_eq "$(jq -r .kind <<<"$resolver_out")" xdg-kind 'XDG did not win'

# HOME fallback is used only when XDG is empty.
run_resolver '' '' '' "$test_root/homedir"
[[ "$resolver_status" -eq 0 ]] || fail "HOME resolver failed"
assert_eq "$(jq -r .kind <<<"$resolver_out")" home-kind 'HOME fallback did not apply'

# Argument order is preserved and unknown kinds are not altered.
ordered="$test_root/ordered.toml"
write_config "$ordered" '[implementation]' 'kind = "unknown-kind-xyz"' \
    'args = ["--model", "synthetic-model", "--flag", "value with space"]'
run_resolver "$ordered" '' '' "$test_root/homedir"
[[ "$resolver_status" -eq 0 ]] || fail "ordered resolver failed"
assert_eq "$(jq -r .kind <<<"$resolver_out")" unknown-kind-xyz 'unknown kind was altered'
assert_eq "$(jq -c .args <<<"$resolver_out")" '["--model","synthetic-model","--flag","value with space"]' 'args order changed'

# Session defaults and optional absolute worklog root are surfaced.
full="$test_root/full.toml"
write_config "$full" '[implementation]' 'kind = "k"' 'args = []' '[herdr]' 'session = "custom-session"' \
    '[worklog]' 'root = "/absolute/worklog/root"'
run_resolver "$full" '' '' "$test_root/homedir"
assert_eq "$(jq -r .session <<<"$resolver_out")" custom-session 'session override'
assert_eq "$(jq -r .worklog_root <<<"$resolver_out")" /absolute/worklog/root 'worklog root'
run_resolver "$home_config" '' '' "$test_root/homedir"
assert_eq "$(jq -r .session <<<"$resolver_out")" agent-exchange 'default session'

# Missing config, invalid TOML, empty kind and non-string args are errors.
for bad_case in \
    'missing.toml|' \
    'invalid.toml|this is not toml' \
    'empty-kind.toml|[implementation]\nkind = ""\nargs = []' \
    'bad-args.toml|[implementation]\nkind = "k"\nargs = [1, 2]' \
    'bare-args.toml|[implementation]\nkind = "k"\nargs = "not-an-array"' \
    'no-impl.toml|[other]\nkind = "k"'; do
    name="${bad_case%%|*}"
    body="${bad_case#*|}"
    path="$test_root/$name"
    [[ -z "$body" ]] || printf '%b\n' "$body" >"$path"
    run_resolver "$path" '' '' "$test_root/homedir"
    [[ "$resolver_status" -ne 0 ]] || fail "bad config was accepted: $name"
done

# A relative worklog root is rejected.
relative="$test_root/relative.toml"
write_config "$relative" '[implementation]' 'kind = "k"' 'args = []' '[worklog]' 'root = "relative/root"'
run_resolver "$relative" '' '' "$test_root/homedir"
[[ "$resolver_status" -ne 0 ]] || fail 'relative worklog root was accepted'

# A relative XDG_CONFIG_HOME is rejected instead of resolving against cwd.
run_resolver '' '' 'relative/xdg' "$test_root/homedir"
[[ "$resolver_status" -ne 0 ]] || fail 'relative XDG_CONFIG_HOME was accepted'
grep -F 'XDG_CONFIG_HOME must be an absolute path' "$test_root/stderr" >/dev/null ||
    fail 'relative XDG_CONFIG_HOME reason is unclear'

# A relative HOME fallback is rejected for the same reason.
run_resolver '' '' '' 'relative/home'
[[ "$resolver_status" -ne 0 ]] || fail 'relative HOME was accepted'

printf 'ok - order config resolver tests passed\n'
