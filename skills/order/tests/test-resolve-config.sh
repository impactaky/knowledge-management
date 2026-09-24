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
    # run_resolver <explicit> <order_config> <xdg> <home> [resolver args...]
    local explicit="$1" order_config="$2" xdg="$3" home="$4"
    shift 4
    local -a extra=()
    [[ -z "$explicit" ]] || extra=(--config "$explicit")
    set +e
    resolver_out="$(
        ORDER_CONFIG="$order_config" XDG_CONFIG_HOME="$xdg" HOME="$home" \
            python3 "$RESOLVER" "${extra[@]}" "$@" 2>"$test_root/stderr"
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

# --- Named implementation candidates ---------------------------------------

named="$test_root/named.toml"
write_config "$named" \
    '[implementation]' \
    'name = "fast-code"' \
    '[implementations.fast-code]' \
    'label = "Example fast coding model"' \
    'kind = "example-cli"' \
    'args = ["--model", "example-model"]' \
    '[implementations.reasoning]' \
    'kind = "another-cli"' \
    'args = ["--model", "another-example-model", "--effort", "high"]' \
    '[herdr]' \
    'session = "custom-session"' \
    '[worklog]' \
    'root = "/absolute/worklog/root"'

# --list reports names, labels, kinds and exact args in config order without
# resolving a default, launching an agent or querying a provider.
run_resolver "$named" '' '' '' --list
[[ "$resolver_status" -eq 0 ]] || fail "--list failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r .config_path <<<"$resolver_out")" \
    "$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$named")" 'list config path'
assert_eq "$(jq -r .default <<<"$resolver_out")" fast-code 'list default name'
assert_eq "$(jq -r '.implementations | length' <<<"$resolver_out")" 2 'list candidate count'
assert_eq "$(jq -r '.implementations[0].name' <<<"$resolver_out")" fast-code 'list preserves config order'
assert_eq "$(jq -r '.implementations[1].name' <<<"$resolver_out")" reasoning 'list preserves config order'
assert_eq "$(jq -r '.implementations[0].label' <<<"$resolver_out")" \
    'Example fast coding model' 'list label'
assert_eq "$(jq -r '.implementations[1].label' <<<"$resolver_out")" reasoning 'label defaults to name'
assert_eq "$(jq -r '.implementations[0].kind' <<<"$resolver_out")" example-cli 'list kind'
assert_eq "$(jq -c '.implementations[1].args' <<<"$resolver_out")" \
    '["--model","another-example-model","--effort","high"]' 'list exact args order'

# A named default resolves without a selector and keeps inherited metadata.
run_resolver "$named" '' '' ''
[[ "$resolver_status" -eq 0 ]] || fail "named default failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r .kind <<<"$resolver_out")" example-cli 'named default kind'
assert_eq "$(jq -c .args <<<"$resolver_out")" '["--model","example-model"]' 'named default args'
assert_eq "$(jq -r .implementation <<<"$resolver_out")" fast-code 'named default identity'
assert_eq "$(jq -r .label <<<"$resolver_out")" 'Example fast coding model' 'named default label'
assert_eq "$(jq -r .source <<<"$resolver_out")" config 'named default source'
assert_eq "$(jq -r .session <<<"$resolver_out")" custom-session 'named default session'
assert_eq "$(jq -r .worklog_root <<<"$resolver_out")" /absolute/worklog/root 'named default worklog'

# --implementation overrides a different valid default for one order only.
run_resolver "$named" '' '' '' --implementation reasoning
[[ "$resolver_status" -eq 0 ]] || fail "--implementation failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r .kind <<<"$resolver_out")" another-cli 'selected kind'
assert_eq "$(jq -c .args <<<"$resolver_out")" \
    '["--model","another-example-model","--effort","high"]' 'selected args'
assert_eq "$(jq -r .implementation <<<"$resolver_out")" reasoning 'selected identity'
assert_eq "$(jq -r .label <<<"$resolver_out")" reasoning 'selected label'
assert_eq "$(jq -r .session <<<"$resolver_out")" custom-session 'selected session inherited'
assert_eq "$(jq -r .worklog_root <<<"$resolver_out")" /absolute/worklog/root 'selected worklog inherited'

