"""
Verification engine for the Hallucination & Plagiarism Trap agent.

Two paths are run on identical input so the comparison is fair:

  - run_baseline() -- the "before" picture. One direct prompt to an LLM,
    no tools, no iteration. This matches the brief's own definition of a
    baseline ("one direct prompt with basic instructions").

  - run_agent()    -- the "after" picture. An LLM decides which tool(s) to
    call, we actually run them, feed the results back, and ask the model for a
    final verdict that has to cite the evidence. This is a real agent loop:
    instructions -> tool call -> tool response -> next decision.

Tool selection is done through structured JSON in the prompt/response,
not a provider's native function-calling feature. That's a deliberate
choice: it doesn't depend on the exact function-calling wire format of
whichever provider you're using (those keep changing), and it produces a
plain, readable trajectory log for the "agent trajectories" deliverable.
You can swap in native tool-calling later (google-genai's types.Tool) once
you've confirmed the current syntax against Google's live docs.

Setup:
    pip install google-genai
    export GEMINI_API_KEY="your-key-here"

I don't have network access in the environment I'm writing this in, so I
could not call the live API to test this end to end. Everything that
doesn't require the network (the tools, the JSON parsing, the trajectory
logger) is tested. Confirm MODEL_NAME below against your own Google AI
Studio account before you rely on it.
"""

import ast
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Confirm this against your own Google AI Studio / AI for Developers account
# before you rely on it -- I can't verify a live model name from here, and
# Gemini's free-tier lineup shifts over time. "gemini-flash-latest" is
# Google's rolling alias for the current Flash model; pin an exact version
# instead if you want a fixed target for judges to reproduce later.
# ---------------------------------------------------------------------------
MODEL_NAME = os.environ.get("VERIFIER_MODEL", "gemini-flash-latest")

_client = None


def get_client():
    """Lazily creates the Gemini client. Requires the google-genai package
    and GEMINI_API_KEY (or GOOGLE_API_KEY) set in the environment."""
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client()
    return _client


def _call_model(prompt: str, max_retries: int = 4) -> str:
    """Single text-in/text-out call, with backoff for free-tier rate limits."""
    client = get_client()
    delay = 2
    last_err = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
            return (response.text or "").strip()
        except Exception as e:  # noqa: BLE001 -- surface any provider error the same way
            last_err = e
            if attempt == max_retries - 1:
                break
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"Model call failed after {max_retries} attempts: {last_err}")


def _extract_json(text: str):
    """Model responses sometimes wrap JSON in markdown fences -- strip those first."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned.strip())


class TrajectoryLogger:
    """Records every model call and tool call, in order. This file IS the
    agent-trajectories deliverable -- it should read start to finish as
    'here's what the agent was told, what it decided, what the tools said
    back, and what it concluded.'"""

    def __init__(self, log_path="outputs/trajectories/trajectory_log.json"):
        self.log_path = log_path
        self.traces = []
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

    def log_step(self, actor: str, action: str, detail, output):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "action": action,
            "detail": detail,
            "output": output,
        }
        self.traces.append(entry)
        with open(self.log_path, "w") as f:
            json.dump(self.traces, f, indent=2)


logger = TrajectoryLogger()


# ---------------------------------------------------------------------------
# Tools available to the agent. Each returns a small structured dict for the
# agent to read -- these are evidence, not verdicts on their own.
# ---------------------------------------------------------------------------

def tool_check_imports(code: str) -> dict:
    """Splits imports into stdlib vs. non-stdlib using Python's own module
    list, rather than a hand-maintained allowlist. A non-stdlib import is
    NOT automatically flagged as fake -- numpy and requests are real and
    would fail that test. It's a signal for the agent to weigh, not a
    verdict."""
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"ok": False, "error": f"SyntaxError: {e}"}

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    return {
        "ok": True,
        "stdlib_imports": sorted(m for m in imported if m in stdlib),
        "non_stdlib_imports": sorted(m for m in imported if m not in stdlib),
        "note": "non-stdlib imports are not necessarily fake -- verify by execution or judgment",
    }


def tool_run_sandboxed(code: str, timeout: int = 5) -> dict:
    """Executes the candidate code in an isolated subprocess and reports
    what happened. Catches crashes, hallucinated imports, bad method calls,
    and infinite loops -- but NOT code that runs cleanly and is simply
    wrong, which is why the agent is told to also read the code itself."""
    temp_file = f"_sandbox_{os.getpid()}_{int(time.time()*1000)}.py"
    with open(temp_file, "w") as f:
        f.write(code)
    try:
        proc = subprocess.run(
            [sys.executable, temp_file],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "executed_successfully": proc.returncode == 0,
            "stdout": proc.stdout.strip()[:500],
            "stderr": proc.stderr.strip()[:500],
        }
    except subprocess.TimeoutExpired:
        return {
            "executed_successfully": False,
            "stdout": "",
            "stderr": f"Timed out after {timeout}s (possible infinite loop).",
        }
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


