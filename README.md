# AI-Native SDLC

Install an ICM-inspired local workflow into an explicitly selected Git checkout.
Editable Markdown holds intent and plans; JSON receipts track local decisions and
verification evidence. CI, authenticated approval, deployment, and monitoring remain
project integrations. Content hashes detect changes; they are not signatures.

The stages follow Anthropic's [AI-Native SDLC Playbook](references/ai-native-sdlc/README.md).
Earlier proposals, evaluations and dogfood seeds were removed from the tree after they
were superseded; they remain in Git history before the `lean-orphan-docs` merge.

## Install and configure

From this skill collection:

```sh
tink skill add jon-devlapaz/tink-sdlc
python3 .agents/skills/ai-native-sdlc/scripts/init.py . --check
python3 .agents/skills/ai-native-sdlc/scripts/init.py .
```

The initializer installs stage contracts, templates, runtime scripts, and the operator
guide at `_system/SDLC.md`. It preserves existing project instructions and refuses
conflicting scaffolds. Existing installs upgrade explicitly with `--upgrade` (preview with
`--upgrade --check`); see `_system/SDLC.md`.
Use a separate worktree or clone for code-writing runs.

**Verification starts unconfigured.** Set `_system/verification.json` to real project
checks before running verification. Tink integrity checking is optional and explicit.
Git needs an initial commit before verification. The runtime requires Python 3.9+,
Git, and Bash on a POSIX system; native Windows operation is not supported.

## Operate a run

```sh
python3 _system/scripts/sdlc.py new feature-name
python3 _system/scripts/sdlc.py status feature-name
python3 _system/scripts/sdlc.py mark feature-name item-id passed --evidence 'what was observed'
```

Edit the generated brief and obtain actual human acceptance before recording the
review decision. Follow the installed guide for the complete decision command,
reproduction baseline for bug fixes, rejection/rework, and verification. Do not
paste fictional reviewer names or evidence into real runs. The full profile separates
intent, spec, and implementation plan; the light profile uses one reviewed brief.


Local status accepts unchanged candidate content after an evidence-only commit.
A release still needs CI for the actual merge revision and independent forge approval.

## Machine clients

Scaffold 1.19.0 introduces API 1: `sdlc.py capabilities --json` and
`sdlc.py status [run] --json`. Human and JSON status share one calculation. Clients
consume normalized run state, available actions, and declared log references rather
than parsing prose or receipt internals. The versioning and field contract lives in
`assets/_system/SDLC.md`; API versions are independent of package versions.

## Maintain this package

`assets/` is the canonical distributable scaffold in this repository. Do not update
it from an external sandbox generator. After an intentional payload change, choose
a new numeric `X.Y.Z` version greater than `assets/manifest.json`'s current version
and refresh the content manifest. Set `RELEASE_VERSION` to that chosen version:

```sh
python3 scripts/package.py --version "${RELEASE_VERSION:?Set RELEASE_VERSION to the chosen X.Y.Z release version}"
python3 scripts/package.py --check
python3 -m unittest discover -s tests -p 'test_sdlc_*.py' -v
```

The package tool refuses malformed versions and downgrades without changing the
manifest. Running it without `--version` refreshes hashes at the current version;
use a new version when publishing changed payloads. CI checks Python 3.9, 3.11,
and 3.14.

The manifest records payload content hashes, not publisher authenticity. Tests use
isolated temporary repositories and synthetic review fixtures, never real approvals.
The package intentionally has one runtime implementation; tests execute that payload.
`tests/test_sdlc_protocol.py` is part of the release suite: preserve API 1 fields and
meaning across compatible releases, and use a new API major for breaking changes.
Cockpit integration tests can consume an unpublished candidate directly through
`SDLC_RUNTIME_SOURCE=/path/to/this/assets/_system/scripts/sdlc.py` with
`SDLC_EXPECT_API=1`; no package copy or production scaffold upgrade is needed.
