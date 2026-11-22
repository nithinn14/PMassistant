import json
from .deterministic_agent import DeterministicAgent
from tools.scheduling.save_schedule_tool import save_schedule
from tools.scheduling.gantt_tool import generate_gantt_chart

def scheduler_logic(input_text: str, integrations=None):
    try:
        # 1. Save schedule (returns {project_name: ..., tasks: ...})
        # input_text is already unwrapped by DeterministicAgent base class
        sched_result_str = save_schedule(
            input_json=input_text,
            integrations=integrations,
        )
        
        # Check for error in tool response
        if '"error":' in sched_result_str:
            return sched_result_str
            
        # 2. Generate Gantt Chart
        if integrations is None or integrations.get("gantt", True):
            generate_gantt_chart(input_json=sched_result_str)
        
        return sched_result_str
    except Exception as e:
        return json.dumps({"error": f"Scheduling logic failed: {str(e)}", "input_received": input_text[:500]})

def build_scheduler_agent(integrations=None):
    return DeterministicAgent(
        name="scheduler_agent",
        description="Deterministic scheduling agent",
        logic=lambda input_text: scheduler_logic(input_text, integrations=integrations)
    )