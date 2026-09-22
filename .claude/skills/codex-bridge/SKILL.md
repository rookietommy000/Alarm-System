---
name: codex-bridge
description: Delegate a scoped implementation task to Codex and review what comes back. Use when the user says to delegate, hand off, or outsource coding work to Codex, or asks to check, resume, or collect the result of a task already delegated to Codex. Only the agent that owns commits for this repository should use this.
---

# Codex Bridge

Hand a well-scoped implementation task to Codex, get a proposal back, review it
yourself, and decide whether it enters the repository. Codex does the typing.
You do the judgement.

This wraps the official OpenAI plugin's `codex-companion.mjs`. It does not
reimplement the transport, auth, or job tracking — it adds project-rule
injection, a fixed prompt contract, a review gate, and loud failure handling.

## The boundary — read this first

**Codex is your tool, not a colleague.** It is in the same category as a shell
command or an editor: something you operate and remain accountable for. It is
not a participant in whatever collaboration structure this project runs. It
receives no messages from anyone else, reports to no one else, holds no memory
or persona, and has no standing of its own.

Three rules follow, and none of them bend:

1. **Only the agent that owns commits for this repository invokes this skill.**
   If another agent thinks something should be delegated, it says so to that
   agent. It does not call this skill itself. The commit path stays exactly as
   narrow as it was before Codex existed.
2. **Codex never writes to git history.** `codex-bridge` has no commit, push, or
   tag path anywhere in it — by construction, not by convention. You commit, by
   hand, after you have reviewed.
3. **Codex never touches production.** If verifying a task needs production
   credentials or a production write, that verification stays with whoever
   already owns it. Delegate the code, not the access.

## First run

```bash
node <skill-dir>/scripts/codex-bridge.mjs doctor
```

`doctor` reports Node version, config file, companion CLI location, Codex
install, and auth. If the companion CLI is missing, install the official plugin
inside Claude Code:

```
/plugin marketplace add openai/codex-plugin-cc
/plugin install codex@openai-codex
/codex:setup
```

Then re-run `doctor`. Do not proceed past a failing check — a half-configured
bridge produces work that silently ignores the project's rules.

If this repository has no `.codex-bridge.json`, create one before the first
delegation. See [references/configuration.md](references/configuration.md) and
`config.example.json`. Without it the delegate gets no project conventions and
no definition of done, and `doctor` will warn you about exactly that.

## When to delegate

Delegate when the task is **bounded and checkable**: a new function against a
known interface, a batch of tests for existing behaviour, a bug whose cause you
have already located, a mechanical refactor across many files, a migration that
follows a pattern already in the codebase.

Do not delegate when the work *is* the thinking: an unresolved design
trade-off, a decision about what the product should do, an investigation with no
stated success condition, anything touching credentials or release machinery,
or anything you could not review afterwards. If you would not be able to say
why each hunk of the resulting diff is there, you cannot delegate it.

## Workflow

**1. Write the task brief.** Be concrete about scope, the files involved, and
what done looks like. Project-wide conventions are injected automatically from
the configured rules file — do not paste them in again. Long briefs go in a
file:

```bash
node <skill-dir>/scripts/codex-bridge.mjs compose --task-file brief.md
```

`compose` prints the exact prompt without sending it. Use it once when you are
new to a repository, to confirm the right rules are being injected.

**2. Delegate.**

```bash
# background (default) — you keep working, collect later
node <skill-dir>/scripts/codex-bridge.mjs delegate --task-file brief.md

# foreground — small tasks, you wait
node <skill-dir>/scripts/codex-bridge.mjs delegate --foreground "Add a unit test for ..."
```

Background is the default because a foreground run holds your shell for as long
as Codex takes, which for real tasks is minutes. Use `--foreground` only for
work you expect to finish quickly.

The delegate writes **directly into the working tree** by default. Commit or
stash your own uncommitted work first, or its changes and yours land in one
undifferentiable diff. `codex-bridge` warns when the tree is already dirty.
Use `--read-only` for analysis-only tasks where nothing should be edited.

**3. Track and collect.**

```bash
node <skill-dir>/scripts/codex-bridge.mjs jobs            # everything this repo knows about
node <skill-dir>/scripts/codex-bridge.mjs result <job-id> # fetch a finished one
node <skill-dir>/scripts/codex-bridge.mjs cancel <job-id> # stop one
```

Job state is stored per repository by the companion CLI, so a session started
later still sees work delegated earlier. Every result is printed under an
UNREVIEWED banner. That banner is not decoration: until you have done step 4,
nothing in the output is established fact, including the delegate's own claim
that it ran the tests.

**4. Review — this step is not optional.**

- `git diff <baseline-HEAD>` (the delegate's output prints the baseline) and
  read every hunk. Be able to state why each one exists. Unexplained changes,
  drive-by reformatting, and unrelated refactors get reverted, not accepted.
- Run the project's own checks **yourself**. Do not accept the delegate's
  report of a passing test suite as evidence that it passes.
- Check the change against the project's documented rules and known pitfalls.
  A change can pass every test and still violate a convention that exists for a
  reason the tests do not encode.
- Then, and only then, commit — by hand, under your own name and judgement.

**5. Correct or restart.** If the output is wrong, send the delta on the same
thread rather than restating everything:

```bash
node <skill-dir>/scripts/codex-bridge.mjs followup "The new helper bypasses the
existing validation layer. Route it through the existing validator instead."
```

`followup` resumes the previous thread, so the original brief and rules still
apply. When the direction has changed materially, start over with
`delegate --fresh` instead — resuming into a wrong frame wastes more time than
restating the task.

## Options worth knowing

| Flag | Effect |
| --- | --- |
| `--background` / `--foreground` | Async (default) vs. blocking. |
| `--read-only` | Codex analyses but does not edit. Default is write-capable. |
| `--fresh` | Force a new thread instead of resuming. |
| `--model <name>` | Override the model. `spark` is an alias for the fast Codex model. |
| `--effort <level>` | `none`…`xhigh`. Leave unset unless you have a reason. |
| `--dry-run` | Print the assembled prompt and the exact command, send nothing. |
| `--no-rules` | Skip project-rule injection. Rarely correct; say why. |
| `--cwd` / `--config` | Target another directory or config file. |

## When it fails

Failures are classified and surfaced in full — raw output is reproduced
verbatim, never swallowed or summarised away. Exit codes: `2` config, `3`
environment, `4` auth, `5` quota, `6` run failed, `7` timeout, `8` no such job.

Report the failure to whoever is waiting on the work rather than quietly
absorbing it. A blocked delegation is a fact about the task's timeline, and the
fact that it happened must stay visible.

- **Quota (`5`)** — delegation is unavailable until the limit resets. Implement
  it yourself, or narrow the scope and retry later. Say which you chose.
- **Auth (`4`)** — `codex login`, then `doctor`.
- **Environment (`3`)** — the Codex CLI or the companion plugin is missing.
  `doctor` names the fix.
- **Timeout (`7`)** — check `jobs`; the work may still be running.

More detail in [references/troubleshooting.md](references/troubleshooting.md).

## Portability

Nothing in this skill is specific to any one project. All project knowledge —
which rules file to inject, which sections of it, which commands define "done"
— lives in `.codex-bridge.json` at the repository root. To use this skill in
another repository, copy the directory and write a new config file. Do not edit
the skill itself; if you find yourself wanting to, the thing you want probably
belongs in the config. See [references/configuration.md](references/configuration.md).