TOOLS = {
    "check_imports": tool_check_imports,
    "run_sandboxed": tool_run_sandboxed,
}


# ---------------------------------------------------------------------------
# Baseline -- exactly what the brief calls a fair baseline: one direct
# prompt, no tools, same input as the agent gets.
# ---------------------------------------------------------------------------

BASELINE_PROMPT = """You are reviewing a code submission from a job candidate.
Read the code below and decide whether it looks correct and genuinely
written by someone who understands it, or whether it looks broken,
AI-hallucinated, or wrong.

Respond with exactly one line: PASS or FAIL, then a one-sentence reason.

CODE:
{code}
"""


def run_baseline(candidate_code: str) -> dict:
    prompt = BASELINE_PROMPT.format(code=candidate_code)
    text = _call_model(prompt)
    verdict = "FAIL" if text.upper().startswith("FAIL") else "PASS"
    result = {"verdict": verdict, "raw_response": text}
    logger.log_step("baseline_llm", "single_prompt_review", {"prompt_chars": len(prompt)}, result)
    return result


# ---------------------------------------------------------------------------
# Advanced path -- a real agent loop: decide -> call tools -> read results
# -> verdict. Nothing here is a plain function pretending to be an agent;
# the LLM is what's making each decision.
# ---------------------------------------------------------------------------

AGENT_DECIDE_PROMPT = """You are an autonomous code-verification agent. You have two tools:

- check_imports: parses the code and reports which imports are Python
  standard library vs. not. A non-stdlib import is not necessarily fake.
- run_sandboxed: actually executes the code in an isolated process and
  reports whether it ran successfully, plus stdout/stderr.

Given the candidate code below, decide which tool(s) to call. Respond with
ONLY a JSON array of tool names, e.g. ["check_imports", "run_sandboxed"].
Call both unless you have a specific reason not to.

CODE:
{code}
"""

AGENT_VERDICT_PROMPT = """You are an autonomous code-verification agent reviewing a job
candidate's submitted code for a hiring team. You gathered this evidence:

TOOL RESULTS:
{tool_results}

CODE:
{code}

Using the evidence above AND your own reading of the code, decide the
verdict. Code can execute without crashing and still be wrong -- a wrong
formula, an off-by-one, a method used incorrectly. Tool results alone
won't catch that, so read the logic yourself too, and say so if that is
the reason for your verdict.

Respond as JSON with exactly these keys:
{{"verdict": "PASS" or "FAIL", "score": <0-100 integer>, "reasoning": "2-3 sentences citing specific evidence", "flags": ["short phrases for anything suspicious"]}}
"""


def run_agent(candidate_code: str) -> dict:
    # Step 1: the agent decides which tools it wants
    decide_prompt = AGENT_DECIDE_PROMPT.format(code=candidate_code)
    decide_text = _call_model(decide_prompt)
    try:
        requested_tools = _extract_json(decide_text)
        if not isinstance(requested_tools, list):
            raise ValueError("expected a JSON list")
    except (json.JSONDecodeError, ValueError):
        requested_tools = list(TOOLS.keys())  # fall back to calling everything
    logger.log_step(
        "agent_llm", "decide_tools",
        {"prompt_chars": len(decide_prompt)},
        {"raw_response": decide_text, "requested": requested_tools},
    )

    # Step 2: we run whatever the agent asked for, and log each tool call
    tool_results = {}
    for name in requested_tools:
        tool_fn = TOOLS.get(name)
        if tool_fn is None:
            continue
        result = tool_fn(candidate_code)
        tool_results[name] = result
        logger.log_step("tool", name, {"code_chars": len(candidate_code)}, result)

    # Step 3: the agent reads the tool results plus the code and commits to a verdict
    verdict_prompt = AGENT_VERDICT_PROMPT.format(
        tool_results=json.dumps(tool_results, indent=2),
        code=candidate_code,
    )
    verdict_text = _call_model(verdict_prompt)
    try:
        final = _extract_json(verdict_text)
    except json.JSONDecodeError:
        final = {
            "verdict": "FAIL",
            "score": 0,
            "reasoning": "Could not parse model output as JSON.",
            "flags": ["parse_error"],
        }

    final["tool_results"] = tool_results
    logger.log_step("agent_llm", "final_verdict", {"prompt_chars": len(verdict_prompt)}, final)
    return final
