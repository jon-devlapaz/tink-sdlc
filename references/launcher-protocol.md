# tink-sdlc launcher protocol 1

For authors and reviewers of build-step launcher adapters. The installed guide `_system/SDLC.md` covers running steps; each bundled adapter's module comment states what it promises and refuses.

## Adapter contract

Arbitrary Pi/Herdr commands are not compatible. A launcher is reviewed executable code
implementing `tink-sdlc-launcher` protocol 1 (one is bundled for Pi, below):

- Offline probe: `ADAPTER resolve --for-operation launch|resume --model PIN
  --report ABS --contract ABS` (plus `--session ID` for resume). It must make **zero
  provider requests**, check the exact model is available/compatible and validate
  the retained session's model before resume. Never resolve an alias or fall back.
- Admission: `ADAPTER launch|resume --model PIN --report ABS --contract ABS`
  (plus `--session ID` for resume). It must pass the pin explicitly to the harness
  and recheck it before any provider request, not rely on mutable defaults. It
  returns after session admission, not after the whole build; no shell is used.
- Finished probe: `ADAPTER finished --model PIN --report ABS --contract ABS --session ID`.
  It must make **zero provider requests** and reply `finished: true` only when the
  retained session's newest turn ended normally on the pin. `complete` refuses on
  `false`, a failure or a missing probe.
- Each operation returns exit zero and one JSON object on stdout containing
  `protocol: "tink-sdlc-launcher"`, integer `api_version: 1`, `operation` (launch, resume
  or finished) and the exact `model`. Resume and finished responses include the same `session`;
  admissions require a nonblank session identity. Admissions may add `process_group`, the
  detached worker's process group, whose leader's command line names the contract path; a stop
  ends that group. Diagnostics belong on stderr.
  Failed, malformed, unsupported or mismatching offline replies refuse admission.
- Probe timeouts are 15 seconds; admission timeout is 60 seconds. A crash or failed
  admission leaves `uncertain`, never automatic retry or completion. Inspect the
  actual harness/session and reconcile it before explicitly preparing another
  uniquely named step; never hand-edit the generated contract to clear uncertainty.

The adapter is trusted code, not a sandbox or an authenticated provider guarantee.
SDLC checks its content hash and protocol; it cannot prevent a dishonest adapter's
probe from charging or its admission from ignoring arguments. Offline resolution
and adapter conformance must be tested before granting it a live-call budget.

## Bundled Pi adapter

`_system/scripts/pi-launcher` runs headless Pi (`pi` on PATH; `TINK_SDLC_PI` names
another executable). Prepare with `--launcher <checkout>/_system/scripts/pi-launcher`
and a pin that names its thinking level, such as `openai-codex/gpt-5.6-luna:low`.

- Every launch and resume passes `--model provider/model-id --thinking level`
  explicitly. Pi lets an explicit `--model` override a session's recorded model and,
  without one, silently falls back to its defaults outside the TUI, so the adapter
  never relies on session restore.
- `resolve` reads only local files and starts nothing. The model must be in Pi's
  catalog cache (`models-store.json`) and its provider must have a stored credential in
  `auth.json` (presence only; environment keys are refused). A non-reasoning model
  with a thinking level other than `off` is refused. Run `pi update --models` yourself
  if the cache lacks a model; the adapter never refreshes it.
- Launch starts `pi --mode json` detached, with a new `tsdlc-<uuid>` session ID in Pi's
  per-checkout session directory (`PI_CODING_AGENT_DIR` and `PI_CODING_AGENT_SESSION_DIR` respected), and returns once
  the session file records the pin, replying with Pi's process group. Output goes to `runs/<run>/steps/<name>.<op>-<time>.pi.log`.
- Resume refuses a session that is missing, archived by session rotation, or whose last
  recorded model (`model_change` or assistant answer on the active branch) differs from
  the pin. It passes the session file path, then waits up to 50 seconds for the first
  answer: another model stops the run. A turn still thinking at 50 seconds is admitted
  when Pi is running and has recorded the new prompt on the pin; otherwise the step is
  left `uncertain` with Pi possibly still running.
