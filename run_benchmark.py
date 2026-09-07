"""
Runs the baseline and the agent on every sample candidate, then writes the
full results table to outputs/results.json (and prints it) so the numbers
in the README are something a judge can actually re-derive, not just a
claim.

Usage:
    export GEMINI_API_KEY="your-key-here"      # or $env:GEMINI_API_KEY = "..." on PowerShell
    cd ai-candidate-verifier
    python src/run_benchmark.py

If you're hitting free-tier rate limits, you don't have to hand-edit this
file to run a subset -- set CANDIDATES to a comma-separated list of exact
filenames, e.g. (PowerShell):
    $env:CANDIDATES = "candidate_01_valid_math.py,candidate_07_hallucinated_method.py,candidate_11_subtly_wrong.py"
    python src/run_benchmark.py
Leave it unset to run all 11.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_candidate import run_baseline, run_agent  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES_DIR = os.path.join(PROJECT_ROOT, "data", "sample_candidates")
RESULTS_PATH = os.path.join(PROJECT_ROOT, "outputs", "results.json")

# Seconds to pause between candidates. Each candidate makes 3 model calls
# (1 baseline + 2 agent); a short gap between candidates spreads those out
# so a small free-tier per-minute limit is less likely to get tripped.
PAUSE_BETWEEN_CANDIDATES = float(os.environ.get("PAUSE_SECONDS", "3"))


def main():
    all_files = sorted(f for f in os.listdir(SAMPLES_DIR) if f.endswith(".py"))

    subset = os.environ.get("CANDIDATES")
    if subset:
        requested = [name.strip() for name in subset.split(",") if name.strip()]
        missing = [name for name in requested if name not in all_files]
        if missing:
            raise SystemExit(f"CANDIDATES lists file(s) not found in {SAMPLES_DIR}: {missing}")
        files = requested
    else:
        files = all_files

    print(f"Running benchmark across {len(files)} candidate(s) (of {len(all_files)} available)...\n")

    results = []
    for i, fname in enumerate(files):
        with open(os.path.join(SAMPLES_DIR, fname)) as f:
            code = f.read()

        print(f"  -> {fname}")
        baseline = run_baseline(code)
        agent = run_agent(code)

        if i < len(files) - 1 and PAUSE_BETWEEN_CANDIDATES > 0:
            time.sleep(PAUSE_BETWEEN_CANDIDATES)

        results.append(
            {
                "file": fname,
                "baseline_verdict": baseline["verdict"],
                "agent_verdict": agent.get("verdict"),
                "agent_score": agent.get("score"),
                "agent_flags": agent.get("flags", []),
                "agent_reasoning": agent.get("reasoning", ""),
            }
        )

    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(results, f, indent=2)

    # Ground truth: files 01-05 and 11 are meant to PASS, 06-10 are meant to FAIL.
    # (11 is the "quietly wrong" case -- see README for why it's the interesting one.)
    should_fail = {
        "candidate_06_hallucinated_package.py",
        "candidate_07_hallucinated_method.py",
        "candidate_08_runtime_zero_division.py",
        "candidate_09_infinite_timeout.py",
        "candidate_10_syntax_error.py",
        "candidate_11_subtly_wrong.py",
    }

    def score(verdict_key):
        correct = 0
        for r in results:
            expected_fail = r["file"] in should_fail
            got_fail = r[verdict_key] == "FAIL"
            if expected_fail == got_fail:
                correct += 1
        return correct, len(results)

    b_correct, total = score("baseline_verdict")
    a_correct, _ = score("agent_verdict")

    print(f"\n{'file':<40} {'baseline':<10} {'agent':<8} score")
    for r in results:
        print(f"{r['file']:<40} {r['baseline_verdict']:<10} {r['agent_verdict']:<8} {r['agent_score']}")

    print(f"\nBaseline correct: {b_correct}/{total}")
    print(f"Agent correct:    {a_correct}/{total}")
    print(f"\nFull results written to {RESULTS_PATH}")
    print("Trajectory log written to outputs/trajectories/trajectory_log.json")


if __name__ == "__main__":
    main()
