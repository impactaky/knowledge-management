#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

command -v jq >/dev/null 2>&1 || {
    printf 'jq is required\n' >&2
    exit 1
}
command -v inotifywait >/dev/null 2>&1 || {
    printf 'inotifywait is required\n' >&2
    exit 1
}
command -v flock >/dev/null 2>&1 || {
    printf 'flock is required\n' >&2
    exit 1
}
command -v python3 >/dev/null 2>&1 || {
    printf 'python3 is required\n' >&2
    exit 1
}

herdr_bin="$(agent_exchange_resolve_herdr)" || {
    printf 'herdr executable not found; set HERDR_BIN\n' >&2
    exit 1
}
export HERDR_SESSION="${HERDR_SESSION:-agent-exchange}"

root="$(agent_exchange_root)"
agent_exchange_ensure_layout "$root"
exec 9>"$root/launcher.lock"
flock -n 9 || {
    printf 'another Agent Exchange Launcher is already running\n' >&2
    exit 1
}

# Resolve the Launcher implementation once at startup. A missing, invalid or
# ambiguous config must stop the Launcher before any agent is started; it must
# never fall back to another kind or model.
launcher_config="$(python3 "$SCRIPT_DIR/launcher-config.py")" || exit 1
agent_kind="$(jq -r '.kind' <<<"$launcher_config")"
mapfile -t agent_user_args < <(jq -r '.args[]?' <<<"$launcher_config")

herdr() {
    "$herdr_bin" "$@"
}

# Reject permission-bypass and conflicting safety arguments before any agent is
# started, so a misconfigured kind cannot silently weaken its sandbox.
validate_user_args() {
    local arg
    for arg in "${agent_user_args[@]}"; do
        case "$arg" in
            --dangerously-skip-permissions)
                printf 'refusing permission-bypass argument for kind %s: %s\n' "$agent_kind" "$arg" >&2
                return 1
                ;;
        esac
    done
    case "$agent_kind" in
        agy)
            for arg in "${agent_user_args[@]}"; do
                case "$arg" in
                    --mode | --mode=*)
                        printf 'refusing conflicting --mode for agy; accept-edits is required\n' >&2
                        return 1
                        ;;
                esac
            done
            ;;
        opencode)
            for arg in "${agent_user_args[@]}"; do
                case "$arg" in
                    --auto)
                        printf 'refusing permission-bypass argument for kind opencode: --auto\n' >&2
                        return 1
                        ;;
                esac
            done
            ;;
    esac
    return 0
}

validate_user_args || exit 1

build_agent_args() {
    # Known kinds keep their required exchange-directory access and sandbox
    # arguments, appending after the user args to preserve their order. Unknown
    # kinds are passed straight to Herdr without guessing kind-specific
    # preparation.
    case "$agent_kind" in
        codex)
            agent_final_args=("${agent_user_args[@]}" --add-dir "$root")
            ;;
        agy)
            agent_final_args=("${agent_user_args[@]}" --add-dir "$root" --mode accept-edits --sandbox)
            ;;
        *)
            agent_final_args=("${agent_user_args[@]}")
            ;;
    esac
}

prepare_opencode_pane() {
    local pane_id="$1"
    local base="${OPENCODE_CONFIG_CONTENT:-}"
    local merged export_cmd

    if [[ -n "$base" ]]; then
        printf '%s' "$base" | jq -e . >/dev/null 2>&1 || {
            printf 'OPENCODE_CONFIG_CONTENT is not valid JSON\n' >&2
            return 1
        }
        merged="$(jq -c -n --argjson base "$base" --arg root "$root" '
            $base
            | .permission = (.permission // {})
            | .permission.external_directory = (.permission.external_directory // {})
            | .permission.external_directory[$root] = "allow"
            | .permission.external_directory[$root + "/**"] = "allow"
        ')" || return 1
    else
        merged="$(jq -c -n --arg root "$root" '{
            permission: {external_directory: {($root): "allow", ($root + "/**"): "allow"}}
        }')" || return 1
    fi

    printf -v export_cmd 'export OPENCODE_CONFIG_CONTENT=%q' "$merged"
    herdr pane run "$pane_id" "$export_cmd" >/dev/null 2>&1
}

prepare_pane() {
    local pane_id="$1"
    case "$agent_kind" in
        opencode)
            prepare_opencode_pane "$pane_id"
            ;;
    esac
}