- `finished` reads only the session file: the newest entry on the active branch must be
  an assistant answer on the pin with stop reason `stop`. A run killed after a tool call,
  aborted, failed or answered off the pin is not finished.
- The check is per answer the adapter observes. Later model switches inside the session
  are not watched.
- With `--max-responses N`, admission leaves a watcher (in its own session) that counts the
  assistant answers in the session file every half second and, once there are N, ends Pi's
  process group and writes the stop to the step's `.pi.log`. Finish such a step with
  `complete --stopped-by cap`. Resume refuses a session that already holds N answers. An
  answer requested before the stop may still be charged; the cap counts recorded answers.

pi-herdr 0.6.0's `herdr_resume_agent` re-derives the model without the spawn-level pin
(it falls back to the parent's model), so do not resume pinned steps through it.

## Bundled Claude Code adapter

`_system/scripts/claude-launcher` runs headless Claude Code (`claude` on PATH;
`TINK_SDLC_CLAUDE` names another executable). Prepare with
`--launcher <checkout>/_system/scripts/claude-launcher` and a pin
`anthropic/<model-id>:<effort>`, such as `anthropic/claude-sonnet-5-5:low`.

- The model must be on the adapter's reviewed list of exact IDs; aliases such as `sonnet`
  are refused. Effort is one of `low`, `medium`, `high`, `xhigh`, `max`, passed as `--effort`.
- `resolve` makes no model request: it reads local files and runs only `git show`,
  `claude --help` and `claude auth status`. A Claude Code without `--restricted` is refused. Claude Code keeps no local model catalog, so it cannot confirm
  that the account can use the pin; a pin Claude Code rejects fails at launch.
  A fallback model named in managed settings, or in an `ANTHROPIC_*` or `CLAUDE_*`
  environment variable, is refused.
- The builder runs in Claude Code's restricted mode: file tools reach only the checkout and
  the report's folder (`--add-dir`; the builder can write anything there, so give the report
  a folder of its own), and edits to `runs/*/steps/` and `_system/` are denied.
  Bash runs only the run's own check commands; anything else is refused without a prompt
  (`--permission-mode acceptEdits --permission-prompts none`). Claude Code also allows
  read-only parts of a compound command, such as `<check> && echo done`. The commands come from the
  committed `_system/verification.json` (an uncommitted edit refuses launch and resume) and
  the run's `checklist.json` and `seed-checks.json`, which the definition gate holds. A check
  with parentheses or a newline cannot be allowlisted exactly and refuses the step.
- User and checkout settings, MCP servers and auto memory are never loaded (`--restricted
  --strict-mcp-config`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`), so settings, hooks, servers or
  memory a builder writes cannot widen what it may run or steer a later session.
- Not a sandbox: an allowed check runs whatever code the builder edited, with no
  confinement, and that code can clear `TINK_SDLC_STEP_SESSION`. Review the diff, not just
  the report. Resume repeats the launch prompt; new instructions need a new step.
- The child runs with stdin closed, without the caller's `CLAUDE*` session variables
  (`CLAUDE_CONFIG_DIR` is kept), with `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` so no
  side request runs on another model, and with `TINK_SDLC_STEP_SESSION=1`.
- Launch passes a new `--session-id`; admission waits until Claude Code's start event names
  the pin and the prompt is recorded. A start on another model stops the run.
  Output goes to `runs/<run>/steps/<name>.<op>-<time>.claude.log`.
- Resume passes `--resume` with the same ID. It refuses a missing session, one recorded in
  another checkout, or one whose last real answer was on another model.
- `finished` reads only the transcript in `$CLAUDE_CONFIG_DIR/projects` (default
  `~/.claude/projects`): the newest turn on the active branch must be an answer on the pin
  with stop reason `end_turn`. Error entries (`<synthetic>` model, `isApiErrorMessage`), such as
  a usage limit, are not finished; Claude Code's own result may still say `success` then.
