from google.adk.agents import LlmAgent
from google.genai import types
from config_loader import load_config
from tools.save_tasks_tool import save_tasks_tool
from data_backend import get_backend

def _available_roles():
    """Distinct job roles in the employee data, read fresh every run."""
    try:
        df = get_backend().read("employees")
        df.columns = df.columns.str.strip()
        return sorted({str(r).strip() for r in df["Role"].dropna() if str(r).strip()})
    except Exception as exc:
        print(f"⚠️ Could not read roles from the employee data: {exc}")
        return []

def build_task_agent():

    config = load_config()
    model_name = config["models"]["default"]
    template = config["agents"]["task_decomposer"]["template"]

    roles = _available_roles()
    role_rule = ""
    if roles:
        print(f"DEBUG: Task agent limited to {len(roles)} roles from the employee data")
        role_rule = (
            "\nROLE RULE (very important):\n"
            "The company has people in ONLY these job roles:\n"
            + "\n".join(f"- {r}" for r in roles)
            + "\nFor every task, assigned_role MUST be copied exactly (same spelling and "
            "capital letters) from this list, as ONE single role per task. Choose the "
            "closest match for the work. Never invent a role, never combine two roles "
            "(no slashes), and never use a different spelling.\n"
        )

    instruction = f"""
{template}
{role_rule}
You MUST:
1. Generate structured task JSON.
2. Call save_tasks_tool.
3. Return ONLY the raw JSON string returned by save_tasks_tool — no markdown
   code fences (no ```json), no explanatory text before or after, no
   commentary. Your entire response must be valid JSON and nothing else.
Do not modify structure.
"""

    return LlmAgent(
        name="task_decomposer_agent",
        description="Generates development tasks from PRD JSON",
        model=model_name,
        instruction=instruction,
        tools=[save_tasks_tool],
        # Ensure large task lists are never silently truncated mid-JSON.
        generate_content_config=types.GenerateContentConfig(
            max_output_tokens=8192,
        ),
    )