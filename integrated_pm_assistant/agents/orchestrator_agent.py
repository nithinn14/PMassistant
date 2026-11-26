import json
import re
from google.adk.agents import SequentialAgent, LoopAgent
from agents.deterministic_agent import DeterministicAgent

from agents.product_manager_agent import build_product_manager_agent
from agents.task_agent import build_task_agent
from agents.resource_agent import build_resource_agent
from agents.resource_validation_agent import build_resource_validation_agent
from agents.scheduler_agent import build_scheduler_agent
from agents.communication import build_communication_agent
from tools.resources.resource_validator import (
    check_workflow_resolution,
    update_workflow_state,
)


def _is_external_hire_task(task: dict) -> bool:
    emp = str(task.get("Assigned_Employee", "")).strip().lower()
    status = str(task.get("task_status", "")).strip().lower()
    return emp == "needs_external_hire" or status == "needs_external_hire"


def _is_unresolved_shortage_task(task: dict) -> bool:
    # Explicitly approved external-hire tasks are not unresolved.
    if _is_external_hire_task(task):
        return False

    emp = str(task.get("Assigned_Employee", "")).strip().lower()
    status = str(task.get("task_status", "")).strip().lower()

    return emp in ("", "no resource available") or status == "waiting_resource"

def _resource_loop_guard(input_text: str) -> str:
    """
    Post-loop guard after Resource_Loop exits.

    Allows the pipeline to continue when every task has an assigned employee
    or is explicitly marked NEEDS_EXTERNAL_HIRE. Otherwise marks the workflow
    RESOURCE_BLOCKED and stops execution before scheduling/communication.
    """
    match = re.search(r"\{.*\}", input_text, re.DOTALL)
    json_str = re.sub(r"\bNaN\b", "null", match.group(0) if match else input_text)
    data = json.loads(json_str)

    project_name = data.get("project_name", "")
    tasks = data.get("tasks", [])

    shortage_tasks = [t for t in tasks if _is_unresolved_shortage_task(t)]

    if not shortage_tasks:
        return input_text

    wf_state = check_workflow_resolution(project_name) if project_name else {}
    resolution = wf_state.get("resolution")

    missing_roles: dict[str, int] = {}
    for t in shortage_tasks:
        role = str(t.get("assigned_role") or "Unknown Role")
        missing_roles[role] = missing_roles.get(role, 0) + 1

    if project_name:
        safe_resolution = (
            resolution
            if resolution not in (None, "", "NO_STATE_FILE")
            else "PENDING"
        )
        update_workflow_state(
            project_name=project_name,
            status="RESOURCE_BLOCKED",
            missing_roles=missing_roles,
            diagnostic_path=wf_state.get("diagnostic_file", ""),
            resolution=safe_resolution,
        )

    raise RuntimeError(
        f"Resource_Loop exhausted with unresolved shortages for '{project_name}'. "
        "Workflow marked RESOURCE_BLOCKED before scheduling."
    )


def build_orchestrator(integrations=None):
    """
    ADK-native orchestrator with interruptible resource resolution.

    Architecture:
        SequentialAgent (PM_Orchestrator)
        ├── ProductManagerAgent     → Generates PRD
        ├── TaskAgent               → Converts PRD to structured tasks
        ├── LoopAgent (Resource_Loop)
        │   ├── ResourceAgent       → Assigns employees to tasks
        │   └── ResourceValidationAgent
        │       ├── If OK → escalate=True (exits loop)
        │       └── If shortage → email PM, poll, loop back to ResourceAgent
        ├── SchedulerAgent          → Creates schedule + Gantt chart
        └── CommunicationAgent      → Manages meetings + notifications
    """
    default_integrations = {
        "google_calendar": True,
        "gmail": True,
        "telegram": True,
        "gantt": True,
    }
    integrations = {**default_integrations, **(integrations or {})}

    return SequentialAgent(
        name="PM_Orchestrator",
        description="""
Executes full project lifecycle with resource shortage detection:

1. Generate PRD
2. Generate Tasks
3. Assign Resources (with retry loop for shortage resolution)
4. Create Schedule
5. Manage Meeting Lifecycle (Hybrid RSVP + Escalation + Auto Reschedule)
""",
        sub_agents=[
            DeterministicAgent(
                name="Start_Signal",
                description="Signals pipeline start",
                logic=lambda x: (print("DEBUG: Orchestrator STARTED"), x)[1]
            ),
            build_product_manager_agent(),
            build_task_agent(),
            LoopAgent(
                name="Resource_Loop",
                description="Loops resource assignment until all tasks are staffed or PM resolves shortages",
                max_iterations=10,
                sub_agents=[
                    build_resource_agent(),
                    build_resource_validation_agent(integrations=integrations),
                ],
            ),
            DeterministicAgent(
                name="Resource_Loop_Guard",
                description="Stops pipeline before scheduling if Resource_Loop exits with unresolved shortages",
                logic=_resource_loop_guard,
            ),
            build_scheduler_agent(integrations=integrations),
            DeterministicAgent(
                name="Signal_Communication",
                description="Signals communication start",
                logic=lambda x: (print("Starting Communication and Meeting Creation..."), x)[1]
            ),
            build_communication_agent(integrations=integrations),
            DeterministicAgent(
                name="Final_Signal",
                description="Signals pipeline completion",
                logic=lambda x: (print("Execution Completed"), x)[1]
            )
        ],
    )