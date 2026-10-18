"""Shared fixture: the saved seed a session needs before `end --status completed`."""
import json
from pathlib import Path


FULL_HEADINGS = (
    "## You are confirming", "## Problem statement", "## Proposed outcome", "## Acceptance criteria",
    "## Affected users and systems", "## Constraints and boundaries", "## Decisions (from the ledger)",
    "## Assumed (not confirmed by you)", "## Knowledge map", "## Evidence and uncertainty",
    "## Suggested first slice", "## Risks and verification", "## Open questions and deferrals",
    "## Owners and next steps", "## Downstream handoff",
)
LEAN_HEADINGS = (
    "## You are confirming", "## Now  (the agent keeps these current)", "## Goal",
    "## Decisions (from the ledger)", "## Assumed (not confirmed by you)", "## Constraints and exclusions",
    "## Acceptance criteria", "## Open questions", "## Knowledge map", "## Evidence",
)


def contract_text(first_line, revision="r1", eol="\n", last_eol=True, decisions="", checks="", profile="lean"):
    text = first_line + eol + "# Seed contract: strict format fixture\n"
    text += eol.join(("revision: %s        date: 2026-10-06" % revision, "Session ledger: ledger.json", "", ""))
    for heading in FULL_HEADINGS if profile == "full" else LEAN_HEADINGS:
        if heading == "## Decisions (from the ledger)":
            text += decisions + eol
        elif heading == "## Assumed (not confirmed by you)":
            continue  # emitted by the unchanged decisions renderer
        elif heading == "## Acceptance criteria":
            text += checks
        else:
            body = "- Goal: a fixture goal" if heading == "## You are confirming" else "TEST fixture prose."
            text += heading + "\n" + body + eol + eol
    return text if last_eol else text.rstrip("\r\n")


def save_seed(session, directory, status_line=None):
    """Write `<session>/seed-contract.md` with the line 1 a completed session needs (confirmed, or simulated for a simulated session).

    A confirmed seed also gets the confirmation record `seed confirm` would write for the current decisions, so the
    session's own checks are what `end` tests. Tests that are about ledger behavior, not about confirmation, use this
    instead of running `seed confirm`. An explicit `status_line` is written as given, with no record.
    """
    directory = Path(directory)
    ledger = session.load(directory)
    record = status_line is None and ledger.get("operator", "human") == "human"
    if status_line is None:
        status_line = session.STATUS_CONFIRMED if record else session.STATUS_SIMULATED
    checks = [{"statement": "Fixture goal is met", "kind": "judge", "source": "TEST operator"}]
    session.set_checks(directory, checks)
    path = directory / session.SEED_FILE
    path.write_text(contract_text(status_line, decisions=session.decisions_block(ledger),
                                  checks=session.checks_block(checks)), encoding="utf-8")
    if record:
        digest = session.sha256(path.read_bytes())
        (directory / session.CONFIRMATION_FILE).write_text(json.dumps({
            "schema_version": 2, "checks_sha256": session.checks_digest(checks), "session_id": ledger["session_id"], "operator": "human", "revision": "r1",
            "source": "TEST fixture confirmation", "confirmed_at": "2026-10-06T00:00:00+00:00",
            "seed_sha256": digest, "decisions_sha256": session.decisions_digest(ledger)}))
