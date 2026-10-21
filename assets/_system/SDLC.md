# SDLC workspace

A small filesystem workflow: define a change, implement and verify it in an isolated checkout, then obtain independent release review. Requires Python 3.9+, Bash, and Git on a POSIX system. Native Windows is not supported. Set `require_tink: true` when project policy requires Tink integrity checks.

## Start a run

Run scripts from any working directory. Paths resolve from the script location.

```sh
python3 _system/scripts/sdlc.py new fix-example --kind bug
python3 _system/scripts/sdlc.py status fix-example
```

The default `light` profile creates one `brief.md` (problem, acceptance criteria, approach, risks, verification) plus `checklist.json`. A human picks `--profile full` for consequential architecture or policy changes; it creates intent, spec and plan. Only runs created by `new` are supported; anything else is refused, and text approval tags are not evidence.

Before asking for definition-gate approval, review the checklist commands and offer a dry run:

```sh
python3 _system/scripts/sdlc.py dry-run fix-example
```

It runs every checklist `check` in the repository root with its timeout, with no approval needed. These are real commands, not a sandbox: review their side effects and use the intended verification environment. Output replaces `runs/<slug>/checklist-dry-run.log`, reporting launch errors, timeouts and nonzero exits per item. No check process survives its check: each starts in a new session, and on normal exit or timeout the rest of its process group is killed and the child reaped. Commands must not escape that session; process-group cleanup is not a sandbox either.
Read the output to tell an expected reproduction failure from a broken command or environment (such as an unsuitable `TMPDIR`), fix the latter, then rerun. Attested items are not executed. The dry run records no decision, mark or receipt, does not enforce a new approval prerequisite, and does not replace `verify`, which still requires current approval and passing checks.

After the human accepts the brief, record the actual review reference:

```sh
python3 _system/scripts/sdlc.py decide fix-example 3 approved \
  --reviewer 'Reviewer name' --source 'Actual review URL or decision reference' \
  --reason 'Accepted scope, approach, and acceptance criteria'
```

Light runs have ONE definition gate, recorded as stage 3 (`sdlc.py decide <run> 3 ...`): the approved `brief.md` + `checklist.json` are the intent, design and plan. Full runs record stages 1, 2 and 3 in order. `changes-requested` records rejection with the same command. Editing an artifact or contract makes dependent decisions stale; older receipts remain. Never invent a reviewer or approval. Local receipts track workflow; they do not authenticate identity or authorize a release.

## Checklist

`runs/<slug>/checklist.json` holds item definitions only: `{"schema":1,"items":[{"id","description","verify","check"?}]}`. Definitions are approved scope: stage 3 cannot be approved with zero or invalid items, and changing them makes that approval stale. Never hand-edit receipts or add status fields to the definitions.

An item may carry `check`: `{"argv":[...],"timeout_seconds":N}`, validated like `verification.json` checks. `verify` runs every item's `check` in the repository root after the configured checks, logging to `test-log.md`; a failure or timeout fails verification (`Checklist check failed: <id>`). A passing check proves the item in that verify run and needs no mark, so `mark` is refused for checked items. Review the commands at approval: they execute like verification checks and are bound to the approval digest, so editing one makes approval stale.

An item without `check` is attested: record it with `sdlc.py mark <run> <item-id> passed|failed --evidence <text>`, which needs a current stage 3 approval and appends a receipt under `marks/`. The latest receipt per item wins, and `verify` requires `passed`. Verification binds a digest of each latest receipt, so any later mark or edit makes it stale: mark first, then verify. Attested marks are self-reported claims for independent review to challenge. Status flags an item marked on an older candidate; re-check it only if the change affects it. The passed receipt records each item as `proven` or `attested`.

`new` always writes `checklist.json`; if it is deleted, status reads MISSING and verification refuses until it is restored from Git. Local files, deletable receipts and `git update-index --skip-worktree` are not a tamper boundary: trusted CI and forge review are.

### Locked seed acceptance checks

At stage-1 intake, use the explicitly supplied session and a trusted Tinkery executable:

```sh
python3 _system/scripts/sdlc.py stage <slug> 1 \
  --seed-contract <session>/seed-contract.md \
  --tinkery <path>/tinkery
```