# Unknown names, and combining --list with --implementation, are errors.
run_resolver "$named" '' '' '' --implementation does-not-exist
[[ "$resolver_status" -ne 0 ]] || fail 'unknown implementation was accepted'
grep -F 'unknown implementation' "$test_root/stderr" >/dev/null ||
    fail 'unknown implementation reason is unclear'
run_resolver "$named" '' '' '' --list --implementation fast-code
[[ "$resolver_status" -ne 0 ]] || fail '--list and --implementation were combined'

# A registry-only config lists and selects, but has no default to resolve.
registry="$test_root/registry.toml"
write_config "$registry" \
    '[implementations.only]' \
    'kind = "only-kind"' \
    'args = ["--only"]'
run_resolver "$registry" '' '' '' --list
[[ "$resolver_status" -eq 0 ]] || fail 'registry-only --list failed'
assert_eq "$(jq -r .default <<<"$resolver_out")" null 'registry-only has no default'
assert_eq "$(jq -r '.implementations | length' <<<"$resolver_out")" 1 'registry-only count'
run_resolver "$registry" '' '' '' --implementation only
[[ "$resolver_status" -eq 0 ]] || fail 'registry-only selection failed'
assert_eq "$(jq -r .kind <<<"$resolver_out")" only-kind 'registry-only selected kind'
run_resolver "$registry" '' '' ''
[[ "$resolver_status" -ne 0 ]] || fail 'registry-only default resolution succeeded'
grep -F 'no default implementation' "$test_root/stderr" >/dev/null ||
    fail 'missing-default reason is unclear'

# An explicitly empty registry is structurally valid: it lists no entries and
# no default, select fails as unknown, and only default resolution reports the
# missing default.
empty_registry="$test_root/empty-registry.toml"
write_config "$empty_registry" '[implementations]'
run_resolver "$empty_registry" '' '' '' --list
[[ "$resolver_status" -eq 0 ]] || fail "empty registry --list failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r .default <<<"$resolver_out")" null 'empty registry has no default'
assert_eq "$(jq -c .implementations <<<"$resolver_out")" '[]' 'empty registry lists no entries'
run_resolver "$empty_registry" '' '' '' --implementation only
[[ "$resolver_status" -ne 0 ]] || fail 'empty registry accepted a name selection'
grep -F 'unknown implementation' "$test_root/stderr" >/dev/null ||
    fail 'empty registry unknown-name reason is unclear'
run_resolver "$empty_registry" '' '' ''
[[ "$resolver_status" -ne 0 ]] || fail 'empty registry default resolution succeeded'
grep -F 'no default implementation' "$test_root/stderr" >/dev/null ||
    fail 'empty registry missing-default reason is unclear'

# A legacy inline-only config lists zero named entries and keeps resolving.
run_resolver "$explicit" '' '' '' --list
[[ "$resolver_status" -eq 0 ]] || fail 'legacy --list failed'
assert_eq "$(jq -r .default <<<"$resolver_out")" null 'legacy inline default is null in list'
assert_eq "$(jq -r '.implementations | length' <<<"$resolver_out")" 0 'legacy lists no candidates'
run_resolver "$explicit" '' '' ''
[[ "$resolver_status" -eq 0 ]] || fail 'legacy default resolution failed'
assert_eq "$(jq -r .kind <<<"$resolver_out")" explicit-kind 'legacy inline kind'
assert_eq "$(jq -c .args <<<"$resolver_out")" '["--one"]' 'legacy inline args'
assert_eq "$(jq -r .implementation <<<"$resolver_out")" null 'legacy identity is null'
assert_eq "$(jq -r .label <<<"$resolver_out")" null 'legacy label is null'

# An inline default can coexist with named candidates: list still returns the
# candidates, the default resolves inline, and explicit selection overrides it.
combo="$test_root/combo.toml"
write_config "$combo" \
    '[implementation]' \
    'kind = "inline-kind"' \
    'args = ["--inline"]' \
    '[implementations.extra]' \
    'label = "Example extra model"' \
    'kind = "extra-cli"' \
    'args = ["--extra", "value"]'
