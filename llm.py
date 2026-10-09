"""LLM layer: LangChain, Gemini by default, OpenRouter as an option. Keys come from env only."""
import os

SYSTEM_PROMPT = (
    "You are Crayon, a friendly, sharp AI assistant on a public website. "
    "Answer clearly and concisely. Use short paragraphs and simple markdown "
    "(bold, lists, code blocks) when it helps. If you are unsure, say so. "
    "Do not claim to take actions in the world, browse, or remember past visits; "
    "you only see this conversation."
)


def provider() -> str:
    return os.environ.get("LLM_PROVIDER", "gemini").strip().lower()


def build_model(streaming: bool = True):
    p = provider()
    if p == "openrouter":
        from langchain_openai import ChatOpenAI
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY missing")
        return ChatOpenAI(
            model=os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash"),
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            streaming=streaming,
            temperature=0.6,
            timeout=60,
            max_retries=1,
        )
    from langchain_google_genai import ChatGoogleGenerativeAI
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    return ChatGoogleGenerativeAI(
        model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        google_api_key=key,
        temperature=0.6,
        timeout=60,
        max_retries=1,
    )


def to_messages(history, user_text):
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    msgs = [SystemMessage(content=SYSTEM_PROMPT)]
    for role, text in history:
        msgs.append(HumanMessage(content=text) if role == "user" else AIMessage(content=text))
    msgs.append(HumanMessage(content=user_text))
    return msgs


def chunk_text(chunk) -> str:
    c = chunk.content
    if isinstance(c, str):
        return c
    out = []
    for part in c or []:
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            out.append(part.get("text", ""))
    return "".join(out)
