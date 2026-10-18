import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid
from urllib.request import urlopen


STATE_FIELDS = {"status", "draft", "goal", "origin", "current_question", "nodes"}
OPTIONAL_STATE_FIELDS = {"assumed", "operator"}
OPERATORS = ("human", "simulated")
# The saved seed is `<session>/seed-contract.md`; line 1 is exactly one of these. Only `seed confirm` writes the confirmed line.
SEED_FILE = "seed-contract.md"
STATUS_DRAFT = "status: draft"
STATUS_CONFIRMED = "status: confirmed for intake"
STATUS_SIMULATED = "status: simulated"
# Helper-owned record of what a human confirmed: this session, seed bytes, decisions and check records.
# Only `seed confirm` writes it. A folder's paths are not in it, so an intact session stays valid when moved.
CONFIRMATION_FILE = "seed-confirmation.json"
CONFIRMATION_FIELDS = {"schema_version", "session_id", "operator", "revision", "source", "confirmed_at",
                       "seed_sha256", "decisions_sha256", "checks_sha256"}
NODE_FIELDS = {"id", "kind", "status", "prerequisites", "predicate", "evidence",
               "answer", "authority", "authority_source", "label", "question",
               "owner", "gate", "recommendation", "defer_reason", "revisit_condition", "reopen_reason", "contradicts",
               "claim", "supported_by", "rationale"}
CLAIM_TYPES = ("observation", "inference", "unknown")
CLAIM_FIELDS = {"type", "scope", "limits"}
RATIONALE_FIELDS = {"feasibility", "intent"}
RECEIPT_FIELDS = {"checked", "at", "observed", "check", "artifact"}
# Helper-owned, never published. premise_version: where in publication order a node's recorded evidence last changed.
# reviewed_version: where its reliance was last reviewed. Labels, questions and owners move neither.
# Positions are 2 * publication version, and an explicit review is one past that: a review given in a publication counts
# as after everything recorded in the same publication, while creating or first settling a node is not a review.
HELPER_NODE_FIELDS = ("revision", "premise_version", "reviewed_version")
EVIDENCE_FIELDS = ("status", "answer", "evidence", "claim", "supported_by", "contradicts")
# A best-effort backstop for the rule "no secrets in receipts"; the rule itself is the host's to keep.
SECRET = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}"
                    r"|\bsk-[A-Za-z0-9_-]{20,}|\bxox[abprs]-[A-Za-z0-9-]{10,}|://[^/\s:@]+:[^/\s@]+@"
                    r"|\b(?:password|passwd|secret|token|api[_-]?key)\s*=\s*\S{6,}|\bbearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_receipt(receipt):
    """A receipt records what was checked, when, and what was observed. `check` and `artifact` are stored, never run."""
    require(isinstance(receipt, dict) and {"checked", "at", "observed"} <= set(receipt) <= RECEIPT_FIELDS
            and all(text(v) for v in receipt.values()),
            "invalid receipt: checked, at, and observed are required; check and artifact are optional; all non-blank strings")
    try:
        when = datetime.fromisoformat(receipt["at"].strip().replace("Z", "+00:00"))
    except ValueError:
        when = None
    require(when is not None and when.tzinfo is not None, "invalid receipt time: an ISO 8601 timestamp with a UTC offset")
    require(not any(SECRET.search(v) for v in receipt.values()), "receipt appears to contain a secret: record where it lives, never its value")


def validate_claim(node):
    claim = node["claim"]
    require(node["kind"] == "fact", "claim is only allowed on a fact: a decision is settled by authority, not evidence")
    require(isinstance(claim, dict) and claim.get("type") in CLAIM_TYPES and set(claim) <= CLAIM_FIELDS,
            "invalid claim: type is observation, inference, or unknown, with scope and optional limits")
    require(text(claim.get("scope")), "claim requires a scope: what was examined, or what the claim covers")
    require("limits" not in claim or text(claim["limits"]), "invalid claim limits")
    if claim["type"] == "inference":
        require(text(claim.get("limits")), "an inference must state its limits: what it does not establish")
        require(bool(node.get("supported_by")), "an inference must cite the observations that support it")


def validate_support(node, nodes):
    support = node["supported_by"]
    require(isinstance(support, list) and bool(support) and all(text(s) for s in support) and len(set(support)) == len(support),
            "invalid supported_by: distinct node IDs")
    claim = node.get("claim")
    require(node["kind"] == "decision" or (claim or {}).get("type") == "inference",
            "supported_by is only allowed on a decision or an inference: an observation is checked directly and an unknown has no support")
    for source in support:
        require(source in nodes and source != node["id"], "supported_by must name another node")
        found = nodes[source]
        found_type = (found.get("claim") or {}).get("type")
        require(found["kind"] == "fact" and found_type != "unknown",
                "supported_by must name a fact that records evidence, not a decision or an unknown")
        require(claim is None or found_type == "observation", "an inference is supported by observations, not by other inferences")


def validate_rationale(node):
    rationale = node["rationale"]
    require(node["kind"] == "decision", "rationale is only allowed on a decision")
    require(isinstance(rationale, dict) and bool(rationale) and set(rationale) <= RATIONALE_FIELDS
            and all(text(v) for v in rationale.values()),
            "invalid rationale: feasibility (what the current architecture allows) and/or intent (the user's outcome or threat model), non-blank")


def editable_payload(directory):
    """One read, as a publication starts: the version it was read at and the state without helper-owned fields.
    The caller edits `state`, adds `reason`, and publishes it. Reads only."""
    ledger = load(directory)
    return {"expected_version": ledger["version"], "state": editable(ledger)}


def editable(ledger):
    state = {key: deepcopy(ledger[key]) for key in STATE_FIELDS}
    state["assumed"] = deepcopy(ledger.get("assumed", []))
    state["operator"] = ledger.get("operator", "human")
    for node in state["nodes"]:
        node.pop("history", None)
        for field in HELPER_NODE_FIELDS:
            node.pop(field, None)
    return state


