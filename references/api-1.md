# tink-sdlc machine interface (API 1)

The field contract for clients of `sdlc.py capabilities --json` and `status --json`, such as Tinkery. Workers do not need it; the installed guide `_system/SDLC.md` points here.

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
`capabilities` lists `status-json`, `verify`, `mark`, `log-file`, `pr`, `close`, and `label`, and
from scaffold 1.58.0 `words`: every word the runtime can put in `verification_status`, `verification.result`,
`gates[].status`, `checklist[].status`, `checklist[].proof`, `steps[].state`, `steps[].turn` and
`steps[].stopped_by`, keyed by those names. Clients must check the identity, version, and
capabilities before enabling actions. Extra fields
and capabilities are additive; removing fields or changing their meaning requires
a new API major. Unknown critical states, malformed responses, and unsupported
majors must fail closed, never fall back to parsing the human CLI.

`new` stores a random UUIDv4 `run_id` in `run.json`; SDLC operations, seed rebinding
and worktree copies preserve it, and recreating a deleted slug creates a new one. The slug
and workspace locate a run; `run_id` identifies its creation. It must be a canonical
lowercase, hyphenated UUIDv4, or the run is refused. The ID is not approval or tamper protection.

From scaffold 1.53.0, a client saving a label from an earlier reading passes
`label <run> --expected-run-id <reviewed UUID>` along with the label fields. The runtime
compares the ID under its run-creation and writer locks before appending; a different
run refuses without a label. Omitting the option keeps the older name-only behavior.

`status --json` returns `runs`: summaries with `slug`, `run_id`, `profile`, `kind`,
`seed_contract`, `verification_status`, `next_action`, `has_lock`, and `checklist_summary`
(`total`, `passed`). An unreadable run instead has `verification_status: "invalid"`,
`error`, and an empty summary; it does not hide valid runs.

When Git records another checkout on branch `<run>` that holds `runs/<run>`, both status
forms read that run there, with this checkout's own code, so a stage-3 build's verification
shows from the main checkout. The run then adds `checkout` (that path), `next_action` starts
`In <path>:`, and `actions` and `log.path` refer to that checkout.

`status <run> --json` returns `run`:

- `slug`, `run_id`, `meta` (`profile`, `kind`), and `gates` (`stage`, `status`, `blocked`).
  Gate states are `approved`, `pending`, `stale`, or `changes-requested`. Optional
  `attribution: agent-recorded` discloses an invalid human receipt; optional `evidence`
  names an independent confirmed-seed match. Seed-bound views add `definition` with
  `departures`, `question` (null for equality or current accepted departures), and `evidence`.
- `pr`: the recorded PR (`url`, `candidate`, `time_ns`) or null; `closure`: `outcome` (`merged` or
  `abandoned`), `merge` for merged runs, and `time_ns`, or null. Both appear in run summaries too.
  Malformed records invalidate the run. `labels`: `count`, `kinds` (count per kind) and `outside`
  (labels about work outside the run), also in run summaries.
- `seed_contract`: `path`, `sha256`, and `source` from run.json, or null
  when unbound (absent or null in run.json). Both status forms expose this binding;
  human run status adds one `Seed contract:` line when bound. A binding must be an
  object with nonempty strings for all three fields and a lowercase 64-character
  SHA-256 hex digest, plus the existing record-intake marker `definitions_sha256`.
  Malformed bindings invalidate the run. Reads never adopt, repair or rewrite a
  binding or require its original source to remain available.
  For agent v3 intake, both status forms additionally expose `confirmation`: the
  unchanged receipt, including `operator: agent` and its caller-declared `actor`.
  Human v2 and legacy bindings retain the three-field status shape.
  This is intake evidence, not approval, goal identity or release authority.
- `verification_status`: `blocked`, `running`, `not-run`, `current`, `stale`,
  `failed`, `interrupted`, or `awaiting-attestation`. The last means command proof
  is fresh but required attestations remain incomplete. It becomes `stale` when
  proof inputs or the log change; definition gates and active writers still take
  precedence. `verification` is null or a normalized record with `result`, `passed`,
  `candidate`, `log` (digest), and `error`. An `awaiting-attestation` result also
  includes `pending_attestations`, a nonempty list of item ids from that invocation,
  even after the proof becomes stale. Its `passed` is always false. Only
  `passed: true` means current passing verification; receipt internals are not a
  client contract. Older API 1 clients must fail closed on the new status word.
- `checklist`: normalized items with their definition, `automatic`, `status`
  (`pending`, `passed`, `failed`, or `unknown` for a seed attestation), `proof` (`automated`, `attested`), optional
  observation `mark`, and `needs_recheck`. `checklist_state` distinguishes
  `defined`, `empty`, `missing`, and `invalid`.
- `actions.verify` and `actions.mark`: `allowed` (boolean) and `reason` (empty
  when allowed). Verification also has `timeout_seconds`, a total execution budget
  or null when blocked. These are UI hints, not authorization: `verify <run>` and
  `mark <run> <item> passed|failed --evidence <text>` recheck their own rules.
- `artifacts` (named text), `decisions` (normalized stage, nanosecond timestamp,
  decision, reviewer, source, reason, and opaque id), with optional actor, attribution,
  automatic basis, seed evidence and departure snapshot. Decision values are `approved`
  or `changes-requested`; reviewer, source, and reason are strings. Known malformed or
  missing fields invalidate that run rather
  than being coerced into display text. Also included: `verification_config`
  (display-only checks/policy or null), `errors`, `next_action`, and `cli_status`
  (display-only human output). Human and machine status share one calculation.
- `steps`: each pinned build step by `name`, with its `state` (`prepared`, `active`,
  `uncertain`, `completed`, or `unreadable`) and, when active, `turn` (`ended`, `not-ended`
  or `unknown`) with the same `observed_turn` words as `step show`. Read `turn`; the words may change.
  An uncertain step adds `owed`: true while it is the newest step and so still owes reconciliation. An active step's turn, or else the newest step when it is uncertain, sets `next_action` (after gates and locks), so a run that nothing is building
  is never mistaken for running work, and one that may be is never mistaken for idle. Older runtimes omit the field: absence means unknown.
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
