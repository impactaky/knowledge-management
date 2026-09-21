#!/usr/bin/env bash
set -Eeuo pipefail

{
    printf '%q ' "$@"
    printf '\n'
} >>"$FAKE_HERDR_LOG"

case "$1 $2" in
    'api snapshot')
        printf '{"result":{}}\n'
        ;;
    'workspace list')
        jq -n --arg repository "$FAKE_REPOSITORY" '{
            result: {
                workspaces: [{
                    workspace_id: "w-parent",
                    worktree: {
                        repo_root: $repository,
                        checkout_path: $repository,
                        is_linked_worktree: false
                    }
                }]
            }
        }'
        ;;
    'workspace create')
        printf '{"result":{"workspace":{"workspace_id":"w-parent"}}}\n'
        ;;
    'worktree create')
        jq -n --arg workspace "$FAKE_WORKSPACE" --arg path "$FAKE_WORKTREE" '{
            result: {
                workspace: {workspace_id: $workspace},
                root_pane: {pane_id: ($workspace + ":p0")},
                worktree: {path: $path}
            }
        }'
        ;;
    'workspace get')
        [[ ! -e "$FAKE_HERDR_STATE/missing-workspace" ]] || exit 1
        jq -n \
            --arg workspace "$3" \
            --arg path "$FAKE_WORKTREE" \
            --arg repository "$FAKE_REPOSITORY" '{
                result: {
                    workspace: {
                        workspace_id: $workspace,
                        worktree: {
                            checkout_path: $path,
                            repo_root: $repository,
                            is_linked_worktree: true
                        }
                    }
                }
            }'
        ;;
    'agent start')
        printf '%s\n' "$3" >"$FAKE_HERDR_STATE/agent"
        printf '{"result":{}}\n'
        ;;
    'agent get')
        [[ ! -e "$FAKE_HERDR_STATE/missing-agent" ]] || exit 1
        jq -n \
            --arg name "$3" \
            --arg workspace "$FAKE_WORKSPACE" \
            --arg cwd "$FAKE_WORKTREE" '{
                result: {
                    agent: {
                        name: $name,
                        workspace_id: $workspace,
                        cwd: $cwd,
                        interactive_ready: true
                    }
                }
            }'
        ;;
    'agent prompt')
        [[ ! -e "$FAKE_HERDR_STATE/fail-prompt" ]] || exit 1
        printf '{"result":{}}\n'
        ;;
    *)
        printf 'unexpected fake Herdr invocation: %s\n' "$*" >&2
        exit 1
        ;;
esac
