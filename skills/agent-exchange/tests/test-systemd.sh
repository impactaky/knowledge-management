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

# The units keep the skill directory out of ExecStart and read it from the
# deployment env at service runtime through the stable /bin/sh executable.
launcher_exec='ExecStart=/bin/sh -c '"'"'exec "$${AGENT_EXCHANGE_SKILL_DIR:?AGENT_EXCHANGE_SKILL_DIR must be set}/scripts/launcher.sh"'"'"''
herdr_exec='ExecStart=/bin/sh -c '"'"'exec "$${AGENT_EXCHANGE_SKILL_DIR:?AGENT_EXCHANGE_SKILL_DIR must be set}/scripts/herdr-server.sh"'"'"''

write_scripts() {
    local directory="$1"
    mkdir -p "$directory/scripts"
    for script in launcher.sh herdr-server.sh; do
        printf '#!/bin/sh\nexit 0\n' >"$directory/scripts/$script"
        chmod +x "$directory/scripts/$script"
    done
}

skill_dir="$test_root/skill dir"
write_scripts "$skill_dir"
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
# verbatim, so spaces survive), and ExecStart= never embeds the skill path.
AGENT_EXCHANGE_ENV_FILE='' XDG_CONFIG_HOME="$xdg_home" HOME="$test_root/homedir" \
    bash "$INSTALLER" --dry-run >"$test_root/dry-run.txt" 2>"$test_root/err"
assert_contains "$test_root/dry-run.txt" "EnvironmentFile=$(realpath -e -- "$xdg_env")"
assert_absent "$test_root/dry-run.txt" "EnvironmentFile=\""
assert_contains "$test_root/dry-run.txt" "$launcher_exec"
assert_contains "$test_root/dry-run.txt" "$herdr_exec"
# Deployment values stay in the env file, not in the unit.
assert_absent "$test_root/dry-run.txt" "$skill_dir"
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

# Writing units embeds the resolved absolute EnvironmentFile unquoted and no
# machine-specific skill path.
target="$test_root/units"
AGENT_EXCHANGE_ENV_FILE='' XDG_CONFIG_HOME="$xdg_home" HOME="$test_root/homedir" \
    bash "$INSTALLER" --target-dir "$target" >"$test_root/write.txt" 2>"$test_root/err"
assert_contains "$target/agent-exchange-launcher.service" "EnvironmentFile=$(realpath -e -- "$xdg_env")"
assert_contains "$target/herdr-agent-exchange.service" "EnvironmentFile=$(realpath -e -- "$xdg_env")"
assert_contains "$target/agent-exchange-launcher.service" "$launcher_exec"
assert_contains "$target/herdr-agent-exchange.service" "$herdr_exec"
assert_absent "$target/agent-exchange-launcher.service" "$skill_dir"
assert_absent "$target/herdr-agent-exchange.service" "$skill_dir"
assert_absent "$target/agent-exchange-launcher.service" '@'
assert_absent "$target/herdr-agent-exchange.service" '@'

# A literal '%' is doubled in the EnvironmentFile path so systemd does not
# treat it as a specifier, and spaces stay part of the path.
percent_dir="$test_root/env %dir"
percent_env="$percent_dir/agent.env"
write_env "$percent_env"
percent_target="$test_root/percent-units"
AGENT_EXCHANGE_ENV_FILE="$percent_env" bash "$INSTALLER" --target-dir "$percent_target" >/dev/null 2>"$test_root/err"
assert_contains "$percent_target/agent-exchange-launcher.service" "EnvironmentFile=${percent_env//%/%%}"

# A skill directory containing spaces, '$' and '%' is safe because it is never
# embedded; it is read from the env at service runtime.
special_skill="$test_root/skill \$pct% dir"
write_scripts "$special_skill"
special_env="$test_root/special.env"
{
    printf 'AGENT_EXCHANGE_SKILL_DIR=%s\n' "$special_skill"
    printf 'AGENT_EXCHANGE_ROOT=%s\n' "$exchange_root"
    printf 'HERDR_BIN=%s\n' "$herdr_bin"
    printf 'HERDR_SESSION=agent-exchange\n'
} >"$special_env"
special_target="$test_root/special-units"
AGENT_EXCHANGE_ENV_FILE="$special_env" bash "$INSTALLER" --target-dir "$special_target" >/dev/null 2>"$test_root/err"
assert_contains "$special_target/agent-exchange-launcher.service" "$launcher_exec"
assert_absent "$special_target/agent-exchange-launcher.service" "$special_skill"

# systemd-analyze (when present) must accept every generated unit: no ExecStart
# truncation at a space, no EnvironmentFile quote or specifier warnings, no
# missing skill path.
if command -v systemd-analyze >/dev/null 2>&1; then
    for units in "$target" "$percent_target" "$special_target"; do
        if ! systemd-analyze verify --man=no \
            "$units/agent-exchange-launcher.service" \
            "$units/herdr-agent-exchange.service" >"$test_root/verify.txt" 2>&1; then
            cat "$test_root/verify.txt" >&2
            fail "systemd-analyze verify rejected units in $units"
        fi
        if grep -E 'path is not absolute|Failed to resolve unit specifiers|Unknown escape|is not executable' \
            "$test_root/verify.txt" >/dev/null; then
            cat "$test_root/verify.txt" >&2
            fail "systemd-analyze verify reported a path problem in $units"
        fi
    done
fi

# A '$' in the deployment env path cannot be represented safely in
# EnvironmentFile= and is refused.
dollar_env="$test_root/dollar\$dir/agent.env"
mkdir -p -- "$(dirname -- "$dollar_env")"
write_env "$dollar_env"
if AGENT_EXCHANGE_ENV_FILE="$dollar_env" bash "$INSTALLER" --dry-run >/dev/null 2>&1; then
    fail 'EnvironmentFile path with a dollar sign was accepted'
fi

printf 'ok - Agent Exchange systemd adapter tests passed\n'
