status: confirmed for intake
# Seed: I have created a software version of stacker (https://en.wikipedia.org/wiki/Stacker_(arcade_game)).
revision: r1
Confirmed by: "i confirm" (revision r1, 2026-10-18). Not approved for implementation.
date: 2026-10-18

## You are confirming
You are confirming the goal: create a software version of Stacker.
You are confirming what gets built: a tink-arcade cabinet with working sounds and the right mechanics.
You are confirming what does not: simulated payout interference or a collect-or-continue checkpoint.
You are confirming accepted unchanged: none recorded; two gameplay choices explicitly chosen.
You are confirming unknowns: source-game rule details and verification results; no earlier decision is reversed.

## Problem statement
You played Stacker at Dave and Busters today, liked it, and found it fun. You want a software version in this arcade. Earlier investigation found no registered Stacker cabinet; see Evidence and uncertainty for receipts.

## Proposed outcome
Create Stacker matching the tink-arcade style and theme, with working sounds and the right mechanics as the game has, subject to your settled skill-only and aim-for-the-top choices. Refer to Accepted decisions and assumed defaults for their exact authority and wording.

## Acceptance criteria
Matches the tink-arcade style and theme.
Proposed integration check, sourced from scripts/check_all.py:8-12 and scripts/check_arcade.py:37-60; requires Stacker registration. Existing assertions cover paper styling, sound status, mute feedback, quitting, and return to the menu.
cmd: python3 -B scripts/check_arcade.py
expect: PASS for every eligible registered cabinet, including Stacker; exit status 0.
cwd: /Users/jondev/dev/active/factory/tink-arcade-taste
you judge: inspect Stacker alongside the existing cabinets for matching style and theme; automated launch checks alone do not establish visual quality.

Has working sounds.
Provisional: Stacker-specific event-to-sound assertions and a repeat-playback verification invocation must be identified or added before this criterion is executable.
cmd: not yet established
expect: successful Stacker actions produce the intended audible feedback; repeated playback remains audible, mute works, and audio failure does not prevent play.
cwd: /Users/jondev/dev/active/factory/tink-arcade-taste
Follow DESIGN.md:147-164,194-200: verify rendered-signal advancement and levels, then listen on the intended output device. Listening is necessary but does not replace code-checkable audio assertions.

Just works with the right mechanics as the game has.
Provisional: read the linked source rules, document their mapping to the settled choices, and add Stacker-specific deterministic rule assertions; no Stacker regression invocation is established yet.
cmd: not yet established
expect: the source-grounded stacking, alignment loss, progression, loss and top-win rules pass deterministic assertions; correct timing can win, and play aims for the top without a collect-or-continue checkpoint.
cwd: /Users/jondev/dev/active/factory/tink-arcade-taste
Proposed behavior-specific regression: a partially overlapping placement retains exactly the overlapping blocks, while zero overlap ends the round. A targeted reversion retaining nonoverlapping blocks should fail the overlap-width assertion. This assertion does not exist in the inspected evidence; neither red nor green has been observed.

## Affected users and systems
Players launching cabinets through the terminal menu. Proposed change surface: a new Stacker package, game-specific tests, arcade_menu/games.json, and documentation. Reuse the shared renderer and audio facilities; shared changes, if needed, could affect every cabinet.

## Constraints and boundaries
Software, with the confirmed style, sound and mechanics requirements. Preserve the settled gameplay choices. Repository conventions separate rules from terminal presentation and require playable degradation without audio or color. Technical structure and checks here are proposals, not implementation approval.

## Knowledge map
What we know, with proof: refer to Evidence and uncertainty and Accepted decisions and assumed defaults rather than duplicating their entries. File observations in this step were made on 2026-10-18; re-check if those files change before implementation.
What we know we don't know: the implementing agent must read the linked Stacker rules and establish exact progression and timing details; the operator owns any newly discovered player-visible alternatives not settled by the goal or ledger.
What we have not read: the external article, all existing game implementations and tests, and audio backend internals. No complete close checklist was supplied.
What could surprise us: Probe: source-game details may conflict with the selected skill-only, top-only experience. Surface such conflicts instead of silently changing the choices. Probe: successful audio calls may still produce silence. Where we did not look: live gameplay and the operator's audio device. Concrete contrary signals are a conflicting source rule, failed regression, or inaudible repeated playback.

