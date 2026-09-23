from google.adk.tools.function_tool import FunctionTool
from pathlib import Path
from typing import List
from .prd_pdf_writer import save_prd_pdf
import json
import warnings


def _normalize_str_field(value, field_name: str) -> str:
    """Coerce a value that should be a plain string to an actual string.

    The Google ADK's function-calling layer does not enforce that the model
    honours the STRING type declared in the tool schema.  Gemini occasionally
    wraps a single string in a one-element list, or rarely sends some other
    type.  This helper normalises those cases *before* anything is written to
    disk, so the corruption never makes it into the PRD JSON or the filename.

    Rules:
    - str            -> returned as-is (the happy path, no cost)
    - list, 1 item   -> unwrap and return that item (cast to str for safety)
    - list, >1 items -> join with a single space; the fields this is used for
                        (product_name, problem_statement) are intended to be
                        one cohesive piece of text, so concatenation preserves
                        all content without discarding anything.
    - anything else  -> str() conversion with a warning logged so the event is
                        visible in the pipeline logs without crashing.
    """
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        if len(value) == 1:
            result = str(value[0])
            warnings.warn(
                f"save_prd: field '{field_name}' arrived as a single-item list "
                f"{value!r}; unwrapped to string {result!r}. "
                "This is a model schema-compliance issue.",
                stacklevel=3,
            )
            return result
        # Multi-item list: join with a space — these fields are prose, not
        # enumerated items, so concatenation is the most faithful fallback.
        result = " ".join(str(item) for item in value)
        warnings.warn(
            f"save_prd: field '{field_name}' arrived as a multi-item list "
            f"{value!r}; joined to {result!r}. "
            "This is a model schema-compliance issue.",
            stacklevel=3,
        )
        return result

    # Unexpected type (e.g. None, int, …)
    result = str(value) if value is not None else ""
    warnings.warn(
        f"save_prd: field '{field_name}' has unexpected type "
        f"{type(value).__name__!r} with value {value!r}; "
        f"converted to string {result!r}.",
        stacklevel=3,
    )
    return result


def save_prd(
    product_name: str,
    problem_statement: str,
    goals: List[str],
    user_personas: List[str],
    functional_requirements: List[str],
    non_functional_requirements: List[str],
    constraints: List[str],
    assumptions: List[str],
    out_of_scope: List[str],
) -> dict:

    # Normalize the two str-typed fields that the model occasionally wraps in a
    # list, before they touch the filename or the JSON file.
    product_name = _normalize_str_field(product_name, "product_name")
    problem_statement = _normalize_str_field(problem_statement, "problem_statement")

    prd_data = {
        "product_name": product_name,
        "problem_statement": problem_statement,
        "goals": goals,
        "user_personas": user_personas,
        "functional_requirements": functional_requirements,
        "non_functional_requirements": non_functional_requirements,
        "constraints": constraints,
        "assumptions": assumptions,
        "out_of_scope": out_of_scope,
    }

    base_dir = Path(__file__).resolve().parent.parent
    output_dir = base_dir / "output"
    output_dir.mkdir(exist_ok=True)

    json_path = output_dir / f"{product_name}_PRD.json"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(prd_data, f, indent=4)

    print(f"✅ PRD JSON saved at: {json_path}")

    pdf_path = save_prd_pdf(prd_data, product_name)
    print(f"✅ PRD PDF saved at: {pdf_path}")

    return {"project_name": product_name}


save_prd_tool = FunctionTool(save_prd)