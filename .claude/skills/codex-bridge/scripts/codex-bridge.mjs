#!/usr/bin/env node
// codex-bridge.mjs — a thin, opinionated wrapper around the official
// `codex-companion.mjs` CLI (from openai/codex-plugin-cc). It does not
// reimplement transport, auth, or job persistence; it adds project-rule
// injection, a review gate, and consistent, loud failure handling.
//
// This file must not gain a commit/push/tag code path, ever — see
// SKILL.md's boundary section. That's a design constraint, not an oversight.

import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

const EXIT = {
  OK: 0,
  USAGE: 1,
  CONFIG: 2,
  ENVIRONMENT: 3,
  AUTH: 4,
  QUOTA: 5,
  RUN_FAILED: 6,
  TIMEOUT: 7,
  NO_SUCH_JOB: 8,
};

const MIN_NODE_MAJOR = 18;
const MIN_NODE_MINOR = 18;

function fail(code, message) {
  process.stderr.write(`[codex-bridge] ${message}\n`);
  process.exit(code);
}

function log(message) {
  process.stdout.write(`${message}\n`);
}

// ── repo / cwd resolution ───────────────────────────────────────────────────

function findRepoRoot(startDir) {
  let dir = resolve(startDir);
  while (true) {
    if (existsSync(join(dir, ".git"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

// ── config loading ──────────────────────────────────────────────────────────

function loadConfig(repoRoot, explicitPath) {
  const path = explicitPath
    ? resolve(repoRoot, explicitPath)
    : join(repoRoot, ".codex-bridge.json");

  if (!existsSync(path)) {
    return { path, exists: false, config: null, error: null };
  }

  let raw;
  try {
    raw = readFileSync(path, "utf8");
  } catch (e) {
    return { path, exists: true, config: null, error: `cannot read ${path}: ${e.message}` };
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    return { path, exists: true, config: null, error: `malformed JSON in ${path}: ${e.message}` };
  }

  if (!parsed.rulesFile || typeof parsed.rulesFile !== "string") {
    return { path, exists: true, config: null, error: `${path}: "rulesFile" is required and must be a string` };
  }

  return { path, exists: true, config: parsed, error: null };
}

function extractSections(fullText, sectionTitles) {
  // Matches ATX-style markdown headings (## Title / ### Title). Sections run
  // from one heading to the next heading of the same or shallower depth.
  const lines = fullText.split("\n");
  const headingRe = /^(#{1,6})\s+(.+?)\s*$/;
  const headings = [];
  lines.forEach((line, idx) => {
    const m = line.match(headingRe);
    if (m) headings.push({ depth: m[1].length, title: m[2].trim(), line: idx });
  });

  const out = [];
  for (const title of sectionTitles) {
    const h = headings.find((h) => h.title === title);
    if (!h) {
      return { ok: false, missing: title };
    }
    const endIdx = headings.find((other) => other.line > h.line && other.depth <= h.depth);
    const end = endIdx ? endIdx.line : lines.length;
    out.push(lines.slice(h.line, end).join("\n").trimEnd());
  }
  return { ok: true, text: out.join("\n\n") };
}

function buildRulesBlock(repoRoot, cfg, noRules) {
  if (noRules) {
    return { block: null, note: "--no-rules: project-rule injection skipped by explicit request" };
  }
  if (!cfg.exists) {
    return { block: null, note: `no .codex-bridge.json found at repo root (${repoRoot}) — delegating with zero project conventions injected` };
  }
  if (cfg.error) {
    fail(EXIT.CONFIG, cfg.error);
  }

  const rulesPath = resolve(repoRoot, cfg.config.rulesFile);
  if (!existsSync(rulesPath)) {
    fail(EXIT.CONFIG, `configured rulesFile does not exist: ${rulesPath}`);
  }
  const fullText = readFileSync(rulesPath, "utf8");

  if (!cfg.config.rulesSections || cfg.config.rulesSections.length === 0) {
    return { block: fullText, note: `injected full file: ${cfg.config.rulesFile}` };
  }

  const extracted = extractSections(fullText, cfg.config.rulesSections);
  if (!extracted.ok) {
    fail(
      EXIT.CONFIG,
      `rulesSections entry "${extracted.missing}" does not match any heading in ${cfg.config.rulesFile} — ` +
      `fix the section title in .codex-bridge.json (this is treated as a config error, not skipped silently, ` +
      `because a typo'd section name would otherwise inject nothing without anyone noticing)`
    );
  }
  return {
    block: extracted.text,
    note: `injected ${cfg.config.rulesSections.length} section(s) from ${cfg.config.rulesFile}: ${cfg.config.rulesSections.join(", ")}`,
  };
}

// ── prompt assembly ──────────────────────────────────────────────────────────

function assemblePrompt({ task, rulesBlock, definitionOfDone }) {
  const parts = [];
  if (rulesBlock) {
    parts.push("# Project conventions (injected automatically — do not restate these to the operator)\n\n" + rulesBlock);
  }
  parts.push("# Task\n\n" + task);
  if (definitionOfDone && definitionOfDone.length > 0) {
    parts.push(
      "# Definition of done\n\n" +
      "The operator will run these commands themselves to verify your work; write toward them succeeding:\n\n" +
      definitionOfDone.map((c) => `- \`${c}\``).join("\n")
    );
  }
  parts.push(
    "# Ground rules for this delegation\n\n" +
    "- Do not touch git history: no commit, no push, no tag, no branch operations. " +
    "The operator commits by hand after reviewing your diff.\n" +
    "- Do not touch production systems or use production credentials. If verifying " +
    "this task would require either, stop and say so instead of proceeding.\n" +
    "- Stay inside the stated scope. Unrelated refactors, drive-by reformatting, and " +
    "unexplained changes will be reverted, not reviewed as part of this task."
  );
  return parts.join("\n\n---\n\n");
}

// ── companion CLI resolution & invocation ───────────────────────────────────

function resolveCompanionCommand(cfg) {
  return (cfg.exists && cfg.config.companionCommand) || "codex-companion.mjs";
}

function findCompanionOnPath(command) {
  // `command -v` is portable across the shells this is likely to run under;
  // avoids assuming a specific plugin install layout.
  const probe = spawnSync("bash", ["-lc", `command -v ${command}`], { encoding: "utf8" });
  if (probe.status === 0 && probe.stdout.trim()) {
    return probe.stdout.trim();
  }
  return null;
}

function runCompanion(command, args, { capture = false } = {}) {
  if (capture) {
    const result = spawnSync(command, args, { encoding: "utf8" });
    return result;
  }
  const result = spawnSync(command, args, { stdio: "inherit" });
  return result;
}

function classifyCompanionFailure(result) {
  // The companion CLI's own exit codes / stderr text are not something this
  // wrapper controls or can assume are stable across plugin versions, so
  // classification is best-effort pattern matching on stderr, falling back
  // to a generic run-failed. The raw output is always printed regardless —
  // see printCompanionResult — so a wrong guess here never hides information,
  // it only affects which exit code the shell sees.
  const text = `${result.stdout || ""}\n${result.stderr || ""}`.toLowerCase();
  if (/not authenticated|auth.*expired|please (log|sign) in|run.*codex login/.test(text)) {
    return EXIT.AUTH;
  }
  if (/quota|usage limit|rate limit exceeded/.test(text)) {
    return EXIT.QUOTA;
  }
  if (/no such job|unknown job|job.*not found/.test(text)) {
    return EXIT.NO_SUCH_JOB;
  }
  if (/timed out|timeout/.test(text)) {
    return EXIT.TIMEOUT;
  }
  return EXIT.RUN_FAILED;
}

function printCompanionResult(result) {
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
}

// Best-effort: shells out to `result <job-id> --json` purely to read
// `threadId` off the job record, so we can print a `codex resume` hint
// (same format the official plugin uses for its own --transfer command).
// This is a side lookup, not part of the primary command's own output —
// it must never change the primary command's exit code or printed text.
// threadId is populated onto the job asynchronously once the underlying
// Codex turn actually starts, so a job looked up moments after being
// enqueued in the background commonly has none yet; that is expected,
// not a bug, and this function silently returns null rather than warn.
function tryResolveResumeHint(companionCommand, jobId) {
  if (!jobId) return null;
  try {
    const probe = spawnSync(companionCommand, ["result", jobId, "--json"], { encoding: "utf8" });
    if (probe.status !== 0 || !probe.stdout) return null;
    const parsed = JSON.parse(probe.stdout);
    const threadId = parsed?.job?.threadId ?? parsed?.storedJob?.threadId ?? null;
    if (!threadId) return null;
    return { threadId, resumeCommand: `codex resume ${threadId}` };
  } catch {
    return null;
  }
}

// Rendered background-launch text is fixed by the official plugin as
// "<title> started in the background as <jobId>. Check /codex:status
// <jobId> for progress." — pull the id back out of it rather than
// reimplementing job creation ourselves.
function extractJobIdFromDelegateOutput(stdout) {
  if (!stdout) return null;
  const match = stdout.match(/started in the background as (\S+?)\./);
  return match ? match[1] : null;
}

// ── arg parsing ──────────────────────────────────────────────────────────────

function parseFlags(argv) {
  const flags = {};
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg.startsWith("--")) {
      const key = arg.slice(2);
      const next = argv[i + 1];
      if (next !== undefined && !next.startsWith("--")) {
        flags[key] = next;
        i++;
      } else {
        flags[key] = true;
      }
    } else {
      positional.push(arg);
    }
  }
  return { flags, positional };
}

function readTaskInput(flags, positional) {
  if (flags["task-file"]) {
    const p = resolve(process.cwd(), flags["task-file"]);
    if (!existsSync(p)) fail(EXIT.USAGE, `--task-file not found: ${p}`);
    return readFileSync(p, "utf8");
  }
  if (positional.length > 0) {
    return positional.join(" ");
  }
  return null;
}

// ── git working-tree state ──────────────────────────────────────────────────

function gitIsDirty(repoRoot) {
  const result = spawnSync("git", ["status", "--porcelain"], { cwd: repoRoot, encoding: "utf8" });
  if (result.status !== 0) return { known: false, dirty: false };
  return { known: true, dirty: result.stdout.trim().length > 0 };
}

function gitHead(repoRoot) {
  const result = spawnSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8" });
  if (result.status !== 0) return null;
  return result.stdout.trim();
}

// ── subcommands ──────────────────────────────────────────────────────────────

function cmdDoctor(ctx) {
  const { repoRoot, cfg, flags } = ctx;
  const lines = [];
  let worstExit = EXIT.OK;

  const [major, minor] = process.versions.node.split(".").map(Number);
  const nodeOk = major > MIN_NODE_MAJOR || (major === MIN_NODE_MAJOR && minor >= MIN_NODE_MINOR);
  lines.push(`${nodeOk ? "OK" : "FAIL"}  Node ${process.versions.node} (need >= ${MIN_NODE_MAJOR}.${MIN_NODE_MINOR})`);
  if (!nodeOk) worstExit = Math.max(worstExit, EXIT.ENVIRONMENT);

  if (!repoRoot) {
    lines.push("FAIL  not inside a git repository (no .git found in any parent directory)");
    worstExit = Math.max(worstExit, EXIT.ENVIRONMENT);
  } else {
    lines.push(`OK    repo root: ${repoRoot}`);
  }

  if (!cfg.exists) {
    lines.push(`WARN  no .codex-bridge.json at repo root — delegations will inject zero project conventions until one is created (see references/configuration.md)`);
  } else if (cfg.error) {
    lines.push(`FAIL  .codex-bridge.json: ${cfg.error}`);
    worstExit = Math.max(worstExit, EXIT.CONFIG);
  } else {
    lines.push(`OK    config: ${cfg.path}`);
    const rulesPath = repoRoot ? resolve(repoRoot, cfg.config.rulesFile) : cfg.config.rulesFile;
    if (repoRoot && !existsSync(rulesPath)) {
      lines.push(`FAIL  rulesFile does not exist: ${rulesPath}`);
      worstExit = Math.max(worstExit, EXIT.CONFIG);
    } else {
      lines.push(`OK    rulesFile: ${rulesPath}`);
    }
  }

  const companionCommand = resolveCompanionCommand(cfg);
  const companionPath = findCompanionOnPath(companionCommand);
  if (!companionPath) {
    lines.push(`FAIL  companion CLI "${companionCommand}" not found on PATH`);
    lines.push(`      install the official plugin inside Claude Code:`);
    lines.push(`        /plugin marketplace add openai/codex-plugin-cc`);
    lines.push(`        /plugin install codex@openai-codex`);
    lines.push(`        /codex:setup`);
    worstExit = Math.max(worstExit, EXIT.ENVIRONMENT);
  } else {
    lines.push(`OK    companion CLI: ${companionPath}`);
    const authProbe = runCompanion(companionCommand, ["status"], { capture: true });
    if (authProbe.error) {
      lines.push(`FAIL  companion CLI did not run: ${authProbe.error.message}`);
      worstExit = Math.max(worstExit, EXIT.ENVIRONMENT);
    } else if (authProbe.status !== 0) {
      const exitClass = classifyCompanionFailure(authProbe);
      lines.push(`WARN  companion CLI "status" exited non-zero (classified as exit ${exitClass}) — see output below`);
      lines.push(indent((authProbe.stdout || "") + (authProbe.stderr || "")));
      worstExit = Math.max(worstExit, exitClass);
    } else {
      lines.push(`OK    companion CLI responded to "status"`);
    }
  }

  log(lines.join("\n"));
  if (worstExit !== EXIT.OK) {
    log("\nDo not proceed past a failing check above — a half-configured bridge produces work that silently ignores the project's rules.");
  }
  process.exit(worstExit);
}

function indent(text, prefix = "      ") {
  return text
    .split("\n")
    .map((l) => (l ? prefix + l : l))
    .join("\n");
}

function cmdCompose(ctx) {
  const { repoRoot, cfg, flags, positional } = ctx;
  const task = readTaskInput(flags, positional);
  if (!task) fail(EXIT.USAGE, "compose requires a task: pass it as an argument or via --task-file <path>");

  const { block, note } = buildRulesBlock(repoRoot, cfg, Boolean(flags["no-rules"]));
  const definitionOfDone = cfg.exists && cfg.config.definitionOfDone ? cfg.config.definitionOfDone : [];
  const prompt = assemblePrompt({ task, rulesBlock: block, definitionOfDone });

  log(`# ${note}\n`);
  log(prompt);
}

function requireCompanion(ctx) {
  const companionCommand = resolveCompanionCommand(ctx.cfg);
  const path = findCompanionOnPath(companionCommand);
  if (!path) {
    fail(
      EXIT.ENVIRONMENT,
      `companion CLI "${companionCommand}" not found on PATH. Run "doctor" for the install steps.`
    );
  }
  return companionCommand;
}

function jobStatePath(repoRoot) {
  const dir = join(repoRoot, ".codex-bridge");
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  return join(dir, "jobs.json");
}

function loadLocalJobLog(repoRoot) {
  const path = jobStatePath(repoRoot);
  if (!existsSync(path)) return { path, jobs: [] };
  try {
    return { path, jobs: JSON.parse(readFileSync(path, "utf8")) };
  } catch {
    return { path, jobs: [] };
  }
}

function appendLocalJobLog(repoRoot, entry) {
  const { path, jobs } = loadLocalJobLog(repoRoot);
  jobs.push(entry);
  writeFileSync(path, JSON.stringify(jobs, null, 2) + "\n");
}

// This wrapper keeps a small local record of baseline HEAD + brief per job
// purely so `result` can print "diff against <sha>" and the brief that
// produced it. It is not a replacement for the companion CLI's own job
// persistence (status/resume/history all still come from there) — see
// SKILL.md: "It does not reimplement ... job tracking."
function cmdDelegate(ctx) {
  const { repoRoot, cfg, flags, positional } = ctx;

  const task = readTaskInput(flags, positional);
  if (!task && !flags.fresh) {
    fail(EXIT.USAGE, "delegate requires a task: pass it as an argument or via --task-file <path>");
  }

  const dirty = repoRoot ? gitIsDirty(repoRoot) : { known: false, dirty: false };
  if (dirty.known && dirty.dirty && !flags["read-only"]) {
    log(
      "[codex-bridge] WARNING: working tree has uncommitted changes. The delegate writes " +
      "directly into the working tree — its changes and yours will land in one " +
      "undifferentiable diff. Commit or stash first, or pass --read-only if this task " +
      "should only analyse.\n"
    );
  }

  const baseline = repoRoot ? gitHead(repoRoot) : null;
  const { block, note } = buildRulesBlock(repoRoot, cfg, Boolean(flags["no-rules"]));
  const definitionOfDone = cfg.exists && cfg.config.definitionOfDone ? cfg.config.definitionOfDone : [];
  const prompt = task ? assemblePrompt({ task, rulesBlock: block, definitionOfDone }) : null;

  // --dry-run only needs to show what *would* be sent — it must work even
  // before the companion CLI is installed, so the companion-CLI presence
  // check happens after this branch, not before it.
  if (flags["dry-run"]) {
    const companionCommand = resolveCompanionCommand(cfg);
    const args = ["task"];
    if (!flags.foreground) args.push("--background");
    if (flags.resume && !flags.fresh) args.push("--resume");
    if (flags.fresh) args.push("--fresh");
    if (flags.model) args.push("--model", String(flags.model));
    if (flags.effort) args.push("--effort", String(flags.effort));
    if (!flags["read-only"]) args.push("--write");
    if (prompt) args.push(prompt);
    log(`# note: ${note}`);
    log(`# would run: ${companionCommand} ${args.map((a) => (a.includes(" ") || a.includes("\n") ? JSON.stringify(a) : a)).join(" ")}`);
    if (prompt) log(`\n${prompt}`);
    return;
  }

  const companionCommand = requireCompanion(ctx);
  const args = ["task"];
  if (!flags.foreground) args.push("--background");
  if (flags.resume && !flags.fresh) args.push("--resume");
  if (flags.fresh) args.push("--fresh");
  if (flags.model) args.push("--model", String(flags.model));
  if (flags.effort) args.push("--effort", String(flags.effort));
  if (!flags["read-only"]) args.push("--write");
  if (prompt) args.push(prompt);

  log(`[codex-bridge] ${note}`);
  const result = runCompanion(companionCommand, args, { capture: true });
  printCompanionResult(result);

  if (result.error) fail(EXIT.ENVIRONMENT, `failed to launch companion CLI: ${result.error.message}`);
  if (result.status !== 0) process.exit(classifyCompanionFailure(result));

  if (repoRoot) {
    appendLocalJobLog(repoRoot, {
      delegatedAt: new Date().toISOString(),
      baselineHead: baseline,
      readOnly: Boolean(flags["read-only"]),
      task: task ? task.slice(0, 500) : "(resume/fresh, no new task text)",
      rawStdout: result.stdout,
    });
    if (baseline) log(`\n[codex-bridge] baseline HEAD for this delegation: ${baseline}`);
  }

  // Best-effort only — see tryResolveResumeHint. For a background job this
  // frequently resolves to nothing yet (the run hasn't started), which is
  // fine: `codex-bridge.mjs result <job-id>` will pick it up once it has.
  const jobId = flags.foreground ? null : extractJobIdFromDelegateOutput(result.stdout);
  const hint = jobId ? tryResolveResumeHint(companionCommand, jobId) : null;
  if (hint) log(`[codex-bridge] Resume in Codex: ${hint.resumeCommand}`);

  log("\n[codex-bridge] UNREVIEWED — nothing above is established fact until you review it yourself (see SKILL.md step 4).");
}

function cmdJobs(ctx) {
  // The installed companion CLI's real subcommand is "status --all", not
  // "jobs" — confirmed against `codex-companion.mjs --help` after the plugin
  // was actually installed. "jobs" was this wrapper's working name for the
  // concept during initial implementation, before the real CLI was available
  // to check against.
  const companionCommand = requireCompanion(ctx);
  const result = runCompanion(companionCommand, ["status", "--all"], { capture: true });
  printCompanionResult(result);
  if (result.error) fail(EXIT.ENVIRONMENT, `failed to launch companion CLI: ${result.error.message}`);
  if (result.status !== 0) process.exit(classifyCompanionFailure(result));
}

function cmdResult(ctx) {
  const { repoRoot, positional } = ctx;
  const jobId = positional[0];
  if (!jobId) fail(EXIT.USAGE, "result requires a job id: codex-bridge.mjs result <job-id>");
  const companionCommand = requireCompanion(ctx);
  const result = runCompanion(companionCommand, ["result", jobId], { capture: true });
  printCompanionResult(result);
  if (result.error) fail(EXIT.ENVIRONMENT, `failed to launch companion CLI: ${result.error.message}`);
  if (result.status !== 0) process.exit(classifyCompanionFailure(result));

  const local = repoRoot ? loadLocalJobLog(repoRoot).jobs.slice().reverse().find(() => true) : null;
  if (local && local.baselineHead) {
    log(`\n[codex-bridge] diff against baseline: git diff ${local.baselineHead}`);
  }

  // Best-effort only — see tryResolveResumeHint. By the time a job has a
  // result to fetch, the run has actually started, so threadId is far more
  // likely to be populated here than right after `delegate` enqueues it.
  const hint = tryResolveResumeHint(companionCommand, jobId);
  if (hint) log(`[codex-bridge] Resume in Codex: ${hint.resumeCommand}`);

  log("[codex-bridge] UNREVIEWED — do not treat this output, including any claim the delegate makes about tests passing, as established fact until you have reviewed it yourself (see SKILL.md step 4).");
}

function cmdCancel(ctx) {
  const { positional } = ctx;
  const jobId = positional[0];
  if (!jobId) fail(EXIT.USAGE, "cancel requires a job id: codex-bridge.mjs cancel <job-id>");
  const companionCommand = requireCompanion(ctx);
  const result = runCompanion(companionCommand, ["cancel", jobId], { capture: true });
  printCompanionResult(result);
  if (result.error) fail(EXIT.ENVIRONMENT, `failed to launch companion CLI: ${result.error.message}`);
  if (result.status !== 0) process.exit(classifyCompanionFailure(result));
}

function cmdFollowup(ctx) {
  const { flags, positional } = ctx;
  const note = readTaskInput(flags, positional);
  if (!note) fail(EXIT.USAGE, "followup requires the correction text: pass it as an argument or via --task-file <path>");
  const companionCommand = requireCompanion(ctx);

  const args = ["task", "--resume", "--write"];
  if (flags.model) args.push("--model", String(flags.model));
  if (flags.effort) args.push("--effort", String(flags.effort));
  args.push(note);

  const result = runCompanion(companionCommand, args, { capture: true });
  printCompanionResult(result);
  if (result.error) fail(EXIT.ENVIRONMENT, `failed to launch companion CLI: ${result.error.message}`);
  if (result.status !== 0) process.exit(classifyCompanionFailure(result));
  log("\n[codex-bridge] UNREVIEWED — this is a correction on the same thread, not a fresh review; the same review step still applies once it's done.");
}

// ── entry point ──────────────────────────────────────────────────────────────

function usage() {
  return `codex-bridge.mjs <command> [options]

Commands:
  doctor                          Check environment, config, companion CLI, auth.
  compose <task> | --task-file f  Print the assembled prompt without sending it.
  delegate <task> | --task-file f Delegate a task to Codex.
  jobs                            List jobs the companion CLI knows about for this repo.
  result <job-id>                 Fetch a finished job's result.
  cancel <job-id>                 Cancel a running job.
  followup <note>                 Resume the previous thread with a correction.

Common options:
  --background / --foreground     Async (default) vs. blocking. (delegate)
  --read-only                     Codex analyses but does not edit. (delegate)
  --fresh                         Force a new thread instead of resuming. (delegate)
  --resume                        Resume the previous thread. (delegate)
  --model <name>                  Override the model.
  --effort <level>                none...xhigh.
  --dry-run                       Print the assembled prompt and command, send nothing. (delegate)
  --no-rules                      Skip project-rule injection. (compose, delegate)
  --cwd <dir>                     Treat <dir> as the working directory.
  --config <path>                 Use a config file other than .codex-bridge.json.

Exit codes: 2 config, 3 environment, 4 auth, 5 quota, 6 run failed, 7 timeout, 8 no such job.
`;
}

function main() {
  const argv = process.argv.slice(2);
  const command = argv[0];
  const rest = argv.slice(1);

  if (!command || command === "--help" || command === "-h") {
    log(usage());
    process.exit(command ? EXIT.OK : EXIT.USAGE);
  }

  const { flags, positional } = parseFlags(rest);
  const cwd = flags.cwd ? resolve(process.cwd(), flags.cwd) : process.cwd();
  const repoRoot = findRepoRoot(cwd);
  const cfg = loadConfig(repoRoot || cwd, flags.config);
  const ctx = { repoRoot, cwd, cfg, flags, positional };

  switch (command) {
    case "doctor":
      return cmdDoctor(ctx);
    case "compose":
      return cmdCompose(ctx);
    case "delegate":
      return cmdDelegate(ctx);
    case "jobs":
      return cmdJobs(ctx);
    case "result":
      return cmdResult(ctx);
    case "cancel":
      return cmdCancel(ctx);
    case "followup":
      return cmdFollowup(ctx);
    default:
      fail(EXIT.USAGE, `unknown command "${command}"\n\n${usage()}`);
  }
}

main();
