#!/usr/bin/env python3
"""Register the 5 RDV-Insertion port loop pipelines (L0-L4) on the orchestrator.

Each pipeline's system_prompt is a clone/commit/push preamble followed by the
full text of the corresponding port/loops/Lx.md file. Idempotent on name: a
pipeline whose name already exists is PATCHed in place rather than duplicated.

Usage:
  PIPOMETA_API_KEY=... PIPOMETA_URL=... python3 register_port_loops.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

LOOPS_DIR = "/Users/louije/Development/gip/dj-rdv-insertion/port/loops"
BRANCH = "claude/plan-django-rewrite-RaF3g"
JOB_DEFINITION_ID = "<worker-job-definition-id>"

LOOPS = [
    ("port-L0-harness-builder", "L0_harness_builder.md"),
    ("port-L1-slice-porter", "L1_slice_porter.md"),
    ("port-L2-divergence-triage", "L2_divergence_triage.md"),
    ("port-L3-idiomatic-auditor", "L3_idiomatic_auditor.md"),
    ("port-L4-coverage-scout", "L4_coverage_scout.md"),
]

PREAMBLE = f"""You run as an autonomous autometa-jobs worker. Your job is to execute ONE iteration
of a Ralph-style loop against the RDV-Insertion Rails->Django port repository.

IMPORTANT — your Bash tool resets the working directory to /app between every
call; a bare `cd` does NOT persist. Either prefix each command with
`cd "$HOME/rdv-insertion" && ...`, or use `git -C "$HOME/rdv-insertion" ...` and
absolute paths. The repo lives at $HOME/rdv-insertion for the whole run.

REPOSITORY SETUP — do this first, before anything else:

1. The environment variable GIT_PAT holds a GitHub token with contents:write on
   louije/rdv-insertion. Never print it, never echo it, never write it to a file
   that gets committed.
2. Clone the repo into your home directory (/app is not writable):

       cd "$HOME" && git clone https://x-access-token:$GIT_PAT@github.com/louije/rdv-insertion.git

3. Check out the integration branch and identify yourself for commits:

       git -C "$HOME/rdv-insertion" checkout {BRANCH}
       git -C "$HOME/rdv-insertion" config user.name "pipometa-worker"
       git -C "$HOME/rdv-insertion" config user.email "pipometa@localhost"

4. All work happens inside $HOME/rdv-insertion. File paths in the loop
   instructions below (e.g. `port/blockers.md`, `port/loops/...`) are relative
   to that repo root — resolve them against $HOME/rdv-insertion.

AFTER the loop iteration finishes its work and has committed:

5. Push your commit(s) back to the remote branch:

       git -C "$HOME/rdv-insertion" push origin {BRANCH}

   If the push is rejected because the branch advanced, run
   `git -C "$HOME/rdv-insertion" pull --rebase origin {BRANCH}` then push again.
6. If the loop produced no commit this iteration (e.g. it only printed a
   completion sentinel or a BLOCKED/WAITING line), there is nothing to push;
   that is fine. Report what happened.

The loop's own exit sentinels (HARNESS COMPLETE, ALL SLICES PORTED, DIFF CLEAN,
AUDIT CLEAN, COVERAGE MET, or a BLOCKED:/WAITING: line) still apply — surface
them in your final output exactly as the loop specifies.

================ LOOP INSTRUCTIONS BELOW ================

"""

CONFIG = {
    "scaleway_job_definition_id": JOB_DEFINITION_ID,
    "allowed_tools": ["Bash", "Read", "Edit", "Write", "Glob", "Grep"],
    "max_turns": 200,
}


def api(method, url, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"{method} {url} -> {e.code}: {e.read().decode()}\n")
        raise


def main():
    key = os.environ["PIPOMETA_API_KEY"]
    base = os.environ["PIPOMETA_URL"].rstrip("/")

    existing = {p["name"]: p["id"] for p in api("GET", f"{base}/pipelines", key)}

    results = []
    for name, filename in LOOPS:
        with open(os.path.join(LOOPS_DIR, filename), encoding="utf-8") as fh:
            loop_text = fh.read()
        system_prompt = PREAMBLE + loop_text
        payload = {"name": name, "system_prompt": system_prompt, "config": CONFIG}

        if name in existing:
            pid = existing[name]
            api("PATCH", f"{base}/pipelines/{pid}", key,
                {"system_prompt": system_prompt, "config": CONFIG})
            results.append((name, pid, "updated"))
        else:
            created = api("POST", f"{base}/pipelines", key, payload)
            results.append((name, created["id"], "created"))

    for name, pid, action in results:
        print(f"{action:8s} {name:28s} {pid}")


if __name__ == "__main__":
    main()
