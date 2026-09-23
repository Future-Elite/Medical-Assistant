import os
import warnings
from typing import *
from dotenv import load_dotenv
#from transformers import logging

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI

from interface import create_demo
# from medrax.agent import *
# from medrax.tools import *
# from medrax.utils import *
from medrax.tools.pubmed import PubMedEvidenceTool
from medrax.utils import load_prompts_from_file


warnings.filterwarnings("ignore")
#logging.set_verbosity_error()
_ = load_dotenv()


DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_MODEL = "qwen3-vl-plus"


def _create_qwen_model(model, temperature, top_p, client_kwargs=None):
    """Create a Qwen client through Alibaba Cloud's OpenAI-compatible endpoint."""
    kwargs = dict(client_kwargs or {})
    kwargs.setdefault("api_key", os.getenv("OPENAI_API_KEY"))
    kwargs.setdefault("base_url", os.getenv("OPENAI_BASE_URL", DEFAULT_QWEN_BASE_URL))
    if not kwargs["api_key"]:
        raise ValueError("OPENAI_API_KEY is required for Alibaba Cloud Qwen")
    return ChatOpenAI(
        model=model or os.getenv("OPENAI_MODEL", DEFAULT_QWEN_MODEL),
        temperature=temperature,
        top_p=top_p,
        **kwargs,
    )


def initialize_agent(
    prompt_file,
    tools_to_use=None,
    model_dir="/model-weights",
    temp_dir="temp",
    device="cuda",
    model=None,
    temperature=0.7,
    top_p=0.95,
    openai_kwargs=None,
):
    from medrax.agent import Agent
    from medrax.tools.classification import ChestXRayClassifierTool
    from medrax.tools.segmentation import ChestXRaySegmentationTool
    from medrax.tools.report_generation import ChestXRayReportGeneratorTool
    from medrax.tools.xray_vqa import XRayVQATool
    from medrax.tools.llava_med import LlavaMedTool
    from medrax.tools.grounding import XRayPhraseGroundingTool
    from medrax.tools.generation import ChestXRayGeneratorTool
    from medrax.tools.dicom import DicomProcessorTool
    from medrax.tools.utils import ImageVisualizerTool
    
    """Initialize the MedRAX agent with specified tools and configuration.

    Args:
        prompt_file (str): Path to file containing system prompts
        tools_to_use (List[str], optional): List of tool names to initialize. If None, all tools are initialized.
        model_dir (str, optional): Directory containing model weights. Defaults to "/model-weights".
        temp_dir (str, optional): Directory for temporary files. Defaults to "temp".
        device (str, optional): Device to run models on. Defaults to "cuda".
        model (str, optional): Qwen model name. Defaults to OPENAI_MODEL or qwen3-vl-plus.
        temperature (float, optional): Temperature for the model. Defaults to 0.7.
        top_p (float, optional): Top P for the model. Defaults to 0.95.
        openai_kwargs (dict, optional): Overrides for the Qwen-compatible client.

    Returns:
        Tuple[Agent, Dict[str, BaseTool]]: Initialized agent and dictionary of tool instances
    """
    prompts = load_prompts_from_file(prompt_file)
    prompt = prompts["MEDICAL_ASSISTANT"]

    all_tools = {
        "ChestXRayClassifierTool": lambda: ChestXRayClassifierTool(device=device),
        "ChestXRaySegmentationTool": lambda: ChestXRaySegmentationTool(device=device),
        "LlavaMedTool": lambda: LlavaMedTool(cache_dir=model_dir, device=device, load_in_8bit=True),
        "XRayVQATool": lambda: XRayVQATool(cache_dir=model_dir, device=device),
        "ChestXRayReportGeneratorTool": lambda: ChestXRayReportGeneratorTool(
            cache_dir=model_dir, device=device
        ),
        "XRayPhraseGroundingTool": lambda: XRayPhraseGroundingTool(
            cache_dir=model_dir, temp_dir=temp_dir, load_in_8bit=True, device=device
        ),
        "ChestXRayGeneratorTool": lambda: ChestXRayGeneratorTool(
            model_path=f"{model_dir}/roentgen", temp_dir=temp_dir, device=device
        ),
        "ImageVisualizerTool": lambda: ImageVisualizerTool(),
        "DicomProcessorTool": lambda: DicomProcessorTool(temp_dir=temp_dir),
    }

    # Initialize only selected tools or all if none specified
    tools_dict = {}
    tools_to_use = tools_to_use or all_tools.keys()
    for tool_name in tools_to_use:
        if tool_name in all_tools:
            tools_dict[tool_name] = all_tools[tool_name]()

    checkpointer = MemorySaver()
    chat_model = _create_qwen_model(model, temperature, top_p, openai_kwargs)
    agent = Agent(
        chat_model,
        tools=list(tools_dict.values()),
        log_tools=True,
        log_dir="logs",
        system_prompt=prompt,
        checkpointer=checkpointer,
    )

    print("Agent initialized")
    return agent, tools_dict


def initialize_pubmed_agent(
    prompt_file,
    model=None,
    temperature=0.2,
    top_p=0.95,
    openai_kwargs=None,
):
    """Initialize the PubMed-only agent without changing the legacy imaging agent."""
    prompts = load_prompts_from_file(prompt_file)
    tool = PubMedEvidenceTool()
    chat_model = _create_qwen_model(model, temperature, top_p, openai_kwargs)

    #Agent is created with a limit on model calls and tool calls to prevent excessive usage.
    agent = create_agent(
        model=chat_model,
        tools=[tool],
        system_prompt=prompts["PUBMED_ASSISTANT"],
        middleware=[
            ModelCallLimitMiddleware(run_limit=4, exit_behavior="error"),
            ToolCallLimitMiddleware(
                tool_name=tool.name,
                run_limit=2,
                exit_behavior="error",
            ),
        ],
        checkpointer=MemorySaver(),
        name="pubmed_agent",
    )
    return agent, {"PubMedEvidenceTool": tool}


if __name__ == "__main__":
    """
    This is the main entry point for the MedRAX application.
    It initializes the agent with the selected tools and creates the demo.
    """
    print("Starting server...")

    # Example: initialize with only specific tools
    # Here three tools are commented out, you can uncomment them to use them
    selected_tools = [
        "ImageVisualizerTool",
        "DicomProcessorTool",
        "ChestXRayClassifierTool",
        "ChestXRaySegmentationTool",
        "ChestXRayReportGeneratorTool",
        "XRayVQATool",
        # "LlavaMedTool",
        # "XRayPhraseGroundingTool",
        # "ChestXRayGeneratorTool",
    ]

    if os.getenv("MEDRAX_AGENT_MODE", "medical").lower() == "pubmed":
        agent, tools_dict = initialize_pubmed_agent(
            "medrax/docs/system_prompts.txt",
        )
    else:
        agent, tools_dict = initialize_agent(
            "medrax/docs/system_prompts.txt",
            tools_to_use=selected_tools,
            model_dir="/model-weights",  # Change this to the path of the model weights
            temp_dir="temp",  # Change this to the path of the temporary directory
            device="cuda",  # Change this to the device you want to use
            temperature=0.7,
            top_p=0.95,
        )
    demo = create_demo(agent, tools_dict)

    demo.launch(server_name="0.0.0.0", server_port=8585, share=False)
