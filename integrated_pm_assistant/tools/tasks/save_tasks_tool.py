import json
import pandas as pd
from google.adk.tools.function_tool import FunctionTool
from data_backend import get_backend


def save_tasks(project_info: str):
    data = json.loads(project_info)

    project_name = data["project_name"]
    tasks = data["tasks"]

    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Valid 'tasks' list is required.")

    df = pd.DataFrame(tasks)
    backend = get_backend()
    backend.write(f"{project_name}_Tasks", df)

    print(f"✅ Tasks saved: {project_name}_Tasks")

    return json.dumps({"project_name": project_name, "tasks": tasks})


save_tasks_tool = FunctionTool(save_tasks)
