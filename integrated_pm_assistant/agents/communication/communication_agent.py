"""
CommunicationAgent (Hybrid Agentic Design)
--------------------------------------------
ADK SequentialAgent with two sub-agents:

1. meeting_creator    (DeterministicAgent)
   → Reads scheduled data and creates Kickoff meeting deterministically.

2. meeting_sync_agent (LlmAgent)
   → Uses FunctionTools (check_rsvp, send_reminder, reschedule_meeting)
     to autonomously manage the meeting lifecycle.

Architecture:
    SequentialAgent (communication_agent)
    ├── DeterministicAgent (meeting_creator)
    │   └── create_meeting_tool
    └── LlmAgent (meeting_sync_agent)
        ├── check_rsvp_tool
        ├── send_reminder_tool
        └── reschedule_meeting_tool
"""

import json
import pandas as pd

from google.adk.agents import SequentialAgent, LlmAgent

from agents.deterministic_agent import DeterministicAgent
from config_loader import load_config
from data_backend import get_backend
from meetings.tools.meeting_tools import (
    create_meeting_tool,
    check_rsvp_tool,
    send_reminder_tool,
    reschedule_meeting_tool,
)


# ---------------------------------------------------------------------------
# Sub-agent 1: Deterministic meeting creator
# ---------------------------------------------------------------------------

def _meeting_creator_logic(input_text: str) -> str:
    """
    Read the scheduled data and create the Kickoff meeting.
    This is deterministic — no LLM needed.
    """
    try:
        data = json.loads(input_text)
    except Exception:
        import re
        match = re.search(r"\{.*\}", input_text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                return json.dumps({"error": "Invalid JSON input to meeting_creator"})
        else:
            return json.dumps({"error": "Invalid JSON input to meeting_creator"})

    project_name = data.get("project_name", "")
    if not project_name:
        return json.dumps({"error": "Missing project_name"})

    backend = get_backend()
    try:
        df = backend.read(f"{project_name}_Scheduled")
    except Exception as e:
        return json.dumps({"error": f"Cannot read scheduled data: {e}"})

    # Create Kickoff meeting (deterministic — always needed for new projects)
    from meetings.tools.meeting_tools import create_meeting
    result = create_meeting(project_name=project_name, meeting_type="Kickoff")

    # Pass project info downstream to the LlmAgent for lifecycle management
    return json.dumps({
        "project_name": project_name,
        "kickoff_result": json.loads(result),
        "meeting_types": ["Kickoff", "Mid-Project Review", "Final Review"],
    })


# ---------------------------------------------------------------------------
# Sub-agent 2: LLM-powered meeting lifecycle agent (instruction)
# ---------------------------------------------------------------------------

MEETING_SYNC_INSTRUCTION = """
You are a Meeting Lifecycle Manager for project management.

You will receive JSON containing:
  - project_name: the project to manage
  - meeting_types: list of meeting types to check

Your job is to manage the RSVP and scheduling lifecycle for each meeting.
For each meeting type that has been created, you MUST:

1. Call check_rsvp to get the latest attendance status.
2. If any participants have declined:
   a. Call send_reminder to send escalation emails to those who haven't proposed alternatives.
   b. Call reschedule_meeting to check if a valid time proposal exists and reschedule.
3. Report a summary of what actions were taken.

IMPORTANT:
- Only check meetings that exist (have been previously created).
- If check_rsvp returns an error like "No meetings file found", skip that meeting.
- Do NOT create new meetings — that is handled by the previous agent.
- Call tools in order: check_rsvp → send_reminder → reschedule_meeting.
- After processing all meetings, return a JSON summary of results.
"""


# ---------------------------------------------------------------------------
# Build the hybrid agent
# ---------------------------------------------------------------------------

def build_communication_agent():
    """
    Build the hybrid CommunicationAgent as an ADK SequentialAgent.

    Flow:
      1. DeterministicAgent creates the Kickoff meeting
      2. LlmAgent uses tools to manage RSVP/reminders/rescheduling
    """
    config = load_config()
    model_name = config["models"]["default"]

    meeting_creator = DeterministicAgent(
        name="meeting_creator",
        description="Creates Kickoff meeting from scheduled data (deterministic)",
        logic=_meeting_creator_logic,
    )

    meeting_sync = LlmAgent(
        name="meeting_sync_agent",
        description="Manages meeting lifecycle: checks RSVP, sends reminders, reschedules",
        model=model_name,
        instruction=MEETING_SYNC_INSTRUCTION,
        tools=[check_rsvp_tool, send_reminder_tool, reschedule_meeting_tool],
    )

    return SequentialAgent(
        name="communication_agent",
        description=(
            "Hybrid communication agent: deterministic meeting creation "
            "followed by LLM-powered RSVP, reminders, and rescheduling."
        ),
        sub_agents=[meeting_creator, meeting_sync],
    )
