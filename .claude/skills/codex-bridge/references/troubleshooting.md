# Troubleshooting

Every failure from `codex-bridge.mjs` prints the raw underlying error (from the
companion CLI, from Node, from the filesystem) in full, then a classification
and exit code. The raw output is never trimmed or summarized — if a stack
trace or a companion-CLI error message is long, it is still all there, because
a truncated error is a slower path to the same diagnosis.

## Exit codes

| Code | Class | Meaning |
| --- | --- | --- |
| `2` | config | `.codex-bridge.json` is missing, unreadable, malformed JSON, or references a `rulesSections` entry that doesn't exist in `rulesFile`. |
| `3` | environment | The companion CLI (`codex-companion.mjs`) or the `codex` binary it depends on cannot be found, or Node is older than 18.18. |
| `4` | auth | The companion CLI reports Codex is not authenticated, or authentication has expired. |
| `5` | quota | Codex reports the usage limit for the current period has been reached. |
| `6` | run failed | Codex ran and returned a non-zero result — the delegated task itself failed, as opposed to the bridge failing to reach Codex at all. |
| `7` | timeout | The companion CLI's own timeout elapsed before the job finished. The job may still be running server-side. |
| `8` | no such job | A `result`/`cancel`/`followup` referenced a job id the companion CLI has no record of — wrong id, wrong repo, or the job expired out of its store. |

## By symptom

**`doctor` fails on "companion CLI not found" (exit `3`)**

The official plugin isn't installed, or is installed somewhere this shell's
`PATH`/plugin directory doesn't resolve. Install it:

```
/plugin marketplace add openai/codex-plugin-cc
/plugin install codex@openai-codex
/codex:setup
```

Re-run `doctor`. If it still can't find the CLI after install, check
`companionCommand` in `.codex-bridge.json` — if you installed the plugin to a
non-default location, point this field at it explicitly.

**`doctor` fails on Node version**

Codex's companion CLI requires Node 18.18+. `node --version` to check; upgrade
via whatever you normally use (`nvm`, `brew`, etc.) — `codex-bridge` does not
manage Node itself.

**`delegate`/`followup` exits `4` (auth)**

Run `codex login` (or whatever the companion CLI's own auth flow is — `doctor`
prints the exact command for the version you have installed), then `doctor`
again to confirm it's picked up. Don't retry the delegation until `doctor`
passes; a retry against expired auth just produces the same failure with less
context each time.

**`delegate` exits `5` (quota)**

The usage limit for the current period is exhausted. There is no retry that
fixes this — it resets on a schedule Codex controls, not this bridge. Two real
options:

- Do the task yourself now.
- Narrow the task's scope and try again once quota resets, if the deadline
  allows waiting.

Say out loud which one you picked, and to whom the delay matters — a silently
absorbed delay is exactly the kind of thing this project's exception-handling
rules exist to prevent.

**`delegate` exits `6` (run failed)**

Codex reached the task and produced a failure, not a bridge-level problem. The
full output from the companion CLI is printed above the classification — read
it before deciding whether to `followup` with a correction or `delegate
--fresh` with a re-scoped brief. A run failure is information about the task,
not a reason to retry blindly.

**`delegate`/`compose` exits `7` (timeout)**

The bridge gave up waiting; Codex may still be working. Run `jobs` — if the job
shows as still active, `result <job-id>` later once it finishes. If `jobs`
shows nothing for it, the job itself was lost and you need to `delegate
--fresh`.

**`result`/`cancel`/`followup <job-id>` exits `8` (no such job)**

Three usual causes, in order of likelihood:

1. Typo'd or truncated job id — check `jobs` for the exact string.
2. Running from a different working directory / different `--cwd` than the one
   the job was created in. Job state is per-repository; the companion CLI looks
   it up relative to where you are.
3. The job aged out of the companion CLI's own store. `codex-bridge` does not
   control retention — that's the companion CLI's job-tracking layer, not this
   wrapper.

**Result looks like it was accepted without review**

It wasn't — check the actual commit history. Every `result` output is printed
under an `UNREVIEWED` banner and `codex-bridge` has no commit path at all (see
SKILL.md's boundary section). If something Codex wrote ended up committed
without a human review step in between, that happened in the operator's own
workflow, not inside this tool — this bridge cannot commit anything, by
construction.

## When none of the above matches

Read the raw output first — it is never hidden — before assuming a new failure
mode needs new handling in the script. Most "new" failures are the companion
CLI surfacing something specific to that run (a malformed prompt, a repo state
it didn't expect) that the classification above correctly buckets as `6`
(run failed) even though the message itself is unfamiliar.