Tinkery is the single seed verifier. Intake invokes exactly `PATH seed status --json DIR`
and `PATH seed checks DIR`, where PATH is the supplied executable and DIR is the seed's
session directory. Status is read again to detect changes during intake. Its existing
JSON shapes supply `handoff.verified`, the seed digest and canonical check records;
no viewer fields or network probe are needed. Tinkery alone verifies the human v2
confirmation against seed bytes, decisions, records, the canonical heading sequence
and rendered acceptance section. SDLC retains the intake snapshot/digest protections,
not a second session verifier. Missing or unavailable executables, failed commands,
malformed output, simulated sessions, missing confirmation and changed bindings refuse
before any intake or checkout writes. Supply trusted executable toolchain code, never
a seed-provided script. Verification proves content binding, not operator identity or
implementation approval.

`--seed-me-helper` is removed; any use refuses with a pointer to `--tinkery`.
The remaining migration gate is Tinkery's separate copied-command switch and consumer
pin bump; that pin bump waits for the operator's explicit "merge". This package does
not change Tinkery's command or consumer pin.

Intake copies the confirmed seed and preserves every record's fields in locked
`runs/<slug>/seed-checks.json` items with stable `seed-N` IDs; run metadata keeps the
v2 confirmation and definitions digest. Seed Markdown is never read as checks; a seed binding
without record intake is refused.

Imported items are **LOCKED, operator-confirmed** definitions, distinct from the
worker's editable `checklist.json`; `seed-` IDs are reserved. They participate in
status, dry runs and verification. Their file is bound to every definition gate:
editing or deleting a check (or the file) stales approval, and `decide` refuses
imports that disagree with the locked digest. Revised records require a newly
displayed draft, renewed Tinkery confirmation, explicit stage-1 rebind and definition
review. Rebinding replaces activations too; retain prior evidence in Git.
Changes to the original session after intake do not change the run-local snapshot.

A `cmd` record runs verbatim through `bash -c`, using the process-group-safe
runner and a 120-second timeout. `cwd` is relative to the selected checkout, never
the session directory. Absolute paths, traversal, backslashes, drive-letter paths,
NUL and CR/LF are refused. Commands are not sandboxed; review them before approval.
Nonzero exits, missing working directories and timeouts fail `verify` even when
all worker-written checks pass. Prose `expect` is not an output matcher: a command
must pass **and** have an operator attestation for `seed-N-expect`.
A `judge` record requires only a manual operator attestation (`seed-N`), using
`how_to_judge` when supplied and otherwise its statement, never an automated pass.

Record only the actual operator's observation, not the worker's judgment:

```sh
python3 _system/scripts/sdlc.py mark <slug> seed-1-expect passed \
  --reviewer 'Actual operator' --source 'Actual observation reference' \
  --evidence 'Observed output and why it satisfies the seed expectation'
```

Each mark prints its expectation and needs evidence about that expectation. Evidence
already recorded for another seed item is refused unless `--covers-all` records that the
operator meant it for each of them; status says so. Mark an expectation `unknown` when
the operator's observation does not address it; `unknown` blocks verification.

These marks require current definition approval and bind the exact seed item,
including its activation; old marks cannot satisfy a revised command. Reviewer and
source are local references, not authentication. This adds no auto-approval or new
meaning to approval; independent forge policy remains the authority boundary.

A `provisional` record names its prerequisite in `missing`; `cmd` is optional and proposed only.
The imported item stays pending and nonblocking while its prerequisite is absent;
it is never reported as proven. At implementation time the worker must inspect the
named prerequisite. **Once it exists**, supply the real command and prerequisite
evidence by adding only this `activation` object to that item in `seed-checks.json`:

```json
{"activation": {"cmd": "./real-command", "cwd": ".", "evidence": "Prerequisite exists at <path>; observed <evidence>"}}
```

Do not change the locked proposal or its expectation. Activation stales all definition
gates; obtain renewed operator approval of the updated definition before verification.
The activated command and its `seed-N-expect` attestation are then required. Verify
refuses evidence that cites a relative file or `dir/` present in neither the run nor the
checkout, so save each cited log before verifying. A prose
prerequisite is not machine-detected: the worker must surface it when it becomes true,
and the operator reviews the evidence rather than accepting a hidden automatic trigger.

