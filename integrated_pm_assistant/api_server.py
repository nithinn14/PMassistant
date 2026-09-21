
"""
api_server.py
─────────────
Flask REST API that wraps the existing ADK orchestrator.

Endpoints:
    POST /api/start-project      → launch pipeline in background thread
    GET  /api/job-status/<id>    → poll progress / completion
    GET  /api/project-results/<name> → structured results JSON
    GET  /api/download/<filename>    → serve output files
"""

import json, os, sys, re, io, uuid, threading, time, math, asyncio, yaml
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime

# Fix Windows encoding
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Import meeting tools for sync endpoint
from tools.meeting_tools import check_rsvp, send_reminder, reschedule_meeting

load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

app = Flask(__name__)
CORS(app, origins=["http://localhost:5173", "http://127.0.0.1:5173"])


def _sanitize(obj):
    """Recursively replace NaN / Infinity with None so JSON is valid."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _safe_jsonify(data, status=200):
    """Return a Flask Response with sanitized JSON (no NaN)."""
    from flask import Response
    cleaned = _sanitize(data)
    body = json.dumps(cleaned, default=str, ensure_ascii=False)
    return Response(body, status=status, mimetype="application/json")

# ── In-memory job store ─────────────────────────────────────────────────────
jobs: dict = {}

STEP_KEYWORDS = [
    ("PRD JSON saved", "Generating PRD..."),
    ("PRD PDF saved", "PRD Generated"),
    ("Tasks saved", "Generating Tasks..."),
    ("All resources assigned for", "Assigning Resources..."),
    ("Schedule saved", "Scheduling Tasks..."),
    ("Starting Communication", "Creating Meetings..."),
    ("Telegram message sent", "Sending Notifications..."),
    ("Kickoff meeting created successfully", "Creating Kickoff Meeting..."),
    ("RSVP", "Checking Meeting RSVPs..."),
    ("Execution Completed", "Finalizing Execution..."),
]


class OutputCapture(io.StringIO):
    """Intercepts print() to capture pipeline progress."""

    def __init__(self, job_id: str):
        super().__init__()
        self.job_id = job_id
        self._real_stdout = sys.stdout
        self.lines: list[str] = []

    def write(self, text: str):
        self._real_stdout.write(text)
        if text.strip():
            self.lines.append(text.strip())
            self._update_step(text)
        return len(text)

    def flush(self):
        self._real_stdout.flush()

    def _update_step(self, text: str):
        # Detect PRD save to capture the AI-generated/actual project name early
        if "prd json saved at:" in text.lower():
            self._detect_actual_project_name(text)

        for keyword, step_label in STEP_KEYWORDS:
            if keyword.lower() in text.lower():
                job = jobs.get(self.job_id)
                if job and step_label not in job["completed_steps"]:
                    job["completed_steps"].append(step_label)
                    job["current_step"] = step_label
                break

    def _detect_actual_project_name(self, text: str):
        try:
            match = re.search(r"PRD JSON saved at:\s*(.*?_PRD\.json)", text, re.IGNORECASE)
            if match:
                json_path = Path(match.group(1).strip())
                actual_name = None
                if json_path.exists():
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            actual_name = data.get("product_name") or data.get("project_name")
                    except Exception:
                        pass
                if not actual_name:
                    actual_name = json_path.stem.replace("_PRD", "").strip()

                job = jobs.get(self.job_id)
                if job and actual_name:
                    job["actual_project_name"] = actual_name
                    job["project_name"] = actual_name
        except Exception:
            pass


def find_actual_project_name(
    original_project_name: str,
    output_dir: Path = OUTPUT_DIR,
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
                                name = data.get("product_name") or data.get("project_name")
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
                    name = data.get("product_name") or data.get("project_name")
                    if name:
                        return name
            except Exception:
                pass
            return latest_path.stem.replace("_PRD", "").strip()

    # 5. Fallback to original
    return original_project_name


def verify_pipeline_output(
    project_name: str,
    output_dir: Path = OUTPUT_DIR,
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


def _run_pipeline(job_id: str, project_name: str, project_description: str):
    """Runs the ADK orchestrator in a background thread with automatic retry on 429."""
    job = jobs[job_id]
    capture = OutputCapture(job_id)
    old_stdout = sys.stdout
    sys.stdout = capture

    MAX_RETRIES = 5

    try:
        from google.adk import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai.types import Content, Part
        from agents.orchestrator_agent import build_orchestrator
        from config_loader import load_config

        config = load_config(str(BASE_DIR / "config.yaml"))
        model_name = config.get("models", {}).get("default", "gemini-2.0-flash")

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
            agent = build_orchestrator()
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
                    output_dir=OUTPUT_DIR,
                    job_start_time=attempt_start_time,
                    logs=capture.lines,
                    job=job,
                )
                job["actual_project_name"] = actual_project_name
                job["project_name"] = actual_project_name

                # Layer 2: Output verification safety net before declaring completion
                verification = verify_pipeline_output(actual_project_name, OUTPUT_DIR, job)

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
                        return # Exit immediately

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
            pass # Error was already set with a specific message
        elif "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
            job["current_step"] = "Error: Gemini API Rate Limit Exceeded"
            job["error"] = "Gemini API rate limit exceeded. The pipeline retried but quota is still exhausted."
        else:
            job["current_step"] = f"Error: {err_msg[:50]}..."
            job["error"] = f"Error in {job.get('current_step', 'Initialization')}: {err_msg}"
        job["logs"] = capture.lines
    finally:
        sys.stdout = old_stdout


@app.route("/api/ping")
def ping():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


# ── API Routes ──────────────────────────────────────────────────────────────

@app.route("/api/start-project", methods=["POST"])
def start_project():
    data = request.get_json(force=True)
    project_name = data.get("project_name", "").strip()
    project_description = data.get("project_description", "").strip()
    backend_choice = data.get("data_backend", "excel").strip().lower()

    if not project_name or not project_description:
        return jsonify({"error": "project_name and project_description are required"}), 400

    # 1. Update config.yaml backend type
    try:
        config_path = BASE_DIR / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config_content = f.read()
        updated = re.sub(
            r'(data_backend:\s*\n\s*type:\s*)"[^"]+"',
            rf'\g<1>"{backend_choice}"',
            config_content,
            count=1,
        )
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(updated)
    except Exception:
        pass

    # 2. Start background job
    job_id = str(uuid.uuid4())
    print(f"DEBUG: Starting project '{project_name}' with job_id {job_id}")
    
    jobs[job_id] = {
        "id": job_id,
        "project_name": project_name,
        "original_project_name": project_name,
        "actual_project_name": project_name,
        "status": "running",
        "current_step": "Initializing...",
        "completed_steps": [],
        "logs": [],
        "error": None,
        "started_at": datetime.now().isoformat(),
    }

    thread = threading.Thread(
        target=_run_pipeline, args=(job_id, project_name, project_description)
    )
    thread.daemon = True
    thread.start()

    return _safe_jsonify({"job_id": job_id, "status": "running"})


@app.route("/api/job-status/<job_id>")
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(
        {
            "status": job["status"],
            "current_step": job["current_step"],
            "completed_steps": job["completed_steps"],
            "error": job["error"],
            "project_name": job.get("actual_project_name") or job["project_name"],
            "original_project_name": job.get("original_project_name", job["project_name"]),
            "actual_project_name": job.get("actual_project_name") or job["project_name"],
        }
    )


def get_available_projects() -> List[str]:
    """Scan OUTPUT_DIR and memory jobs to find all existing project names."""
    names = set()
    if OUTPUT_DIR.exists():
        for f in OUTPUT_DIR.glob("*_PRD.json"):
            if f.stem.endswith("_PRD"):
                names.add(f.stem[:-4])
        for suffix in ("_Tasks.xlsx", "_Assigned.xlsx", "_Scheduled.xlsx", "_PRD.pdf", "_workflow_state.yaml"):
            stem_suffix = suffix.split(".")[0]
            for f in OUTPUT_DIR.glob(f"*{suffix}"):
                if f.stem.endswith(stem_suffix):
                    names.add(f.stem[:-len(stem_suffix)])
    for j in jobs.values():
        if j.get("actual_project_name"):
            names.add(j["actual_project_name"])
        elif j.get("project_name"):
            names.add(j["project_name"])
    return sorted([n for n in names if n])


def resolve_project_name(query: str) -> tuple[Optional[str], List[str]]:
    """
    Resolve a user query to matching existing projects.
    Returns:
        (canonical_name, []) if exactly 1 match
        (None, [matches...]) if >1 matches (ambiguous)
        (None, []) if 0 matches
    """
    # If query matches original_project_name of a renamed job, resolve first
    for j in jobs.values():
        if j.get("original_project_name") == query and j.get("actual_project_name"):
            query = j["actual_project_name"]
            break

    clean_q = query.strip()
    if not clean_q:
        return None, []

    available = get_available_projects()

    # 1. Exact case-sensitive match
    if clean_q in available:
        return clean_q, []

    # 2. Exact case-insensitive match
    exact_ci = [p for p in available if p.lower() == clean_q.lower()]
    if len(exact_ci) == 1:
        return exact_ci[0], []
    elif len(exact_ci) > 1:
        return None, exact_ci

    # 3. Partial / substring match (case-insensitive)
    q_lower = clean_q.lower()
    matches = [p for p in available if q_lower in p.lower()]
    if len(matches) == 1:
        return matches[0], []
    elif len(matches) > 1:
        return None, matches
    else:
        return None, []


@app.route("/api/project-results/<path:project_name>")
def project_results(project_name):
    import pandas as pd

    canonical_name, ambiguous_matches = resolve_project_name(project_name)

    if ambiguous_matches:
        return _safe_jsonify({
            "ambiguous": True,
            "query": project_name,
            "matches": ambiguous_matches,
        }, 200)

    if not canonical_name:
        return _safe_jsonify({
            "error": f"No project found matching '{project_name}'. Check the Projects page for available projects."
        }, 404)

    project_name = canonical_name
    result = {"project_name": project_name}

    # PRD JSON
    prd_json_path = OUTPUT_DIR / f"{project_name}_PRD.json"
    if prd_json_path.exists():
        with open(prd_json_path, "r", encoding="utf-8") as f:
            result["prd"] = json.load(f)
        result["prd_json_url"] = f"/api/download/{project_name}_PRD.json"
        result["prd_pdf_url"] = f"/api/download/{project_name}_PRD.pdf"

    # Tasks
    tasks_path = OUTPUT_DIR / f"{project_name}_Tasks.xlsx"
    if tasks_path.exists():
        df = pd.read_excel(tasks_path)
        result["tasks"] = df.where(pd.notnull(df), None).to_dict(orient="records")

    # Assigned
    assigned_path = OUTPUT_DIR / f"{project_name}_Assigned.xlsx"
    if assigned_path.exists():
        df = pd.read_excel(assigned_path)
        result["assigned"] = df.where(pd.notnull(df), None).to_dict(orient="records")

    # Scheduled
    sched_path = OUTPUT_DIR / f"{project_name}_Scheduled.xlsx"
    if sched_path.exists():
        df = pd.read_excel(sched_path)
        df = df.where(pd.notnull(df), None)
        result["schedule"] = df.to_dict(orient="records")

        # Extract telegram notifications from the scheduled data
        if "assigned_empl" in df.columns:
            notified = df["assigned_empl"].dropna().unique().tolist()
            result["telegram_notifications"] = notified

    # Meetings
    meetings_path = OUTPUT_DIR / f"{project_name}_Meetings.xlsx"
    if meetings_path.exists():
        df = pd.read_excel(meetings_path)
        result["meetings"] = df.where(pd.notnull(df), None).to_dict(orient="records")

    # Participants
    parts_path = OUTPUT_DIR / f"{project_name}_Participants.xlsx"
    if parts_path.exists():
        df = pd.read_excel(parts_path)
        result["participants"] = df.where(pd.notnull(df), None).to_dict(orient="records")

    # Meeting summary
    if "meetings" in result:
        meeting = result["meetings"][-1] if result["meetings"] else {}
        event_id = meeting.get("Event_ID")
        participants = result.get("participants", [])
        
        # Dashboard expects capitalized keys: Accepted, Declined, Tentative, Awaiting
        rsvp_counts = {"Accepted": 0, "Declined": 0, "Tentative": 0, "Awaiting": 0}
        
        count_debug = []
        for p in participants:
            # ONLY count participants for THIS meeting
            if event_id and p.get("Meeting_ID") != event_id:
                continue

            # Check both possible column names from different tool versions
            raw_status = p.get("RSVP_Status") or p.get("Response") or "Awaiting"
            status = str(raw_status).strip().lower()
            
            count_debug.append(f"{p.get('Email')}: {status}")

            if status == "accepted":
                rsvp_counts["Accepted"] += 1
            elif status == "declined":
                rsvp_counts["Declined"] += 1
            elif status == "tentative":
                rsvp_counts["Tentative"] += 1
            else:
                rsvp_counts["Awaiting"] += 1
        
        if count_debug:
            print(f"DEBUG: Project: {project_name}, Meeting: {event_id}, Statuses: {count_debug}")
                
        result["meeting_summary"] = {
            "status": meeting.get("Status", "Unknown"),
            "meeting_type": meeting.get("Meeting_Type", ""),
            "event_id": event_id,
            **rsvp_counts,
        }

    return _safe_jsonify(result)


@app.route("/api/upload-file", methods=["POST"])
def upload_file():
    """Accept an uploaded data file and save it to the output directory."""
    if "file" not in request.files:
        return _safe_jsonify({"error": "No file provided"}, 400)
    f = request.files["file"]
    if not f.filename:
        return _safe_jsonify({"error": "Empty filename"}, 400)
    ext = Path(f.filename).suffix.lower()
    if ext not in (".xlsx", ".xls", ".csv", ".json"):
        return _safe_jsonify({"error": "Only .xlsx, .csv, .json files are accepted"}, 400)
    safe_name = secure_filename(f.filename)
    dest = BASE_DIR / safe_name

    # Smart merge for employees.xlsx to prevent workload data loss
    if safe_name == "employees.xlsx" and dest.exists():
        import pandas as pd
        try:
            print(f"📊 [Server] Smart Merge: {safe_name} detected. Preserving project history...")
            old_df = pd.read_excel(dest)
            temp_path = str(dest) + ".tmp"
            f.save(temp_path)
            new_df = pd.read_excel(temp_path)
            os.remove(temp_path)

            # Preserve workload and project state for existing employees
            # Find a matching key: Email, Employee_ID, or Employee_Name
            key = next((k for k in ["Email", "Employee_ID", "Employee_Name"] 
                       if k in old_df.columns and k in new_df.columns), None)
            
            if key:
                # Normalize keys for robust matching
                old_df[key] = old_df[key].astype(str).str.strip()
                new_df[key] = new_df[key].astype(str).str.strip()
                
                cols_to_sync = ["Current_Project", "Allocated_Hours", "Free_Hours"]
                synced = []
                for col in cols_to_sync:
                    if col in old_df.columns:
                        # Re-create column in new_df if missing
                        if col not in new_df.columns:
                            new_df[col] = None
                        
                        # Map old state to new dataframe
                        mapping = old_df.set_index(key)[col].to_dict()
                        
                        def merge_row(row):
                            k = str(row[key]).strip()
                            old_v = mapping.get(k)
                            # Convert null/NaN to empty string for comparison
                            old_s = str(old_v).strip() if pd.notnull(old_v) else ""
                            new_v = row[col]
                            new_s = str(new_v).strip() if pd.notnull(new_v) else ""

                            # If server has historical data (non-empty), keep it
                            if old_s and old_s.lower() != "nan":
                                return old_v
                            # Otherwise use whatever was in the upload
                            return new_v

                        new_df[col] = new_df.apply(merge_row, axis=1)

                        synced.append(col)
                
                print(f"✅ [Server] Merged {len(new_df)} employees using key '{key}'. Synced: {synced}")
                new_df.to_excel(dest, index=False)
                return _safe_jsonify({"saved_as": safe_name, "merged": True, "key_used": key, "synced_cols": synced})
            
            # If no key found, fallback to standard save
            print(f"⚠️ [Server] No common key found for merge. Saving {safe_name} as-is.")
            new_df.to_excel(dest, index=False)
            return _safe_jsonify({"saved_as": safe_name, "merged": False, "reason": "No common key found"})


        except Exception as e:
            print(f"❌ [Server] Smart Merge failed: {str(e)}")
            # Fallback in case of error
            f.save(str(dest))
            return _safe_jsonify({"saved_as": safe_name, "error_merging": str(e)})

    f.save(str(dest))
    return _safe_jsonify({"saved_as": safe_name, "path": str(dest)})



@app.route("/api/download/<filename>")
def download_file(filename):
    return send_from_directory(str(OUTPUT_DIR), filename, as_attachment=True)


@app.route("/api/projects")
def list_projects():
    """List all generated projects by scanning *_PRD.json in the output dir."""
    projects = []
    if OUTPUT_DIR.exists():
        for f in sorted(OUTPUT_DIR.glob("*_PRD.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            name = f.stem.replace("_PRD", "")
            project_files = list(OUTPUT_DIR.glob(f"{name}*"))
            mtime = f.stat().st_mtime
            from datetime import datetime
            modified = datetime.fromtimestamp(mtime).strftime("%b %d, %Y %I:%M %p")
            projects.append({
                "name": name,
                "files": len(project_files),
                "modified": modified,
            })
    return _safe_jsonify({"projects": projects})


@app.route("/api/sync-meetings", methods=["POST"])
def sync_meetings():
    """Sync RSVPs, send reminders, and potentially reschedule meetings."""
    data = request.get_json(force=True)
    project_name = data.get("project_name", "").strip()
    meeting_type = data.get("meeting_type", "Kickoff").strip()

    if not project_name:
        return _safe_jsonify({"error": "project_name is required"}, 400)

    try:
        # Step 1: Check RSVP
        rsvp_res = json.loads(check_rsvp(project_name, meeting_type))
        if "error" in rsvp_res:
            return _safe_jsonify({"error": rsvp_res["error"]}, 400)

        counts = rsvp_res.get("counts", {})

        # Step 2: Send reminders if anyone declined
        if counts.get("declined", 0) > 0:
            send_reminder(project_name, meeting_type)

        # Step 3: Try to reschedule if proposals exist
        reschedule_meeting(project_name, meeting_type)

        # Return updated project results for the UI to refresh
        return project_results(project_name)

    except Exception as e:
        return _safe_jsonify({"error": str(e)}, 500)


if __name__ == "__main__":
    import logging
    # Suppress Flask/Werkzeug default logs and banner for a clean terminal
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    # Silence the Flask "Running on..." banner
    import flask.cli
    #flask.cli.show_server_banner = lambda *args: None
    
    print("\n" + "=" * 60)
    print("🚀 PM Assistant is ready!")
    print("👉 Open the Web UI here: http://localhost:5173")
    print("=" * 60 + "\n")
    
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
