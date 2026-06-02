#!/usr/bin/env python3
"""Autonomous loop driver for the RDV-Insertion Rails->Django port.

One invocation = one orchestration tick. The driver:

  1. Reads the port control files from the dj-rdv-insertion integration branch
     (`claude/plan-django-rewrite-RaF3g`) via the GitHub Contents API.
  2. Derives the current phase from `port/rig/CHECKLIST.md` and
     `port/backlog.yaml`, following `port/README.md` orchestration.
  3. Asks the autometa-jobs orchestrator which loop runs are already active.
  4. Triggers the loop run(s) that should run and are not already running,
     never exceeding the autometa-jobs dispatch concurrency.

It is idempotent: re-running it mid-flight starts no duplicate runs. It does not
gate on any human sign-off: a slice closes on automated verification alone (the
differential rig is green, plus Playwright for web slices). L1 closes a rig-green
slice itself (`status: done`) and logs a completion entry to `REVIEW_LOG.md`;
the driver advances on that `status: done` and cascades to dependents.

Designed to be safe to crash: every decision is re-derived from durable state
(the repo + the orchestrator DB). It holds no local state of its own. It is
meant to run unattended on a ~30-minute Scaleway cron.

Environment:
  PIPOMETA_URL       orchestrator base URL              (required)
  PIPOMETA_API_KEY   orchestrator bearer key            (required)
  GIT_PAT            GitHub token, contents:read on the repo (required)
  PORT_REPO          owner/name, default louije/rdv-insertion
  PORT_BRANCH        integration branch, default claude/plan-django-rewrite-RaF3g
  DRIVER_DRY_RUN     if set, decide and log but trigger nothing
  DRIVER_CONCURRENCY autometa-jobs dispatch concurrency, default 3

Exit code is 0 on a clean tick (including "nothing to do") and 1 only if the
driver could not reach the orchestrator or the repo -- a transient API/network
error must not crash the cron into a bad state, so all such failures are caught,
logged to stderr, and surfaced as exit 1 without a traceback.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

# ---- pipeline ids (registered by scripts/register_port_loops.py) -----------

PIPELINES = {
    "L0": "<pipeline-id-L0>",  # port-L0-harness-builder
    "L1": "<pipeline-id-L1>",  # port-L1-slice-porter
    "L2": "<pipeline-id-L2>",  # port-L2-divergence-triage
    "L3": "<pipeline-id-L3>",  # port-L3-idiomatic-auditor
    "L4": "<pipeline-id-L4>",  # port-L4-coverage-scout
}

# A run in one of these states still occupies a worker / concurrency slot.
ACTIVE_RUN_STATUSES = {"queued", "starting", "running"}

REPO = os.environ.get("PORT_REPO", "louije/rdv-insertion")
BRANCH = os.environ.get("PORT_BRANCH", "claude/plan-django-rewrite-RaF3g")
CONCURRENCY = int(os.environ.get("DRIVER_CONCURRENCY", "3"))
DRY_RUN = bool(os.environ.get("DRIVER_DRY_RUN"))


# ---- tiny logging helper ---------------------------------------------------

def log(msg):
    print(f"[drive_port_loops] {msg}", flush=True)


class TickError(RuntimeError):
    """A failure that should abort the tick without a traceback (exit 1)."""


# ---- HTTP with bounded retry ----------------------------------------------

def _http(method, url, headers, body=None, retries=3):
    """One HTTP call with a small exponential backoff on transient failures.

    A prior attempt at this driver crashed outright on API/network blips. Here
    every network call goes through this wrapper: 5xx and connection errors are
    retried a few times, then raised as a TickError (caught in main()).
    """
    data = json.dumps(body).encode() if body is not None else None
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, raw
        except urllib.error.HTTPError as e:
            raw = e.read()
            # 4xx is a real error -- a retry will not help.
            if e.code < 500:
                raise TickError(f"{method} {url} -> {e.code}: {raw.decode(errors='replace')[:300]}")
            last_err = TickError(f"{method} {url} -> {e.code} (attempt {attempt + 1})")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = TickError(f"{method} {url} -> network error: {e} (attempt {attempt + 1})")
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    raise last_err or TickError(f"{method} {url} -> exhausted retries")


# ---- GitHub: read port control files --------------------------------------

def _gh_raw(path, allow_missing=False):
    """Fetch one file from the integration branch as raw text via GitHub API.

    If allow_missing is set, a 404 returns "" instead of raising.
    """
    token = os.environ.get("GIT_PAT")
    if not token:
        raise TickError("GIT_PAT is not set -- cannot read the port repo")
    url = f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.raw+json",
        "User-Agent": "pipometa-port-driver",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        _, raw = _http("GET", url, headers)
    except TickError as e:
        if allow_missing and " -> 404:" in str(e):
            return ""
        raise
    return raw.decode("utf-8")


# ---- orchestrator: list active runs ----------------------------------------

def _orch_headers():
    key = os.environ.get("PIPOMETA_API_KEY")
    if not key:
        raise TickError("PIPOMETA_API_KEY is not set")
    return {"Authorization": f"Bearer {key}", "User-Agent": "pipometa-port-driver"}


def active_runs_by_pipeline(base):
    """Map pipeline_id -> count of runs in queued/starting/running.

    This is the idempotency check: the driver only triggers a loop that has no
    active run, so a re-tick mid-flight never piles duplicates.
    """
    headers = _orch_headers()
    counts = {pid: 0 for pid in PIPELINES.values()}
    for status in ACTIVE_RUN_STATUSES:
        url = f"{base}/runs?status={status}&limit=200"
        _, raw = _http("GET", url, headers)
        for run in json.loads(raw):
            pid = run.get("pipeline_id")
            if pid in counts:
                counts[pid] += 1
    return counts


def trigger(base, loop, idempotency_key):
    """POST a run for one loop pipeline. Idempotency key dedupes server-side."""
    pid = PIPELINES[loop]
    if DRY_RUN:
        log(f"DRY RUN: would trigger {loop} ({pid}) key={idempotency_key}")
        return None
    headers = dict(_orch_headers())
    headers["Content-Type"] = "application/json"
    url = f"{base}/pipelines/{pid}/runs"
    _, raw = _http("POST", url, headers, body={"idempotency_key": idempotency_key})
    run = json.loads(raw)
    log(f"triggered {loop} -> run {run.get('id')} status={run.get('status')}")
    return run


# ---- port-state parsing ----------------------------------------------------

def rig_complete(checklist_md):
    """True once every C1..C13 box in rig/CHECKLIST.md is checked.

    The checklist uses `- [ ]` / `- [x]`. The harness is complete only when no
    unchecked box remains and at least one box exists (guards against an empty
    or malformed file being read as 'done').
    """
    checked = unchecked = 0
    for line in checklist_md.splitlines():
        s = line.strip()
        if s.startswith("- [x]") or s.startswith("- [X]"):
            checked += 1
        elif s.startswith("- [ ]"):
            unchecked += 1
    return unchecked == 0 and checked > 0


def parse_backlog(backlog_yaml):
    """Minimal YAML reader for port/backlog.yaml.

    backlog.yaml is a stable, hand-maintained, flat-ish structure. To keep the
    driver dependency-free (stdlib only, like register_port_loops.py) it is
    parsed with a small purpose-built reader rather than pulling in PyYAML.

    Returns a list of slice dicts: {id, priority, status, phase, depends_on,
    dod_all_done}. Anything it cannot parse it leaves conservative (status as
    read, dod_all_done False) so the driver fails safe.
    """
    slices = []
    cur = None
    in_slices = False
    in_dod = False
    dod_done = []

    def flush():
        if cur is not None:
            cur["dod_all_done"] = bool(dod_done) and all(dod_done)
            slices.append(cur)

    for raw in backlog_yaml.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("slices:"):
            in_slices = True
            continue
        if not in_slices:
            continue
        indent = len(line) - len(line.lstrip())

        # A new slice starts with `  - id: <name>`.
        if line.lstrip().startswith("- id:"):
            flush()
            cur = {
                "id": line.split("- id:", 1)[1].strip(),
                "priority": 999,
                "status": "todo",
                "phase": None,
                "depends_on": [],
            }
            dod_done = []
            in_dod = False
            continue
        if cur is None:
            continue

        body = line.strip()
        # `dod:` opens the list of dod items; it closes at `done_when:`.
        if body == "dod:":
            in_dod = True
            continue
        if body.startswith("done_when:"):
            in_dod = False
            continue
        if in_dod and body.startswith("- {"):
            # e.g.  - {id: a1, done: false, desc: "..."}
            dod_done.append("done: true" in body or "done:true" in body)
            continue

        # Slice-level scalar fields (indent of a slice's own keys, ~4 spaces).
        if indent <= 6:
            if body.startswith("priority:"):
                try:
                    cur["priority"] = int(body.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif body.startswith("status:"):
                cur["status"] = body.split(":", 1)[1].strip()
            elif body.startswith("phase:"):
                cur["phase"] = body.split(":", 1)[1].strip()
            elif body.startswith("depends_on:"):
                inline = body.split(":", 1)[1].strip()
                if inline.startswith("[") and inline.endswith("]"):
                    items = inline[1:-1].strip()
                    cur["depends_on"] = [x.strip() for x in items.split(",") if x.strip()]
                else:
                    cur["depends_on"] = []
    flush()
    return slices


# ---- the orchestration decision -------------------------------------------

def decide(checklist_md, backlog_yaml):
    """Return (decisions, notes).

    decisions: list of {loop, idempotency_key, reason} the driver wants to run.
    notes:     human-readable lines describing the state.

    Phase logic, straight from port/README.md:

      * Bring-up is sequential. L0 (one instance) builds the rig; until the rig
        CHECKLIST is fully checked, no Phase-1 L1 slice runs. The one exception
        is slice S0 (`schema`), which L1 may port first.
      * A slice closes on automated verification alone -- the differential rig
        is green (plus Playwright for web slices). L1 closes a rig-green slice
        itself by setting `status: done` in backlog.yaml and logging a
        completion entry to REVIEW_LOG.md. No human sign-off gates progress.
      * Once the rig is complete, L1 runs as up to `CONCURRENCY` parallel
        instances, one per ready slice. A slice is ready when its status is
        todo/in_progress and every `depends_on` slice is `status: done`.
        L4 (coverage scout) runs as a permanent extra lane once L0 is done.
        L2/L3 run on the integration branch between merges.
      * The driver self-advances: when a slice flips to `status: done` in the
        backlog, its dependents become ready and the driver triggers L1 for
        them. It never waits on REVIEW_LOG.md -- that file is a post-hoc record.

    Concurrency: autometa-jobs dispatches one worker per tick but does not cap the
    number of concurrently running workers, so the driver itself is the cap.
    The driver never asks for more new runs than free slots, counting runs it
    just decided to trigger plus runs already active on the orchestrator.
    """
    notes = []
    decisions = []
    day = time.strftime("%Y%m%d")

    rig_done = rig_complete(checklist_md)
    slices = parse_backlog(backlog_yaml)
    by_id = {s["id"]: s for s in slices}

    notes.append(f"rig CHECKLIST complete: {rig_done}")
    done_now = sorted(s["id"] for s in slices if s["status"] == "done")
    notes.append(f"slices parsed: {len(slices)}; closed (status: done): {done_now or 'none'}")

    # ---- Phase 0: build the rig -------------------------------------------
    if not rig_done:
        notes.append("PHASE 0 (rig build): L0 runs solo until HARNESS COMPLETE.")
        decisions.append({
            "loop": "L0",
            "idempotency_key": f"L0-rig-{day}",
            "reason": "rig CHECKLIST has unchecked components",
        })
        # S0 (schema) may proceed in parallel with L0. It closes itself on its
        # automated gate -- no human gate. The driver runs L1 for S0 until S0's
        # status is `done`: while dod items remain L1 completes them, and on the
        # iteration where the dod is complete L1 flips the slice to `done` and
        # writes its completion entry.
        schema = by_id.get("schema")
        if schema and schema["status"] != "done":
            reason = ("S0 schema is dod-complete -- L1 closes the slice"
                      if schema["dod_all_done"]
                      else "S0 schema slice still has unfinished dod items")
            decisions.append({
                "loop": "L1",
                "idempotency_key": f"L1-schema-{day}",
                "reason": reason,
            })
        return decisions, notes

    # ---- L0 done: L4 coverage scout runs as a permanent lane --------------
    decisions.append({
        "loop": "L4",
        "idempotency_key": f"L4-coverage-{day}",
        "reason": "rig is complete; L4 grows the corpus as a permanent lane",
    })

    # ---- S0 must close before Phase 1 -------------------------------------
    # S0 closes itself on its automated gate (dod-complete -> L1 sets done).
    # While S0 is not `done`, the driver keeps running L1 for it; Phase 1
    # waits, but only on S0 actually being closed, not on a sign-off.
    schema = by_id.get("schema")
    if schema and schema["status"] != "done":
        if not schema["dod_all_done"]:
            decisions.append({
                "loop": "L1",
                "idempotency_key": f"L1-schema-{day}",
                "reason": "S0 schema slice still has unfinished dod items",
            })
        else:
            notes.append("S0 schema is dod-complete; L1 will close it "
                          "(status: done) on its next iteration. Phase 1 "
                          "starts as soon as S0 is closed.")
            decisions.append({
                "loop": "L1",
                "idempotency_key": f"L1-schema-{day}",
                "reason": "S0 schema is dod-complete -- L1 closes the slice",
            })
        return decisions, notes

    # ---- Phase 1 + 2: parallel L1 slices ----------------------------------
    # A slice satisfies a downstream depends_on once it is `status: done`,
    # which L1 sets on rig-green. There is no separate sign-off gate.
    done_ids = {s["id"] for s in slices if s["status"] == "done"}

    ready = []
    for s in sorted(slices, key=lambda x: x["priority"]):
        if s["id"] == "schema":
            continue
        if s["id"] in done_ids:
            continue
        deps_met = all(d in done_ids for d in s["depends_on"])
        if not deps_met:
            continue
        ready.append(s["id"])

    if ready:
        notes.append(f"L1-ready slices (deps met, not yet closed): {', '.join(ready)}")
    else:
        notes.append("no L1-ready slices this tick")

    # L1 fills slots up to CONCURRENCY, leaving room for L4 already decided.
    # L4 occupies one slot; L1 may take the rest. The orchestrator-side active
    # check in main() does the final clamp against runs already in flight.
    for sid in ready:
        decisions.append({
            "loop": "L1",
            "idempotency_key": f"L1-{sid}-{day}",
            "reason": f"slice {sid} is ready (deps met, not yet closed)",
        })

    # ---- L2 / L3: integration-branch lanes between merges -----------------
    # They must NOT run while an L1 slice is in flight on the integration
    # branch's code. They are scheduled only on a quiet tick: no ready L1 work
    # and at least one slice already closed (something to triage / audit).
    if not ready and done_ids:
        for loop, name in (("L2", "divergence triage"), ("L3", "idiomatic audit")):
            decisions.append({
                "loop": loop,
                "idempotency_key": f"{loop}-{day}",
                "reason": f"quiet tick, closed slices exist -- run {name}",
            })
    elif ready:
        notes.append("L2/L3 held: L1 slices are in flight on the integration branch")

    return decisions, notes


# ---- main ------------------------------------------------------------------

def main():
    base = os.environ.get("PIPOMETA_URL", "").rstrip("/")
    if not base:
        log("FATAL: PIPOMETA_URL is not set")
        return 1

    log(f"tick start -- repo={REPO} branch={BRANCH} concurrency={CONCURRENCY} dry_run={DRY_RUN}")

    try:
        checklist_md = _gh_raw("port/rig/CHECKLIST.md")
        backlog_yaml = _gh_raw("port/backlog.yaml")
        decisions, notes = decide(checklist_md, backlog_yaml)
        active = active_runs_by_pipeline(base)
    except TickError as e:
        log(f"FATAL: {e}")
        return 1

    for n in notes:
        log(n)

    active_total = sum(active.values())
    breakdown = ", ".join(f"{loop}={active.get(pid, 0)}"
                          for loop, pid in PIPELINES.items() if active.get(pid, 0))
    log(f"active runs on orchestrator: total={active_total}"
        + (f" ({breakdown})" if breakdown else ""))

    # Drop decisions whose loop already has an active run -- idempotency.
    pending = []
    for d in decisions:
        pid = PIPELINES[d["loop"]]
        if active.get(pid, 0) > 0:
            log(f"skip {d['loop']}: already has an active run ({d['reason']})")
            continue
        pending.append(d)

    # Clamp to free concurrency slots. active_total is the current load; each
    # trigger we accept adds one. Never exceed CONCURRENCY.
    free = max(0, CONCURRENCY - active_total)
    if len(pending) > free:
        log(f"concurrency: {len(pending)} wanted, {free} free slot(s) -- deferring "
            f"{len(pending) - free} to a later tick")
    to_trigger = pending[:free]

    triggered = 0
    for d in to_trigger:
        log(f"decide {d['loop']}: {d['reason']}")
        try:
            trigger(base, d["loop"], d["idempotency_key"])
            triggered += 1
        except TickError as e:
            # One failed trigger must not abort the whole tick -- the next cron
            # tick re-derives state and retries. Log and move on.
            log(f"WARN: trigger {d['loop']} failed: {e}")

    if not decisions:
        log("nothing to do this tick")
    log(f"tick done -- {triggered} run(s) triggered, "
        f"{len(pending) - len(to_trigger)} deferred")
    return 0


if __name__ == "__main__":
    sys.exit(main())