Verification logs label seed checks separately; receipts report `seed_checks`
(`proven`, `attested`, `pending`, or `not-run` on failure) separately from worker
`checklist_items`. Pending proposals are not passing acceptance evidence. Manual
observations still need rechecking when candidate changes affect them.

## Implementation and verification

Give each code-writing run its own Git worktree or clone, never just a second branch in one checkout, and keep its artifacts there. Ports, databases and credentials need their own isolation when used.

Configure `_system/verification.json` with nonempty argv arrays and timeouts. A fresh install has no checks and fails verification until real tests, build, lint and other required checks are selected; file-name heuristics are not evidence a command is right, and the runner should reject zero tests, since an exit code alone proves no coverage. Missing configuration, failed checks, timeouts, and required Tink checks without Tink all fail. A failed check's error ends with `(output: runs/<slug>/04-test/output/test-log.md)`.
To hold commit authorship, add `"authors": {"since": "<commit>", "allow": ["Claude session_* <noreply@anthropic.com>", "Name <email>"]}`.
Verify then refuses any commit after `since` whose `Name <email>` author matches no `allow` pattern (shell-style `*`), and names it;
history up to `since` is never judged. Without `authors`, authorship is not checked.

```sh
python3 _system/scripts/sdlc.py verify fix-example
```

Verification writes the check output and a receipt under `04-test/output/`. The receipt binds the commit, the contents and modes of tracked and nonignored untracked files (not untracked Python caches), artifact inputs, policy, test lock and log digest.
- `runs/` is outside the fingerprint, so keep application source and test infrastructure out of it. Committing only run evidence does not stale unchanged code.
- Ignored files and external services are outside it too: pin dependencies and environments in trusted CI, which must still verify the revision being merged.
- Generated `tink:rules` blocks in the root `AGENTS.md` are excluded from the candidate fingerprint (`walk` and `tink use --check` verify them); all other `AGENTS.md` text counts.
- Commit before final verification and leave the checkout alone while checks run. If it changes anyway, the error lists up to five paths and hints at tracked bytecode or new untracked output (add that to `.gitignore`).

A preexisting log is never passing evidence, and failed or interrupted verification never reuses an older passing receipt. Local status is not deployment status.

## Bug reproduction baseline

Before fixing a bug, demonstrate that a meaningful reproduction fails for the expected reason on the unfixed code, and have an independent reviewer accept it. Then record the actual failure and review references:

```sh
python3 _system/scripts/sdlc.py lock-tests fix-example tests/test_regression.py \
  --source 'Actual accepted test review reference' \
  --failure-evidence 'Actual failing CI job or retained reproduction evidence'
```

Include relevant fixtures, snapshots, discovery configuration, and runner helpers in the locked paths. Bug runs cannot verify without a lock; changed or missing locked files fail. Add `--reproduce '["python3","-m","unittest","tests.test_regression"]'` (a JSON argv array) to run the reproduction at lock time: the lock is refused if it passes, and the exit code and log digest are recorded and shown by `status` (`reproduction` in `status --json`). Without it the lock records review evidence only, `status` says the failure was not run locally, and CI must confirm it. A mistaken baseline requires an independently reviewed replacement run, linked to the previous one; tests are never unlocked silently.

This detects local changes, not adversarial tampering. Strict protection requires CI to fetch the accepted test revision independently, protect verification policy, run against the candidate revision, and require the resulting check before merge. Agent-writable lock files and hooks are not an authorization boundary. This scaffold does not configure a forge or deployment environment.

## Tinkery ownership and handoff

SDLC can operate standalone or as Tinkery's external implementation workflow.
SDLC owns run identity, implementation orchestration, technical review and local
verification freshness. Tinkery owns the seed and its discovery decisions.
Tinkery owns the intended goal-to-result presentation, not another set of SDLC
approvals. The operator judges usefulness and authorizes merging; the forge
owns remote checks and enforces configured release requirements. Technical review
asks whether the change is correct and meets approved scope; Taste asks whether
the exact result serves the unchanged operator goal. A model's praise is neither.

For a Tinkery-originated run:

