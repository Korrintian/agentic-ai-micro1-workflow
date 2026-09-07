# Hallucination Trap: Code-Submission Verification Agent

## 1. Who has this problem, and what's the bottleneck

**Who:** technical evaluators verifying that a submitted code sample is genuinely
correct and genuinely the applicant's own understanding — for example,
qualification reviewers at AI-training platforms checking a submitted code
sample before approving someone to work.

**Bottleneck:** candidates increasingly lean on an LLM to produce code that
*looks* right. A quick read, or a single "does this look okay?" prompt, misses
imports that don't exist, methods that were never real, and logic that runs
without crashing but is simply wrong. A reviewer skimming dozens of
submissions a week does not have time to execute every one and reason
carefully about every line.

## 2. Baseline vs. agent

**Baseline** (the "before" picture): one direct prompt to an LLM — "does this
code look right?" — no tools, no execution, nothing else. This is what a
reviewer leaning on a single ChatGPT-style question would get today.

**Agent** (the "after" picture): an LLM that is given two tools and decides
for itself which to use:

- `check_imports` — parses the code and splits its imports into Python
  standard-library vs. not, using Python's own module list rather than a
  hand-maintained allowlist. A non-stdlib import is a signal, not an
  automatic fail — `numpy` and `requests` are real.
- `run_sandboxed` — actually executes the code in an isolated subprocess
  and reports whether it ran, plus stdout/stderr.

The agent reads whatever the tools report, **and** reads the code itself,
then commits to a verdict with a reason. That second part matters: code can
run cleanly and still be wrong, and no tool here can catch that on its own.

Baseline and agent see the exact same 11 candidate files, in the same
order, with no extra resources given to either side.

## 3. Improvement changelog

| Stage | What was tried and why | Evidence | Decision / learning |
|---|---|---|---|
| Baseline (first draft) | A hardcoded stub standing in for "the baseline" — returned the same PASS result regardless of input. | Identical output on all 10 test files, including the 5 broken ones. | Removed. A baseline that doesn't look at the input isn't a baseline — the "improvement" it implied wasn't a measured result. |
| Iteration 1 | Replaced the stub with one real LLM call per the brief's own baseline definition (a single direct prompt). | Baseline now varies with input and gets some, but not all, of the flawed candidates. | Kept — this is what a fair "before" picture actually requires. |
| Iteration 2 | First "advanced" version used a 5-module import allowlist and a sandbox, both plain deterministic code with no model call, despite being logged as agents. | `time` — a completely standard import — was flagged as hallucinated for not being on the list. Sandbox execution alone reproduced the identical pass/fail verdict as the full pipeline on all 10 cases. | Removed the allowlist. Neither piece was actually agentic, and one had a real false-positive bug. |
| Iteration 3 | Replaced the allowlist with a check against Python's actual standard-library list, kept the sandbox as a tool, and put a real LLM in charge of deciding which tool(s) to call and writing the final verdict. | `time` is now correctly recognized as standard library. The agent reads tool output *and* the code itself before deciding. | Kept — this is the first version where a model is actually making decisions, not just running fixed logic labeled as one. |
| Iteration 4 | Added `candidate_11_subtly_wrong.py`: real import, runs cleanly, no crash — but computes circumference instead of area. | Neither `check_imports` nor `run_sandboxed` flags anything on this file — by design. | Kept as the one deliberately hard case. It's the only file in the set that isolates what the model's own reading contributes beyond the tools. |
| Final | Baseline = one honest prompt. Agent = tool-selecting LLM + two tools + a verdict that has to cite evidence and consider logic the tools can't check. | See `outputs/results.json` after running the benchmark. | Main contribution: moving verification from "does it crash" to "is it actually right," using the tools as evidence rather than as the verdict. |

## 4. Evaluation

Run `python src/run_benchmark.py` (see REPRODUCTION.md) to regenerate
`outputs/results.json` from a clean environment. The 11 candidates are a
fixed ground truth: files 1–5 should pass, files 6–11 should fail. The
script prints a `baseline correct: X/11` vs `agent correct: X/11` summary at
the end — those two numbers are the primary outcome metric for this project.

Candidate 11 is the one challenging case: it passes every mechanical check
and only fails if the model actually reads what the code computes.

## 5. Main failure mode and hot take

**Main failure mode:** if `run_sandboxed`'s isolation is not kept strict
(timeout, no filesystem/network access beyond what's needed), a malicious
candidate submission could use the sandbox step itself as an attack surface.
This project keeps it to a subprocess with a timeout and nothing else, which
is enough for this evaluation but not hardened for production use on
untrusted code from strangers.

**Hot take:** a verification tool built only from static analysis and
execution checks converges on "does it crash," not "is it right" — and
crashing is the easy 80% of this problem. The two of those together
couldn't distinguish `candidate_11` from `candidate_01`. It took a model
actually reading the logic to close that last gap, which suggests
verification tools for AI-generated code need a reasoning step even when
strong static and dynamic checks already exist — not as a replacement for
them, but because they're checking a different thing.

## Ground rules followed

- No real candidate data used — all 11 code samples are synthetic, written
  for this project.
- This tool produces a scorecard and reasoning for a human reviewer; it does
  not autonomously reject anyone. A qualified person makes the actual call.
- No credentials are stored in this repo. Set `GEMINI_API_KEY` as an
  environment variable, never in code.