def validate(state):
    require(isinstance(state, dict) and STATE_FIELDS <= set(state) <= STATE_FIELDS | OPTIONAL_STATE_FIELDS, "invalid session fields")
    operator = state.get("operator", "human")
    require(operator in OPERATORS, "invalid operator: human or simulated")
    assumed = state.get("assumed", [])
    require(isinstance(assumed, list) and all(isinstance(a, dict) and set(a) == {"text", "why"} and text(a["text"]) and text(a["why"]) for a in assumed),
            "invalid assumed: a list of {\"text\", \"why\"} entries with non-blank strings")
    require(state["status"] in ("active", "stopped", "completed"), "invalid session status")
    draft = state["draft"]
    require(isinstance(draft, dict) and set(draft) == {"goal", "outcome", "options"}, "invalid draft")
    require(isinstance(draft["goal"], str) and isinstance(draft["outcome"], str), "invalid draft text")
    require(isinstance(draft["options"], list) and all(text(v) for v in draft["options"]), "invalid draft options: each option must be a non-empty string such as 'Label — tradeoff'")
    require(isinstance(state["nodes"], list), "nodes must be a list")
    nodes = {}
    for node in state["nodes"]:
        require(isinstance(node, dict), "invalid node fields")
        extra = sorted(set(node) - NODE_FIELDS)
        require(not extra, "invalid node fields: %s (helper-owned or unknown; start from `read --editable`)" % ", ".join(extra))
        require(text(node.get("id")), "node requires an id")
        require(node["id"] not in nodes, "duplicate node id")
        nodes[node["id"]] = node
        require(node.get("kind") in ("fact", "decision"), "invalid node kind")
        require(node.get("status") in ("unresolved", "settled", "deferred", "superseded"), "invalid node status")
        prerequisites = node.get("prerequisites")
        require(isinstance(prerequisites, list) and all(text(p) for p in prerequisites), "invalid prerequisites")
        require(len(set(prerequisites)) == len(prerequisites), "duplicate prerequisite")
        require(node.get("predicate", True) is None or type(node.get("predicate", True)) is bool,
                "predicate must be an observed boolean or unknown (null)")
        evidence = node.get("evidence")
        require(isinstance(evidence, list) and all(text(e) or isinstance(e, dict) for e in evidence), "invalid evidence")
        for entry in evidence:
            if isinstance(entry, dict):
                validate_receipt(entry)
        if node["kind"] == "decision":
            require(text(node.get("owner")) and text(node.get("gate")), "decision requires owner and gate")
        if node["status"] == "settled":
            require(text(node.get("answer")), "settled node requires answer")
            require(text(node.get("authority_source")), "settled node requires authority source")
            expected = ("evidence",) if node["kind"] == "fact" else (("simulated",) if operator == "simulated" else ("user", "delegated"))
            require(node.get("authority") in expected, "authority does not match node kind or operator: a simulated operator can only settle decisions as 'simulated', a human session never as 'simulated'")
            if node["authority"] in ("evidence", "delegated"):
                require(bool(evidence), "evidence or delegated settlement requires evidence")
        else:
            require(node.get("answer") is None and node.get("authority") is None
                    and node.get("authority_source") is None, "unsettled node cannot carry an active answer")
        if "reopen_reason" in node:
            require(text(node["reopen_reason"]), "invalid reopen reason")
        if node["status"] == "deferred":
            require(text(node.get("defer_reason")) and text(node.get("revisit_condition")),
                    "deferral requires reason and revisit condition")
    for node in nodes.values():
        if "claim" in node:
            validate_claim(node)
        if "supported_by" in node:
            validate_support(node, nodes)
        if "rationale" in node:
            validate_rationale(node)
    for node in nodes.values():
        if "contradicts" in node:
            target = node["contradicts"]
            require(node["kind"] == "fact" and text(target) and target != node["id"] and target in nodes,
                    "contradicts must name another node and is only allowed on a fact")
    visiting, visited = set(), set()

    def visit(node_id):
        require(node_id in nodes, "missing prerequisite")
        require(node_id not in visiting, "dependency cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        for parent in nodes[node_id]["prerequisites"]:
            visit(parent)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in nodes:
        visit(node_id)
    for node in nodes.values():
        if node["status"] == "settled":
            require(node.get("predicate", True) is True, "settled node has inactive or unknown predicate")
            require(all(nodes[p]["status"] == "settled" for p in node["prerequisites"]),
                    "settled node has unsettled prerequisites")
    origin = state["origin"]
    if origin is None:
        require(state["goal"] is None and not nodes, "unconfirmed draft cannot contain ledger nodes")
    else:
        require(text(origin) and origin in nodes and text(state["goal"]), "invalid confirmed goal")
        root = nodes[origin]
        require(root["kind"] == "decision" and root["status"] == "settled"
                and root["authority"] == ("simulated" if operator == "simulated" else "user") and root["answer"] == state["goal"],
                "origin requires an explicitly confirmed user goal")
    ready = [n["id"] for n in nodes.values() if n["status"] == "unresolved"
             and n.get("predicate", True) is True
             and all(nodes[p]["status"] == "settled" for p in n["prerequisites"])]
    current = state["current_question"]
    require(current is None or (current in ready and nodes[current]["kind"] == "decision"),
            "current question must be a ready decision")
    if state["status"] != "active":
        require(current is None, "ended session cannot have a current question")
    if state["status"] == "completed":
        require(origin is not None and all(n["status"] != "unresolved" for n in nodes.values()),
                "cannot complete with unresolved concerns")
    return ready


def descendants(nodes, starts):
    reached = set(starts)
    while True:
        added = {n["id"] for n in nodes if any(p in reached for p in n["prerequisites"])} - reached
        if not added:
            return reached - set(starts)
        reached.update(added)


def evidence_differs(before, after):
    """Whether what a node records as evidence changed. Classifying a legacy entry (no claim yet) only labels it,
    and the order of `supported_by` carries no meaning."""
    def view(node, field):
        value = node.get(field)
        return sorted(value) if field == "supported_by" and isinstance(value, list) else value
    return any(view(before, f) != view(after, f) for f in EVIDENCE_FIELDS if not (f == "claim" and before.get(f) is None))


def support_chain(by_id, node_id):
    """Everything the node relies on as evidence, directly or through other nodes, nearest first."""
    seen, queue = [], list(by_id[node_id].get("supported_by", ()))
    while queue:
        source = queue.pop(0)
        if source not in seen and source in by_id:
            seen.append(source)
            queue.extend(by_id[source].get("supported_by", ()))
    return seen


def contradictions(nodes):
    """Every settled fact that names each target in `contradicts`, not only the last one listed."""
    found = {}
    for fact in nodes:
        if fact["status"] == "settled" and fact.get("contradicts"):
            found.setdefault(fact["contradicts"], []).append(fact)
    return found


def review_flags(nodes):
    """Settled nodes that rely, directly or through other nodes, on evidence that changed, was withdrawn, or was
    contradicted after the node was last reviewed. Each node is measured against its own `reviewed_version` only.

    Derived and read-only: a flag asks for review. It never reopens a node, erases its answer or authority, or picks a replacement.
    """
    by_id = {n["id"]: n for n in nodes}
    contradicted = contradictions(nodes)
    flags = {}
    for node in nodes:
        if node["status"] != "settled":
            continue
        reviewed = node.get("reviewed_version", 0)
        for source in support_chain(by_id, node["id"]):
            found, moved = by_id[source], by_id[source].get("premise_version", 1)
            fact_moved = max((f.get("premise_version", 1) for f in contradicted.get(source, ())), default=0)
            if moved > reviewed:
                why = "withdrawn" if found["status"] != "settled" else "changed"
            elif found["status"] == "settled" and moved <= fact_moved and fact_moved > reviewed:
                why = "contradicted"
            else:
                continue
            flags.setdefault(node["id"], []).append({"because": source, "why": why})
        # A settled fact that names this node in `contradicts` flags the node itself until it is reviewed or revised
        # (revising it moves its premise past the fact's). `>=`: a fact recorded together with its target counts.
        for fact in contradicted.get(node["id"], ()):
            fact_moved = fact.get("premise_version", 1)
            if node.get("premise_version", 1) <= fact_moved and fact_moved >= reviewed:
                flags.setdefault(node["id"], []).append({"because": fact["id"], "why": "contradicting"})
    return flags


def transition(ledger, state, expected_version, reason, revalidated=None):
    state = deepcopy(state)
    state.setdefault("assumed", [])
    state.setdefault("operator", ledger.get("operator", "human"))
    ready = validate(state)
    require(state["operator"] == ledger.get("operator", "human"), "operator cannot change in a session")
    old_state = editable(ledger)
    require(type(expected_version) is int and expected_version >= 0, "invalid expected version")
    # An exact repeat is a no-op, but a review of flagged nodes (revalidated) records a decision even when nothing else changed.
    if state == old_state and expected_version <= ledger["version"] and not (revalidated and expected_version == ledger["version"]):
        return ledger
    require(expected_version == ledger["version"], "stale publication version")
    require(ledger["status"] == "active", "ended session is read-only")
    require(text(reason), "update requires a reason")
    if ledger["origin"] is not None:
        require(state["origin"] == ledger["origin"] and state["goal"] == ledger["goal"],
                "confirmed origin and goal cannot be replaced in this session")
    old = {n["id"]: n for n in old_state["nodes"]}
    new = {n["id"]: n for n in state["nodes"]}
    require(set(old) <= set(new), "preserve nodes; supersede instead of deleting")
    for node_id in old:
        require(old[node_id]["kind"] == new[node_id]["kind"], "node kind is immutable")
        if old[node_id]["status"] == "settled" and new[node_id]["status"] == "unresolved":
            require(text(new[node_id].get("reopen_reason")), "reopened node requires a reopen reason")
    premise_fields = ("status", "answer", "authority", "authority_source", "evidence", "prerequisites", "predicate", "question", "owner", "gate",
                      "claim", "supported_by")
    # Classifying a legacy entry (no claim yet) labels it; it does not change what was recorded.
    changed = {key for key, node in old.items() if node["status"] == "settled"
               and any(node.get(f) != new[key].get(f) for f in premise_fields if not (f == "claim" and node.get(f) is None))}
    affected = set()
    for node_id in changed:
        affected |= descendants(list(old.values()), {node_id}) | descendants(list(new.values()), {node_id})
    revalidated = {} if revalidated is None else revalidated
    require(isinstance(revalidated, dict) and all(isinstance(k, str) for k in revalidated), "invalid revalidation targets")
    require(all(text(v) for v in revalidated.values()), "revalidation requires a justification")
    for node_id in affected:
        if new[node_id]["status"] == "settled":
            require(text(revalidated.get(node_id)) and bool(new[node_id]["evidence"]),
                    "affected settlement requires explicit revalidation with evidence: " + node_id)
    assumed_changed = state["assumed"] != old_state["assumed"]
    revision = ledger["revision"] + bool(changed or assumed_changed)
    updated = now()
    result = {**deepcopy(ledger), **state, "version": ledger["version"] + 1,
              "revision": revision, "updated_at": updated, "frontier": ready}
    old_stored = {n["id"]: n for n in ledger["nodes"]}
    added, unreviewed = {}, {}
    for node in result["nodes"]:
        previous = old_stored.get(node["id"])
        history = deepcopy(previous["history"]) if previous else []
        material_change = previous is not None and (old[node["id"]] != node or node["id"] in revalidated)
        if material_change:
            history.append({"state": {k: deepcopy(v) for k, v in previous.items() if k != "history"},
                            "superseded_at": updated, "reason": revalidated.get(node["id"], reason)})
        node["history"] = history
        node["revision"] = revision if previous is None or material_change else previous["revision"]
        moved = previous is None or evidence_differs(old[node["id"]], node)
        first_settled = node["status"] == "settled" and (previous is None or previous["status"] != "settled")
        chain = support_chain(new, node["id"])
        # Reliance a settled node newly takes on (all of it when first settled) must already be recorded, not withdrawn.
        added[node["id"]] = [] if node["status"] != "settled" else (chain if first_settled else [x for x in chain if x not in support_chain(old, node["id"])])
        missing = [x for x in added[node["id"]] if new[x]["status"] != "settled"]
        require(not missing, "cannot settle on evidence that is not recorded: " + ", ".join(missing))
        implicit = previous is None or first_settled
        position = 2 * result["version"]
        if moved:
            node["premise_version"] = position
        elif "premise_version" in previous:
            node["premise_version"] = previous["premise_version"]
        before_review = position if implicit else (previous or {}).get("reviewed_version", 0)
        node["reviewed_version"] = position + 1 if node["id"] in revalidated else before_review
        unreviewed[node["id"]] = before_review
    # What a review may target is exactly what the flag rule flags on this publication's state, before the reviews are applied.
    before = [{**n, "reviewed_version": unreviewed[n["id"]]} for n in result["nodes"]]
    require(set(revalidated) <= affected | set(review_flags(before)), "invalid revalidation targets")
    # Newly relied-on evidence must not be contradicted in force: the flag rule's own answer, with this publication's reviews applied.
    after = review_flags(result["nodes"])
    for sources in added.values():
        clashing = [x for x in sources if any(f["why"] == "contradicting" for f in after.get(x, ()))]
        require(not clashing, "cannot rely on evidence that is contradicted and not revisited: " + ", ".join(clashing))
    if state["status"] == "completed":
        flagged = sorted(review_flags(result["nodes"]))
        require(not flagged, "cannot complete: evidence behind these settled nodes changed, was withdrawn, or was contradicted "
                "and they were not reviewed, revised or resolved (revalidate each with a reason; answers stay as they are): " + ", ".join(flagged))
    return result


def atomic_write(path, ledger):
    atomic_write_text(path, json.dumps(ledger, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def atomic_write_text(path, content, mode=None):
    descriptor, temporary = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


ASSET = Path(__file__).resolve().parents[1] / "assets/ledger-view.html"
VENDOR = Path(__file__).resolve().parents[1] / "assets/vendor/cytoscape.min.js"
LIBRARY_TAG = re.compile(r'<script async id="graph-library"[^>]*></script>')
SNAPSHOT_MARKER = '{"__LEDGER_JSON__":true,"revision":0,"goal":null,"origin":null,"frontier":[],"nodes":[]}'


def snapshot_data(ledger):
    return json.dumps(ledger, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c")


def snapshot_page(ledger):
    template = ASSET.read_text(encoding="utf-8")
    if template.count(SNAPSHOT_MARKER) != 1:
        raise ValueError("viewer template requires one snapshot marker")
    page = template.replace(SNAPSHOT_MARKER, snapshot_data(ledger))
    if VENDOR.is_file():
        # Self-contained: the graph library travels inside the page, so it works with no network.
        library = VENDOR.read_text(encoding="utf-8")
        require("</script" not in library.lower() and "<!--" not in library, "vendored library cannot be inlined")
        page, count = LIBRARY_TAG.subn(lambda _: '<script id="graph-library">' + library + "</script>", page, count=1)
        require(count == 1, "viewer template requires one graph-library tag")
    return page


def write_snapshot(directory):
    """Save the view next to the ledger. Callers hold the writer lock, so the view can never lag the ledger."""
    destination = Path(directory) / "ledger-view.html"
    require(not destination.is_symlink(), "viewer destination must not be a symlink")
    atomic_write_text(destination, snapshot_page(load(directory)))
    return destination


def viewer_record(directory):
    try:
        record = json.loads((Path(directory) / "viewer.json").read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def viewer_state(directory):
    record = viewer_record(directory)
    if record is None:
        return "not started"
    if record.get("declined"):
        return "declined: " + record["declined"]
    try:
        with urlopen(record["url"], timeout=1) as response:
            if response.status == 200:
                return "live " + record["url"]
    except (OSError, KeyError, ValueError):
        pass
    return "started earlier, not running now"


def status(directory):
    directory = Path(directory)
    ledger = load(directory)
    snapshot = directory / "ledger-view.html"
    current = snapshot.is_file() and snapshot_data(ledger) in snapshot.read_text(encoding="utf-8")
    nodes = ledger["nodes"]
    return {"status": ledger["status"], "revision": ledger["revision"], "version": ledger["version"],
            "open": sum(1 for n in nodes if n["status"] == "unresolved"),
            "settled": sum(1 for n in nodes if n["status"] == "settled"),
            "current_question": ledger["current_question"], "viewer": viewer_state(directory),
            "snapshot": str(snapshot), "snapshot_current": current,
            "seed": seed_summary(directory), "handoff": verify_handoff(directory, ledger)}


def create(root=None, operator="human"):
    require(operator in OPERATORS, "invalid operator: human or simulated")
    root = Path(root) if root is not None else Path.home() / ".local/share/seed-me/sessions"
    root = root.expanduser().resolve()
    require(not any((p / ".git").exists() for p in (root, *root.parents)), "session storage must be outside a repository")
    root.mkdir(parents=True, exist_ok=True)
    session_id = str(uuid.uuid4())
    directory = root / session_id
    directory.mkdir(mode=0o700)
    timestamp = now()
    ledger = {"schema_version": 1, "session_id": session_id, "version": 0, "revision": 0,
              "created_at": timestamp, "updated_at": timestamp, "status": "active",
              "draft": {"goal": "", "outcome": "", "options": []}, "goal": None,
              "origin": None, "current_question": None, "frontier": [], "nodes": [], "assumed": [], "operator": operator}
    atomic_write(directory / "ledger.json", ledger)
    write_snapshot(directory)
    return directory


def load(directory):
    ledger = json.loads((Path(directory) / "ledger.json").read_text())
    require(isinstance(ledger, dict) and type(ledger.get("schema_version")) is int
            and ledger["schema_version"] == 1, "unsupported ledger schema")
    require(set(ledger) == STATE_FIELDS | OPTIONAL_STATE_FIELDS | {"schema_version", "session_id", "version", "revision",
                                         "created_at", "updated_at", "frontier"}, "invalid stored ledger fields")
    require(text(ledger.get("session_id")), "missing session identity")
    for field in ("version", "revision"):
        require(type(ledger.get(field)) is int and ledger[field] >= 0, "invalid " + field)
    for node in ledger.get("nodes", []):
        require(isinstance(node, dict) and isinstance(node.get("history"), list), "missing node history")
        require(type(node.get("revision")) is int and 0 <= node["revision"] <= ledger["revision"], "invalid node revision")
        for field in HELPER_NODE_FIELDS[1:]:
            require(field not in node or (type(node[field]) is int and 0 <= node[field] <= 2 * ledger["version"] + 1), "invalid node " + field)
        for entry in node["history"]:
            require(isinstance(entry, dict) and set(entry) == {"state", "superseded_at", "reason"}
                    and text(entry["reason"]) and text(entry["superseded_at"]), "invalid history entry")
            prior = entry["state"]
            require(isinstance(prior, dict) and not set(prior) - (NODE_FIELDS | set(HELPER_NODE_FIELDS))
                    and prior.get("id") == node["id"] and prior.get("kind") == node["kind"],
                    "invalid historical node")
    require(validate(editable(ledger)) == ledger.get("frontier"), "stored frontier is inconsistent")
    return ledger


@contextmanager
def writer(directory):
    with (Path(directory) / ".writer.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("session already has a writer") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class SaveOutcome(OSError):
    """A publish or end whose durable result must be reported, not inferred from the message.

    `save_state` is `saved` (the ledger holds `result`; only the view failed), `not_saved` (the ledger is unchanged) or
    `unknown` (the ledger could not be read back). Raised only after the writer's checks passed.
    """

    def __init__(self, message, save_state, directory, version, view_state, result=None):
        super().__init__(message)
        self.save_state, self.directory, self.version, self.view_state, self.result = (
            save_state, Path(directory), version, view_state, result)

    def diagnostic(self):
        session, viewer = str(self.directory), str(Path(__file__).with_name("viewer.py"))
        next_action = {
            "saved": 'repair the view: python3 "%s" "%s" --snapshot (or repeat the same command)' % (viewer, session),
            "not_saved": "nothing was saved; fix the cause and repeat the same command",
            "unknown": 'check what was saved: python3 "%s" status "%s", then reconcile before repeating' % (__file__, session),
        }[self.save_state]
        return {"save_state": self.save_state, "session": session, "version": self.version,
                "view_state": self.view_state, "next_action": next_action}


def commit(directory, ledger, result, also=None):
    """Save `result` over `ledger`, run `also`, then save the view; the caller holds the writer lock.

    A failed ledger write is read back: unchanged is `not_saved`, `result` carries on, anything else is `unknown`.
    Once the ledger is saved, a failure in `also` or the view never undoes it: it raises `saved` with the result.
    """
    if result is not ledger:
        try:
            atomic_write(directory / "ledger.json", result)
        except OSError as error:
            try:
                current = load(directory)
            except (OSError, ValueError):
                current = None
            if current != result:
                state = "not_saved" if current == ledger else "unknown"
                raise SaveOutcome("could not save the ledger: %s" % error, state, directory,
                                  ledger["version"] if state == "not_saved" else None,
                                  "unchanged" if state == "not_saved" else "unknown") from error
    try:
        if also is not None:
            also()
        write_snapshot(directory)
    except (OSError, ValueError) as error:
        raise SaveOutcome("saved version %d, but the view was not saved: %s" % (result["version"], error), "saved",
                          directory, result["version"], "stale", result) from error
    return result


def publish(directory, state, expected_version, reason, revalidated=None):
    directory = Path(directory)
    with writer(directory):
        ledger = load(directory)
        result = transition(ledger, state, expected_version, reason, revalidated)
        require(result is ledger or result["status"] != "completed",
                "publish cannot complete a session: use `end --status completed`, which checks the confirmed seed")
        return commit(directory, ledger, result)


def seed_lines(directory):
    """The saved seed as its lines, each with its own terminator, so a rewrite can leave every other byte alone."""
    path = Path(directory) / SEED_FILE
    require(path.is_file(), "no seed to use: %s is missing; save the seed there first" % path)
    with path.open(encoding="utf-8", newline="") as handle:
        return re.findall(r"[^\n]*\n|[^\n]+", handle.read())


def split_terminator(line):
    body = line[:-1] if line.endswith("\n") else line
    body = body[:-1] if body.endswith("\r") and line.endswith("\n") else body
    return body, line[len(body):]


def seed_line_one(lines):
    return split_terminator(lines[0])[0] if lines else ""


def require_seed_line(directory, expected):
    require((Path(directory) / SEED_FILE).is_file(), "cannot complete: %s is missing; save the seed there with line 1 %r"
            % (Path(directory) / SEED_FILE, expected))
    found = seed_line_one(seed_lines(directory))
    require(found == expected, "cannot complete: line 1 of %s must be exactly %r for this session, found %r"
            % (Path(directory) / SEED_FILE, expected, found))
    require_seed_body(directory, "cannot complete")


def require_seed_body(directory, refusal):
    """The saved seed must be a contract, not a status line: a `revision:` line, then a non-empty `## You are confirming` section."""
    lines = [split_terminator(line)[0] for line in seed_lines(directory)]
    fenced, inside = [], False
    for line in lines:
        if re.match(r" {0,3}(```|~~~)", line):
            inside = not inside
            fenced.append(False)  # the fence line itself is content, not a heading
        else:
            fenced.append(inside)
    box = next((i for i, line in enumerate(lines) if i and not fenced[i] and line.rstrip() == "## You are confirming"), None)
    section = []
    for i in range(box + 1 if box is not None else len(lines), len(lines)):
        if lines[i].startswith("#") and not fenced[i]:
            break
        section.append(lines[i])
    revision = r"revision:[ \t]*\S"
    require(box is not None and any(re.match(revision, lines[i]) and not fenced[i] for i in range(1, box))
            and any(line.strip() and not re.match(revision, line) for line in section),
            "%s: %s is not a seed contract; after its status line it needs a 'revision:' line and a "
            "'## You are confirming' section with content" % (refusal, Path(directory) / SEED_FILE))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def decisions_digest(ledger):
    """What a confirmation covers in the ledger: the draft, goal, decisions and assumptions, with their answers, authority,
    evidence and dependencies. Status, the current question, history and helper counters are bookkeeping, not content."""
    state = editable(ledger)
    covered = {key: state[key] for key in ("draft", "goal", "origin", "nodes", "assumed", "operator")}
    return sha256(json.dumps(covered, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def one_line(value):
    return " ".join(str(value).split())


def decisions_lines(ledger):
    """The seed's decisions and assumptions as (label, line) pairs, rendered from the ledger, which owns them.
    Settled and deferred nodes are decisions; superseded nodes are history and unresolved ones block confirmation."""
    rows = [("heading", "## Decisions (from the ledger)")]
    for node in ledger["nodes"]:
        after = " (after: %s)" % ", ".join(node["prerequisites"]) if node["prerequisites"] else ""
        if node["status"] == "settled":
            rows.append((node["id"], "- %s [%s] %s (source: %s)%s" % (
                node["id"], node["authority"], one_line(node["answer"]), one_line(node["authority_source"]), after)))
        elif node["status"] == "deferred":
            rows.append((node["id"], "- %s deferred: %s (revisit if: %s)%s" % (
                node["id"], one_line(node["defer_reason"]), one_line(node["revisit_condition"]), after)))
    rows += [("heading", ""), ("heading", "## Assumed (not confirmed by you)")]
    assumed = ledger.get("assumed", [])
    rows += [("assumed %r" % one_line(a["text"])[:40], "- %s (why: %s)" % (one_line(a["text"]), one_line(a["why"])))
             for a in assumed] or [("assumed", "- none")]
    return rows


def decisions_block(ledger):
    """What `seed decisions` prints and the seed must carry verbatim. Read-only."""
    return "".join(line + "\n" for _, line in decisions_lines(ledger))


def require_seed_decisions(directory, ledger, refusal):
    """The seed must carry the ledger's current decisions block, so the two files cannot disagree on what was decided."""
    lines = [split_terminator(line)[0] for line in seed_lines(directory)]
    if "\n" + decisions_block(ledger) in "".join("\n" + line for line in lines) + "\n":
        return
    present = set(lines)
    stale = [label for label, line in decisions_lines(ledger) if label != "heading" and line not in present]
    require(False, "%s: the seed's decisions do not match the ledger (%s); replace its Decisions and Assumed sections "
            "with the output of `session.py seed decisions <session>`"
            % (refusal, "not current: " + ", ".join(stale) if stale else "the block is incomplete or out of order"))


CHECKS_FILE = "seed-checks.json"
CHECK_FIELDS = {
    "cmd": ({"statement", "kind", "source", "cmd", "expect", "cwd"}, set()),
    "provisional": ({"statement", "kind", "source", "expect", "cwd", "missing"}, {"cmd"}),
    "judge": ({"statement", "kind", "source"}, {"how_to_judge"}),
}


def validate_checks(records):
    require(isinstance(records, list) and records, "checks must be a non-empty JSON array")
    for index, record in enumerate(records, 1):
        prefix = "check %s" % index
        require(isinstance(record, dict), prefix + " must be an object")
        kind = record.get("kind")
        require(isinstance(kind, str) and kind in CHECK_FIELDS, prefix + " has unknown kind")
        required, optional = CHECK_FIELDS[kind]
        require(required <= set(record), prefix + " missing fields: " + ", ".join(sorted(required - set(record))))
        require(set(record) <= required | optional, prefix + " has unknown fields: " + ", ".join(sorted(set(record) - required - optional)))
        for field, value in record.items():
            require(text(value) and "\x00" not in value, prefix + "." + field + " must be a non-empty string without NUL")
        if "cwd" in record:
            cwd = record["cwd"]
            require(not cwd.startswith("/") and ".." not in cwd.split("/") and "\\" not in cwd
                    and not re.match(r"^[A-Za-z]:", cwd) and "\n" not in cwd and "\r" not in cwd,
                    prefix + ".cwd must be repo-relative, without '..', absolute paths or backslashes")
    return records


def read_checks(directory):
    path = Path(directory) / CHECKS_FILE
    require(path.is_file(), "checks are missing: use `seed checks set <session> <input.json>`")
    return validate_checks(json.loads(path.read_text(encoding="utf-8")))


def checks_digest(records):
    return sha256(json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def checks_block(records):
    # JSON quoting prevents statements or commands from injecting Markdown headings or fences.
    data = json.dumps(validate_checks(records), sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
    return "## Acceptance criteria\n\n```json\n" + data + "\n```\n\n"


def set_checks(directory, records):
    records = validate_checks(records)
    directory = Path(directory)
    with writer(directory):
        require(load(directory)["status"] == "active", "ended session is read-only")
        atomic_write_text(directory / CHECKS_FILE, json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    return {"checks": str(directory / CHECKS_FILE), "count": len(records), "sha256": checks_digest(records)}


def without_comments(line, inside):
    out, index = [], 0
    while index < len(line):
        if inside:
            end = line.find("-->", index)
            if end < 0:
                return "".join(out), True
            index, inside = end + 3, False
        else:
            start = line.find("<!--", index)
            if start < 0:
                out.append(line[index:])
                break
            out.append(line[index:start])
            index, inside = start + 4, True
    return "".join(out), inside


SEED_HEADINGS = {
    "full": (
        "## You are confirming", "## Problem statement", "## Proposed outcome", "## Acceptance criteria",
        "## Affected users and systems", "## Constraints and boundaries", "## Decisions (from the ledger)",
        "## Assumed (not confirmed by you)", "## Knowledge map", "## Evidence and uncertainty",
        "## Suggested first slice", "## Risks and verification", "## Open questions and deferrals",
        "## Owners and next steps", "## Downstream handoff",
    ),
    "lean": (
        "## You are confirming", "## Now  (the agent keeps these current)", "## Goal",
        "## Decisions (from the ledger)", "## Assumed (not confirmed by you)", "## Constraints and exclusions",
        "## Acceptance criteria", "## Open questions", "## Knowledge map", "## Evidence",
    ),
}


def document_headings(body):
    """Only literal template headings are content; comments and fenced examples are opaque."""
    headings, fence, comment = [], None, False
    for match in re.finditer(r"[^\n]*\n|[^\n]+", body):
        raw = split_terminator(match.group())[0]
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + r"{%s,}[ \t]*" % len(fence), raw):
                fence = None
            continue
        opening = re.match(r" {0,3}(`{3,}|~{3,})(.*)$", raw) if not comment else None
        if opening and (opening[1][0] == "~" or "`" not in opening[2]):
            fence = opening[1]
            continue
        line, comment = without_comments(raw, comment)
        candidate = line.lstrip()
        while True:
            require(not re.fullmatch(r"(?:=+|-+)[ \t]*", candidate),
                    "checks refuse injected Setext heading syntax; body prose cannot contain headings")
            container = re.match(r"(?:>[ \t]*|(?:[-+*]|\d+[.)])[ \t]+)", candidate)
            if not container:
                break
            candidate = candidate[container.end():].lstrip()
        if re.match(r"#{1,6}(?:[ \t]+|$)", candidate):
            require(raw == candidate and line == raw and match.group() == raw + "\n",
                    "checks refuse injected, indented, commented or non-literal heading lines")
            headings.append((match.start(), raw))
    return headings


def require_seed_checks(directory):
    records = read_checks(directory)
    body = "".join(seed_lines(directory))
    require(not re.search(r"\r(?!\n)", body), "checks refuse lone CR: seed line endings must be LF or CRLF")
    headings = document_headings(body)
    require(headings and re.fullmatch(r"# [^\r\n]*\S", headings[0][1]),
            "checks need one leading H1 title and canonical Full or Lean section headings")
    section_titles = tuple(line for _, line in headings[1:])
    require(section_titles in SEED_HEADINGS.values(),
            "checks refuse injected, missing, reordered or non-canonical headings; use the Full or Lean template")
    index = next(i for i, (_, line) in enumerate(headings) if line == "## Acceptance criteria")
    start = headings[index][0]
    end = headings[index + 1][0] if index + 1 < len(headings) else len(body)
    require(body[start:end] == checks_block(records), "checks do not match the rendered Acceptance criteria section; use `seed checks render`")
    return records


def seed_summary(directory):
    """The saved seed's line 1, first revision label and sha256, or None when there is no seed. Read-only."""
    path = Path(directory) / SEED_FILE
    if not path.is_file():
        return None
    data = path.read_bytes()
    lines = data.decode("utf-8", errors="replace").split("\n")
    label = next((m.group(1) for m in (re.match(r"revision:[ \t]*(\S+)", line) for line in lines[1:]) if m), None)
    return {"line_1": lines[0].rstrip("\r"), "revision": label, "sha256": sha256(data)}


def confirmation_record(directory):
    try:
        record = json.loads((Path(directory) / CONFIRMATION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(record, dict) and set(record) == CONFIRMATION_FIELDS and record["schema_version"] == 2
            and all(text(record[k]) for k in CONFIRMATION_FIELDS - {"schema_version"})):
        return None
    return record


def verify_handoff(directory, ledger=None):
    """Read-only: whether the saved seed and ledger are exactly what a human confirmed in this session.

    Promises `verified` only when this session's confirmation record matches seed bytes, decisions and check records.
    A seed confirmed before the record existed, or by an interrupted confirm, is unverified, never upgraded on read.
    Proves content, not who confirmed it or that its evidence is true.
    """
    directory = Path(directory)
    ledger = load(directory) if ledger is None else ledger
    if ledger.get("operator", "human") != "human":
        return {"verified": False, "reason": "a simulated session is a practice run that no person confirmed"}
    seed = seed_summary(directory)
    if seed is None:
        return {"verified": False, "reason": "no seed is saved"}
    if seed["line_1"] != STATUS_CONFIRMED:
        return {"verified": False, "reason": "the seed is not confirmed (line 1 is %r)" % seed["line_1"]}
    record = confirmation_record(directory)
    if record is None:
        return {"verified": False, "reason": "the seed has no confirmation record from `seed confirm` in this session"}
    if record["session_id"] != ledger["session_id"] or record["operator"] != "human":
        return {"verified": False, "reason": "the confirmation record belongs to another session"}
    if record["seed_sha256"] != seed["sha256"]:
        return {"verified": False, "reason": "the seed changed after revision %s was confirmed" % record["revision"]}
    if record["decisions_sha256"] != decisions_digest(ledger):
        return {"verified": False, "reason": "the decisions changed after revision %s was confirmed" % record["revision"]}
    try:
        records = require_seed_checks(directory)
    except (ValueError, OSError, TypeError):
        return {"verified": False, "reason": "checks are missing, invalid or differ from the rendered seed"}
    if record["checks_sha256"] != checks_digest(records):
        return {"verified": False, "reason": "the checks changed after revision %s was confirmed" % record["revision"]}
    return {"verified": True, "reason": "revision %s as confirmed" % record["revision"]}


def confirm_seed(directory, revision, source):
    """Rewrite line 1 of a draft seed to the confirmed line and add `Confirmed by:`. Refuses before writing anything.

    The helper cannot know that a human affirmed; it makes the caller name the revision and quote the human's words. It
    records them, with the confirmed seed's bytes, decisions and check records, in the confirmation record.
    """
    directory = Path(directory)
    require(text(revision), "--revision is required: the label shown to the human")
    require(text(source), "--source is required: quote the human's words that confirmed this revision")
    require("\n" not in source and "\r" not in source, "--source must be one line")
    with writer(directory):
        ledger = load(directory)
        require(ledger.get("operator", "human") == "human", "a simulated session cannot be confirmed: its seed stays %r" % STATUS_SIMULATED)
        require(ledger["status"] == "active", "session is not active (%s): confirm before ending it" % ledger["status"])
        require(ledger["origin"] is not None, "the goal is not confirmed yet")
        unresolved = [n["id"] for n in ledger["nodes"] if n["status"] == "unresolved"]
        require(not unresolved, "unresolved nodes remain: " + ", ".join(unresolved))
        flagged = sorted(review_flags(ledger["nodes"]))
        require(not flagged, "nodes flagged for review remain: " + ", ".join(flagged))
        lines = seed_lines(directory)
        first = seed_line_one(lines)
        require(first != STATUS_CONFIRMED, "the seed is already confirmed (line 1 is %r); to confirm a change, save a new "
                "revision with line 1 %r and no 'Confirmed by:' line, show it, and confirm that" % (first, STATUS_DRAFT))
        require(first == STATUS_DRAFT, "line 1 of %s must be exactly %r to confirm it, found %r" % (SEED_FILE, STATUS_DRAFT, first))
        stale = [i + 1 for i, line in enumerate(lines) if i and line.startswith("Confirmed by:")]
        require(not stale, "%s still has an earlier 'Confirmed by:' line (line %s); remove it from the new draft"
                % (SEED_FILE, ", ".join(map(str, stale))))
        found = [(i, re.match(r"revision:[ \t]*(\S+)", line)) for i, line in enumerate(lines) if i > 0]
        found = [(i, m.group(1)) for i, m in found if m]
        require(found, "%s has no 'revision:' line, so the revision cannot be matched" % SEED_FILE)
        index, label = found[0]
        require(label == revision.strip(), "revision %r does not match the file's revision line (%r)" % (revision.strip(), label))
        require_seed_body(directory, "cannot confirm")
        require_seed_decisions(directory, ledger, "cannot confirm")
        records = require_seed_checks(directory)
        first_end = split_terminator(lines[0])[1] or "\n"
        body, terminator = split_terminator(lines[index])
        note = 'Confirmed by: "%s" (revision %s, %s). Not approved for implementation.' % (
            source.strip(), label, datetime.now(timezone.utc).date().isoformat())
        out = list(lines)
        out[0] = STATUS_CONFIRMED + first_end
        if terminator:
            out.insert(index + 1, note + terminator)
        else:
            out[index] = body + first_end
            out.insert(index + 1, note)
        path = directory / SEED_FILE
        confirmed = "".join(out)
        record = {"schema_version": 2, "checks_sha256": checks_digest(records), "session_id": ledger["session_id"], "operator": "human", "revision": label,
                  "source": source.strip(), "confirmed_at": now(), "seed_sha256": sha256(confirmed.encode("utf-8")), "decisions_sha256": decisions_digest(ledger)}
        # The record goes first: if the seed write then fails, the seed is still a draft and the record matches nothing.
        atomic_write_text(directory / CONFIRMATION_FILE, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        atomic_write_text(path, confirmed, stat.S_IMODE(path.stat().st_mode))
        return {"seed": str(path), "line_1": STATUS_CONFIRMED, "revision": label, "sha256": record["seed_sha256"]}


def end(directory, status, reason, no_viewer=None):
    """Completing an active session needs the viewer: viewer.py was started, or no_viewer says why it could not be."""
    require(status in ("stopped", "completed"), "invalid end status")
    directory = Path(directory)
    with writer(directory):
        ledger = load(directory)
        require(ledger["status"] in ("active", status), "ended session is read-only")
        needs_viewer = status == "completed" and ledger["status"] == "active"
        if needs_viewer:
            if no_viewer is not None:
                require(no_viewer.strip(), "--no-viewer needs a reason")
            else:
                record = viewer_record(directory)
                require(record and record.get("pid"), "completion needs the viewer: start viewer.py, "
                        "or pass --no-viewer with the reason it could not run")
        state = editable(ledger)
        state.update(status=status, current_question=None)
        result = transition(ledger, state, ledger["version"], reason)
        if status == "completed":
            human = ledger.get("operator", "human") == "human"
            require_seed_line(directory, STATUS_CONFIRMED if human else STATUS_SIMULATED)
            if human:
                handoff = verify_handoff(directory, ledger)
                require(handoff["verified"], "cannot complete: %s; save the current revision as a draft, show it, and "
                        "confirm it again with `seed confirm`" % handoff["reason"])
            require_seed_decisions(directory, ledger, "cannot complete")
            require_seed_checks(directory)
        declined = None
        if needs_viewer and no_viewer is not None:
            def declined():
                atomic_write_text(directory / "viewer.json", json.dumps({"declined": no_viewer.strip(), "at": now()}))
        return commit(directory, ledger, result, declined)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--root", type=Path)
    init.add_argument("--operator", choices=OPERATORS, default="human")
    read = commands.add_parser("read")
    read.add_argument("session", type=Path)
    read.add_argument("--editable", action="store_true",
                      help="print {expected_version, state} ready to edit and publish, without helper-owned fields")
    update = commands.add_parser("publish")
    update.add_argument("session", type=Path)
    update.add_argument("input", type=Path)
    finish = commands.add_parser("end")
    finish.add_argument("session", type=Path)
    finish.add_argument("--status", choices=("stopped", "completed"), required=True)
    finish.add_argument("--reason", required=True)
    finish.add_argument("--no-viewer", metavar="REASON", help="complete without a running viewer, stating why")
    commands.add_parser("status").add_argument("session", type=Path)
    seed = commands.add_parser("seed").add_subparsers(dest="seed_command", required=True)
    confirm = seed.add_parser("confirm", help="rewrite line 1 of a draft seed to the confirmed line")
    confirm.add_argument("session", type=Path)
    confirm.add_argument("--revision", required=True, help="the revision label the human confirmed, as in the seed's revision line")
    confirm.add_argument("--source", required=True, help="the human's words that confirmed it")
    seed.add_parser("decisions", help="print the seed's Decisions and Assumed sections from the ledger").add_argument("session", type=Path)
    checks = seed.add_parser("checks", help="store, show or render strict acceptance records").add_subparsers(dest="checks_command", required=True)
    for name in ("set", "show", "render"):
        command = checks.add_parser(name)
        command.add_argument("session", type=Path)
        if name == "set":
            command.add_argument("input", type=Path, help="non-empty JSON array of check records")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = {"session": str(create(args.root, args.operator))}
        elif args.command == "read":
            result = editable_payload(args.session) if args.editable else load(args.session)
        elif args.command == "end":
            result = end(args.session, args.status, args.reason, args.no_viewer)
        elif args.command == "status":
            result = status(args.session)
        elif args.command == "seed" and args.seed_command == "decisions":
            sys.stdout.write(decisions_block(load(args.session)))
            return
        elif args.command == "seed" and args.seed_command == "checks":
            if args.checks_command == "set":
                result = set_checks(args.session, json.loads(args.input.read_text(encoding="utf-8")))
            else:
                load(args.session)
                records = read_checks(args.session)
                if args.checks_command == "render":
                    sys.stdout.write(checks_block(records))
                    return
                result = records
        elif args.command == "seed":
            result = confirm_seed(args.session, args.revision, args.source)
        else:
            payload = json.loads(args.input.read_text())
            result = publish(args.session, **payload)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except SaveOutcome as outcome:
        report = str(outcome) + "\n" + json.dumps(outcome.diagnostic(), ensure_ascii=False) + "\n"
        if outcome.save_state != "saved":
            parser.exit(1, report)
        print(json.dumps(outcome.result, ensure_ascii=False, allow_nan=False))
        sys.stderr.write(report)
    except (ValueError, OSError, KeyError, TypeError) as error:
        if args.command in ("publish", "end"):
            refused = SaveOutcome(str(error), "not_saved", args.session, None, "unchanged")
            parser.exit(1, str(error) + "\n" + json.dumps(refused.diagnostic(), ensure_ascii=False) + "\n")
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