- **Load:** Load only the explicitly supplied, operator-confirmed seed for seed
  intake; never discover operator sessions to fill a gap. Also read the active
  contract's immediate inputs, approved brief or spec/plan, checklist, relevant
  project policy, and repository source and project files needed by the active stage.
  Use the stage-1 seed-copy mechanism below. Carry forward the supplied seed's
  Problem statement, Proposed outcome (desired user-visible results and success criteria),
  Acceptance criteria, and Constraints and boundaries (non-negotiables, exclusions
  and rejected alternatives). Preserve settled `cmd:` and `expect:` checks verbatim;
  keep provisional checks labelled as proposals, not completed verification.
  Preserve Accepted decisions and assumed defaults with their separate authority,
  plus evidence, uncertainty, open questions and deferrals. Keep the confirmed goal
  intact rather than reconstructing it from a headline. Seed confirmation is not
  implementation permission. Never infer a goal-to-run connection from matching names,
  paths or seed bytes.
- **Hand off:** Use the existing review findings and PR handoff to identify the
  run ID and checkout, bound seed reference/digest, candidate commit and explicit
  PR/result revision. Link each claimed completion condition to evidence for that result;
  name unresolved conditions and findings. Keep automated proof, manual attestation, independent review and operator acceptance
  separate. This assembled handoff is human-readable only, not a normalized machine interface.
  API 1 exposes run_id, the seed_contract binding and verification candidate provenance,
  but not an explicit PR reference. Use structured status for the seed binding, not this
  prose as input to Tinkery's machine binding or reader. A binding is evidence of intake,
  not approval, goal identity or release authority.
  Acceptance or correction returns to the existing external review process
  for the displayed result. Copying a handoff records no verdict and starts nothing;
  recheck current state before recording any external decision.
- **Retain:** Keep the run's seed copy/binding, approved artifacts, checklist,
  decision and mark history, verification receipts/logs, review findings and closure
  references through the existing retention process before removing a checkout.
  Preserve source identities, revisions/digests, read times and decision provenance
  (reviewer, source and reason) in the handoff where available; name absent facts.
  Missing, stale, failed, interrupted and conflicting evidence stay distinct.
  A reader cannot recover records deleted with a worktree.

