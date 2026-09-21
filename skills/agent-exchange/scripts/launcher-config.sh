#!/usr/bin/env bash
# Resolve and validate the Agent Exchange Launcher config, then print a JSON
# snapshot on stdout. The lookup order is an explicit AGENT_EXCHANGE_CONFIG,
# then ${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/agent-exchange.toml.
#
# The file is a small TOML subset:
#
#   [implementation]
#   kind = "<herdr kind>"
#   args = ["<native arg>", "..."]
#
# A missing file, an invalid file, an empty kind or a non-string args array is
# an explicit error before any agent starts. Unknown kinds are returned as-is;
# the resolver never substitutes a built-in kind or model.
set -Eeuo pipefail

die() {
    printf 'agent-exchange config: %s\n' "$1" >&2
    exit 1
}

strip_quotes() {
    # Sets REPLY to the unquoted content of a simple TOML string, or fails.
    local value="$1"
    if [[ "$value" == \"*\" && "$value" != *\\* ]]; then
        REPLY="${value:1:${#value}-2}"
        [[ "$REPLY" != *\"* ]] || return 1
        return 0
    fi
    return 1
}

if [[ -n "${AGENT_EXCHANGE_CONFIG:-}" ]]; then
    config_path="$AGENT_EXCHANGE_CONFIG"
elif [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
    config_path="$XDG_CONFIG_HOME/knowledge-management/agent-exchange.toml"
else
    config_path="${HOME:?HOME must be set}/.config/knowledge-management/agent-exchange.toml"
fi

if [[ ! -f "$config_path" || -L "$config_path" ]]; then
    die "config file not found: $config_path"
fi
config_path="$(realpath -e -- "$config_path")"

section=''
kind=''
declare -a args=()
implementation_seen=false
args_seen=false
line_number=0

while IFS= read -r raw || [[ -n "$raw" ]]; do
    line_number=$((line_number + 1))
    line="${raw%$'\r'}"
    trimmed="${line#"${line%%[![:space:]]*}"}"

    [[ -z "$trimmed" ]] && continue
    [[ "$trimmed" == \#* ]] && continue

    if [[ "$trimmed" =~ ^\[([A-Za-z0-9_.-]+)\]$ ]]; then
        section="${BASH_REMATCH[1]}"
        [[ "$section" == implementation ]] && implementation_seen=true
        continue
    fi

    if [[ ! "$trimmed" =~ ^([A-Za-z0-9_-]+)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
        die "invalid TOML at line $line_number: $raw"
    fi
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    [[ -n "$value" ]] || die "missing value at line $line_number: $raw"

    if [[ "$section" != implementation ]]; then
        # Other sections are parsed for TOML validity but not interpreted.
        if [[ "$value" == \[*\] ]]; then
            continue
        fi
        strip_quotes "$value" || die "invalid value at line $line_number: $raw"
        continue
    fi

    case "$key" in
        kind)
            strip_quotes "$value" || die "kind must be a string at line $line_number"
            kind="$REPLY"
            ;;
        args)
            [[ "$value" == \[*\] ]] || die "args must be an array of strings at line $line_number"
            inner="${value:1:${#value}-2}"
            args=()
            args_seen=true
            if [[ -n "${inner//[[:space:]]/}" ]]; then
                IFS=',' read -r -a parts <<<"$inner"
                for part in "${parts[@]}"; do
                    part="${part#"${part%%[![:space:]]*}"}"
                    part="${part%"${part##*[![:space:]]}"}"
                    strip_quotes "$part" || die "args must be an array of strings at line $line_number"
                    args+=("$REPLY")
                done
            fi
            ;;
        *)
            # Unknown keys must still be well-formed TOML.
            if [[ "$value" == \[*\] ]]; then
                continue
            fi
            strip_quotes "$value" || die "invalid value at line $line_number: $raw"
            ;;
    esac
done <"$config_path"

[[ "$implementation_seen" == true ]] || die "missing [implementation] section in $config_path"
[[ -n "$kind" ]] || die "implementation kind must be a non-empty string in $config_path"
[[ "$args_seen" == true ]] || die "missing implementation args array in $config_path"

args_json="$(jq -n --args '$ARGS.positional' -- "${args[@]}")" || die "could not encode args"
jq -n \
    --arg source config \
    --arg config_path "$config_path" \
    --arg kind "$kind" \
    --argjson args "$args_json" \
    '{source: $source, config_path: $config_path, kind: $kind, args: $args}'
