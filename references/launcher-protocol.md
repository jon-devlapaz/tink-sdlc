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
  A capped adapter can also report the boolean `response_cap_reached`. When it
  reports `true`, ordinary completion refuses even if the turn ended normally.
  Record the stop with `complete --stopped-by cap`.
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
  Launch writes that session directory to `runs/<run>/steps/<name>.session-dir`; resume and finished read it from there,
  so a later reader's environment cannot hide the session.
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
  process group before saving any stop evidence. The finished probe counts the
  same recorded answers, so a missing checkpoint cannot hide a reached cap.
  Finish such a step with `complete --stopped-by cap`. Resume refuses a session that already holds N answers. An
  answer requested before the stop may still be charged; the cap counts recorded answers.

## Bundled Pi Durable adapter

`_system/scripts/pi-durable-launcher` takes the same pins and gives the same offline refusals, builder
prompt, response cap watcher and cap-stop evidence as `pi-launcher`. It runs `pi-launcher`'s code only while
that file's SHA-256 matches the one pinned in the adapter, and it pins its bundle in `_system/scripts/pi-durable/`
the same way.

- `install` is the only operation that downloads. It runs `npm ci --ignore-scripts` on the pinned lockfile
  into `~/.local/share/tink-sdlc/pi-durable/<bundle digest>/` (`TINK_SDLC_PI_DURABLE_HOME` moves it).
  `resolve` refuses until it has run, and refuses Node older than 22.19 (`TINK_SDLC_NODE` names another).
- Launch and resume start `node worker.mjs` detached in the step checkout. The worker opens one SQLite session
  per step with the exact pin and Pi's stored credentials, skills and project context files, read through Pi's
  own package. Launch records the session folder beside the contract, as `pi-launcher` does.
- Admission is the worker's durable submission: launch returns once the input is committed. Resume
  continues a run that a crash left pending, with the same submission and no new input. Only an
  ended or failed run gets the builder prompt again.
- An interrupted tool call is not replayed. The model gets an `interrupted` result, and the system prompt
  tells it to check a command's effects before running it again. Each command runs through `tool-shell`, which
  ends the command's process group when the worker dies.
- Every committed message is mirrored in Pi's session format to `<session>.jsonl` in the session folder, and
  as `message_end` events in the step's `.pi.log`. `finished`, the cap count and cap-stop evidence read that
  mirror with `pi-launcher`'s code.
- Resume refuses while the step's recorded process group is alive: two workers on one store would both drive its
  pending run.
- Trust limits: npm checks the packages' integrity at install, not at each launch. `TINK_SDLC_PI_DURABLE_STUB`
  (tests only) replaces the provider with an offline module and, like `TINK_SDLC_PI`, trusts the caller's
  environment.

pi-herdr 0.6.0's `herdr_resume_agent` re-derives the model without the spawn-level pin
(it falls back to the parent's model), so do not resume pinned steps through it.
