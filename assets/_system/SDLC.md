# SDLC workspace

A small filesystem workflow: define a change, implement and verify it in an isolated checkout, then obtain independent release review. Requires Python 3.9+, Bash, and Git on a POSIX system. Native Windows is not supported. Set `require_tink: true` when project policy requires Tink integrity checks.

**Boundaries, said once.** Receipts, digests, locks and reviewer names are local workflow records: they detect mistakes and changes, not tampering, and authenticate no one. Checks run real commands, never in a sandbox; review them before approval. Nothing here authorizes a release: trusted CI, protected branches and independent forge review do. Never invent a reviewer or approval, and never hand-edit generated records.

## Start a run

Run scripts from any working directory; paths resolve from the script location.

```sh
python3 _system/scripts/sdlc.py new fix-example --kind bug
python3 _system/scripts/sdlc.py status fix-example
```

The default `light` profile creates `brief.md` (problem, acceptance criteria, approach, risks, verification) and `checklist.json`. A human picks `--profile full` for consequential architecture or policy changes; it creates intent, spec and plan. Only runs created by `new` are supported, and text approval tags are not evidence.

Without a confirmed seed, review the checklist commands and offer a dry run before definition approval (seed-bound runs use the [seed definition comparison](#seed-definition-comparison)):

```sh
python3 _system/scripts/sdlc.py dry-run fix-example
```

It runs every checklist `check` in the repository root with its timeout, replacing `runs/<slug>/checklist-dry-run.log` with launch errors, timeouts and nonzero exits per item. No check process survives its check: each starts in a new session, and on normal exit or timeout the rest of its process group is killed and reaped. Tell an expected reproduction failure from a broken command or environment (such as an unsuitable `TMPDIR`), fix the latter and rerun. The dry run records no decision, mark or receipt, does not enforce a new approval prerequisite, and does not replace `verify`, which still requires current approval and passing checks.

After the human accepts the brief, record the actual review reference:

```sh
python3 _system/scripts/sdlc.py decide fix-example 3 approved \
  --reviewer 'Reviewer name' --source 'Actual review URL or decision reference' \
  --reason 'Accepted scope, approach, and acceptance criteria'
```

Light runs have ONE definition gate, recorded as stage 3: `brief.md` + `checklist.json` are the intent, design and plan. Full runs record stages 1, 2 and 3 in order. `changes-requested` records rejection with the same command. Decisions bind their artifacts and contract inputs; edits stale them.

`decide` and seed-judge `mark` are human-only: they refuse a reviewer starting with `Agent`, containing `not human`, or an agent actor (`--actor`, or run metadata). Older agent records stay readable, labelled `agent-recorded`, and never count as human acceptance.

## Checklist

`runs/<slug>/checklist.json` holds item definitions only: `{"schema":1,"items":[{"id","description","verify","check"?}]}`. They are approved scope: stage 3 cannot be approved with zero or invalid items, and changing them stales that approval. Never add status fields. If the file is deleted, status reads MISSING and verification refuses until it is restored from Git.

- **Checked items** carry `check`: `{"argv":[...],"timeout_seconds":N}`. `verify` runs them in the repository root after the configured checks, logging to `test-log.md`; a failure fails verification (`Checklist check failed: <id>`). A pass proves the item, so `mark` is refused for it.
- **Attested items** have no `check`: `sdlc.py mark <run> <item-id> passed|failed --evidence <text>` appends a receipt under `marks/` (needs current stage 3 approval; latest wins; `verify` requires `passed`). Verification binds each latest mark, so mark first, then verify. Attestations are self-reported claims for review to challenge. Status flags a mark made on an older candidate; re-check it if the change affects it.

When every command passes but attestations are missing, `verify` exits nonzero with `Checklist incomplete`, the mark commands to run, and `result: awaiting-attestation` with `pending_attestations`. That is machine proof only, not a pass. It stays current while its proof inputs and log match (evidence-only commits keep it); a mark or changed input retires it. An item a person marked `failed` makes the result `failed`. Record the actual observations, then rerun `verify`.

### Locked seed acceptance checks

At stage-1 intake, use the explicitly supplied session and a trusted Tinkery executable:

```sh
python3 _system/scripts/sdlc.py stage <slug> 1 \
  --seed-contract <session>/seed-contract.md \
  --tinkery <path>/tinkery
```

Tinkery is the single seed verifier. Intake runs exactly `PATH seed status --json DIR` and `PATH seed checks DIR` (DIR is the seed's session directory) and reads status again to detect changes. Tinkery verifies human v2 and agent v3 confirmations against the seed; intake accepts v3 only with `operator: agent` and an agent `actor`, which records agent confirmation, never human confirmation. Missing executables, failed commands, malformed output, simulated sessions, missing confirmation and changed bindings refuse before any write. Supply trusted toolchain code, never a seed-provided script. Verification proves content binding, not operator identity or implementation approval.

Intake copies the confirmed seed and its records into locked `runs/<slug>/seed-checks.json` items with reserved `seed-N` IDs; run metadata keeps the confirmation receipt and the original definitions' digest. Seed Markdown is never read as checks. Imported items are **LOCKED, seed-confirmed** definitions, distinct from the worker's `checklist.json`. Never silently weaken or remove acceptance: such edits are departures. Deleting the file or changing the run-local seed refuses verification. Later edits to the original session do not reach the run-local snapshot; to rebind, run verified intake again; rebinding replaces activations, and prior evidence stays in Git.

#### Seed definition comparison

Stage 3 compares effective checks with the intake baseline. With no added, removed or changed checks, and no activation other than the confirmed proposal, it passes without another human question and records `matches confirmed seed (sha256: …)` as evidence, not a human decision. This is structural, not a claim about the brief or permission to implement. An explicit human `changes-requested` stays blocking until a later human decision supersedes it.

Any departure blocks automatic passage. Status lists each changed field, removed or added check, added worker item and non-proposal activation, and asks only `Accept these departures from the confirmed seed?`. `decide … 3` records the human answer bound to those exact inputs. Full-profile stages 1 and 2 keep their own reviews. Legacy bindings without a stored baseline compare against the intake digest; if their definitions changed, rebind rather than invent a baseline.

Seed record kinds:

- **`cmd`** runs verbatim through `bash -c` with a 120-second timeout; `cwd` is relative to the checkout (absolute paths, traversal, backslashes, drive letters, NUL and CR/LF refused). Failures and timeouts fail `verify` even when all worker checks pass. **Passing output proves a `cmd` or activated proposal entirely; no `seed-N-expect` mark is owed.** `expect` is descriptive context, not a matcher.
- **`judge`** (prose or taste) needs a manual human attestation, judged by `how_to_judge` or else the statement, never an automated pass. Record only the actual operator's observation:

  ```sh
  python3 _system/scripts/sdlc.py mark <slug> seed-1 passed \
    --reviewer 'Actual operator' --source 'Actual observation reference' \
    --evidence 'Observed output and why it satisfies the seed expectation'
  ```

  Evidence already used for another item is refused unless `--covers-all` says the operator meant it for each. Mark `unknown` when the observation does not address the expectation; `unknown` blocks verification. Marks need current definition approval and bind the exact item.
- **`provisional`** names its prerequisite in `missing`; `cmd` is an optional proposal. It stays pending and nonblocking, never proven, while the prerequisite is absent. At implementation time, inspect the prerequisite. **Once it exists**, add only an `activation` object to that item in `seed-checks.json`, keeping the proposal intact:

  ```json
  {"activation": {"cmd": "./real-command", "cwd": ".", "evidence": "Prerequisite exists at <path>; observed <evidence>"}}
  ```

  Activating the proposed `cmd` in its `cwd` matches the confirmed seed (evidence adds ``seed-N activated as confirmed: runs `<cmd>` ``) and still must run green; any other command is a departure for the operator. Verify refuses evidence citing a relative file or `dir/` that exists in neither the run nor the checkout, so save cited logs first. A prose prerequisite is not machine-detected: surface it when it becomes true.

Receipts report `seed_checks` (`proven`, `attested`, `pending`, or `not-run` on failure) separately from `checklist_items`. A passing verify's `next_action` still names each pending proposal.

## Implementation and verification

Give each code-writing run its own Git worktree or clone, never just a second branch in one checkout. Ports, databases and credentials need their own isolation when used.

Configure `_system/verification.json` with nonempty argv arrays and timeouts. A fresh install has no checks and fails until real tests, build and lint are selected; the runner should reject zero tests, since an exit code alone proves no coverage. Optional keys:

- `"setup": {"argv": [...], "timeout_seconds": N}` prepares ignored dependencies, such as `["pnpm", "install", "--frozen-lockfile"]`. `stage <run> 3` and `5` run it from the committed file in each new worktree; a failure removes the worktree and opens no stage. Keep its output Git-ignored.
- `"authors": {"since": "<commit>", "allow": ["Name <email>", "Claude session_* <noreply@anthropic.com>"]}` makes verify refuse any later commit whose author matches no pattern.

```sh
python3 _system/scripts/sdlc.py verify fix-example
```

Verification writes the log (`runs/<slug>/04-test/output/test-log.md`, named in any failure) and a receipt binding the commit, tracked and nonignored untracked files, artifact inputs, policy, test lock and log digest.

- `runs/` is outside the fingerprint: keep source and test infrastructure out of it. Committing run evidence does not stale code.
- Submodules and untracked nested repositories count by checked-out commit only. Ignored files and external services are outside it: pin them in CI.
- Generated `tink:rules` blocks in the root `AGENTS.md` are excluded from the candidate fingerprint; all other `AGENTS.md` text counts.
- Commit before final verification and leave the checkout alone while checks run; if it changes, the error lists up to five paths.

A preexisting log is never passing evidence, and a failed or interrupted run never reuses an older pass.

## Bug reproduction baseline

Before fixing a bug, show that a meaningful reproduction fails for the expected reason on the unfixed code, and have an independent reviewer accept it. Then record it:

```sh
python3 _system/scripts/sdlc.py lock-tests fix-example tests/test_regression.py \
  --source 'Actual accepted test review reference' \
  --failure-evidence 'Actual failing CI job or retained reproduction evidence'
```

Lock fixtures, snapshots, discovery configuration and runner helpers too. Bug runs cannot verify without a lock; changed or missing locked files fail. `--reproduce '<JSON argv>'` runs the reproduction at lock time and refuses the lock if it passes; without it, `status` says the failure was not run locally and CI must confirm it. A mistaken baseline needs an independently reviewed replacement run, linked to the old one; tests are never unlocked silently. Strict protection needs CI to fetch the accepted test revision independently and require its check before merge.

## Tinkery ownership and handoff

SDLC can operate standalone or as Tinkery's external implementation workflow.
SDLC owns run identity, implementation orchestration, technical review and local
verification freshness. Tinkery owns the seed and its discovery decisions.
Tinkery owns the intended goal-to-result presentation, not another set of SDLC
approvals. The operator judges usefulness and authorizes merging. Technical review
asks whether the change is correct and meets approved scope; Taste asks whether
the exact result serves the unchanged operator goal. A model's praise is neither.

For a Tinkery-originated run:

- **Load:** Load only the explicitly supplied, verified confirmed seed for seed
  intake; never discover operator sessions to fill a gap. Also read the active
  contract's immediate inputs, approved brief or spec/plan, checklist, relevant
  project policy, and repository source and project files needed by the active stage.
  Carry forward the seed's Problem statement, Proposed outcome (desired user-visible
  results and success criteria), Acceptance criteria, and Constraints and boundaries
  (non-negotiables, exclusions and rejected alternatives). Preserve settled checks
  verbatim and provisional ones as proposals. Preserve Accepted decisions and assumed
  defaults with their separate authority, plus evidence, uncertainty, open questions
  and deferrals. Keep the confirmed goal intact. Seed confirmation is not
  implementation permission. Never infer a goal-to-run connection from matching names,
  paths or seed bytes.
- **Hand off:** Identify the run ID and checkout, bound seed reference/digest,
  candidate commit and PR/result revision. Link each claimed completion condition to
  evidence for that result; name unresolved conditions and findings. Keep automated
  proof, manual attestation, independent review and operator acceptance separate. The
  handoff is human-readable only, not a normalized machine interface. API 1 exposes
  run_id, the seed_contract binding and verification candidate provenance, but not an
  explicit PR reference. Use structured status for the seed binding, not this prose.
  A binding is evidence of intake, not approval, goal identity or release authority.
  Copying a handoff records no verdict and starts nothing; recheck current state
  before recording any external decision.
- **Retain:** Keep the seed copy/binding, approved artifacts, checklist, decision and
  mark history, verification receipts/logs, review findings and closure references
  before removing a checkout, with their provenance; name absent facts. Missing, stale,
  failed, interrupted and conflicting evidence stay distinct. A reader cannot recover
  records deleted with a worktree.

Do not create a Taste verdict format or a competing acceptance ledger. Goal/run binding,
the shared reader and Taste are Tinkery's work. When changing that integration, consult
Tinkery's [constitution](https://github.com/jon-devlapaz/tinkery/blob/main/docs/yohaku.md)
and [job-record contract](https://github.com/jon-devlapaz/tinkery/blob/main/docs/design/job-records.md);
proposals grant no implementation authority.

## Who does what at a stage boundary

- The launcher (the human, or a script acting for them) records the gate, then runs
  `python3 _system/scripts/sdlc.py stage <run> <n>` (`--check` previews; `--worktree PATH` or `--here`
  picks the checkout; stage 1 takes `--seed-contract PATH --tinkery PATH`). Only a worktree
  launch (stages 3 and 5 unless `--here`) commits `runs/<run>`; other launches commit nothing.
  It creates the worktree under `~/dev/worktrees/<repo>/` (stage 3: branch `<run>`; stage 5: detached
  `review-<run>`), runs `tink use <skillset> --snapshot runs/<run>/<stage-dir>` there, and prints the
  prompt for a NEW session. It refuses unless entry gates are approved and current (stage 5 also needs
  current verification). Stage 4 runs inside the stage-3 session.
- The agent never creates the run, never runs `tink use`, and never approves. It reads
  `AGENTS.md`, its stage `CONTEXT.md`, and `python3 _system/scripts/sdlc.py status <run>`.
- Build order in stages 3 and 4: implement, `sdlc.py mark` the attested items, commit before verifying,
  then `python3 _system/scripts/sdlc.py verify`.
- The reviewer works in a separate checkout, writes `runs/<slug>/05-deploy/output/REVIEW-findings.md`,
  and changes no code or evidence. The candidate's owner brings the findings to the PR branch.

## Pinned build steps

`stage` prepares a checkout and prints a prompt; it never starts an agent. For an explicitly
authorized build agent, open stage 3, then, from that checkout, take these deliberate steps one at a time:

```sh
python3 _system/scripts/sdlc.py step <run> build prepare \
  --report /absolute/run-notes/build-report.md \
  --model provider/exact-model-id --launcher /absolute/trusted-adapter [--max-responses N]
python3 _system/scripts/sdlc.py step <run> build show
python3 _system/scripts/sdlc.py step <run> build launch
python3 _system/scripts/sdlc.py step <run> build resume
python3 _system/scripts/sdlc.py step <run> build complete
python3 _system/scripts/sdlc.py step <run> build complete --stopped-by operator  # or cap
```

- `prepare` writes an immutable `runs/<run>/steps/<name>.json` binding the checkout, the exact model,
  the launcher's path and SHA-256, and a fresh absolute report path whose parent exists. It starts nothing.
  It refuses existing names or reports, relative paths, model aliases, and a run branch checked out
  elsewhere. Contracts are not portable: prepare a new step in a new checkout.
- `--max-responses N` caps responses; an adapter that cannot enforce it refuses, so a cap is never ignored.
- `launch` and `resume` recheck every definition gate and resolve the exact model offline first. The
  prompt names the approved inputs, `stages/03-build/CONTEXT.md` and the report path; limits writes to
  the checkout and report; and forbids `sdlc.py step`, `stage` and `decide`. Inside the session
  `TINK_SDLC_STEP_SESSION=1` makes `step` refuse everything but `show`. Resume keeps the same session.
- `complete` needs a nonempty report at the exact path and the adapter's offline confirmation that the
  agent's latest turn ended on the pin. A paused, failed or running agent leaves the step `active`.
  `--stopped-by operator|cap` ends the step's process group and records it `stopped`, never as an ended turn.
- `show` adds `observed_turn` (`ended`, `not ended` or `unknown`) from an offline probe.
- A crash or failed admission leaves the step `uncertain`, never retried. Inspect the real session and
  reconcile it before preparing another step; never hand-edit the contract.

A report is not proof of passing checks, authorship or approval; `verify` and forge gates stay independent.

Bundled adapters (their module comments state what each promises and refuses):

| Adapter | Pin | Notes |
|---|---|---|
| `_system/scripts/pi-launcher` | `provider/model-id:thinking`, e.g. `openai-codex/gpt-5.6-luna:low` | The model must be in Pi's local catalog (run `pi update --models` yourself) with a stored credential. Enforces `--max-responses`. |

Resume pinned steps only through `step … resume`: pi-herdr's resume drops the pin. Another launcher
must implement the [launcher protocol](https://github.com/jon-devlapaz/tink-sdlc/blob/main/references/launcher-protocol.md)
and pass offline-resolution tests before it gets a live-call budget.

## Stage skills

Each stage has a skillset pin committed at `.tink/skillsets/<name>-skillset.json`: 01-plan `planning-skillset`, 02-design `design-skillset`, 03-build `build-skillset`, 04-test `testing-skillset`, 05-deploy `deployment-skillset`, 06-maintain `maintenance-skillset`. A pin selects exact upstream code, so review a pin change like a lockfile change.

```sh
tink library fetch .tink/skillsets/<name>-skillset.json    # once per machine/library, after reviewing the pin
tink use <name>-skillset --snapshot runs/<slug>/<stage-dir>  # at stage open, then start a NEW session
tink-route --receipt runs/<slug>/skills.jsonl "<what you need>"
```

`required` lists the disciplines `tink use` compiles into `AGENTS.md` at stage open. Capability skills come only through `tink-route` for genuine gaps. It searches the whole library (`--anywhere`, the default; `--skillset NAME` restricts it, and exit 1 may then add `Hint: <skill> fits but is on another shelf`). Exit 1 or 2 means continue without a skill. Keep its receipt with the run and read the delivered skill before relying on it. `tink-route` mounts into the git-ignored `.tink/.active` and changes no project skills.

Commit `.tink/skills.toml` and `.tink/skills.lock` when Tink creates them through an authorized operation; restore them into each worktree through Tink, never by symlink. Run `tink` mutations through the wrapper, which serializes them on this host and rejects symlinked skill state:

```sh
python3 _system/scripts/sdlc.py skills tink -- skill check
```

Tinkery's verified confirmed seed contract is the input to 01-plan (bind it as in [Locked seed acceptance checks](#locked-seed-acceptance-checks)). `tink use` and `tink-route` are optional; runs work without them.

## Recovery and release

Runs have one writer at a time; a busy or interrupted operation fails with its lock path. After a crash, confirm no process owns the operation, remove the lock directory and rerun.

Open the PR once the candidate is committed and verification is current (end of stage 4; `stage <slug> 5` enforces it). Delivery and merge authority belong to `stages/05-deploy/CONTEXT.md`. Keep deployed revision, result and rollback references in the deployment system.

Record the run's ending:

```sh
python3 _system/scripts/sdlc.py pr <run> <PR URL>   # needs current verification; one PR per run
python3 _system/scripts/sdlc.py label <run> --kind correction|none|accepted|rejected --words TEXT \
  --reviewer NAME --source REF [--about FILE|outside] [--consent local|shareable]
python3 _system/scripts/sdlc.py close <run> --outcome merged --merge <full commit>   # or --outcome abandoned
```

Put `Tink-Run: <run_id>` in the PR description. Labels are human-only (refused inside a build step session), keep the words verbatim, and chain to each other by digest; `none` records no corrections and `outside` a correction outside the run's records. `close` needs a label (and, when merged, the recorded PR); a run closes once. While the run builds in another checkout, these refuse and name it. Closing in a linked worktree copies the run's records into the main checkout's `runs/<run>/` (refusing if that folder has uncommitted changes or another run); commit them there.

Maintenance is optional intake. Enable it only with metric thresholds, deduplication, cooldowns and run limits; alerts create drafts for triage, never approvals.

## Machine interface (API 1)

Clients read `python3 _system/scripts/sdlc.py capabilities --json` and `status [<run>] --json` from the selected checkout's runtime, check `protocol`, `api_version` and capabilities first, and fail closed on unknown states, never falling back to the human CLI output. The field contract is in [references/api-1.md](https://github.com/jon-devlapaz/tink-sdlc/blob/main/references/api-1.md).

## Maintaining the scaffold

`python3 _system/scripts/sdlc.py walk [--json]` is a read-only structural check that a memoryless agent can orient from the files: router size, resolving pointers, stage contracts naming Inputs, Output and a human Gate, per-stage context size, skillset pins, `status` for every run, and current `tink:rules` blocks. A real walk by a fresh agent stays the test.

Preview, then apply an upgrade (`_system/scaffold.json` is the trust baseline):

    python3 <package>/scripts/init.py <target> --upgrade --check
    python3 <package>/scripts/init.py <target> --upgrade

Unmodified managed files are updated, restored or removed; project-owned files (`_system/verification.json`, `.tink/skillsets/*.json`) are never overwritten. A customized managed file blocks the upgrade and is listed with a `diff`: merge by hand, or commit and rerun with `--overwrite-customized`. Only the `AGENTS.md` router block is replaced; `runs/` is never modified, but changed stage 1-3 contracts stale existing approvals. Downgrades are refused, and an interrupted upgrade is safe to rerun. A killed install can leave a partial one: inspect it and its lock, confirm no writer remains, and recover through a reviewed migration, never a forced overwrite. Never update factory files during an active feature run. Afterward run `walk`.

Templates live in `_shared/` (`brief-template.md` is the light-profile edit surface); run outputs go under `runs/`. Read the active contract and its immediate inputs, not the whole factory.
