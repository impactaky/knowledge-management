#!/usr/bin/env bash
set -euo pipefail

ui_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$ui_dir/../../.." && pwd)
export UV_CACHE_DIR=${UV_CACHE_DIR:-"$repo_dir/.cache/uv"}

env_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      echo "Usage: run.sh [--env-file FILE]"
      echo ""
      echo "Start the Knowledge Browser UI with optional managed backend services."
      echo ""
      echo "Options:"
      echo "  --env-file FILE   Load environment variables from FILE via uv --env-file"
      echo '  Default: ${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/runtime.env (if present)'
      echo "  --help, -h        Show this help message and exit"
      exit 0
      ;;
    --env-file)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "Error: --env-file requires a file path argument" >&2
        exit 2
      fi
      env_file="$2"
      shift 2
      ;;
    --env-file=*)
      env_file="${1#*=}"
      if [[ -z "$env_file" ]]; then
        echo "Error: --env-file requires a file path argument" >&2
        exit 2
      fi
      shift
      ;;
    *)
      echo "Error: unrecognized argument: $1" >&2
      echo "Usage: run.sh [--env-file FILE]" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$env_file" ]]; then
  default_env_file="${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/runtime.env"
  if [[ -f "$default_env_file" ]]; then
    env_file="$default_env_file"
  fi
fi

uv_args=()
if [[ -n "$env_file" ]]; then
  # Resolve env_file relative to caller's cwd before changing directories
  if [[ ! "$env_file" = /* ]]; then
    env_file="$PWD/$env_file"
  fi
  if [[ ! -f "$env_file" ]]; then
    echo "Error: env file not found: $env_file" >&2
    exit 1
  fi
  # uv 0.12.x CLI splits --env-file on literal whitespace and unescapes backslashes.
  # Escape original backslashes first, then spaces, tabs, CR, and LF:
  uv_env_file="${env_file//\\/\\\\}"
  uv_env_file="${uv_env_file// /\\ }"
  uv_env_file="${uv_env_file//$'\t'/\\$'\t'}"
  uv_env_file="${uv_env_file//$'\r'/\\$'\r'}"
  uv_env_file="${uv_env_file//$'\n'/\\$'\n'}"
  uv_args+=(--env-file "$uv_env_file")
fi

cd "$ui_dir"
exec uv run --locked --project "$ui_dir" "${uv_args[@]}" python launch.py