start_agent() {
    local agent_name="$1"
    local pane_id="$2"

    build_agent_args
    if [[ ${#agent_final_args[@]} -gt 0 ]]; then
        herdr agent start "$agent_name" --kind "$agent_kind" --pane "$pane_id" -- "${agent_final_args[@]}" 2>&1
    else
        herdr agent start "$agent_name" --kind "$agent_kind" --pane "$pane_id" 2>&1
    fi
}

server_ready() {
    herdr api snapshot >/dev/null 2>&1
}

failure_response() {
    local id="$1"
    local stage="$2"
    local detail="$3"

    detail="${detail//$'\n'/ }"
    detail="${detail:0:1200}"
    printf "# Agent Exchange failure\n\n- Stage: \`%s\`\n- Detail: %s\n" "$stage" "$detail" |
        "$SCRIPT_DIR/respond.sh" "$id" || true
}

thread_failure_response() {
    local id="$1"
    local code="$2"
    local thread_id="$3"
    local detail="$4"
    local thread_path="$root/threads/$thread_id"
    local worktree='' last_commit=''

    detail="${detail//$'\n'/ }"
    detail="${detail:0:1200}"
    if [[ -d "$thread_path/binding" && ! -L "$thread_path/binding" ]]; then
        worktree="$(agent_exchange_read_line "$thread_path/binding/worktree" 2>/dev/null || true)"
    fi
    if [[ "$worktree" == /* && -d "$worktree" ]]; then
        last_commit="$(git -C "$worktree" rev-parse HEAD 2>/dev/null || true)"
    fi

    {
        printf '# Agent Exchange thread failure\n\n'
        printf -- '- Code: %s\n' "$code"
        printf -- '- Thread ID: %s\n' "$thread_id"
        printf -- '- Detail: %s\n' "$detail"
        if [[ -n "$worktree" ]]; then
            printf -- '- Worktree: %s\n' "$worktree"
        fi
        if [[ -n "$last_commit" ]]; then
            printf -- '- Last commit: %s\n' "$last_commit"
        fi
        printf -- '- Recovery: No automatic fresh fallback was attempted. Publish a self-contained ordinary Request explicitly, using the last reviewed worktree and commit when available.\n'
    } | "$SCRIPT_DIR/respond.sh" "$id" || true
}

find_parent_workspace() {
    local repository="$1"
    local json

    json="$(herdr workspace list 2>&1)" || return 1
    jq -r --arg repository "$repository" '
        first(
            .result.workspaces[]?
            | select(.worktree.repo_root == $repository)
            | select(.worktree.checkout_path == $repository)
            | select(.worktree.is_linked_worktree == false)
            | .workspace_id
        ) // empty
    ' <<<"$json"
}

validate_initial_thread() {
    local id="$1"
    local running_dir="$2"
    local thread_id="$3"
    local reservation thread_path repository initial active

    reservation="$(agent_exchange_read_line "$running_dir/reservation" 2>/dev/null || true)"
    thread_path="$root/threads/$thread_id"
    if [[ "$reservation" != active ]]; then
        thread_failure_response "$id" thread-invalid "$thread_id" 'new-thread bundle does not hold the active reservation'
        return 1
    fi
    if [[ ! -d "$thread_path" || -L "$thread_path" ]]; then
        thread_failure_response "$id" thread-missing "$thread_id" 'durable Thread metadata directory is missing or unsafe'
        return 1
    fi
    repository="$(agent_exchange_read_line "$thread_path/repository" 2>/dev/null || true)"
    initial="$(agent_exchange_read_line "$thread_path/initial-request" 2>/dev/null || true)"
    active="$(agent_exchange_read_line "$thread_path/active" 2>/dev/null || true)"
    if [[ "$repository" != "$(agent_exchange_read_line "$running_dir/repository" 2>/dev/null || true)" ||
        "$initial" != "$id" || "$active" != "$id" ||
        -e "$thread_path/binding" || -e "$thread_path/ready" ]]; then
        thread_failure_response "$id" thread-invalid "$thread_id" 'initial Thread metadata is inconsistent with its Request bundle'
        return 1
    fi
}

publish_thread_binding() {
    local thread_id="$1"
    local worktree_path="$2"
    local worktree_workspace="$3"
    local agent_name="$4"
    local common_repository="$5"
    local thread_path="$root/threads/$thread_id"
    local binding_staging

    binding_staging="$(mktemp -d --tmpdir="$thread_path" '.binding.XXXXXX.tmp')"
    chmod 700 -- "$binding_staging"
    agent_exchange_write_line "$binding_staging/worktree" "$worktree_path"
    agent_exchange_write_line "$binding_staging/workspace" "$worktree_workspace"
    agent_exchange_write_line "$binding_staging/agent" "$agent_name"
    agent_exchange_write_line "$binding_staging/common-repository" "$common_repository"
    [[ ! -e "$thread_path/binding" ]] || {
        rm -rf -- "$binding_staging"
        return 1
    }
    mv -T -- "$binding_staging" "$thread_path/binding"
}

process_fresh_request() {
    local id="$1"
    local running_dir="$2"
    local thread_id="${3:-}"
    local repository repository_root primary_worktree_line common_repository base_commit
    local parent_workspace parent_json
    local worktree_json worktree_workspace pane_id worktree_path
    local compact_id agent_name agent_output prepare_output prompt_output prompt respond_command
    local agent_started=false agent_start_attempt

    if [[ ! -s "$running_dir/request.md" || -L "$running_dir/request.md" ||
        ! -f "$running_dir/repository" || -L "$running_dir/repository" ]]; then
        failure_response "$id" validate 'request bundle must contain regular non-empty request.md and repository files'
        return
    fi
    repository="$(agent_exchange_read_line "$running_dir/repository" 2>/dev/null || true)"
    if [[ "$repository" != /* ]]; then
        failure_response "$id" validate 'repository must contain exactly one absolute path'
        return
    fi
    repository="$(realpath -e -- "$repository" 2>/dev/null)" || {
        failure_response "$id" validate 'repository path does not exist on the Launcher host'
        return
    }
    repository_root="$(git -C "$repository" rev-parse --show-toplevel 2>/dev/null)" || {
        failure_response "$id" validate 'repository path is not a Git worktree'
        return
    }
    repository_root="$(realpath -e -- "$repository_root")"
    if [[ "$repository_root" != "$repository" ]]; then
        failure_response "$id" validate 'repository path must be the Git repository root'
        return
    fi

    IFS= read -r primary_worktree_line < <(git -C "$repository" worktree list --porcelain 2>/dev/null | sed -n '1p') || {
        failure_response "$id" validate 'could not resolve the primary Git worktree'
        return
    }
    if [[ "$primary_worktree_line" != 'worktree '* ]]; then
        failure_response "$id" validate 'Git did not report a primary worktree'
        return
    fi
    common_repository="$(realpath -e -- "${primary_worktree_line#worktree }")"
    base_commit="$(git -C "$repository" rev-parse HEAD 2>/dev/null)" || {
        failure_response "$id" validate 'repository HEAD does not resolve to a commit'
        return
    }

    parent_workspace="$(find_parent_workspace "$common_repository")" || {
        failure_response "$id" workspace-list 'could not list Herdr workspaces'
        return
    }
    if [[ -z "$parent_workspace" ]]; then
        parent_json="$(herdr workspace create --cwd "$common_repository" --label "${common_repository##*/}" --no-focus 2>&1)" || {
            failure_response "$id" workspace-create "$parent_json"
            return
        }
        parent_workspace="$(jq -r '.result.workspace.workspace_id // empty' <<<"$parent_json")"
        if [[ -z "$parent_workspace" ]]; then
            failure_response "$id" workspace-create 'Herdr response did not contain workspace_id'
            return
        fi
    fi

    worktree_json="$(herdr worktree create \
        --workspace "$parent_workspace" \
        --branch "agent-exchange/$id" \
        --base "$base_commit" \
        --label "${common_repository##*/}:${id:0:8}" \
        --no-focus 2>&1)" || {
        failure_response "$id" worktree-create "$worktree_json"
        return
    }
    worktree_workspace="$(jq -r '.result.workspace.workspace_id // empty' <<<"$worktree_json")"
    pane_id="$(jq -r '.result.root_pane.pane_id // empty' <<<"$worktree_json")"
    worktree_path="$(jq -r '.result.worktree.path // empty' <<<"$worktree_json")"
    if [[ -z "$worktree_workspace" || -z "$pane_id" || "$worktree_path" != /* ]]; then
        failure_response "$id" worktree-create 'Herdr response omitted workspace, pane, or absolute worktree path'
        return
    fi
    agent_exchange_write_line "$running_dir/worktree" "$worktree_path"
    agent_exchange_write_line "$running_dir/workspace" "$worktree_workspace"

    compact_id="${id//-/}"
    agent_name="ae-${compact_id:0:28}"

    # Known kinds may need session-only pane preparation before the agent starts
    # (for example the Exchange Directory permission for opencode).
    if ! prepare_output="$(prepare_pane "$pane_id" 2>&1)"; then
        failure_response "$id" pane-prepare "${prepare_output:-could not prepare the agent pane}"
        return
    fi

    # Worktree integrations may briefly run setup in the new root pane after
    # worktree creation returns. Retry only that pre-start busy race; other
    # start failures remain terminal for this Request.
    for ((agent_start_attempt = 1; agent_start_attempt <= 60; agent_start_attempt++)); do
        if agent_output="$(start_agent "$agent_name" "$pane_id")"; then
            agent_started=true
            break
        fi
        if [[ "$agent_output" != *'"code":"agent_pane_busy"'* ]]; then
            break
        fi
        sleep 1
    done
    if [[ "$agent_started" != true ]]; then
        failure_response "$id" agent-start "$agent_output"
        return
    fi
    agent_exchange_write_line "$running_dir/agent" "$agent_name"

    if [[ -n "$thread_id" ]] &&
        ! publish_thread_binding "$thread_id" "$worktree_path" "$worktree_workspace" "$agent_name" "$common_repository"; then
        thread_failure_response "$id" thread-binding-failed "$thread_id" 'could not atomically publish the Thread resource binding'
        return
    fi

    printf -v respond_command '%q %q' "$SCRIPT_DIR/respond.sh" "$id"
    if [[ -n "$thread_id" ]]; then
        printf -v prompt '%s\n' \
            "You are the fresh Responding Agent for the initial Request $id in Agent Exchange Thread $thread_id." \
            "Work in the current worktree. Read $running_dir/request.md and complete the entire Request autonomously." \
            "Continue until implementation and verification are finished, inspect the final diff, and make coherent reviewable commits when the Request changes a repository." \
            "This conversation, Herdr workspace, and Git worktree are retained for explicit Continuation Requests in this Thread." \
            "Your only completion signal for this Request is one final Response. It must include the absolute worktree path and summarize changes, commits, verification, and any deviations or blockers." \
            "Publish it exactly once by piping Markdown on stdin to: $respond_command" \
            "If you cannot continue, publish the reason as the final Response. Do not publish progress or questions."
    else
        printf -v prompt '%s\n' \
            "You are the fresh Responding Agent for Agent Exchange Request $id." \
            "Work in the current worktree. Read $running_dir/request.md and complete the entire Request autonomously." \
            "Continue until implementation and verification are finished, inspect the final diff, and make coherent reviewable commits when the Request changes a repository." \
            "Your only completion signal is one final Response. It must include the absolute worktree path and summarize changes, commits, verification, and any deviations or blockers." \
            "Publish it exactly once by piping Markdown on stdin to: $respond_command" \
            "If you cannot continue, publish the reason as the final Response. Do not publish progress or questions."
    fi

    prompt_output="$(herdr agent prompt "$agent_name" "$prompt" 2>&1)" || {
        if [[ -n "$thread_id" ]]; then
            thread_failure_response "$id" thread-prompt-failed "$thread_id" "$prompt_output"
        else
            failure_response "$id" prompt-submit "$prompt_output"
        fi
        return
    }
    if [[ -n "$thread_id" ]]; then
        agent_exchange_write_line "$root/threads/$thread_id/ready" ready
    fi
}

process_continuation() {
    local id="$1"
    local running_dir="$2"
    local thread_id="$3"
    local reservation thread_path active repository initial ready
    local binding worktree_path worktree_real workspace agent common_real
    local repository_root primary_worktree_line
    local workspace_json workspace_id workspace_path workspace_repo workspace_linked
    local agent_json agent_name agent_workspace agent_cwd agent_ready
    local prompt prompt_output respond_command

    reservation="$(agent_exchange_read_line "$running_dir/reservation" 2>/dev/null || true)"
    thread_path="$root/threads/$thread_id"
    if [[ "$reservation" == busy ]]; then
        active="$(agent_exchange_read_line "$thread_path/active" 2>/dev/null || true)"
        thread_failure_response "$id" thread-busy "$thread_id" "another Request still owns this Thread: ${active:-unknown}"
        return
    fi
    if [[ ! -d "$thread_path" || -L "$thread_path" ]]; then
        thread_failure_response "$id" thread-missing "$thread_id" 'Thread metadata is missing; it was never created or is no longer safely accessible'
        return
    fi
    if [[ "$reservation" != active ]]; then
        thread_failure_response "$id" thread-invalid "$thread_id" 'Continuation publication could not reserve valid Thread metadata'
        return
    fi

    active="$(agent_exchange_read_line "$thread_path/active" 2>/dev/null || true)"
    repository="$(agent_exchange_read_line "$thread_path/repository" 2>/dev/null || true)"
    initial="$(agent_exchange_read_line "$thread_path/initial-request" 2>/dev/null || true)"
    ready="$(agent_exchange_read_line "$thread_path/ready" 2>/dev/null || true)"
    if [[ "$active" != "$id" ]]; then
        thread_failure_response "$id" thread-active-mismatch "$thread_id" 'active Request changed or does not match this Continuation Request'
        return
    fi
    if [[ "$repository" != /* ]] || ! agent_exchange_validate_id "$initial"; then
        thread_failure_response "$id" thread-invalid "$thread_id" 'Thread identity or repository metadata is malformed'
        return
    fi
    if [[ "$ready" != ready ]]; then
        thread_failure_response "$id" thread-not-ready "$thread_id" 'initial Request never completed prompt delivery for this Thread'
        return
    fi

    binding="$thread_path/binding"
    if [[ ! -d "$binding" || -L "$binding" ]]; then
        thread_failure_response "$id" thread-binding-missing "$thread_id" 'durable resource binding is missing'
        return
    fi
    worktree_path="$(agent_exchange_read_line "$binding/worktree" 2>/dev/null || true)"
    workspace="$(agent_exchange_read_line "$binding/workspace" 2>/dev/null || true)"
    agent="$(agent_exchange_read_line "$binding/agent" 2>/dev/null || true)"
    local common_repository="$(agent_exchange_read_line "$binding/common-repository" 2>/dev/null || true)"
    if [[ "$worktree_path" != /* || -z "$workspace" || -z "$agent" || "$common_repository" != /* ]]; then
        thread_failure_response "$id" thread-binding-invalid "$thread_id" 'resource binding contains malformed or multiline metadata'
        return
    fi

    worktree_real="$(realpath -e -- "$worktree_path" 2>/dev/null)" || {
        thread_failure_response "$id" thread-worktree-missing "$thread_id" 'bound Git worktree path no longer exists'
        return
    }
    common_real="$(realpath -e -- "$common_repository" 2>/dev/null)" || {
        thread_failure_response "$id" thread-worktree-mismatch "$thread_id" 'bound common repository path no longer exists'
        return
    }
    repository_root="$(git -C "$worktree_real" rev-parse --show-toplevel 2>/dev/null)" || {
        thread_failure_response "$id" thread-worktree-mismatch "$thread_id" 'bound path is no longer a Git worktree'
        return
    }
    repository_root="$(realpath -e -- "$repository_root")"
    IFS= read -r primary_worktree_line < <(git -C "$worktree_real" worktree list --porcelain 2>/dev/null | sed -n '1p') || true
    if [[ "$repository_root" != "$worktree_real" || "$primary_worktree_line" != 'worktree '* ||
        "$(realpath -e -- "${primary_worktree_line#worktree }" 2>/dev/null || true)" != "$common_real" ]]; then
        thread_failure_response "$id" thread-worktree-mismatch "$thread_id" 'bound worktree does not belong to the recorded common repository'
        return
    fi

    workspace_json="$(herdr workspace get "$workspace" 2>&1)" || {
        thread_failure_response "$id" thread-workspace-missing "$thread_id" "$workspace_json"
        return
    }
    workspace_id="$(jq -r '.result.workspace.workspace_id // empty' <<<"$workspace_json")"
    workspace_path="$(jq -r '.result.workspace.worktree.checkout_path // empty' <<<"$workspace_json")"
    workspace_repo="$(jq -r '.result.workspace.worktree.repo_root // empty' <<<"$workspace_json")"
    workspace_linked="$(jq -r '.result.workspace.worktree.is_linked_worktree // false' <<<"$workspace_json")"
    if [[ "$workspace_id" != "$workspace" || "$workspace_linked" != true ||
        "$(realpath -e -- "$workspace_path" 2>/dev/null || true)" != "$worktree_real" ||
        "$(realpath -e -- "$workspace_repo" 2>/dev/null || true)" != "$common_real" ]]; then
        thread_failure_response "$id" thread-workspace-mismatch "$thread_id" 'Herdr workspace does not match the bound linked Git worktree'
        return
    fi

    agent_json="$(herdr agent get "$agent" 2>&1)" || {
        thread_failure_response "$id" thread-agent-missing "$thread_id" "$agent_json"
        return
    }
    agent_name="$(jq -r '.result.agent.name // empty' <<<"$agent_json")"
    agent_workspace="$(jq -r '.result.agent.workspace_id // empty' <<<"$agent_json")"
    agent_cwd="$(jq -r '.result.agent.cwd // empty' <<<"$agent_json")"
    agent_ready="$(jq -r '.result.agent.interactive_ready // false' <<<"$agent_json")"
    if [[ "$agent_name" != "$agent" || "$agent_workspace" != "$workspace" ||
        "$agent_ready" != true ||
        "$(realpath -e -- "$agent_cwd" 2>/dev/null || true)" != "$worktree_real" ]]; then
        thread_failure_response "$id" thread-agent-mismatch "$thread_id" 'Herdr agent is not the interactive agent bound to this Thread workspace'
        return
    fi

    agent_exchange_write_line "$running_dir/worktree" "$worktree_real"
    agent_exchange_write_line "$running_dir/workspace" "$workspace"
    agent_exchange_write_line "$running_dir/agent" "$agent"

    printf -v respond_command '%q %q' "$SCRIPT_DIR/respond.sh" "$id"
    printf -v prompt '%s\n' \
        "Continue the existing Responding Agent conversation for Agent Exchange Thread $thread_id with Continuation Request $id." \
        "Remain in the current worktree. Read $running_dir/request.md and treat it as the next delta request in this Thread." \
        "Continue until implementation and verification are finished, inspect the final diff, and make coherent reviewable commits when it changes the repository." \
        "This Request still has exactly one final Response; prior Responses are not progress channels and must not be republished." \
        "The Response must include the absolute worktree path and summarize changes, commits, verification, and any deviations or blockers." \
        "Publish it exactly once by piping Markdown on stdin to: $respond_command" \
        "If you cannot continue, publish the reason as the final Response. Do not publish progress or questions."

    prompt_output="$(herdr agent prompt "$agent" "$prompt" 2>&1)" || {
        thread_failure_response "$id" thread-prompt-failed "$thread_id" "$prompt_output"
        return
    }
}

process_request() {
    local request_dir="$1"
    local id="${request_dir##*/}"
    local running_dir="$root/running/$id"
    local mode=fresh thread_id

    if ! agent_exchange_validate_id "$id"; then
        printf 'ignoring request with invalid ID: %s\n' "$id" >&2
        return
    fi
    server_ready || return
    [[ ! -e "$running_dir" ]] || {
        printf 'running path already exists for request: %s\n' "$id" >&2
        return
    }

    mv -T -- "$request_dir" "$running_dir" || return
    if [[ -e "$running_dir/mode" ]]; then
        mode="$(agent_exchange_read_line "$running_dir/mode" 2>/dev/null || true)"
    fi
    case "$mode" in
        fresh)
            process_fresh_request "$id" "$running_dir"
            ;;
        new-thread | continue)
            thread_id="$(agent_exchange_read_line "$running_dir/thread" 2>/dev/null || true)"
            if ! agent_exchange_validate_id "$thread_id"; then
                failure_response "$id" validate 'thread must contain exactly one UUID v4 without symlink indirection'
                return
            fi
            if [[ "$mode" == new-thread ]]; then
                validate_initial_thread "$id" "$running_dir" "$thread_id" || return
                process_fresh_request "$id" "$running_dir" "$thread_id"
            else
                if [[ ! -s "$running_dir/request.md" || -L "$running_dir/request.md" ]]; then
                    thread_failure_response "$id" thread-invalid "$thread_id" 'Continuation bundle must contain a regular non-empty request.md'
                    return
                fi
                process_continuation "$id" "$running_dir" "$thread_id"
            fi
            ;;
        *)
            failure_response "$id" validate 'mode must be absent, new-thread, or continue'
            ;;
    esac
}

scan_requests() {
    local request_dir
    while IFS= read -r -d '' request_dir; do
        process_request "$request_dir"
    done < <(find "$root/requests" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)
}

scan_requests
if [[ "${AGENT_EXCHANGE_RUN_ONCE:-}" == 1 ]]; then
    exit
fi
while true; do
    inotifywait -q -t 60 -e moved_to -- "$root/requests" >/dev/null 2>&1 || true
    scan_requests
done
