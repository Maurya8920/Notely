# load_dotenv MUST run first, before any other imports that read env vars
from dotenv import load_dotenv
load_dotenv()

import os
import sys
import time

# ── Startup env validation ────────────────────────────────────────────────────
# Ensure compatibility between GEMINI_API_KEY and GOOGLE_API_KEY
if not os.getenv("GEMINI_API_KEY") and os.getenv("GOOGLE_API_KEY"):
    os.environ["GEMINI_API_KEY"] = os.getenv("GOOGLE_API_KEY")
if not os.getenv("GOOGLE_API_KEY") and os.getenv("GEMINI_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.getenv("GEMINI_API_KEY")

_REQUIRED = ["GEMINI_API_KEY", "QDRANT_ENDPOINT", "QDRANT_API_KEY"]
_missing = [k for k in _REQUIRED if not os.getenv(k)]
if _missing:
    print(
        f"\n[ERROR] Missing required environment variables: {', '.join(_missing)}\n"
        f"  → Make sure RAG/.env exists and contains these keys.\n",
        file=sys.stderr,
    )
    sys.exit(1)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.upload import router as upload_router
from routes.generation import router as generation_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Warm up heavy models once at startup (Qdrant, OCR, Docling)
    so user requests don't suffer first-request stalls.
    """
    print("[startup] Warming up Qdrant client...", flush=True)
    try:
        from qdrant_store.qdrant_client import get_qdrant_client, ensure_collection
        client = get_qdrant_client()
        ensure_collection(client)
        print("[startup] Qdrant ready.", flush=True)
    except Exception as e:
        print(f"[startup] Warning: Qdrant warmup failed: {e}", flush=True)

    print("[startup] Warming up OCR engine...", flush=True)
    try:
        from parsers.parser import get_rapid_engine
        get_rapid_engine()
        print("[startup] OCR engine ready.", flush=True)
    except Exception as e:
        print(f"[startup] Warning: OCR warmup failed: {e}", flush=True)

    print("[startup] Warming up Document Converter (Docling)...", flush=True)
    try:
        from parsers.parser import get_converter
        get_converter()
        print("[startup] Docling document converter ready.", flush=True)
    except Exception as e:
        print(f"[startup] Warning: Docling warmup failed: {e}", flush=True)

    print("[startup] Ready — all services initialised.", flush=True)
    yield


app = FastAPI(title="Notely RAG Pipeline", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
@app.get("/health")
@app.get("/ping")
def health_check():
    return {
        "status": "ok",
        "service": "notely-rag-fastapi",
        "timestamp": time.time(),
    }


app.include_router(upload_router)
app.include_router(generation_router)
