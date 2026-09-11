"""
resource_agent.py
─────────────────
Deterministic resource allocation step.

Parsing strategy (layered — Bug A fix):
  1. Fast path: direct json.loads on the input.
  2. Classify failure as TRUNCATED or HAS_PREAMBLE.
     - TRUNCATED: brace counts unbalanced or text doesn't end with '}'.
       Regex extraction is unlikely to help — go straight to LLM retry.
     - HAS_PREAMBLE: text looks like valid JSON buried inside prose
       (starts with non-'{' or has a markdown fence).  Try regex extraction.
  3. If extraction still fails, call the upstream model directly with an
     explicit correction prompt (up to MAX_PARSE_RETRIES=2 attempts).
     This mirrors the retry-loop pattern in api_server._run_pipeline.
  4. If all retries exhaust, raise ResourceAgentParseError — loudly, never
     silently fall through with partial data.
"""

import json
import os
import re
import time
from .deterministic_agent import DeterministicAgent
from tools.assign_employee_tool import assign_resources


# ── Custom exception ────────────────────────────────────────────────────────

class ResourceAgentParseError(Exception):
    """
    Raised when ResourceAllocationLogic cannot recover valid JSON from the
    upstream agent's output after exhausting all parsing and retry strategies.

    Attributes:
        reason  -- short token describing the failure class:
                   'truncated', 'no_json', 'invalid_json', 'retry_exhausted'
        snippet -- first 200 chars of the raw input for log context
    """
    def __init__(self, reason: str, snippet: str, detail: str = ""):
        self.reason = reason
        self.snippet = snippet
        msg = (
            f"ResourceAgent could not parse task JSON from upstream output. "
            f"Reason: {reason}. "
            f"Input snippet: {snippet!r}. "
            f"{detail}"
        )
        super().__init__(msg)


# ── Constants ───────────────────────────────────────────────────────────────

MAX_PARSE_RETRIES = 2   # LLM correction calls before giving up

_CORRECTION_PROMPT = (
    "Your previous response could not be parsed as valid JSON.\n\n"
    "Please respond with ONLY a single complete, valid JSON object — "
    "no markdown code fences (no ```json), no explanation before or after, "
    "no commentary. The JSON must have exactly these top-level keys:\n"
    '  "project_name": "<string>",\n'
    '  "tasks": [<array of task objects>]\n\n'
    "Previous (broken) response:\n{broken}\n\n"
    "Corrected JSON only:"
)


# ── Parsing helpers ─────────────────────────────────────────────────────────

