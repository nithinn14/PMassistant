"""
Job execution runtime for PMassistant.

Provides the shared in-memory job store, per-job stdout capture, the
process-wide stdout router, and the ContextVar that ties each pipeline
thread to its own capture buffer.

This module installs the process-wide JobOutputRouter on sys.stdout
exactly once, at import time.  api_server.py imports from here so that
no second router is ever installed.
"""

import contextvars
import io
import json
import re
import sys
import threading
from pathlib import Path
from typing import Optional


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
        # Reach through any installed router to get the real terminal stdout,
        # so that OutputCapture.write() (called only in legacy/direct paths)
        # does not re-enter the router.
        self._real_stdout = getattr(sys.stdout, "_orig", sys.stdout)
        self.lines: list[str] = []
        self._line_buf: str = ""  # accumulates partial writes from JobOutputRouter

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


# ── Per-job context variable ─────────────────────────────────────────────────
# Set to the current job_id at the start of each _run_pipeline thread; reset
# in the finally block.  No value (None) means "not inside a pipeline thread".
_current_job_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_current_job_id_var", default=None
)

# Lock used only to guard list.append() in the router (list.append is
# thread-safe in CPython, but we protect it for portability).
_router_lock = threading.Lock()


class JobOutputRouter(io.TextIOBase):
    """
    Process-wide stdout replacement that routes each write() call to the
    per-job OutputCapture stored in jobs[job_id]["_capture"], identified via
    _current_job_id_var.

    Rules:
    - Installed ONCE at module load; never replaced per-job.
    - Each pipeline thread sets _current_job_id_var at entry and resets it in
      finally, so no sys.stdout assignment is needed per job.
    - If no job context is active (e.g. Flask request handler, standalone
      print), output falls through to _original_stdout unchanged.
    - A thread-local re-entrancy guard prevents infinite recursion if the
      router's own flush() or the original stdout's write() triggers another
      write through the same router.
    - Partial writes (text without a newline, e.g. the newline that Python's
      print() sends as a separate write()) are accumulated in a per-job buffer
      and flushed as a complete line when '\\n' arrives, preserving the same
      line-strip and step-detection logic that OutputCapture uses.
    """

    def __init__(self, original_stdout: io.TextIOBase):
        # Do NOT call super().__init__() — TextIOBase.__init__ would mark the
        # stream as closed, which breaks isatty() and similar checks.
        self._orig = original_stdout
        # Per-thread re-entrancy guard (bool stored in thread-local storage)
        self._in_write = threading.local()

    # ------------------------------------------------------------------
    # io.TextIOBase required interface
    # ------------------------------------------------------------------

    @property
    def encoding(self) -> str:
        return getattr(self._orig, "encoding", "utf-8") or "utf-8"

    @property
    def errors(self) -> str:
        return getattr(self._orig, "errors", "replace") or "replace"

    def isatty(self) -> bool:
        return False

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def flush(self) -> None:
        # Only flush the original stdout; per-job buffers are flushed inline.
        try:
            self._orig.flush()
        except Exception:
            pass

    def reconfigure(self, **kwargs) -> None:
        """Delegate reconfigure() to the underlying real stdout if it supports it.

        sys.stdout.reconfigure(encoding='utf-8') is called at module import
        time (line 20) on the real stdout before the router is installed, so
        that call always works.  Any subsequent call (e.g. from a library that
        expects sys.stdout to be reconfigurable) lands here; we delegate safely
        and swallow AttributeError so callers are never broken.
        """
        reconfigure_fn = getattr(self._orig, "reconfigure", None)
        if callable(reconfigure_fn):
            try:
                reconfigure_fn(**kwargs)
            except Exception:
                pass  # Best-effort; silently ignore if the underlying stream refuses

    # ------------------------------------------------------------------
    # Core routing logic
    # ------------------------------------------------------------------

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            text = str(text)

        # Re-entrancy guard: if this thread is already inside write(), fall
        # through to the original stdout immediately to avoid infinite loops.
        if getattr(self._in_write, "active", False):
            try:
                return self._orig.write(text)
            except Exception:
                return len(text)

        self._in_write.active = True
        try:
            job_id: Optional[str] = _current_job_id_var.get()

            if job_id is None:
                # Not inside a pipeline thread — pass straight through.
                try:
                    return self._orig.write(text)
                except Exception:
                    return len(text)

            job = jobs.get(job_id)
            capture: Optional[OutputCapture] = job.get("_capture") if job else None

            if capture is None:
                # Job exists but capture not set up yet (race at thread start),
                # or job is unknown.  Emit to real stdout so nothing is lost.
                try:
                    return self._orig.write(text)
                except Exception:
                    return len(text)

            # Also mirror to terminal so the developer console still shows output.
            try:
                self._orig.write(text)
            except Exception:
                pass

            # Accumulate text into the per-job line buffer, then process
            # complete lines.  This handles Python print() which calls
            # write(message) then write('\\n') as two separate calls.
            capture._line_buf += text
            while "\n" in capture._line_buf:
                line, capture._line_buf = capture._line_buf.split("\n", 1)
                stripped = line.strip()
                if stripped:
                    with _router_lock:
                        capture.lines.append(stripped)
                    capture._update_step(stripped)

            return len(text)
        finally:
            self._in_write.active = False


# Install the process-wide router exactly once, replacing sys.stdout.
# All subsequent print() calls in any thread are now routed through this
# object.  Per-job routing happens via _current_job_id_var.
_stdout_router: JobOutputRouter = JobOutputRouter(sys.stdout)
sys.stdout = _stdout_router


__all__ = [
    "jobs",
    "STEP_KEYWORDS",
    "OutputCapture",
    "_current_job_id_var",
    "_router_lock",
    "JobOutputRouter",
    "_stdout_router",
]
