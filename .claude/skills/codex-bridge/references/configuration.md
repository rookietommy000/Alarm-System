# Configuration

`codex-bridge` reads a single JSON file at the repository root:
`.codex-bridge.json`. Nothing else. There is no global config, no per-user
override, no environment-variable escape hatch for the settings below — one
file, checked into the repo, so every agent operating on it sees the same
rules.

If the file is missing, `doctor` reports it and every other subcommand refuses
to run except `compose --no-rules` and `delegate --no-rules` (which exist
specifically to let you work before a config is written, and say so loudly
when they do).

## Fields

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `rulesFile` | string | yes | Path, relative to the repo root, to the file containing this project's conventions (e.g. `CLAUDE.md`). Read fresh on every delegation — never cached across runs, so edits to the rules file take effect immediately. |
| `rulesSections` | string[] | no | Markdown `##`/`###` heading titles to extract from `rulesFile`. When omitted, the entire file is injected. When present, only these sections (matched by heading text, case-sensitive, exact match) are injected, in the order given — not the order they appear in the source file. A heading listed here that does not exist in `rulesFile` is a config error (exit `2`), not a silent skip: a typo'd section name would otherwise inject nothing and no one would notice until Codex produced something that violated a rule it was never actually shown. |
| `definitionOfDone` | string[] | no | Shell commands that define "done" for this repo (e.g. `pytest tests/`). Appended verbatim to every composed prompt as the acceptance check Codex should aim to satisfy. This does not run the commands on your behalf — you still run them yourself in the review step. It exists so Codex knows what target it's writing toward. |
| `companionCommand` | string | no | How to invoke the official plugin's CLI. Defaults to `codex-companion.mjs`, resolved via the Claude Code plugin install location. Override only if you've installed it somewhere `codex-bridge` can't find on its own (`doctor` will tell you if resolution fails). |
| `defaultModel` | string \| null | no | Passed as `--model` when the caller doesn't supply one. `null` (the default) means: let the companion CLI use its own default. |
| `defaultEffort` | string \| null | no | Passed as `--effort` when the caller doesn't supply one. Same default behavior as `defaultModel`. |

## Minimal config

```json
{
  "rulesFile": "CLAUDE.md"
}
```

This is a complete, valid config. It injects the whole rules file, defines no
acceptance commands, and lets the companion CLI pick its own model and effort.
Start here; add sections and a definition-of-done once you know which parts of
your rules file actually matter to the kind of work you delegate.

## Full example

See `config.example.json` next to this file. It targets this project's actual
`CLAUDE.md` structure and is meant to be copied and edited, not used verbatim
in a different repository — the section titles are this project's, not a
schema.

## Porting to another project

1. Copy the `codex-bridge/` skill directory as-is. Nothing inside it should
   need to change.
2. Write a new `.codex-bridge.json` at the new repository's root, pointing
   `rulesFile` at whatever that project calls its conventions document —
   `AGENTS.md`, `CONTRIBUTING.md`, a `docs/engineering-standards.md`, anything.
3. Update `rulesSections` to that document's actual heading names, or omit the
   field to inject the whole thing.
4. Run `doctor`. A correctly ported config passes the same checks this
   project's config does, with no changes to `scripts/codex-bridge.mjs`.

If step 4 requires touching the script rather than the config, that's a bug in
the skill's portability, not a normal part of porting it — the config file is
supposed to be the only thing that changes.

## Why a repo-root file, not a per-user setting

The rules injected into a delegated task are a property of the *task*, not of
whoever happens to be running the delegation this week. Anyone who invokes
`codex-bridge` in this repository — today, or after the project changes hands —
should inject the same conventions from the same place, without first having
to discover and copy someone else's local setup. That's also why the field
list above is intentionally small: every knob here is something a delegated
task's correctness can depend on, not a personal preference.
