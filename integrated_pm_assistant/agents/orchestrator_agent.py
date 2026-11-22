from google.adk.agents import SequentialAgent, LoopAgent
from agents.deterministic_agent import DeterministicAgent

from agents.product_manager_agent import build_product_manager_agent
from agents.task_agent import build_task_agent
from agents.resource_agent import build_resource_agent
from agents.resource_validation_agent import build_resource_validation_agent
from agents.scheduler_agent import build_scheduler_agent
from agents.communication import build_communication_agent


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