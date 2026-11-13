from google.adk.agents import LlmAgent
from config_loader import load_config
from tools.prd.save_prd_tool import save_prd_tool


def build_product_manager_agent():

    config = load_config()
    model_name = config["models"]["default"]
    template = config["agents"]["product_manager"]["template"]

    print(f"DEBUG: Building Product Manager Agent with model: {model_name}")
    return LlmAgent(
        name="product_manager_agent",
        description="Generates structured PRD JSON",
        model=model_name,
        instruction=template,
        tools=[save_prd_tool]
    )