run_resolver "$combo" '' '' '' --list
[[ "$resolver_status" -eq 0 ]] || fail "combo --list failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r .default <<<"$resolver_out")" null 'combo inline default is null in list'
assert_eq "$(jq -r '.implementations | length' <<<"$resolver_out")" 1 'combo lists candidates'
assert_eq "$(jq -r '.implementations[0].name' <<<"$resolver_out")" extra 'combo candidate name'
run_resolver "$combo" '' '' ''
[[ "$resolver_status" -eq 0 ]] || fail 'combo default resolution failed'
assert_eq "$(jq -r .kind <<<"$resolver_out")" inline-kind 'combo inline kind'
assert_eq "$(jq -c .args <<<"$resolver_out")" '["--inline"]' 'combo inline args'
assert_eq "$(jq -r .implementation <<<"$resolver_out")" null 'combo inline identity is null'
run_resolver "$combo" '' '' '' --implementation extra
[[ "$resolver_status" -eq 0 ]] || fail 'combo selection failed'
assert_eq "$(jq -r .kind <<<"$resolver_out")" extra-cli 'combo selected kind'
assert_eq "$(jq -c .args <<<"$resolver_out")" '["--extra","value"]' 'combo selected args'
assert_eq "$(jq -r .implementation <<<"$resolver_out")" extra 'combo selected identity'
assert_eq "$(jq -r .label <<<"$resolver_out")" 'Example extra model' 'combo selected label'

# A config with neither section is invalid for both list and resolution, so it
# is covered by the malformed-config loop below (empty file and unrelated
# section only). Malformed or ambiguous candidates and defaults fail for both
# list and resolution, with an explicit reason and no traceback.
for bad_case in \
    'empty-file.toml|' \
    'no-impl-other.toml|[other]\nkind = "k"' \
    'impl-empty.toml|[implementation]' \
    'impl-mixed.toml|[implementation]\nname = "x"\nkind = "k"\nargs = []\n[implementations.x]\nkind = "k2"\nargs = []' \
    'impl-unknown.toml|[implementation]\nname = "missing"' \
    'impl-blank-name.toml|[implementation]\nname = "  "\n[implementations.x]\nkind = "k"\nargs = []' \
    'candidate-blank-name.toml|[implementations."  "]\nkind = "k"\nargs = []' \
    'candidate-empty-label.toml|[implementations.x]\nlabel = ""\nkind = "k"\nargs = []' \
    'candidate-blank-kind.toml|[implementations.x]\nkind = "  "\nargs = []' \
    'candidate-bad-args.toml|[implementations.x]\nkind = "k"\nargs = [1, "two"]' \
    'impl-not-table.toml|implementation = "x"' \
    'impls-not-table.toml|implementations = "x"' \
    'candidate-not-table.toml|[implementations]\nx = "y"'; do
    name="${bad_case%%|*}"
    body="${bad_case#*|}"
    path="$test_root/$name"
    printf '%b\n' "$body" >"$path"
    run_resolver "$path" '' '' ''
    [[ "$resolver_status" -ne 0 ]] || fail "bad config was accepted on resolve: $name"
    run_resolver "$path" '' '' '' --list
    [[ "$resolver_status" -ne 0 ]] || fail "bad config was accepted on list: $name"
    if grep -F 'Traceback' "$test_root/stderr" >/dev/null; then
        fail "bad config produced a traceback: $name"
    fi
done

# Argument strings round-trip as opaque data, in order, including spaces,
# quotes, dollar signs, tabs, newlines and an empty argument.
arguments="$test_root/arguments.toml"
# The literal "${dollar}" below is fixture data, not a shell expansion.
# shellcheck disable=SC2016
write_config "$arguments" \
    '[implementations.tricky]' \
    'kind = "unknown-kind-xyz"' \
    'args = ["two words", "quote\"and${dollar}", "line1\nline2", "tab\there", ""]'
run_resolver "$arguments" '' '' '' --implementation tricky
[[ "$resolver_status" -eq 0 ]] || fail "argument preservation failed: $(cat "$test_root/stderr")"
# The literal "${dollar}" below is expected JSON data, not a shell expansion.
# shellcheck disable=SC2016
assert_eq "$(jq -c .args <<<"$resolver_out")" \
    '["two words","quote\"and${dollar}","line1\nline2","tab\there",""]' \
    'special arguments did not round-trip as data'

# --- Optional router route resolution --------------------------------------

