#!/usr/bin/env bash
# Contract tests for the Agent Exchange systemd adapter. Uses only temporary
# directories and a synthetic env file; no user or system unit is changed.
set -Eeuo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd -- "$TEST_DIR/../scripts" && pwd)"
INSTALLER="$SCRIPT_DIR/install-systemd.sh"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

# Do not inherit a real deployment from the host environment.
unset AGENT_EXCHANGE_ENV_FILE AGENT_EXCHANGE_CONFIG AGENT_EXCHANGE_ROOT \
    HERDR_BIN HERDR_SESSION XDG_CONFIG_HOME 2>/dev/null || true

fail() {
    printf 'not ok - %s\n' "$1" >&2
    exit 1
}

assert_contains() {
    grep -F -- "$2" "$1" >/dev/null || fail "$1 does not contain $2"
}

assert_absent() {
    if grep -F -- "$2" "$1" >/dev/null; then
        fail "$1 unexpectedly contains $2"
    fi
}

skill_dir="$test_root/skill dir"
mkdir -p "$skill_dir/scripts"
herdr_bin="$test_root/herdr"
printf '#!/bin/sh\nexit 0\n' >"$herdr_bin"
chmod +x "$herdr_bin"
exchange_root="$test_root/exchange root"

write_env() {
    local path="$1"
    mkdir -p -- "$(dirname -- "$path")"
    {
        printf 'AGENT_EXCHANGE_SKILL_DIR=%s\n' "$skill_dir"
        printf 'AGENT_EXCHANGE_ROOT=%s\n' "$exchange_root"
        printf 'HERDR_BIN=%s\n' "$herdr_bin"
        printf 'HERDR_SESSION=agent-exchange\n'
    } >"$path"
}

xdg_home="$test_root/xdg"
xdg_env="$xdg_home/knowledge-management/agent-exchange.env"
write_env "$xdg_env"

# The XDG env file is resolved to an absolute EnvironmentFile at install time.
AGENT_EXCHANGE_ENV_FILE='' XDG_CONFIG_HOME="$xdg_home" HOME="$test_root/homedir" \
    bash "$INSTALLER" --dry-run >"$test_root/dry-run.txt" 2>"$test_root/err"
assert_contains "$test_root/dry-run.txt" "EnvironmentFile=$(realpath -e -- "$xdg_env")"
assert_contains "$test_root/dry-run.txt" "ExecStart=$skill_dir/scripts/launcher.sh"
assert_contains "$test_root/dry-run.txt" "ExecStart=$skill_dir/scripts/herdr-server.sh"
# Deployment values stay in the env file, not in the unit.
assert_absent "$test_root/dry-run.txt" "$exchange_root"
assert_absent "$test_root/dry-run.txt" "$herdr_bin"
assert_absent "$test_root/dry-run.txt" '@'
# No private markers from any source layout.
for marker in shelffiles zsh .hermes; do
    assert_absent "$test_root/dry-run.txt" "$marker"
done

# An explicit AGENT_EXCHANGE_ENV_FILE wins over XDG.
explicit_env="$test_root/explicit.env"
write_env "$explicit_env"
AGENT_EXCHANGE_ENV_FILE="$explicit_env" XDG_CONFIG_HOME="$xdg_home" HOME="$test_root/homedir" \
    bash "$INSTALLER" --dry-run >"$test_root/explicit.txt" 2>"$test_root/err"
assert_contains "$test_root/explicit.txt" "EnvironmentFile=$(realpath -e -- "$explicit_env")"

# HOME fallback is used when XDG and the explicit override are empty.
home_env="$test_root/homedir/.config/knowledge-management/agent-exchange.env"
write_env "$home_env"
AGENT_EXCHANGE_ENV_FILE='' XDG_CONFIG_HOME='' HOME="$test_root/homedir" \
    bash "$INSTALLER" --dry-run >"$test_root/homedir.txt" 2>"$test_root/err"
assert_contains "$test_root/homedir.txt" "EnvironmentFile=$(realpath -e -- "$home_env")"

# A missing required value stops before writing anything.
missing_env="$test_root/missing.env"
grep -v '^HERDR_BIN=' "$xdg_env" >"$missing_env"
if AGENT_EXCHANGE_ENV_FILE="$missing_env" bash "$INSTALLER" --dry-run >"$test_root/missing.txt" 2>"$test_root/missing-err"; then
    fail 'missing required value was accepted'
fi
grep -F 'HERDR_BIN' "$test_root/missing-err" >/dev/null || fail 'missing value reason is unclear'

# Relative deployment values are rejected.
relative_env="$test_root/relative.env"
sed "s|AGENT_EXCHANGE_ROOT=.*|AGENT_EXCHANGE_ROOT=relative/exchange|" "$xdg_env" >"$relative_env"
if AGENT_EXCHANGE_ENV_FILE="$relative_env" bash "$INSTALLER" --dry-run >/dev/null 2>&1; then
    fail 'relative AGENT_EXCHANGE_ROOT was accepted'
fi

# Writing units embeds the resolved absolute EnvironmentFile.
target="$test_root/units"
AGENT_EXCHANGE_ENV_FILE='' XDG_CONFIG_HOME="$xdg_home" HOME="$test_root/homedir" \
    bash "$INSTALLER" --target-dir "$target" >"$test_root/write.txt" 2>"$test_root/err"
assert_contains "$target/agent-exchange-launcher.service" "EnvironmentFile=$(realpath -e -- "$xdg_env")"
assert_contains "$target/herdr-agent-exchange.service" "EnvironmentFile=$(realpath -e -- "$xdg_env")"
assert_absent "$target/agent-exchange-launcher.service" '@'
assert_absent "$target/herdr-agent-exchange.service" '@'

printf 'ok - Agent Exchange systemd adapter tests passed\n'
