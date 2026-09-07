# Reproduction Guide

Written for someone starting from a clean environment with no context on
this project beyond this file.

## Requirements

- Python 3.10 or later (needed for `sys.stdlib_module_names`, used by
  `check_imports`).
- A Google Gemini API key (free tier is enough) from Google AI Studio.
- No other accounts or paid services required.

## Setup

```bash
cd ai-candidate-verifier
pip install google-genai
export GEMINI_API_KEY="your-key-here"
```

Before running for real, open `src/verify_candidate.py` and confirm
`MODEL_NAME` against the current model list in your own Google AI Studio
account — Gemini's free-tier model names are updated over time and I could
not verify a specific one from the environment this was built in. You can
also override it without editing the file:

```bash
export VERIFIER_MODEL="gemini-flash-latest"   # or whatever your account shows
```

## Running it

```bash
python src/run_benchmark.py
```

This runs both the baseline and the agent against all 11 files in
`data/sample_candidates/`, prints a results table to the console, and
writes:

- `outputs/results.json` — the full per-candidate results table.
- `outputs/trajectories/trajectory_log.json` — every model call and tool
  call, in order, including the raw tool-selection response, each tool's
  output, and the final verdict with reasoning.

## Expected output

Ground truth: candidates 1–5 are valid, candidates 6–11 are flawed
(6–10 crash or hang; 11 runs cleanly but computes the wrong thing).

A working baseline typically catches some but not all of 6–11 — it has no
way to verify execution or check logic, only impressions from reading the
prompt. A working agent should get close to 11/11, with candidate 11 being
the one where a weak agent (or a weak model) is most likely to slip, since
nothing but careful reading catches it.

If you see `RuntimeError: Model call failed after 4 attempts`, you've most
likely hit the free-tier rate limit — wait a minute and rerun, or reduce the
candidate set.

## Runtime and cost

- Runtime: roughly 30–90 seconds for all 11 candidates (3 model calls each
  for the agent path, 1 for baseline — 44 calls total), depending on API
  latency. Sandboxed execution itself is near-instant except for
  `candidate_09`, which intentionally times out after 5 seconds.
- Cost: $0 on Gemini's free tier at this volume. If you re-run the full
  benchmark dozens of times while iterating, watch your daily request quota
  in Google AI Studio.
