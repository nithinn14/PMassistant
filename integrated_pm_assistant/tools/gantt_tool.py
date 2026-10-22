import pandas as pd
import plotly.express as px
import json
import re
from pathlib import Path
from google.adk.tools.function_tool import FunctionTool

import ast
import numpy as np

def _safe_json_parse(raw_input):
    if isinstance(raw_input, dict): return raw_input
    raw_str = str(raw_input)
    match = re.search(r'\{.*\}', raw_str, re.DOTALL)
    if not match: raise ValueError("No JSON object found")
    cleaned = re.sub(r'\bNaN\b', 'null', match.group(0))
    return json.loads(cleaned)

def _parse_deps(dep):
    """Accepts a real list, JSON text '["TASK-001"]', or Python-style text "['TASK-001']"."""
    if isinstance(dep, (list, tuple)):
        return [str(d).strip() for d in dep if str(d).strip()]
    if dep is None or (isinstance(dep, float) and pd.isna(dep)):
        return []
    if isinstance(dep, str):
        text = dep.strip()
        if not text:
            return []
        for loader in (json.loads, ast.literal_eval):
            try:
                value = loader(text)
            except Exception:
                continue
            if isinstance(value, (list, tuple)):
                return [str(d).strip() for d in value if str(d).strip()]
            if isinstance(value, str) and value.strip():
                return [value.strip()]
        parts = re.split(r"[,\s]+", text.strip("[]"))
        return [p.strip("'\"") for p in parts if p.strip("'\"")]
    return []
 
 
def _work_days(start, end):
    """Working days from start to end, both included (at least 1)."""
    return max(int(np.busday_count(start.date(), (end + pd.Timedelta(days=1)).date())), 1)
 
 
def _add_work_days(start, n):
    """The date n working days after start (n=0 is start itself, moved off a weekend)."""
    return pd.Timestamp(np.busday_offset(start.date(), n, roll="forward"))
 
 
def _apply_dependency_scheduling(df, project_start):
    """
    Schedule tasks by their dependencies: a task with no dependencies starts on
    the project start date; any other task starts on the working day after the
    last of its dependencies ends. Task length (in working days) is kept.
    """
    df = df.copy()
    project_start = pd.Timestamp(project_start)
    df["start_date"] = pd.to_datetime(df["start_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
 
    if "dependencies" not in df.columns:
        df["dependencies"] = [[] for _ in range(len(df))]
    df["dependencies"] = df["dependencies"].apply(_parse_deps)
 
    # Identify each task by task_id; fall back to the name when the id is missing.
    def _key(row):
        tid = row.get("task_id")
        if tid is None or (isinstance(tid, float) and pd.isna(tid)) or not str(tid).strip():
            return str(row["task_name"])
        return str(tid).strip()
    df["_key"] = df.apply(_key, axis=1)
 
    # Length of each task in working days, measured BEFORE any date is changed.
    lengths = {i: _work_days(df.at[i, "start_date"], df.at[i, "end_date"]) for i in df.index}
 
    end_dates = {}
    pending = list(df.index)
    while pending:
        progressed = False
        for i in list(pending):
            deps = df.at[i, "dependencies"]
            if all(d in end_dates for d in deps):
                if deps:
                    start = _add_work_days(max(end_dates[d] for d in deps), 1)
                else:
                    start = _add_work_days(project_start, 0)
                end = _add_work_days(start, lengths[i] - 1)
                df.at[i, "start_date"], df.at[i, "end_date"] = start, end
                end_dates[df.at[i, "_key"]] = end
                pending.remove(i)
                progressed = True
        if not progressed:
            stuck = {df.at[i, "_key"]: [d for d in df.at[i, "dependencies"] if d not in end_dates]
                     for i in pending}
            print("Warning: could not schedule these tasks (circular dependency, or a dependency "
                  f"that does not exist). They keep their original dates: {stuck}")
            break
 
    return df.drop(columns=["_key"])

def generate_gantt_chart(input_json: str) -> str:
    try:
        data = _safe_json_parse(input_json)
        project_name = data["project_name"]
        tasks = data.get("tasks", [])

        df = pd.DataFrame(tasks)

        # Ensure dates are in datetime format
        df["start_date"] = pd.to_datetime(df["start_date"])
        df["end_date"] = pd.to_datetime(df["end_date"])

        # NEW: Fix waterfall issue
        project_start = df["start_date"].min()
        df = _apply_dependency_scheduling(df, project_start)

        fig = px.timeline(
            df, 
            x_start="start_date",  
            x_end="end_date",    
            y="task_name",
            color="assigned_empl",
            title=f"Project Roadmap: {project_name}",
            hover_data={
                "assigned_email": True,
                "project_manager": True,
                "RACI": True,
                "start_date": "|%b %d, %Y",
                "end_date": "|%b %d, %Y"
            }
        )

        fig.update_yaxes(autorange="reversed") 
        
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        
        html_path = output_dir / f"{project_name}_Gantt.html"
        fig.write_html(str(html_path))
        
        png_path = output_dir / f"{project_name}_Gantt.png"
        fig.write_image(str(png_path))

        print(f"📊 Gantt HTML: {html_path}")
        print(f"🖼️ Gantt PNG: {png_path}")

        return json.dumps({
            "status": "Success", 
            "html_path": str(html_path),
            "png_path": str(png_path)
        })
        
    except Exception as e:
        return json.dumps({"error": f"Gantt Tool failed: {str(e)}"})


gantt_chart_tool = FunctionTool(generate_gantt_chart)