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
for script in launcher.sh herdr-server.sh; do
    printf '#!/bin/sh\nexit 0\n' >"$skill_dir/scripts/$script"
    chmod +x "$skill_dir/scripts/$script"
done
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
# EnvironmentFile= is emitted unquoted (systemd keeps the rest of the line
# verbatim, so spaces survive), while ExecStart= is quoted as one word.
AGENT_EXCHANGE_ENV_FILE='' XDG_CONFIG_HOME="$xdg_home" HOME="$test_root/homedir" \
    bash "$INSTALLER" --dry-run >"$test_root/dry-run.txt" 2>"$test_root/err"
assert_contains "$test_root/dry-run.txt" "EnvironmentFile=$(realpath -e -- "$xdg_env")"
assert_absent "$test_root/dry-run.txt" "EnvironmentFile=\""
assert_contains "$test_root/dry-run.txt" "ExecStart=\"$skill_dir/scripts/launcher.sh\""
assert_contains "$test_root/dry-run.txt" "ExecStart=\"$skill_dir/scripts/herdr-server.sh\""
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

# Writing units embeds the resolved absolute EnvironmentFile unquoted.
target="$test_root/units"
AGENT_EXCHANGE_ENV_FILE='' XDG_CONFIG_HOME="$xdg_home" HOME="$test_root/homedir" \
    bash "$INSTALLER" --target-dir "$target" >"$test_root/write.txt" 2>"$test_root/err"
assert_contains "$target/agent-exchange-launcher.service" "EnvironmentFile=$(realpath -e -- "$xdg_env")"
assert_contains "$target/herdr-agent-exchange.service" "EnvironmentFile=$(realpath -e -- "$xdg_env")"
assert_contains "$target/agent-exchange-launcher.service" "ExecStart=\"$skill_dir/scripts/launcher.sh\""
assert_absent "$target/agent-exchange-launcher.service" '@'
assert_absent "$target/herdr-agent-exchange.service" '@'

# A literal '%' is doubled so systemd does not treat it as a specifier, and
# spaces stay part of the EnvironmentFile path.
percent_dir="$test_root/env %dir"
percent_env="$percent_dir/agent.env"
write_env "$percent_env"
percent_target="$test_root/percent-units"
AGENT_EXCHANGE_ENV_FILE="$percent_env" bash "$INSTALLER" --target-dir "$percent_target" >/dev/null 2>"$test_root/err"
assert_contains "$percent_target/agent-exchange-launcher.service" "EnvironmentFile=${percent_env//%/%%}"
if command -v systemd-analyze >/dev/null 2>&1; then
    if ! systemd-analyze verify --man=no \
        "$percent_target/agent-exchange-launcher.service" \
        "$percent_target/herdr-agent-exchange.service" \
        >"$test_root/percent-verify.txt" 2>&1; then
        cat "$test_root/percent-verify.txt" >&2
        fail 'systemd-analyze verify rejected the percent-escaped unit'
    fi
    if grep -E 'Failed to resolve unit specifiers' "$test_root/percent-verify.txt" >/dev/null; then
        cat "$test_root/percent-verify.txt" >&2
        fail 'percent escaping produced a specifier error'
    fi
fi

# ExecStart= is quoted and doubles '%' so it survives systemd specifier
# expansion while spaces stay in one word.
special_skill="$test_root/skill pct% dir"
mkdir -p "$special_skill/scripts"
for script in launcher.sh herdr-server.sh; do
    printf '#!/bin/sh\nexit 0\n' >"$special_skill/scripts/$script"
    chmod +x "$special_skill/scripts/$script"
done
special_env="$test_root/special.env"
{
    printf 'AGENT_EXCHANGE_SKILL_DIR=%s\n' "$special_skill"
    printf 'AGENT_EXCHANGE_ROOT=%s\n' "$exchange_root"
    printf 'HERDR_BIN=%s\n' "$herdr_bin"
    printf 'HERDR_SESSION=agent-exchange\n'
} >"$special_env"
special_target="$test_root/special-units"
AGENT_EXCHANGE_ENV_FILE="$special_env" bash "$INSTALLER" --target-dir "$special_target" >/dev/null 2>"$test_root/err"
expected_skill="${special_skill//%/%%}"
assert_contains "$special_target/agent-exchange-launcher.service" "ExecStart=\"$expected_skill/scripts/launcher.sh\""
if command -v systemd-analyze >/dev/null 2>&1; then
    if ! systemd-analyze verify --man=no \
        "$special_target/agent-exchange-launcher.service" \
        "$special_target/herdr-agent-exchange.service" >"$test_root/special-verify.txt" 2>&1; then
        cat "$test_root/special-verify.txt" >&2
        fail 'systemd-analyze verify rejected the escaped ExecStart unit'
    fi
fi

# systemd-analyze (when present) must accept the generated units: no ExecStart
# truncation at a space, no EnvironmentFile quote or specifier warnings.
if command -v systemd-analyze >/dev/null 2>&1; then
    if ! systemd-analyze verify --man=no \
        "$target/agent-exchange-launcher.service" \
        "$target/herdr-agent-exchange.service" >"$test_root/verify.txt" 2>&1; then
        cat "$test_root/verify.txt" >&2
        fail 'systemd-analyze verify rejected the generated units'
    fi
    if grep -E 'path is not absolute|Failed to resolve unit specifiers|Unknown escape|is not executable' \
        "$test_root/verify.txt" >/dev/null; then
        cat "$test_root/verify.txt" >&2
        fail 'systemd-analyze verify reported a path problem'
    fi
fi

# Paths with characters the generator cannot represent safely are refused.
for bad_value in 'bad"quote' 'bad\backslash' 'bad$variable'; do
    mkdir -p -- "$test_root/$bad_value/scripts"
    bad_env="$test_root/unrepresentable.env"
    {
        printf 'AGENT_EXCHANGE_SKILL_DIR=%s\n' "$test_root/$bad_value"
        printf 'AGENT_EXCHANGE_ROOT=%s\n' "$exchange_root"
        printf 'HERDR_BIN=%s\n' "$herdr_bin"
        printf 'HERDR_SESSION=agent-exchange\n'
    } >"$bad_env"
    if AGENT_EXCHANGE_ENV_FILE="$bad_env" bash "$INSTALLER" --dry-run >/dev/null 2>&1; then
        fail "unrepresentable skill dir was accepted: $bad_value"
    fi
done

# A '$' in the deployment env path cannot be represented safely in
# EnvironmentFile= and is refused.
dollar_env="$test_root/dollar\$dir/agent.env"
mkdir -p -- "$(dirname -- "$dollar_env")"
write_env "$dollar_env"
if AGENT_EXCHANGE_ENV_FILE="$dollar_env" bash "$INSTALLER" --dry-run >/dev/null 2>&1; then
    fail 'EnvironmentFile path with a dollar sign was accepted'
fi

printf 'ok - Agent Exchange systemd adapter tests passed\n'