def _balanced(text: str) -> bool:
    """Return True if every '{' in *text* has a matching '}'."""
    depth = 0
    for ch in text:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _classify_input(text: str) -> str:
    """
    Classify the raw input to guide the parsing strategy.

    Returns one of:
      'valid'      — direct json.loads succeeded (caller shouldn't need this)
      'truncated'  — looks like JSON that was cut off mid-stream
      'preamble'   — valid JSON is present but buried in prose / markdown
      'garbage'    — no recognisable JSON structure at all
    """
    stripped = text.strip()

    # Check for markdown fences wrapping JSON
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fence_match:
        return 'preamble'

    # Does it start like a JSON object?
    starts_json = stripped.startswith('{')

    if starts_json:
        # Could be truncated JSON or complete JSON
        ends_json = stripped.endswith('}')
        if not ends_json or not _balanced(stripped):
            return 'truncated'
        # Balanced — it might still be invalid JSON internally (bad escapes,
        # trailing commas, etc.), but let the caller try; classify as preamble
        # so the regex extraction path is attempted.
        return 'preamble'

    # Doesn't start with '{' — JSON is buried in prose if it exists at all
    if re.search(r'\{', stripped):
        return 'preamble'

    return 'garbage'


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if present, returning the inner content."""
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1) if match else text


def _attempt_parse(text: str):
    """
    Try to extract a valid JSON dict from *text*.

    Returns the parsed dict on success, or raises json.JSONDecodeError /
    ValueError if nothing works.
    """
    # Fast path
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences first, then retry
    defenced = _strip_fences(text).strip()
    try:
        return json.loads(defenced)
    except json.JSONDecodeError:
        pass

    # Regex: find the outermost {...} block (handles preamble/postamble prose)
    match = re.search(r'\{.*\}', defenced, re.DOTALL)
    if not match:
        match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in input")

    cleaned = re.sub(r'\bNaN\b', 'null', match.group(0))
    return json.loads(cleaned)   # raises JSONDecodeError on truncation


def _call_model_for_correction(broken_text: str, model_name: str) -> str:
    """
    Make a direct synchronous generate_content call asking the model to
    re-emit only clean JSON.  Returns the model's raw text response.
    Raises on API errors.
    """
    from google.genai import Client

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise ResourceAgentParseError(
            reason="no_api_key",
            snippet=broken_text[:200],
            detail="GOOGLE_API_KEY not set; cannot attempt LLM correction.",
        )

    client = Client(api_key=api_key)
    prompt = _CORRECTION_PROMPT.format(broken=broken_text[:2000])
    response = client.models.generate_content(model=model_name, contents=prompt)
    return response.text or ""


# ── Main logic ───────────────────────────────────────────────────────────────

class ResourceAllocationLogic:
    """
    Deterministic logic for the ResourceAgent step.
    Parses input, delegates to assign_resources tool, detects shortages.
    """

    def __call__(self, input_text: str) -> str:
        data = self._parse(input_text)

        project_name = data["project_name"]
        tasks = data["tasks"]

        result = assign_resources(project_name=project_name, tasks=tasks)
        result["resource_blocked"] = self._has_shortage(result.get("tasks", []))

        if result["resource_blocked"]:
            count = sum(1 for t in result["tasks"] if t.get("Assigned_Employee") == "No Resource Available")
            print(f"\n🚨 RESOURCE SHORTAGE: {count} tasks unassigned in '{project_name}'\n")

        return json.dumps(result)

    # ── Private: layered parsing ────────────────────────────────────────────

    def _parse(self, input_text: str) -> dict:
        """
        Try to obtain a valid {project_name, tasks} dict from *input_text*
        using a layered strategy.  Raises ResourceAgentParseError on
        complete failure — never returns partial data.
        """
        snippet = input_text[:200]

        # ── Layer 1: direct parse ────────────────────────────────────────────
        try:
            return _attempt_parse(input_text)
        except (json.JSONDecodeError, ValueError):
            pass

        # ── Layer 2: classify to pick the right recovery path ────────────────
        failure_class = _classify_input(input_text)
        print(
            f"⚠️  [ResourceAgent] JSON parse failed. "
            f"Classification: {failure_class!r}. Entering recovery path."
        )

        if failure_class == 'garbage':
            # No JSON-like structure at all — skip regex, go straight to retry
            raise ResourceAgentParseError(
                reason="no_json",
                snippet=snippet,
                detail=(
                    "Input contains no recognisable JSON structure. "
                    "This is likely a completely non-JSON upstream response. "
                    "Check Task Decomposition Agent logs."
                ),
            )

        if failure_class == 'truncated':
            # Regex extraction won't help on truncated JSON — skip it, retry
            print(
                "⚠️  [ResourceAgent] Input appears truncated "
                "(brace mismatch or no closing '}'). "
                "Skipping regex extraction — going to LLM retry."
            )
        else:
            # 'preamble' — JSON is buried in prose; try regex extraction
            try:
                data = _attempt_parse(input_text)
                print("✅ [ResourceAgent] JSON recovered via regex extraction.")
                return data
            except (json.JSONDecodeError, ValueError) as exc:
                print(f"⚠️  [ResourceAgent] Regex extraction also failed: {exc}. Proceeding to LLM retry.")

        # ── Layer 3: retry-with-correction ───────────────────────────────────
        return self._retry_with_correction(input_text, failure_class, snippet)

    def _retry_with_correction(
        self, broken_text: str, failure_class: str, snippet: str
    ) -> dict:
        """
        Ask the upstream model to re-emit clean JSON, up to MAX_PARSE_RETRIES
        attempts.  Mirrors the retry-loop pattern in api_server._run_pipeline.
        """
        from config_loader import load_config
        config = load_config()
        model_name = config.get("models", {}).get("default", "gemini-2.0-flash")

        last_exc = None
        for attempt in range(1, MAX_PARSE_RETRIES + 1):
            print(
                f"🔄 [ResourceAgent] LLM correction attempt "
                f"{attempt}/{MAX_PARSE_RETRIES} — model: {model_name}"
            )
            try:
                corrected_text = _call_model_for_correction(broken_text, model_name)
                data = _attempt_parse(corrected_text)
                print(f"✅ [ResourceAgent] JSON recovered on correction attempt {attempt}.")
                return data

            except (json.JSONDecodeError, ValueError) as exc:
                print(f"⚠️  [ResourceAgent] Correction attempt {attempt} produced invalid JSON: {exc}")
                last_exc = exc
                broken_text = corrected_text if 'corrected_text' in dir() else broken_text

            except Exception as exc:
                err_msg = str(exc)
                is_rate_limit = "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg
                if is_rate_limit and attempt < MAX_PARSE_RETRIES:
                    wait = 60
                    delay_match = re.search(r"retryDelay.*?(\d+)", err_msg)
                    if delay_match:
                        wait = int(delay_match.group(1)) + 5
                    print(
                        f"⚠️  [ResourceAgent] Rate-limited on correction attempt {attempt}. "
                        f"Waiting {wait}s before retry..."
                    )
                    time.sleep(wait)
                    continue
                last_exc = exc
                break

        # All retries exhausted
        raise ResourceAgentParseError(
            reason="retry_exhausted",
            snippet=snippet,
            detail=(
                f"All {MAX_PARSE_RETRIES} LLM correction attempts failed. "
                f"Failure class was {failure_class!r}. "
                f"Last error: {last_exc}"
            ),
        )

    def _has_shortage(self, tasks: list) -> bool:
        return any(t.get("Assigned_Employee") == "No Resource Available" for t in tasks)


def build_resource_agent():
    return DeterministicAgent(
        name="resource_agent",
        description="Deterministic resource allocation agent",
        logic=ResourceAllocationLogic(),
    )