# A config without [route] reports the route defaults, so an order that reads
# the snapshot sees the router disabled and packaged the same as before.
run_resolver "$named" '' '' '' --list
[[ "$resolver_status" -eq 0 ]] || fail "--list without [route] failed"
assert_eq "$(jq -r '.route.enabled' <<<"$resolver_out")" false 'route enabled defaults to false'
assert_eq "$(jq -r '.route.mode' <<<"$resolver_out")" balanced 'route mode defaults to balanced'
run_resolver "$named" '' '' ''
[[ "$resolver_status" -eq 0 ]] || fail 'default resolution without [route] failed'
assert_eq "$(jq -r '.route.enabled' <<<"$resolver_out")" false 'resolution route enabled default'
assert_eq "$(jq -r '.route.mode' <<<"$resolver_out")" balanced 'resolution route mode default'
run_resolver "$named" '' '' '' --implementation reasoning
[[ "$resolver_status" -eq 0 ]] || fail '--implementation without [route] failed'
assert_eq "$(jq -r '.route.enabled' <<<"$resolver_out")" false 'selection route enabled default'
assert_eq "$(jq -r '.route.mode' <<<"$resolver_out")" balanced 'selection route mode default'

# [route] without enabled/mode uses the same defaults even when it declares
# services and configs.
route_defaults="$test_root/route-defaults.toml"
write_config "$route_defaults" \
    '[implementation]' 'kind = "k"' 'args = []' \
    '[route]' \
    '[route.services]' 'example-service = "example-kind"' \
    '[route.configs."example-config"]' 'example-service = []'
run_resolver "$route_defaults" '' '' '' --list
[[ "$resolver_status" -eq 0 ]] || fail "route defaults --list failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r '.route.enabled' <<<"$resolver_out")" false 'explicit route keeps enabled default'
assert_eq "$(jq -r '.route.mode' <<<"$resolver_out")" balanced 'explicit route keeps mode default'

# A fully configured route resolves one config/service pair into a kind and
# ordered native args snapshot, preserving inherited metadata.
route_config="$test_root/route.toml"
write_config "$route_config" \
    '[implementation]' 'kind = "inline-kind"' 'args = ["--inline"]' \
    '[herdr]' 'session = "custom-session"' \
    '[worklog]' 'root = "/absolute/worklog/root"' \
    '[route]' 'enabled = true' 'mode = "best"' \
    '[route.services]' 'example-service = "example-kind"' 'another-service = "another-kind"' \
    '[route.configs."example-config"]' \
    'another-service = ["--model", "example-model", "--flag", "value with space"]' \
    'example-service = ["--example-flag", "value"]' \
    '[route.configs."empty-config"]' \
    'example-service = []'
run_resolver "$route_config" '' '' '' --list
[[ "$resolver_status" -eq 0 ]] || fail "route --list failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r '.route.enabled' <<<"$resolver_out")" true 'route list enabled'
assert_eq "$(jq -r '.route.mode' <<<"$resolver_out")" best 'route list mode'
run_resolver "$route_config" '' '' '' --route example-config another-service
[[ "$resolver_status" -eq 0 ]] || fail "--route failed: $(cat "$test_root/stderr")"
assert_eq "$(jq -r .source <<<"$resolver_out")" route 'route source'
assert_eq "$(jq -r .kind <<<"$resolver_out")" another-kind 'route service kind'
assert_eq "$(jq -c .args <<<"$resolver_out")" \
    '["--model","example-model","--flag","value with space"]' 'route args order and spaces'
assert_eq "$(jq -r .implementation <<<"$resolver_out")" null 'route implementation is null'
assert_eq "$(jq -r .label <<<"$resolver_out")" null 'route label is null'
assert_eq "$(jq -r .route_config <<<"$resolver_out")" example-config 'route config id'
assert_eq "$(jq -r .route_service <<<"$resolver_out")" another-service 'route service id'
assert_eq "$(jq -r .session <<<"$resolver_out")" custom-session 'route session inherited'
assert_eq "$(jq -r .worklog_root <<<"$resolver_out")" /absolute/worklog/root 'route worklog inherited'
assert_eq "$(jq -r .config_path <<<"$resolver_out")" \
    "$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$route_config")" \
    'route config path'
run_resolver "$route_config" '' '' '' --route example-config example-service
[[ "$resolver_status" -eq 0 ]] || fail 'second route pair failed'
assert_eq "$(jq -r .kind <<<"$resolver_out")" example-kind 'second route kind'
assert_eq "$(jq -c .args <<<"$resolver_out")" '["--example-flag","value"]' 'second route args'
run_resolver "$route_config" '' '' '' --route empty-config example-service
[[ "$resolver_status" -eq 0 ]] || fail 'empty route args failed'
assert_eq "$(jq -c .args <<<"$resolver_out")" '[]' 'empty route args stay empty'