## Suggested first slice
Proposal only: establish the existing baseline before adding the rules-only Stacker slice and its regression tests.
cmd: python3 -B scripts/check_all.py
cwd: /Users/jondev/dev/active/factory/tink-arcade-taste
This is a suggested first implementation-stage check, not authorization to run or implement anything now.

## Risks and verification
Misreading the source rules could produce recognizable-looking but incorrect gameplay; ground the rule tests before UI work. New registration extends the existing menu integration path and can expose incompatible launch, mute or exit behavior.
For a targeted integration reversion, remove the new cabinet's paper styling after registration: the existing PAPER assertion in scripts/check_arcade.py:49 should fail. This is intended red behavior, not an observed result. The proposed invocation above is established; registration and the reversion probe remain prerequisites.
Repeated audio can silently fail despite successful API calls; verify real playback as well as event mapping. All checks remain proposed and unrun. The aggregate suite is a baseline and compatibility check, not a replacement for Stacker regressions.

## Open questions and deferrals
No unresolved operator decision blocks this draft. Exact source-rule details and test invocations remain agent research tasks before implementation. If that research reveals distinguishable gameplay alternatives not already settled, return the choice to the operator rather than choosing silently. Session location and confirmation identity are not supplied here.

## Owners and next steps
Operator: review and confirm or revise this seed; confirmation is not implementation approval.
Implementing agent: read outstanding source rules, establish missing tests, and follow the repository's isolated-workspace and downstream workflow requirements before code changes.
Ledger mapping: confirmed goal and gameplay choices are settled; missing rule evidence and verification are risks requiring research. Guardrails are source-grounded rule assertions, menu integration checks, and real repeated-audio verification.

## Downstream handoff
The helper supplies Evidence and uncertainty and Accepted decisions and assumed defaults from the recorded findings and ledger. The handoff is the session's seed-contract.md, ledger.json and seed-confirmation.json; its actual session path is not supplied. A consumer checks session.py status <session> and requires handoff.verified to be true. That proves confirmed content, not confirmer identity. This seed is discovery input, not an implementation plan, approved specification or review receipt. Preserve accepted constraints and surface downstream conflicts; no downstream profile or stage approval is selected here.

## Evidence and uncertainty
Grounded in:
- AGENTS.md
- DESIGN.md
- README.md
- _system/SDLC.md
- arcade_common/audio.py
- arcade_common/theme.py
- arcade_menu/games.json
- scripts/check_all.py
- scripts/check_arcade.py

Provenance unavailable: reported reads do not verify filesystem origins or unrecorded reads. Some recorded findings have no attributable read path.

