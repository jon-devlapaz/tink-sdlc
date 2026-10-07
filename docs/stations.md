# Stations: the launcher loads the stage's skills, the model does not

A **station** is one SDLC stage (`01-plan` ... `06-maintain`). Its `TRIGGERS.json` is a short table that says
*when this signal appears, this skill is needed.* Jev (`tink-route`) turned each trigger's one-line **need** into a
skill, three times, and the answer is frozen in `TRIGGERS.lock.json`. At run time nobody asks the router and the
agent never chooses a skill: `sdlc.py` reads the run's diff and tags, fires the triggers, and records which locked
skills to load and why.

```
stages/04-test/
  CONTEXT.md             what the stage does
  TRIGGERS.json          humans write this: signal -> need -> expected skill
  TRIGGERS.lock.json     `stations.py lock` writes this: what Jev resolved, 3 votes each
.tink/
  pool.lock.json         every skill a station may pull: repo, commit, path, tree digest
  pool-overlay.json      routing descriptions for skills a router cannot otherwise match
runs/<run>/skills/
  stage-4-pulls.json     the pull sheet for one run: what fired, what was pulled, what was skipped and why
```

The tool is `_system/scripts/stations.py` (standard library, Python 3.9+). The tables, their locks and the two
pool files are project-owned: an upgrade installs new ones but never overwrites yours. In kitchen terms: the stage
is a station, `TRIGGERS.json` is the station's card, Jev is the pass, the lock is what the pass signed off, the pull
sheet is the ticket on the rail, and the pool lock is the pantry inventory.

## When pulls happen

- **Stage open** (stages 1, 2, 3, 5, 6): `sdlc.py stage <run> <n>` evaluates the stage's table in-process, prints
  the sheet, writes `runs/<run>/skills/stage-<n>-pulls.json` (committed with the run when the launcher creates a
  worktree), and names the pulled skills in the launch prompt. No router call, no network. A missing or broken
  table, or any other failure, prints one `pulls: skipped (...)` line and the stage still opens.
- **Stage 4**, which has no stage open, and any re-run: `python3 _system/scripts/sdlc.py pull <run> <n>`.
- **The diff** is the committed change from the merge-base of `HEAD` and the default branch (`origin/HEAD`, else
  `main`, else `master`) to `HEAD`. When no merge-base can be found, diff triggers are skipped with one line saying so; tag and
  `always` triggers still run.
- **Tags** are facts a human states when creating the run: `sdlc.py new <run> --tag web-ui --tag ios`. They are
  stored in `run.json`. A tag that no table uses is refused with the list of known tags (the union of all
  `profile` lists), so a typo cannot silently never fire.

## `TRIGGERS.json`

```json
{
  "version": 1,
  "stage": "04-test",
  "max_pulls": 5,
  "max_src_files": 40,
  "triggers": [
    {
      "id": "form-input",
      "why": "The diff adds form fields; people will type garbage into them.",
      "need": "users will type garbage into every field; find what breaks",
      "expect": "break-ui",
      "when": {"paths": "\\.(tsx|jsx|html|vue|svelte)$", "added": "<input|<form|<textarea"}
    }
  ]
}
```

