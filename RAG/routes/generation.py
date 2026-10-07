from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from utils import generate_answer, generate_flashcards, llm, _retrieve_context

router = APIRouter()


class AskRequest(BaseModel):
    query: str
    user_id: str
    course_id: str
    document_name: Optional[str] = None


class HistoryMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    query: str
    history: Optional[list[HistoryMessage]] = []
    user_id: Optional[str] = None
    course_id: Optional[str] = None
    document_name: Optional[str] = None


class ChatWithContextRequest(BaseModel):
    query: str
    user_id: str
    course_id: str
    history: Optional[list[HistoryMessage]] = []
    document_name: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    sources: Optional[list[int]] = []


class AskResponse(BaseModel):
    answer: str
    sources: list[int]


class FlashcardRequest(BaseModel):
    query: str
    user_id: str
    course_id: str
    num_cards: int = Field(default=5, ge=1, le=20)
    document_name: Optional[str] = None


class FlashcardItem(BaseModel):
    question: str
    answer: str
    sources: list[int]


class FlashcardResponse(BaseModel):
    flashcards: list[FlashcardItem]


def extract_text(content) -> str:
    """Normalize AIMessage.content into plain text.

    content can be:
    - a plain string
    - a list of content block dicts, e.g. [{"type": "text", "text": "...", ...}, ...]
    """
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts).strip()

    return str(content).strip()


@router.post("/ask", response_model=AskResponse)
def ask_question(request: AskRequest):
    """
    Answer a question using only the user's retrieved course material.
    """
    try:
        result = generate_answer(
            query_text=request.query,
            user_id=request.user_id,
            course_id=request.course_id,
            document_name=request.document_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"Generation failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

    return result


@router.post("/chat-with-context", response_model=ChatResponse)
def chat_with_context(request: ChatWithContextRequest):
    """Answer a course conversation message with retrieved course document context and history."""
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Please provide a message.")

    context, source_count = _retrieve_context(
        query_text=query,
        user_id=request.user_id,
        course_id=request.course_id,
        document_name=request.document_name,
        limit=10,
    )

    system_content = (
        "You are Notely, a helpful study assistant. "
        "You have access to the user's uploaded course materials and notes below, "
        "along with the conversation history. "
        "Use the retrieved course material to answer questions, solve assignments, or explain topics accurately. "
        "If the user asks to solve, explain, or summarize their document or assignment, base your response on the provided material. "
        "Be concise, coherent, and accurate."
    )

    if context:
        system_content += (
            f"\n\n--- RETRIEVED COURSE MATERIAL ({source_count} sources) ---\n"
            f"{context}\n"
            f"----------------------------------------"
        )

    messages = [SystemMessage(content=system_content)]

    for msg in (request.history or []):
        role = msg.role.strip().lower()
        text = msg.content.strip()
        if not text:
            continue
        if role == "user":
            messages.append(HumanMessage(content=text))
        elif role == "assistant":
            messages.append(AIMessage(content=text))

    messages.append(HumanMessage(content=query))

    try:
        response = llm.invoke(messages)
        answer = extract_text(response.content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Generation failed: {e}")

    if not answer:
        raise HTTPException(
            status_code=502, detail="Generation returned an empty response."
        )

    sources = list(range(1, source_count + 1)) if source_count > 0 else []
    return {"answer": answer, "sources": sources}


@router.post("/chat")
def chat(request: ChatRequest):
    """Answer a general course conversation message with full history context (with retrieved context if user/course provided)."""
    # If user_id and course_id are provided, leverage retrieved course context
    if request.user_id and request.course_id:
        return chat_with_context(
            ChatWithContextRequest(
                query=request.query,
                user_id=request.user_id,
                course_id=request.course_id,
                history=request.history,
                document_name=request.document_name,
            )
        )

    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Please provide a message.")

    # Build the LangChain message list from stored conversation history
    messages = [
        SystemMessage(
            content=(
                "You are Notely, a helpful study assistant. "
                "You have access to the full conversation history below. "
                "Use it to answer follow-up questions, remember context, and give "
                "coherent multi-turn responses. Be concise and accurate."
            )
        )
    ]

    for msg in (request.history or []):
        role = msg.role.strip().lower()
        text = msg.content.strip()
        if not text:
            continue
        if role == "user":
            messages.append(HumanMessage(content=text))
        elif role == "assistant":
            messages.append(AIMessage(content=text))

    # Append the new user message
    messages.append(HumanMessage(content=query))

    try:
        response = llm.invoke(messages)
        answer = extract_text(response.content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Generation failed: {e}")

    if not answer:
        raise HTTPException(
            status_code=502, detail="Generation returned an empty response."
        )

    return {"answer": answer}


@router.post("/generate-flashcards", response_model=FlashcardResponse)
def create_flashcards(request: FlashcardRequest):
    """
    Generate study flashcards from the user's retrieved course material.
    """
    try:
        cards = generate_flashcards(
            query_text=request.query,
            user_id=request.user_id,
            course_id=request.course_id,
            num_cards=request.num_cards,
            document_name=request.document_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"Generation failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

    return {"flashcards": cards}

