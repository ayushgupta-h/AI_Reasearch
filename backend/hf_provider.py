"""
HuggingFace LLM Provider
=========================
Provides free LLM access through HuggingFace's Inference API.

Supports multiple free models:
  - HuggingFaceH4/zephyr-7b-beta        (general purpose, fast)
  - mistralai/Mistral-7B-Instruct-v0.3  (strong reasoning)
  - microsoft/Phi-3-mini-4k-instruct    (compact, efficient)
  - Qwen/Qwen2.5-7B-Instruct            (multilingual)

Usage::

    from hf_provider import get_hf_llm, get_hf_writer_chain, get_hf_critic_chain

    # Get a raw chat model
    llm = get_hf_llm()

    # Get ready-made chains
    writer = get_hf_writer_chain()
    critic = get_hf_critic_chain()
"""

import os
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from rich.console import Console

load_dotenv()

console = Console()

# ─── Available Free Models ───────────────────────────────────────────────────

HF_MODELS = {
    "zephyr":  "HuggingFaceH4/zephyr-7b-beta",
    "mistral": "mistralai/Mistral-7B-Instruct-v0.3",
    "phi3":    "microsoft/Phi-3-mini-4k-instruct",
    "qwen":    "Qwen/Qwen2.5-7B-Instruct",
}

DEFAULT_MODEL = "mistral"


# ─── LLM Builder ────────────────────────────────────────────────────────────


def get_hf_llm(model_key: str = DEFAULT_MODEL, max_new_tokens: int = 2048):
    """
    Create a HuggingFace chat LLM via the free Inference API.

    Args:
        model_key: One of 'zephyr', 'mistral', 'phi3', 'qwen',
                   or a full HuggingFace repo ID (e.g. 'org/model-name').
        max_new_tokens: Maximum tokens to generate.

    Returns:
        A LangChain ChatModel backed by HuggingFace Inference API.
    """
    from langchain_huggingface import HuggingFaceEndpoint, ChatHuggingFace

    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")
    if not hf_token:
        raise ValueError(
            "HuggingFace token not found. "
            "Set HF_TOKEN in your .env file. "
            "Get one at https://huggingface.co/settings/tokens"
        )

    # Resolve short name → full repo ID
    repo_id = HF_MODELS.get(model_key, model_key)

    console.print(f"  [dim cyan]🤗 Loading HuggingFace model: {repo_id}[/dim cyan]")

    endpoint = HuggingFaceEndpoint(
        repo_id=repo_id,
        task="text-generation",
        max_new_tokens=max_new_tokens,
        huggingfacehub_api_token=hf_token,
        temperature=0.3,
    )

    return ChatHuggingFace(llm=endpoint)


def is_hf_available() -> bool:
    """Check if HuggingFace is configured (token is set)."""
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")
    return token is not None


def list_available_models() -> dict[str, str]:
    """Return the dict of available model shortcuts → repo IDs."""
    return HF_MODELS.copy()


# ─── Research-Specific Chains ────────────────────────────────────────────────


def get_hf_writer_chain(model_key: str = DEFAULT_MODEL):
    """
    Build a research report writer chain powered by a free HuggingFace model.
    """
    hf_llm = get_hf_llm(model_key)

    writer_prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are an expert research writer. Write clear, structured and "
         "insightful reports. Be detailed, factual and professional."),
        ("human", """Write a detailed research report on the topic below.

Topic: {topic}

Research Gathered:
{research}

Structure the report as:
- Introduction
- Key Findings (minimum 3 well-explained points)
- Conclusion
- Sources (list all URLs found in the research)

Be detailed, factual and professional."""),
    ])

    return writer_prompt | hf_llm | StrOutputParser()


def get_hf_critic_chain(model_key: str = DEFAULT_MODEL):
    """
    Build a research report critic chain powered by a free HuggingFace model.
    """
    hf_llm = get_hf_llm(model_key)

    critic_prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are a sharp and constructive research critic. "
         "Be honest and specific."),
        ("human", """Review the research report below and evaluate it strictly.

Report:
{report}

Respond in this exact format:

Score: X/10

Strengths:
- ...
- ...
Areas to improve:
- ...
- ...

One line verdict:
... """),
    ])

    return critic_prompt | hf_llm | StrOutputParser()


def get_hf_summarizer_chain(model_key: str = DEFAULT_MODEL):
    """
    Build a summarization chain — useful for condensing
    long scraped content before feeding it to the writer.
    """
    hf_llm = get_hf_llm(model_key)

    summarizer_prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are a precise research summarizer. Extract the most important "
         "facts, findings, and insights. Preserve all URLs and citations."),
        ("human", """Summarize the following research content concisely while
preserving all key facts, data points, URLs, and citations.

Content:
{content}

Provide a structured summary with bullet points."""),
    ])

    return summarizer_prompt | hf_llm | StrOutputParser()