| Key | Meaning |
|---|---|
| `version` | Required, must be 1. |
| `stage` | Required, the stage directory name. |
| `max_pulls` | Optional, at most this many skills per pull (default 5). Earlier triggers win. |
| `max_src_files` | Optional, a diff touching more source files skips diff triggers (default 40; guards bulk imports). Tag and `always` triggers still run. |
| `ignore` | Optional regexes of paths that never count (default: `.agents .claude .tink runs node_modules vendor docs dist build` folders, `*.lock`, `lock.json`, `SKILL.md`, and the scaffold's own `_system/`, `_shared/` and `stages/0N-*/` files, so installing it on a branch does not count as a wide change). |
| `triggers[].id` | Lowercase-with-dashes, unique in the file. |
| `triggers[].why` | One sentence a stranger can read. |
| `triggers[].need` | What Jev is asked, 8 to 200 characters, about the capability. |
| `triggers[].expect` | The skill the author intends; must exist in the pool. |
| `triggers[].when` | The signal, below. |

Unknown keys anywhere are errors. `stations.py validate` names every problem at once.

### `when`: a closed vocabulary, every key must hold

| Key | Meaning |
|---|---|
| `"always": true` | Fires every time. Use sparingly, for what every run of the stage needs. |
| `"profile": ["ios"]` | Fires if any listed tag is one of the run's tags. |
| `"paths": "regex"` | Changed source files matching; needs `min_files` (default 1) of them. |
| `"added": "regex"` | Matches **added** diff lines in the files selected by `paths` (or any source file); needs `added_min` (default 1) lines per file. |
| `"deleted_min": N` | At least N source files deleted. |
| `"dirs_min": N` | At least N distinct top-level folders touched. |

Adding a key to this list is a format change: change `stations.py`, with a test.

### Writing a good `need`

Describe the *situation or capability* in plain words, the way someone in the middle of the work would say it
("the same migration might run twice and must not double-apply"). Do not name skills, paste code, or describe a
stage. Test it with `lock`: if Jev disagrees with `expect`, rewrite the need or fix the skill's routing
description; do not change `expect` to match. Keep each trigger narrow: aim for under about 15% of commits.
`principle-*` disciplines belong in the stage skillset's `required` list, where they load every time.

## `TRIGGERS.lock.json` (generated, committed, reviewed)

`stations.py lock` asks Jev 3 times per need (`tink-route --pick --json --anywhere --library DIR -- "<need>"`) and
records per trigger: `skill`, `votes`, `min_probability`, `stable`, `matches_expect`, `need_sha` (first 16 hex of
the need's sha256), plus the file-level `pool` (the same hash of `.tink/pool.lock.json`). It refuses to write if
any trigger is unstable (not 3 of 3 at probability >= 0.85) or disagrees with `expect`. A pull uses an entry only
if it is `stable`, `skill == expect`, and `need_sha` still matches; otherwise the trigger is skipped and says why.

## Commands

```sh
S=_system/scripts/stations.py
python3 $S validate stages/*/TRIGGERS.json --pool-lock .tink/pool.lock.json   # format + skills exist; offline
python3 $S lint     stages/*/TRIGGERS.json --pool-lock .tink/pool.lock.json   # locks match tables and pool; offline (walk W8)
python3 $S pool build --lock .tink/pool.lock.json --overlay .tink/pool-overlay.json --dest ../pool
python3 $S lock  stages/*/TRIGGERS.json --library ../pool --pool-lock .tink/pool.lock.json   # live Jev, writes locks
python3 $S check stages/*/TRIGGERS.json --library ../pool --pool-lock .tink/pool.lock.json   # live Jev, writes nothing
python3 $S pull  stages/04-test/TRIGGERS.json --tags web-ui --out /tmp/pulls.json            # what sdlc.py pull does
```

The router command is `$STATIONS_ROUTE_BIN`, else `tink-route`. Only `lock` and `check` call it, and only with the
fixed `need` strings. Exit codes: 0 ok, 1 drift, unstable or stale, 2 invalid file or tool error. A pull never
blocks work; a skipped trigger is information.

## The pool: what Jev routes over

`.tink/pool.lock.json` pins every skill the stations may pull (71 from `emilkowalski/skills`, `humanlayer/skills`,
`cursor/plugins` pstack). `pool pin --sources NAME=PATH[#SUBDIR] ... --out pool.lock.json` records each skill's
source, commit, path and tree digest from local clones (`#pstack/skills` limits a source to a folder).
`pool build` clones the pinned commits, verifies every digest, applies the overlay, refuses a non-empty
destination, and refuses to write inside `~/.tink-library`.

`.tink/pool-overlay.json` maps a skill name to `{"description": "..."}`: a routing description that replaces the
upstream description in the built copy only. Most pstack skills say "only use when invoked by name", which a
router cannot match. Add an entry only after `tink-route --pick --json -- "<the skill's own description>"` fails
to return the skill; write what it does in the words of the moment it is needed.

Jev's answers depend on what else is in the pool, so each lock records the pool hash. Changing the pool (a skill
added, a commit bumped, an overlay edited) means rebuilding it and running `check`; `sdlc.py walk` (W8) flags
every lock made against a different pool.

## Why it is shaped this way (evidence, not taste)

From about 4,300 live `tink-route --pick` calls (router-lab `REPORT.md` and `DESIGN-stations.md` in the factory repo):

| Finding | Consequence |
|---|---|
| Whole stage documents found the right specialist 10% of the time; a 1,000-character slice 5%. | Never send documents. The old whole-document pick is removed. |
| A short capability sentence about a moment: 25 of 39 exact and stable; 26 of 28 right at probability >= 0.85. | Triggers are moments, written once by a human. |
| The same query sent 5 times changed winner on 7 of 24 documents. | Freeze answers in a lock: 3 of 3 at probability >= 0.85. |
| Broad rules ("tests changed") fired on 37 to 45% of commits. | Keep triggers narrow; skip diff triggers on bulk diffs. |
| 9 overlay descriptions lifted self-retrieval from 67 to 69 of 71 skills. | Fix invisible skills in the overlay, not upstream. |

Limits: the triggers were written by the author and judged by eye on 396 commits from 17 repositories (UI is
under-represented; the Swift and migration rules never fired). Whether loading these skills improves finished
work is untested; the first real runs start to answer that.

## Boundaries

- **Privacy.** The only text ever sent to the router is the fixed `need` strings, by `lock`/`check`, run by a
  human or a scheduled job. No diffs, seeds, briefs, or code.
- **No model in the selection.** Not to write needs, not to notice moments, not to pick among results.
- **Advisory.** A pull recommends loading a skill; the human sees the sheet. Nothing here approves a stage or
  changes a gate. Stage skillsets (`.tink/skillsets/`) and `tink-route "<capability gap>"` for ad hoc needs are
  unchanged.