# Unknown configs/services, a service without args for a config, and --route
# with the router disabled or combined with another mode are all errors.
for bad_route in \
    'unknown-service|example-config|does-not-exist' \
    'unknown-config|does-not-exist|example-service' \
    'missing-pair|empty-config|another-service'; do
    IFS='|' read -r label route_id service <<<"$bad_route"
    run_resolver "$route_config" '' '' '' --route "$route_id" "$service"
    [[ "$resolver_status" -ne 0 ]] || fail "--route accepted $label"
done
route_disabled="$test_root/route-disabled.toml"
write_config "$route_disabled" \
    '[implementation]' 'kind = "k"' 'args = []' \
    '[route]' 'enabled = false' \
    '[route.services]' 'example-service = "example-kind"' \
    '[route.configs."example-config"]' 'example-service = []'
run_resolver "$route_disabled" '' '' '' --route example-config example-service
[[ "$resolver_status" -ne 0 ]] || fail '--route accepted a disabled [route]'
grep -F 'enabled = true' "$test_root/stderr" >/dev/null ||
    fail 'disabled --route reason is unclear'
run_resolver "$route_config" '' '' '' --route example-config example-service --list
[[ "$resolver_status" -ne 0 ]] || fail '--route and --list were combined'
run_resolver "$route_config" '' '' '' --route example-config example-service --implementation fast-code
[[ "$resolver_status" -ne 0 ]] || fail '--route and --implementation were combined'

# Malformed [route] tables fail for both list and resolution with a clear
# reason and no traceback.
for bad_case in \
    'route-not-table.toml|route = "x"\n[implementation]\nkind = "k"\nargs = []' \
    'route-enabled-type.toml|[implementation]\nkind = "k"\nargs = []\n[route]\nenabled = "yes"' \
    'route-mode-value.toml|[implementation]\nkind = "k"\nargs = []\n[route]\nmode = "fast"' \
    'route-mode-type.toml|[implementation]\nkind = "k"\nargs = []\n[route]\nmode = 5' \
    'route-services-not-table.toml|[implementation]\nkind = "k"\nargs = []\n[route]\nservices = "x"' \
    'route-configs-not-table.toml|[implementation]\nkind = "k"\nargs = []\n[route]\nconfigs = "x"' \
    'route-service-kind-empty.toml|[implementation]\nkind = "k"\nargs = []\n[route]\n[route.services]\nexample-service = ""' \
    'route-config-not-table.toml|[implementation]\nkind = "k"\nargs = []\n[route]\n[route.services]\nexample-service = "example-kind"\n[route.configs]\nexample-config = "x"' \
    'route-unknown-service.toml|[implementation]\nkind = "k"\nargs = []\n[route]\n[route.services]\nexample-service = "example-kind"\n[route.configs."example-config"]\nmissing = []' \
    'route-args-not-array.toml|[implementation]\nkind = "k"\nargs = []\n[route]\n[route.services]\nexample-service = "example-kind"\n[route.configs."example-config"]\nexample-service = "--flag"' \
    'route-args-mixed.toml|[implementation]\nkind = "k"\nargs = []\n[route]\n[route.services]\nexample-service = "example-kind"\n[route.configs."example-config"]\nexample-service = [1, "two"]' \
    'route-enabled-no-services.toml|[implementation]\nkind = "k"\nargs = []\n[route]\nenabled = true' \
    'route-enabled-no-configs.toml|[implementation]\nkind = "k"\nargs = []\n[route]\nenabled = true\n[route.services]\nexample-service = "example-kind"'; do
    name="${bad_case%%|*}"
    body="${bad_case#*|}"
    path="$test_root/$name"
    printf '%b\n' "$body" >"$path"
    run_resolver "$path" '' '' ''
    [[ "$resolver_status" -ne 0 ]] || fail "bad route config was accepted on resolve: $name"
    run_resolver "$path" '' '' '' --list
    [[ "$resolver_status" -ne 0 ]] || fail "bad route config was accepted on list: $name"
    if grep -F 'Traceback' "$test_root/stderr" >/dev/null; then
        fail "bad route config produced a traceback: $name"
    fi
done

printf 'ok - order config resolver tests passed\n'
