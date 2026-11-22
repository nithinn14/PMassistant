"""
api/services/pipeline.py
────────────────────────
Pipeline execution service for PMassistant.

Contains:
  - _coerce_project_name_to_str  — normalise raw PRD project-name fields
  - find_actual_project_name     — resolve the name the pipeline actually used
  - verify_pipeline_output       — Layer 2 artifact-existence safety net
  - _run_pipeline                — background-thread ADK orchestrator runner

Shared runtime objects (jobs, OutputCapture, _current_job_id_var) are
imported from api.job_runtime so that all callers operate on the same
in-memory state.  Filesystem paths are resolved at call time from paths.py
so that tests can redirect OUTPUT_DIR / BASE_DIR without touching module
globals.
"""

import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml

# ── Shared runtime objects (single instances, shared by reference) ────────────
from api.job_runtime import OutputCapture, _current_job_id_var, jobs

# ── Filesystem paths ──────────────────────────────────────────────────────────
# Imported from the central paths module; never redefined here.
from paths import APP_ROOT, CONFIG_PATH, DEFAULT_OUTPUT_DIR

# ── Pipeline constants ────────────────────────────────────────────────────────
MAX_RETRIES = 5


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _coerce_project_name_to_str(value) -> str:
    """Ensure a value read from a PRD JSON 'product_name'/'project_name' field
    is always a plain string, even if an old (pre-Bug-K-fix) file stored it as
    a list due to the model schema-compliance issue.
    - list, 1 item   -> unwrap
    - list, >1 items -> space-join
    - non-str        -> str()
    - str            -> unchanged
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(item) for item in value) if value else ""
    return str(value) if value is not None else ""


# ─────────────────────────────────────────────────────────────────────────────
# Exported pipeline functions
# ─────────────────────────────────────────────────────────────────────────────

def find_actual_project_name(
    original_project_name: str,
    output_dir: Optional[Path] = None,
    job_start_time: Optional[float] = None,
    logs: Optional[List[str]] = None,
    job: Optional[dict] = None,
) -> str:
    """
    Determines the actual project name used by the pipeline.
    The PM Agent / save_prd_tool may generate an AI-refined product_name
    different from the original typed-in project_name.

    Priority:
    1. job['actual_project_name'] if set and different from original, provided its PRD exists
    2. Inspect captured execution logs for 'PRD JSON saved at: <path>'
    3. Check if {original_project_name}_PRD.json exists and was modified during this run
    4. If job_start_time is known, scan output_dir for any *_PRD.json modified during this run
    5. Fallback: original_project_name
    """
    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR

    if job_start_time is None and job and job.get("started_at"):
        try:
            job_start_time = datetime.fromisoformat(job["started_at"]).timestamp()
        except Exception:
            pass

    # 1. Job dictionary check
    if job and job.get("actual_project_name"):
        candidate = job["actual_project_name"]
        if candidate != original_project_name and (output_dir / f"{candidate}_PRD.json").exists():
            return candidate

    # 2. Inspect logs for "PRD JSON saved at:"
    if logs:
        for line in reversed(logs):
            if "prd json saved at:" in line.lower():
                match = re.search(r"PRD JSON saved at:\s*(.*?_PRD\.json)", line, re.IGNORECASE)
                if match:
                    json_path = Path(match.group(1).strip())
                    if json_path.exists():
                        try:
                            with open(json_path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                                raw = data.get("product_name") or data.get("project_name")
                                name = _coerce_project_name_to_str(raw) if raw is not None else None
                                if name:
                                    return name
                        except Exception:
                            pass
                    name = json_path.stem.replace("_PRD", "").strip()
                    if name:
                        return name

    # 3. Check if original name's PRD exists and was modified since job started
    orig_prd = output_dir / f"{original_project_name}_PRD.json"
    if orig_prd.exists():
        if job_start_time is None or orig_prd.stat().st_mtime >= (job_start_time - 5):
            return original_project_name

    # 4. If job_start_time is known, scan output directory for any *_PRD.json modified during this run
    if job_start_time and output_dir.exists():
        candidates = []
        for p in output_dir.glob("*_PRD.json"):
            mtime = p.stat().st_mtime
            if mtime >= (job_start_time - 5):
                candidates.append((mtime, p))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            latest_path = candidates[0][1]
            try:
                with open(latest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    raw = data.get("product_name") or data.get("project_name")
                    name = _coerce_project_name_to_str(raw) if raw is not None else None
                    if name:
                        return name
            except Exception:
                pass
            return latest_path.stem.replace("_PRD", "").strip()

    # 5. Fallback to original
    return original_project_name


def verify_pipeline_output(
    project_name: str,
    output_dir: Optional[Path] = None,
    job: Optional[dict] = None,
) -> dict:
    """
    Layer 2 Safety Net:
    Verifies that the required output artifacts for a project actually exist.
    Distinguishes between:
      1. Genuine success (all core artifacts present)
      2. Valid paused state (RESOURCE_BLOCKED in workflow_state.yaml)
      3. Failure (missing artifacts indicating early, unannounced crash or termination)

    Returns:
        dict: {
            "success": bool,
            "status": "completed" | "resource_blocked" | "failed",
            "error": Optional[str],
            "current_step": str,
        }
    """
    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR

    actual_name = project_name
    prd_path = output_dir / f"{actual_name}_PRD.json"

    # If PRD under project_name does not exist, check if project was renamed
    if not prd_path.exists():
        resolved = find_actual_project_name(
            original_project_name=project_name,
            output_dir=output_dir,
            logs=job.get("logs") if job else None,
            job=job,
        )
        if resolved and resolved != project_name and (output_dir / f"{resolved}_PRD.json").exists():
            actual_name = resolved
            if job:
                job["actual_project_name"] = actual_name
                job["project_name"] = actual_name
            prd_path = output_dir / f"{actual_name}_PRD.json"

    tasks_path = output_dir / f"{actual_name}_Tasks.xlsx"
    assigned_path = output_dir / f"{actual_name}_Assigned.xlsx"
    sched_path = output_dir / f"{actual_name}_Scheduled.xlsx"
    wf_path = output_dir / f"{actual_name}_workflow_state.yaml"

    # 1. Check PRD stage
    if not prd_path.exists():
        return {
            "success": False,
            "status": "failed",
            "error": "Pipeline stopped during PRD generation — no PRD file was created.",
            "current_step": "Error: Missing PRD Output",
        }

    # 2. Check Tasks stage
    if not tasks_path.exists():
        return {
            "success": False,
            "status": "failed",
            "error": "Pipeline stopped after PRD generation — no tasks decomposition file was created.",
            "current_step": "Error: Missing Tasks Output",
        }

    # 3. Check Resource Assignment stage
    if not assigned_path.exists():
        return {
            "success": False,
            "status": "failed",
            "error": "Pipeline stopped after Task Decomposition — no resource assignment file was created.",
            "current_step": "Error: Missing Resource Assignment Output",
        }

    # 4. Check for intentional paused workflow state (e.g. RESOURCE_BLOCKED)
    if wf_path.exists():
        try:
            with open(wf_path, "r", encoding="utf-8") as f:
                wf_data = yaml.safe_load(f) or {}
            if wf_data.get("status") == "RESOURCE_BLOCKED":
                return {
                    "success": True,
                    "status": "resource_blocked",
                    "error": None,
                    "current_step": "Resource Shortage Detected (Waiting for PM Resolution)",
                }
        except Exception as e:
            print(f"⚠️ Error reading workflow_state.yaml: {e}")

    # 5. Check Schedule stage
    if not sched_path.exists():
        return {
            "success": False,
            "status": "failed",
            "error": "Pipeline stopped after Resource Assignment — no schedule file was created.",
            "current_step": "Error: Missing Schedule Output",
        }

    # All required core stages produced their artifacts!
    return {
        "success": True,
        "status": "completed",
        "error": None,
        "current_step": "Done",
    }


def _run_pipeline(
    job_id: str,
    project_name: str,
    project_description: str,
    backend_choice: str = "excel",
    integrations: Optional[dict] = None,
):
    """Runs the ADK orchestrator in a background thread with automatic retry on 429."""
    job = jobs[job_id]

    # Create a per-job OutputCapture and store it on the job dict so that the
    # process-wide JobOutputRouter can find it via _current_job_id_var.
    capture = OutputCapture(job_id)
    job["_capture"] = capture

    # Bind this thread's job-id in the context var so the router routes to us.
    # Do NOT assign sys.stdout — the router is already installed process-wide.
    # Initialized to None so the finally block can safely guard the reset.
    job_id_token = None
    job_id_token = _current_job_id_var.set(job_id)

    backend_token = None

    try:
        from google.adk import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai.types import Content, Part
        from agents.orchestrator_agent import build_orchestrator
        from config_loader import load_config
        from data_backend import create_backend, bind_backend

        config = load_config(str(CONFIG_PATH))
        model_name = config.get("models", {}).get("default", "gemini-2.0-flash")

        # Create and bind the job-specific DataBackend in memory
        if "data_backend" in config:
            backend_cfg = dict(config["data_backend"])
            backend_cfg["type"] = backend_choice
            pipeline_backend = create_backend(backend_cfg)
            backend_token = bind_backend(pipeline_backend)

        payload = json.dumps(
            {
                "project_name": project_name,
                "project_description": project_description,
            }
        )

        job["current_step"] = "Validating API Key..."
        print(f"DEBUG: Running pre-flight check for model: {model_name}")

        # PRE-FLIGHT CHECK: Catch hard zero quota deadlocks before ADK threads start
        try:
            from google.genai import Client
            import os
            api_key = os.environ.get("GOOGLE_API_KEY", "")
            if not api_key:
                raise ValueError("GOOGLE_API_KEY is not set in environment or .env file.")

            client = Client(api_key=api_key)
            # A tiny sync request to trigger any quota/auth errors instantly
            client.models.generate_content(model=model_name, contents="ok")
        except Exception as e:
            err_msg = str(e)
            sys.stderr.write(f"Pre-flight check failed: {err_msg[:200]}\n")
            if "limit: 0" in err_msg:
                job["error"] = "Your API key has a quota limit of 0. You MUST generate a key from https://aistudio.google.com/apikey. Keys from Google Cloud Console do not work on the free tier."
                job["status"] = "failed"
                job["current_step"] = "Error: Invalid API Key Quota (Limit: 0)"
                return
            elif "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                job["error"] = "Gemini API rate limit exceeded. Please wait a few minutes and try again."
                job["status"] = "failed"
                job["current_step"] = "Error: Gemini API Rate Limit Exceeded"
                return
            elif "403" in err_msg or "PERMISSION_DENIED" in err_msg:
                job["error"] = "Permission denied. The project associated with this API key might be suspended."
                job["status"] = "failed"
                job["current_step"] = "Error: Permission Denied (403)"
                return
            elif "API_KEY" in err_msg or "invalid" in err_msg.lower():
                job["error"] = "Invalid API key provided. Please check your .env file."
                job["status"] = "failed"
                job["current_step"] = "Error: Invalid API Key"
                return
            else:
                job["error"] = f"API Pre-flight failed: {err_msg[:100]}"
                job["status"] = "failed"
                job["current_step"] = "Error: API Validation Failed"
                return

        for attempt in range(1, MAX_RETRIES + 1):
            attempt_start_time = time.time()
            print(f"DEBUG: Attempt {attempt}/{MAX_RETRIES} — model: {model_name}")

            # Rebuild everything fresh on each attempt
            agent = build_orchestrator(integrations=integrations)
            session_service = InMemorySessionService()
            runner = Runner(
                app_name="pm_assistant_app",
                agent=agent,
                session_service=session_service,
            )
            sid = f"session_{job_id}_attempt{attempt}"
            session_service.create_session_sync(
                app_name="pm_assistant_app", user_id="user1", session_id=sid
            )
            content = Content(role="user", parts=[Part(text=payload)])

            # Reset job progress for this attempt
            job["current_step"] = "Generating PRD..."
            job["completed_steps"] = []
            job["status"] = "running"
            job["error"] = None

            try:
                # Layer 1: Run via run_async in an asyncio event loop so agent exceptions propagate directly
                async def _consume_events():
                    out = ""
                    idx = 0
                    async for event in runner.run_async(
                        user_id="user1", session_id=sid, new_message=content
                    ):
                        if hasattr(event, "step_name"):
                            print(f"DEBUG: [Event {idx}] Step: {event.step_name}")
                        idx += 1

                        if not hasattr(event, "content") or event.content is None:
                            continue

                        parts = getattr(event.content, "parts", None)
                        if not parts:
                            continue
                        for part in parts:
                            text = getattr(part, "text", None)
                            if text:
                                out += text
                    return out

                final_output = asyncio.run(_consume_events())

                # Resolve actual project name (in case PM Agent renamed the project)
                actual_project_name = find_actual_project_name(
                    original_project_name=project_name,
                    output_dir=DEFAULT_OUTPUT_DIR,
                    job_start_time=attempt_start_time,
                    logs=capture.lines,
                    job=job,
                )
                job["actual_project_name"] = actual_project_name
                job["project_name"] = actual_project_name

                # Layer 2: Output verification safety net before declaring completion
                verification = verify_pipeline_output(actual_project_name, DEFAULT_OUTPUT_DIR, job)

                if verification["status"] == "completed":
                    job["status"] = "completed"
                    job["current_step"] = "Done"
                    if "Done" not in job["completed_steps"]:
                        job["completed_steps"].append("Done")
                    job["final_output"] = final_output
                    job["logs"] = capture.lines
                    print(f"✅ Pipeline completed on attempt {attempt} for '{actual_project_name}'")
                    return

                elif verification["status"] == "resource_blocked":
                    job["status"] = "resource_blocked"
                    job["current_step"] = verification["current_step"]
                    if verification["current_step"] not in job["completed_steps"]:
                        job["completed_steps"].append(verification["current_step"])
                    job["final_output"] = final_output
                    job["logs"] = capture.lines
                    print(f"⏸️ Pipeline paused at Resource Validation for '{actual_project_name}' (status: RESOURCE_BLOCKED)")
                    return

                else:  # verification["status"] == "failed"
                    err_msg = verification["error"]
                    print(f"❌ Output verification failed: {err_msg}")
                    job["status"] = "failed"
                    job["error"] = err_msg
                    job["current_step"] = verification["current_step"]
                    job["logs"] = capture.lines
                    return

            except Exception as e:
                err_msg = str(e)
                is_rate_limit = "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg
                is_hard_zero_quota = "limit: 0" in err_msg

                if is_rate_limit and not is_hard_zero_quota and attempt < MAX_RETRIES:
                    # Extract retry delay from error message
                    wait_seconds = 60  # default
                    import re as _re
                    delay_match = _re.search(r"retryDelay.*?(\d+)", err_msg)
                    if delay_match:
                        wait_seconds = int(delay_match.group(1)) + 5  # add 5s buffer

                    sys.stderr.write(f"⚠️ Rate limit hit (attempt {attempt}). Waiting {wait_seconds}s before retry...\n")
                    job["current_step"] = f"Rate limited — waiting {wait_seconds}s (attempt {attempt}/{MAX_RETRIES})"
                    time.sleep(wait_seconds)
                    continue  # Retry
                else:
                    if is_hard_zero_quota:
                        sys.stderr.write(f"❌ Hard zero quota detected. Failing immediately.\n")
                        job["error"] = "Your API key has a quota limit of 0. You MUST generate a key from https://aistudio.google.com/apikey. Keys from Google Cloud Console do not work on the free tier."
                        job["status"] = "failed"
                        job["current_step"] = "Error: Invalid API Key Quota (Limit: 0)"
                        return  # Exit immediately

                    raise e  # Re-raise non-429 errors or final attempt failure

        # If we get here, all retries exhausted
        job["status"] = "failed"
        job["current_step"] = "Error: All retries exhausted"
        job["error"] = f"Pipeline failed after {MAX_RETRIES} attempts due to rate limiting."

    except Exception as exc:
        sys.stderr.write(f"❌ Pipeline Failed: {str(exc)}\n")
        job["status"] = "failed"
        err_msg = str(exc)
        if hasattr(job, "error") and job.get("error"):
            pass  # Error was already set with a specific message
        elif "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
            job["current_step"] = "Error: Gemini API Rate Limit Exceeded"
            job["error"] = "Gemini API rate limit exceeded. The pipeline retried but quota is still exhausted."
        else:
            job["current_step"] = f"Error: {err_msg[:50]}..."
            job["error"] = f"Error in {job.get('current_step', 'Initialization')}: {err_msg}"
        job["logs"] = capture.lines
    finally:
        if backend_token is not None:
            from data_backend import reset_bound_backend
            reset_bound_backend(backend_token)
        # Flush any partial line still in the capture buffer before finalizing logs.
        # This ensures output from early-return paths is not silently discarded.
        if capture._line_buf.strip():
            capture.lines.append(capture._line_buf.strip())
            capture._line_buf = ""
        # Unconditionally sync job["logs"] so callers always see the latest
        # captured lines, including when the pipeline exits early or raises.
        # Only overwrite if capture.lines is non-empty or job["logs"] is still
        # the empty list created at job inception (avoids replacing richer logs
        # that an explicit assignment earlier in the try block may have set to
        # an equivalent reference, while still updating on early-return paths
        # where no explicit assignment was made).
        if capture.lines or not job.get("logs"):
            job["logs"] = capture.lines
        # Reset the job-id context var only if the token was successfully created.
        # If _current_job_id_var.set() itself had raised, job_id_token would be
        # None and calling reset(None) would raise a TypeError.
        if job_id_token is not None:
            _current_job_id_var.reset(job_id_token)


__all__ = [
    "MAX_RETRIES",
    "_coerce_project_name_to_str",
    "find_actual_project_name",
    "verify_pipeline_output",
    "_run_pipeline",
]