Do not create a Taste verdict format or a competing acceptance ledger. Goal/run
binding, the shared reader and Taste are Tinkery integration work, not capabilities
provided by this scaffold. Consult Tinkery's [constitution](https://github.com/jon-devlapaz/tinkery/blob/main/docs/yohaku.md),
[proposed job-record contract](https://github.com/jon-devlapaz/tinkery/blob/main/docs/design/job-records.md)
and [Taste scope](https://github.com/jon-devlapaz/tinkery/issues/114) when changing
that integration; proposals grant no implementation or execution authority.

## Who does what at a stage boundary

- The launcher (the human, or a script acting for them) records the gate, then runs
  `python3 _system/scripts/sdlc.py stage <run> <n>` (`--check` previews, `--worktree PATH` or `--here`
  picks the checkout, and at stage 1 `--seed-contract PATH --tinkery PATH` binds a run-local copy at
  `runs/<slug>/seed-contract.md`, as in Locked seed acceptance checks; editing the copy stales approvals and verification). Only a worktree
  launch (stages 3 and 5 unless `--here`) commits `runs/<run>`, so the new checkout has the run; other
  launches commit nothing. It creates the worktree under `~/dev/worktrees/<repo>/` (stage 3: `<run>` on branch `<run>`; stage 5: detached `review-<run>`),
  runs `tink use <skillset> --snapshot runs/<run>/<stage-dir>` inside it, and prints the launch prompt
  for a NEW session. It refuses unless the entry gates are approved and current (stage 5 also needs
  current verification), and it fails closed when the skillset does not compile. Stage 4 runs inside the
  stage-3 session. Manual fallback: approve, commit `runs/<run>`, `git worktree add`, `tink use`, prompt.
- The agent never creates the run, never runs `tink use`, and never approves. It reads
  `AGENTS.md`, its stage `CONTEXT.md`, and `python3 _system/scripts/sdlc.py status <run>`.
- Build order in stages 3 and 4: implement, then `sdlc.py mark` the attested items (marks
  need an approved gate), then `python3 _system/scripts/sdlc.py verify`. Checked items are proven by `verify` and need no
  mark. Commit before verifying, because the candidate fingerprint includes uncommitted files.
- The reviewer works in a separate checkout, writes
  `runs/<slug>/05-deploy/output/REVIEW-findings.md`, and does not modify code or evidence. The candidate's
  owner brings the findings to the PR branch; a repeat review replaces the file and Git keeps the previous one.

## Skills and concurrency

Commit baseline `.tink/skills.toml` and `.tink/skills.lock` when Tink has created them through an authorized operation. Restore into each worktree through Tink; do not copy writable directories by symlink. Lockfile updates are reviewed dependency changes.

Load mandatory skills deterministically. Route only genuine capability gaps with `tink-route`; allow abstention. `tink-route` does not mutate project skills: it mounts the delivered skill into the git-ignored `.active` directory inside `.tink`, prints it on stdout, and needs no cleanup. Run `tink` mutations through the wrapper:

```sh
python3 _system/scripts/sdlc.py skills tink -- skill check
```

The wrapper covers `tink` operations only. It serializes them across this host and rejects symlinked skill state. It does not grant mutation authority, isolate a shared home library, or coordinate direct CLI calls outside the wrapper. Command shapes after `--` belong to the installed Tink CLI; confirm them against `tink --help`. Cross-host shared storage requires external coordination.

Preserve the `tink-route` receipt with the run. Read the delivered skill before relying on it. Low confidence or a failed delivery leaves the capability unresolved.

## Stage skills

Each stage has a skillset pin committed at `.tink/skillsets/<name>-skillset.json`: 01-plan `planning-skillset`, 02-design `design-skillset`, 03-build `build-skillset`, 04-test `testing-skillset`, 05-deploy `deployment-skillset`, 06-maintain `maintenance-skillset`. A pin selects exact upstream code (`source`, `revision`, `sourceRoot`, `members`), so review pin changes like a lockfile: a pin change is a supply-chain change.

```sh
tink library fetch .tink/skillsets/<name>-skillset.json    # once per machine/library, after reviewing the pin
tink use <name>-skillset --snapshot runs/<slug>/<stage-dir>  # at stage open, then start a NEW session
tink-route --receipt runs/<slug>/skills.jsonl "<what you need>"
```

`required` lists the disciplines that `tink use` compiles into `AGENTS.md` at stage open, so each stage gets a fresh session that re-reads it. The starting set is every `principle-*` member of the pin, provisional until tuned by ablation. Capability skills come only through `tink-route` for genuine gaps; exit 1 or 2 means continue without a skill.

`tink-route` searches the whole skill library and never reads `AGENTS.md`, so the command is identical in every stage: in our routing eval 56% of the skills a stage needed were not on its shelf, and the whole library was as precise. `--skillset NAME` restricts a call to one skillset (exit 1 may then add `Hint: <skill> fits but is on another shelf (<skillset>); it was not delivered.`); `--anywhere` is the default spelled out.

Tinkery's operator-confirmed seed contract is the input to 01-plan; bind it at stage 1
as described in Locked seed acceptance checks. Shaping is not a stage skillset or
permission to implement.

`tink use` and `tink-route` are optional integrations; runs work without them. `tink-route` 0.10.0 or newer is required for whole-library routing (older versions scope routing to the stage shelf in the `AGENTS.md` rules block); `sdlc.py stage` warns when an older one is on PATH.

## Recovery and release

Runs are created atomically and have one writer at a time; busy or interrupted operations fail with the lock path. After a crash, confirm no process owns the operation before removing its lock directory, then rerun. Status is derived from receipts; never hand-edit generated records.

Use protected branches, current required CI, independent code-owner approval and deployment checks in your forge. Open the PR once the candidate is committed and verification is current (end of stage 4; `stage <slug> 5` enforces it); stages 1-2 need none, and a draft PR during build is not review. Delivery, merge authority and the no-`origin` warning are owned by `stages/05-deploy/CONTEXT.md`; `stage <slug> 3` prints that warning early, without refusing. PR creation and review findings do not prove deployment: keep deployed revision, result and rollback references in the deployment system.

A run ends when its closure record exists: after review and rework, commit `runs/<run>/closure.md` (PR URL, merge revision, outcome), then record it with `python3 -m tink_substrate archive --checkout <path> --project <repo> --run <run> --phase closure` (see tink-substrate's `docs/finish-a-change.md`); strip only after that. `status` describes candidate evidence and never marks a run merged or closed.

Maintenance is optional intake, not a required completion stage. Enable it only after defining metric-specific thresholds, deduplication, cooldowns, and run limits. Alerts create drafts for service-owner triage, never fabricated approvals.

## Machine interface (API 1)

Use the runtime installed in the selected checkout, not a global runtime. Opening a
checkout trusts its executable code; discovery alone must not execute it.

```sh
python3 _system/scripts/sdlc.py capabilities --json
python3 _system/scripts/sdlc.py status --json
python3 _system/scripts/sdlc.py status example --json
```

These read-only commands emit one JSON object on stdout. Every response carries
`protocol: "tink-sdlc"`, integer `api_version: 1`, and `workspace` (the canonical
absolute checkout path). The API version is independent of the scaffold release.
`capabilities` lists `status-json`, `verify`, `mark`, and `log-file`. Clients must
check the identity, version, and capabilities before enabling actions. Extra fields
and capabilities are additive; removing fields or changing their meaning requires
a new API major. Unknown critical states, malformed responses, and unsupported
majors must fail closed, never fall back to parsing the human CLI.

`new` stores a random UUIDv4 `run_id` in `run.json`; SDLC operations, seed rebinding
and worktree copies preserve it, and recreating a deleted slug creates a new one. The slug
and workspace locate a run; `run_id` identifies its creation. It must be a canonical
lowercase, hyphenated UUIDv4, or the run is refused. The ID is not approval or tamper protection.

`status --json` returns `runs`: summaries with `slug`, `run_id`, `profile`, `kind`,
`seed_contract`, `verification_status`, `next_action`, `has_lock`, and `checklist_summary`
(`total`, `passed`). An unreadable run instead has `verification_status: "invalid"`,
`error`, and an empty summary; it does not hide valid runs.

`status <run> --json` returns `run`:

- `slug`, `run_id`, `meta` (`profile`, `kind`), and `gates` (`stage`, `status`, `blocked`).
  Gate states are `approved`, `pending`, `stale`, or `changes-requested`.
- `seed_contract`: exactly `path`, `sha256`, and `source` from run.json, or null
  when unbound (absent or null in run.json). Both status forms expose this binding;
  human run status adds one `Seed contract:` line when bound. A binding must be an
  object with nonempty strings for all three fields and a lowercase 64-character
  SHA-256 hex digest, plus the existing record-intake marker `definitions_sha256`.
  Malformed bindings invalidate the run. Reads never adopt, repair or rewrite a
  binding or require its original source to remain available.
  This is intake evidence, not approval, goal identity or release authority.
- `verification_status`: `blocked`, `running`, `not-run`, `current`, `stale`,
  `failed`, or `interrupted`. `verification` is null or a normalized record with
  `result`, `passed`, `candidate`, `log` (digest), and `error`. Only `passed: true`
  means current evidence; receipt internals are not a client contract.
- `checklist`: normalized items with their definition, `automatic`, `status`
  (`pending`, `passed`, `failed`, or `unknown` for a seed attestation), `proof` (`automated`, `attested`), optional
  observation `mark`, and `needs_recheck`. `checklist_state` distinguishes
  `defined`, `empty`, `missing`, and `invalid`.
- `actions.verify` and `actions.mark`: `allowed` (boolean) and `reason` (empty
  when allowed). Verification also has `timeout_seconds`, a total execution budget
  or null when blocked. These are UI hints, not authorization: `verify <run>` and
  `mark <run> <item> passed|failed --evidence <text>` recheck their own rules.
- `artifacts` (named text), `decisions` (normalized stage, nanosecond timestamp,
  decision, reviewer, source, reason, and opaque id). Decision values are `approved`
  or `changes-requested`; reviewer, source, and reason are strings. Known malformed or
  missing fields invalidate that run rather
  than being coerced into display text. Also included: `verification_config`
  (display-only checks/policy or null), `errors`, `next_action`, and `cli_status`
  (display-only human output). Human and machine status share one calculation.
- `log`: `path` (checkout-relative, regular UTF-8 file), `text` (last 256 KiB,
  decoded with replacement), and `truncated`. The advertised path stays fixed
  for that verification invocation; the runtime may truncate/rewrite it on start.
  Clients may tail this file but must reject escaping paths and symlinks, including
  replacements during streaming. Do not hardcode a receipt or log directory.

For `status` failures, stdout is the same envelope with `error.code` (`not-found`
or `invalid-state`) and `error.message`, and exit status is nonzero. Usage errors
remain normal CLI errors on stderr. Exit zero from `verify` is not enough to display
a pass: refresh structured status. Launch verification in its own process group;
on cancellation, disconnect, timeout, or shutdown, terminate and reap that group,
including check descendants. Local evidence still grants no release authority.

## Walk lint

```sh
python3 _system/scripts/sdlc.py walk [--json]
```

A read-only, deterministic check of the structural preconditions of the ICM walk test: an agent with no memory should orient, act, and report status from the files alone. No network, no model, and it writes nothing. It checks: W1 the `AGENTS.md` entry file (router markers present, the router block at most 60 lines, generated blocks within their cap; project instructions kept beside the router past 60 lines total are a warning, not a failure); W2 backticked `_system/`, `_shared/`, `stages/`, `.tink/`, `.agents/` pointers in the router and stage contracts resolve (`runs/` paths are exempt); W3 each stage contract names Inputs, Output, and a human Gate; W4 estimated context tokens per stage (warn above 8000, fail above 16000); W5 every `*-skillset` name has a pin and every pin is named; W6 `sdlc.py status` runs for every run; W7 the compiled `tink:rules` block is current (`tink use <name> --check`, skipped when `tink` is absent).

It is structural: a real walk by a fresh agent stays the test. A `tink:rules` block written by `tink use` (between `<!-- tink:rules begin ... -->` and `<!-- tink:rules end -->`) may hold up to 8192 bytes, a deliberate divergence from ICM's content-free router. Exit 0 when nothing fails (warnings do not fail), 1 when a check fails, 2 for usage errors.

## Installation and migration

The installed `_system/scaffold.json` identifies the package and its original hashes.
Verification starts unconfigured (see Implementation and verification), and Git needs an
initial commit before it can run.

The installer requires an explicit target. `--check` previews a fresh installation
or validates an existing one. Repeat initialization preserves the project verification
configuration. Modified or missing managed files are reported as errors; a scaffold
from a different package version is refused with a pointer to `--upgrade`. Preserve run
evidence and customizations; never update factory files during an active feature run.

## Upgrading the scaffold

Preview, then apply (the receipt `_system/scaffold.json` is the trust baseline):

    python3 <package>/scripts/init.py <target> --upgrade --check
    python3 <package>/scripts/init.py <target> --upgrade

- Managed files (stages, `_shared/`, `_system/` except `verification.json`) are updated
  when unmodified, restored when missing, removed when dropped upstream and unmodified.
- Project-owned files (`_system/verification.json`, `.tink/skillsets/*.json`) are never overwritten; locally changed ones are kept and reported. New upstream pins are created.
- A managed file you customized blocks the upgrade: nothing is written, and the plan lists
  each file with a `diff` to compare. Merge your changes by hand, or commit them and rerun
  with `--overwrite-customized` to take the package version (git keeps your old copy).
- Only the router block of `AGENTS.md` is replaced; the rest of the file is preserved.
  Missing or duplicated router markers refuse the upgrade. Downgrades are refused.
- `runs/` is never modified. When stage `CONTEXT.md` files change, approvals in runs that
  have decision receipts read as stale until re-approved.
- The receipt is written last, so an interrupted upgrade is safe to rerun.
- Afterward run `python3 _system/scripts/sdlc.py walk`.

Initialization uses a cooperating-process lock and preflights collisions and symlinks.
Ordinary write failures roll back files written by that attempt; empty directories may
remain. A killed process can leave a partial installation. Inspect it and the lock,
confirm no writer remains, then recover through a reviewed migration; do not force overwrite.
The installer does not create Git history, CI, deployment, monitoring, or tool credentials.

## ICM conventions

This is an ICM-inspired pipeline with explicit adaptations: stage contracts are
shared factory files rather than duplicated into every run, and machine evidence
is immutable-by-convention JSON alongside editable Markdown. Light profiles merge
planning checkpoints into one reviewed brief. Verification is automated; human
release review remains external. Status reads current artifacts and receipts from
the filesystem; neither folder existence nor Markdown approval labels advance it.
Stage numbers are fixed protocol identifiers, not configurable ordering labels.

Templates live in `_shared/`; `brief-template.md` is the light-profile edit surface.
Run outputs belong under `runs/`. Read the active contract and immediate inputs rather
than loading the full factory.
