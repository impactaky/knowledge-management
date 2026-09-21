#!/usr/bin/env bash
# Generate the systemd user units for Agent Exchange from a deployment env
# file. The env file is resolved from an explicit --env-file or
# AGENT_EXCHANGE_ENV_FILE, then
# ${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/agent-exchange.env.
#
# Only the resolved absolute env-file path is embedded in the units. Skill
# directory, exchange directory, Herdr executable and session name stay in the
# env file and are read at service runtime, so the generated units contain no
# machine-specific deployment values and do not depend on the systemd user
# manager inheriting XDG variables.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_DIR="$SKILL_DIR/assets/systemd"

die() {
    printf 'agent-exchange install-systemd: %s\n' "$1" >&2
    exit 1
}

usage() {
    printf 'usage: %s [--env-file PATH] [--target-dir DIR] [--dry-run]\n' "${0##*/}" >&2
}

target_dir=''
env_file=''
dry_run=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            env_file="$2"
            shift 2
            ;;
        --target-dir)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            target_dir="$2"
            shift 2
            ;;
        --dry-run)
            dry_run=true
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

if [[ -z "$env_file" ]]; then
    if [[ -n "${AGENT_EXCHANGE_ENV_FILE:-}" ]]; then
        env_file="$AGENT_EXCHANGE_ENV_FILE"
    elif [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
        env_file="$XDG_CONFIG_HOME/knowledge-management/agent-exchange.env"
    else
        env_file="${HOME:?HOME must be set}/.config/knowledge-management/agent-exchange.env"
    fi
fi

[[ -n "$env_file" ]] || die 'no env file selected'
[[ -f "$env_file" && ! -L "$env_file" ]] || die "env file not found: $env_file"
env_file="$(realpath -e -- "$env_file")"

# Read systemd EnvironmentFile syntax without shell-evaluating the values.
# Unquoted values keep the rest of the line verbatim, including spaces, and one
# layer of surrounding quotes is stripped.
load_env_file() {
    local file="$1"
    local line key value first last
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ -z "$line" || "$line" == \#* ]] && continue
        [[ "$line" == *=* ]] || die "invalid env line in $file: $line"
        key="${line%%=*}"
        value="${line#*=}"
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "invalid env key in $file: $key"
        if [[ ${#value} -ge 2 ]]; then
            first="${value:0:1}"
            last="${value: -1}"
            if [[ ( "$first" == '"' && "$last" == '"' ) || ( "$first" == "'" && "$last" == "'" ) ]]; then
                value="${value:1:${#value}-2}"
            fi
        fi
        export "$key=$value"
    done <"$file"
}

load_env_file "$env_file"

require_value() {
    local name="$1"
    [[ -n "${!name:-}" ]] || die "required value $name is not set in $env_file"
}

require_value AGENT_EXCHANGE_SKILL_DIR
require_value AGENT_EXCHANGE_ROOT
require_value HERDR_BIN
require_value HERDR_SESSION

[[ "$AGENT_EXCHANGE_SKILL_DIR" == /* ]] || die 'AGENT_EXCHANGE_SKILL_DIR must be an absolute path'
[[ "$AGENT_EXCHANGE_ROOT" == /* ]] || die 'AGENT_EXCHANGE_ROOT must be an absolute path'
[[ "$HERDR_BIN" == /* ]] || die 'HERDR_BIN must be an absolute path'
[[ -d "$AGENT_EXCHANGE_SKILL_DIR" ]] || die "AGENT_EXCHANGE_SKILL_DIR does not exist: $AGENT_EXCHANGE_SKILL_DIR"
[[ -x "$HERDR_BIN" ]] || die "HERDR_BIN is not executable: $HERDR_BIN"

# systemd unit syntax handles these paths differently:
#   * EnvironmentFile= takes the rest of the line verbatim, so spaces are safe
#     unquoted, but unit specifiers are expanded. A literal '%' is doubled.
#   * ExecStart= uses the stable /bin/sh executable and reads
#     AGENT_EXCHANGE_SKILL_DIR from the deployment env at service runtime, so no
#     machine-specific skill path is embedded in the unit. A literal '$' cannot
#     be represented safely in the environment-file path, so it is rejected.
# The installer resolves paths itself; it never shell-evaluates env-file content.
reject_control_characters() {
    local name="$1"
    local value="$2"
    case "$value" in
        *$'\n'* | *$'\r'*)
            die "$name contains a newline or carriage return: $value"
            ;;
    esac
}

environment_file_value() {
    local value="$1"
    reject_control_characters EnvironmentFile "$value"
    case "$value" in
        *'"'* | *\\* | *'$'*)
            die "EnvironmentFile path cannot be represented safely in a systemd unit: $value"
            ;;
    esac
    case "$value" in
        [[:space:]]* | *[[:space:]])
            die "EnvironmentFile path must not start or end with whitespace: $value"
            ;;
    esac
    printf '%s\n' "${value//%/%%}"
}

if [[ -z "$target_dir" ]]; then
    if [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
        target_dir="$XDG_CONFIG_HOME/systemd/user"
    else
        target_dir="${HOME:?HOME must be set}/.config/systemd/user"
    fi
fi

escape_replacement() {
    printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'
}

env_value="$(environment_file_value "$env_file")" || exit 1
env_replacement="$(escape_replacement "$env_value")"

render_template() {
    local template="$1"
    [[ -f "$template" ]] || die "missing template: $template"
    sed -e "s|@ENVIRONMENT_FILE@|$env_replacement|g" "$template"
}

units=(agent-exchange-launcher.service herdr-agent-exchange.service)

if [[ "$dry_run" == true ]]; then
    for unit in "${units[@]}"; do
        printf '# file: %s\n' "$unit"
        render_template "$TEMPLATE_DIR/$unit"
    done
    exit 0
fi

mkdir -p -- "$target_dir"
for unit in "${units[@]}"; do
    render_template "$TEMPLATE_DIR/$unit" >"$target_dir/$unit"
    chmod 644 -- "$target_dir/$unit"
done
printf 'Wrote %s and %s to %s\n' "${units[0]}" "${units[1]}" "$target_dir"
printf 'Next: systemctl --user daemon-reload && systemctl --user enable --now %s\n' "${units[*]}"