Checked by read-only file tools in /Users/jondev/dev/active/factory/tink-arcade-taste; citation not found: arcade_menu/games.json has 166 lines, not 167:
- README.md:1-29; arcade_menu/games.json:1-167: This is an offline Python terminal arcade. The documented new-game structure is engine.py, a PaperRenderer-based ui.py, __main__.py, and a menu registration. The complete menu has no Stacker entry. The likely blast radius is a new game package, menu registration, documentation, and game-specific tests. (observed 2026-10-18)
Checked by read-only file tools in /Users/jondev/dev/active/factory/tink-arcade-taste:
- DESIGN.md:5-16,126-164; arcade_common/theme.py:1-33; arcade_common/audio.py:9-19: Existing conventions supply paper/ink/jade visuals and short mechanical keyboard sounds, with silent automatic motion and feedback for successful actions. Shared renderer and cue definitions can support the new game; modifying these shared pieces could affect every cabinet. The design also requires playable degradation without sound or color. (observed 2026-10-18)
- scripts/check_all.py:1-26; scripts/check_arcade.py:37-60: The aggregate check runs unittest discovery and the menu PTY check. Registering Stacker extends that PTY check automatically: it expects paper styling, visible sound-on status, mute feedback, quitting, and return to the menu. A new cabinet that deviates from these conventions could break this integration path. Neither check establishes Stacker mechanics without new behavior-specific assertions. (observed 2026-10-18)
- AGENTS.md:1-10; _system/SDLC.md:21-23,42-44: Later code-writing work requires an isolated worktree or clone and separate human workflow decisions. This investigation has not run commands, tested gameplay, or approved implementation. (observed 2026-10-18)
- DESIGN.md:106-108,194-198: The existing game design explicitly values fairness and separates game-rule tests from presentation checks. No inspected source settles Stacker's skill-versus-payout behavior. The linked external rules have not been read with the available file tools. (observed 2026-10-18)
- README.md:19-29: A new cabinet uses a rules-only engine.py, a PaperRenderer-based ui.py, __main__.py, and menu registration. The proposed blast radius remains the new Stacker package, game-specific tests, arcade_menu/games.json, and documentation; shared rendering and audio can be reused rather than changed. (observed 2026-10-18)
- scripts/check_arcade.py:37-60: The menu integration check launches every uncredentialed registered game and asserts paper styling, visible sound-on status, mute feedback, and return to the menu. Stacker registration will bring its launch and exit behavior into this existing check; incompatible UI or controls could break it. (observed 2026-10-18)
- DESIGN.md:126-164,194-200: The sound contract requires successful-action feedback, silent automatic motion, visible mute status, and playable audio failure. Audio verification includes real repeated playback rather than successful API calls alone. Rules tests must remain separate from presentation tests. (observed 2026-10-18)
- arcade_menu/games.json:139-166: The registry's final entries illustrate cabinet metadata and controls. A checkpoint choice would need corresponding player-facing controls and explanatory copy in the new cabinet and its registration. (observed 2026-10-18)
- README.md:19-29; DESIGN.md:126-164: These repository conventions settle integration and sound direction, but do not specify Stacker's collect-or-continue rule. The linked external rules remain unread; checkpoint behavior is a proposed product choice, not verified source-game evidence. (observed 2026-10-18)
- README.md:19-29: The documented new-game surface is a rules-only engine.py, PaperRenderer-based ui.py, __main__.py, and menu registration. Shared rendering and audio are available; proposed additions also need game tests and documentation. (observed 2026-10-18)
- scripts/check_all.py:8-12,16-23; scripts/check_arcade.py:37-60: The aggregate runner invokes unit discovery and the menu PTY check. The latter launches every eligible registered cabinet and asserts styling, sound status, mute feedback and return to the menu. Registering Stacker expands this integration blast radius; incompatible launch or controls can break it. (observed 2026-10-18)
- DESIGN.md:133-164,194-200: Sound conventions require successful-action feedback, silent automatic motion, visible mute status and playable audio failure. Real repeated playback and rendered-signal checks are required, not successful API calls alone. Rules tests remain separate from presentation tests; no checks were run in this step. (observed 2026-10-18)

## Accepted decisions and assumed defaults
## Decisions (from the ledger)
- goal [user] I have created a software version of stacker (https://en.wikipedia.org/wiki/Stacker_(arcade_game)). Why: i like the game and played it at dave and busters today and it was fun. Done when: matches the tink-arcade style and theme has working sounds just works with the right mechanics as the game has. (source: Goal confirmed in Tinkery with typed confirm and Enter; caller-declared attribution: unrecorded; goal only, not seed or implementation approval.)
- stacker-fairness [user] skill (source: the operator's own answer, typed in Tinkery Shape)
- stacker-checkpoint [user] aim for the top (source: the operator's own answer, typed in Tinkery Shape)

## Assumed (not confirmed by you)
- none
