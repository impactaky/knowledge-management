# Agent Exchange deployment

This reference covers the Launcher config, the systemd deployment env, and the
required commands. It is optional infrastructure around the exchange protocol
defined in [SKILL.md](../SKILL.md); the protocol does not depend on systemd.

## Two different files

- **Launcher config** (`agent-exchange.toml`): which Agent implementation the
  Launcher starts, as `[implementation] kind` and `args`. It is real TOML parsed
  with Python's standard-library `tomllib`. Selected with `AGENT_EXCHANGE_CONFIG`,
  then `${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/agent-exchange.toml`.
  A relative `XDG_CONFIG_HOME` or `HOME` fallback is rejected, not resolved
  against the current directory. See [../config.example.toml](../config.example.toml).
- **Deployment env** (`agent-exchange.env`): where the skill lives and how the
  responder reaches Herdr. Read only at install time by the systemd adapter.
  Selected with `AGENT_EXCHANGE_ENV_FILE`, then
  `${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/agent-exchange.env`.

Both are user configuration. Keep them outside this repository.

## Deployment env values

The env file uses systemd `EnvironmentFile` syntax (`KEY=value`, `#` comments)
and must define:

| Variable | Meaning |
|---|---|
| `AGENT_EXCHANGE_SKILL_DIR` | Absolute path of this `skills/agent-exchange` directory |
| `AGENT_EXCHANGE_ROOT` | Absolute Exchange Directory shared by requesting and responding agents |
| `HERDR_BIN` | Absolute path of the Herdr executable |
| `HERDR_SESSION` | Herdr session name for responders, for example `agent-exchange` |

Example (replace every path with your own; never commit real values):

```sh
AGENT_EXCHANGE_SKILL_DIR=/absolute/path/to/knowledge-management/skills/agent-exchange
AGENT_EXCHANGE_ROOT=/absolute/path/to/agent-exchange
HERDR_BIN=/absolute/path/to/herdr
HERDR_SESSION=agent-exchange
```

Responder executables must be discoverable by the Herdr server. If they are not
on the default `PATH`, extend `PATH` in the env file rather than committing a
machine-specific path to a unit.

## Install the systemd user units

```bash
skills/agent-exchange/scripts/install-systemd.sh --dry-run   # review
skills/agent-exchange/scripts/install-systemd.sh             # write units
systemctl --user daemon-reload
systemctl --user enable --now herdr-agent-exchange.service agent-exchange-launcher.service
```

The installer resolves the env file once, at install time, and writes its
absolute path into `EnvironmentFile=`. The generated unit therefore does not
depend on the systemd user manager inheriting `XDG_CONFIG_HOME`, and it does not
embed the skill directory, Exchange Directory, Herdr executable or session name.
Generated units default to `$XDG_CONFIG_HOME/systemd/user`, or
`$HOME/.config/systemd/user` when `XDG_CONFIG_HOME` is unset. Use
`--target-dir` to stage them elsewhere. `--env-file` overrides the env
file lookup.

## Required commands

Send, respond, wait and cleanup:

- `bash`, `git`, `jq`, `inotifywait`, `flock`

Launcher and systemd adapter:

- the above plus `python3` (standard-library `tomllib`), a Herdr executable and `timeout`

If a command is missing, report which command is unavailable and which
operation needs it. Do not substitute a different exchange mechanism.

## Known-kind preparation

The Launcher applies only the preparation the published contracts require, and
never guesses for unknown kinds:

- `codex`: appends `--add-dir <Exchange Directory>` so the responder can reach
  the exchange while sandboxed.
- `agy`: appends `--add-dir <Exchange Directory> --mode accept-edits --sandbox`.
- `opencode`: exports a session-only `OPENCODE_CONFIG_CONTENT` into the agent
  pane that grants `permission.external_directory` `"allow"` for the Exchange
  Directory and its descendants, merged with any existing inline config. It
  does not change global configuration.
- Unknown kinds receive the configured args unchanged.

Permission-bypass and conflicting safety arguments are refused before any agent
starts: `--dangerously-skip-permissions` for any kind, `--mode` for `agy`, and
`--auto` for `opencode`.

## Unit path safety

`EnvironmentFile=` is emitted unquoted, because systemd keeps the rest of the
line verbatim (so spaces survive), and a literal `%` is doubled so it is not
read as a unit specifier. A `"`, `\`, `$`, newline, carriage return or
leading/trailing whitespace in the env-file path cannot be represented safely
and is rejected instead of emitting a unit that truncates or rewrites it.

`ExecStart=` embeds no deployment path. It runs the stable `/bin/sh` with
`exec "$${AGENT_EXCHANGE_SKILL_DIR:?…}/scripts/…"`, so systemd reads
`AGENT_EXCHANGE_SKILL_DIR` from the deployment env when the service starts; a
missing value fails with a clear message. Skill directories containing spaces,
`%` or `$` are therefore safe, and the installer never shell-evaluates env-file
content.

## Operational boundary

Herdr is an external system. This skill does not vendor Herdr, and it does not
connect Herdr sessions to the file-based Agent Exchange protocol: a shared
session name is only a naming convention. `order` drives Herdr directly and
does not call Agent Exchange.
