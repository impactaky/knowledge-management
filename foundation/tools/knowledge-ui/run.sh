#!/usr/bin/env bash
set -euo pipefail
ui_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$ui_dir/../../.." && pwd)
export UV_CACHE_DIR=${UV_CACHE_DIR:-"$repo_dir/.cache/uv"}

cd "$ui_dir"
exec uv run --locked --project "$ui_dir" uvicorn server:app --host "${UI_HOST:-127.0.0.1}" --port "${UI_PORT:-7776}"
