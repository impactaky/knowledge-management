#!/usr/bin/env bash
# Contract tests for the Agent Exchange Launcher config resolver. Uses only
# temporary directories; no real config, store or agent is touched.
set -Eeuo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd -- "$TEST_DIR/../scripts" && pwd)"
RESOLVER="$SCRIPT_DIR/launcher-config.sh"
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
    # run_resolver <config> <xdg> <home> ; prints stdout, captures status/stderr
    local config="$1" xdg="$2" home="$3"
    set +e
    resolver_out="$(AGENT_EXCHANGE_CONFIG="$config" XDG_CONFIG_HOME="$xdg" HOME="$home" \
        bash "$RESOLVER" 2>"$test_root/stderr")"
    resolver_status=$?
    set -e
}

mkdir -p "$test_root/xdg/knowledge-management" "$test_root/homedir/.config/knowledge-management"

explicit="$test_root/explicit.toml"
write_config "$explicit" '[implementation]' 'kind = "explicit-kind"' 'args = ["--one"]'
xdg_config="$test_root/xdg/knowledge-management/agent-exchange.toml"
write_config "$xdg_config" '[implementation]' 'kind = "xdg-kind"' 'args = ["--two"]'
home_config="$test_root/homedir/.config/knowledge-management/agent-exchange.toml"
write_config "$home_config" '[implementation]' 'kind = "home-kind"' 'args = ["--three"]'

# Explicit AGENT_EXCHANGE_CONFIG wins over XDG and HOME.
run_resolver "$explicit" "$test_root/xdg" "$test_root/homedir"
[[ "$resolver_status" -eq 0 ]] || fail "explicit resolver failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r .kind <<<"$resolver_out")" explicit-kind 'explicit config did not win'

# XDG wins over HOME when no explicit path is set.
run_resolver '' "$test_root/xdg" "$test_root/homedir"
[[ "$resolver_status" -eq 0 ]] || fail "XDG resolver failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r .kind <<<"$resolver_out")" xdg-kind 'XDG config did not win'
assert_eq "$(jq -r .config_path <<<"$resolver_out")" "$(realpath -e -- "$xdg_config")" 'XDG path'

# HOME fallback is used only when XDG is empty.
run_resolver '' '' "$test_root/homedir"
[[ "$resolver_status" -eq 0 ]] || fail "HOME resolver failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r .kind <<<"$resolver_out")" home-kind 'HOME fallback did not apply'
assert_eq "$(jq -r .config_path <<<"$resolver_out")" "$(realpath -e -- "$home_config")" 'HOME path'

# Argument order and unknown-kind passthrough are preserved exactly.
ordered="$test_root/ordered.toml"
write_config "$ordered" '[implementation]' 'kind = "unknown-kind-xyz"' \
    'args = ["--model", "synthetic-model", "--flag", "value with space"]'
run_resolver "$ordered" '' "$test_root/homedir"
[[ "$resolver_status" -eq 0 ]] || fail "ordered resolver failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r .kind <<<"$resolver_out")" unknown-kind-xyz 'unknown kind was altered'
assert_eq "$(jq -c .args <<<"$resolver_out")" '["--model","synthetic-model","--flag","value with space"]' 'args order changed'

# A missing config is an explicit error before any agent starts.
run_resolver "$test_root/absent.toml" '' "$test_root/homedir"
[[ "$resolver_status" -ne 0 ]] || fail 'missing config was accepted'
grep -F 'config file not found' "$test_root/stderr" >/dev/null || fail 'missing config reason is unclear'

# Invalid TOML is rejected.
invalid="$test_root/invalid.toml"
write_config "$invalid" 'this is not toml'
run_resolver "$invalid" '' "$test_root/homedir"
[[ "$resolver_status" -ne 0 ]] || fail 'invalid TOML was accepted'

# Empty kind is rejected.
empty_kind="$test_root/empty-kind.toml"
write_config "$empty_kind" '[implementation]' 'kind = ""' 'args = []'
run_resolver "$empty_kind" '' "$test_root/homedir"
[[ "$resolver_status" -ne 0 ]] || fail 'empty kind was accepted'

# Missing [implementation] is rejected.
missing_section="$test_root/missing-section.toml"
write_config "$missing_section" '[other]' 'kind = "x"'
run_resolver "$missing_section" '' "$test_root/homedir"
[[ "$resolver_status" -ne 0 ]] || fail 'missing implementation section was accepted'

# Non-string args (typed integers and a bare string) are rejected.
for bad in 'args = [1, 2]' 'args = "not-an-array"' 'args = ["ok", 3]'; do
    bad_config="$test_root/bad-args.toml"
    write_config "$bad_config" '[implementation]' 'kind = "k"' "$bad"
    run_resolver "$bad_config" '' "$test_root/homedir"
    [[ "$resolver_status" -ne 0 ]] || fail "non-string args were accepted: $bad"
done

# A symlinked config is refused rather than followed.
ln -s "$explicit" "$test_root/symlink.toml"
run_resolver "$test_root/symlink.toml" '' "$test_root/homedir"
[[ "$resolver_status" -ne 0 ]] || fail 'symlinked config was followed'

printf 'ok - Agent Exchange Launcher config resolver tests passed\n'
