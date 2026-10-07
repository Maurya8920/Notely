import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, Form
from fastapi.concurrency import run_in_threadpool

from parsers.parser import parse_document
from chunking.chunker import chunk_document
from embedding.embedder import embed_and_upload

logger = logging.getLogger(__name__)

router = APIRouter()

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx"}
MAX_FILE_SIZE_MB = 20
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


@router.post("/upload")
async def upload_document(
    file: UploadFile,
    user_id: str = Form(...),
    course_id: str = Form(...),
):
    """
    Upload a document (PDF, DOCX, PPTX), parse it, chunk it,
    embed it, and store it in Qdrant scoped to the given user and course.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    # Validate extension
    original_name = file.filename
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Save with a UUID name to avoid path-traversal and collision issues
    temp_path = TEMP_DIR / f"{uuid.uuid4()}{suffix}"

    try:
        # Stream to disk and enforce size limit
        total_written = 0
        with temp_path.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MB at a time
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_FILE_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {MAX_FILE_SIZE_MB} MB size limit.",
                    )
                f.write(chunk)
    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except Exception:
        temp_path.unlink(missing_ok=True)
        logger.exception("Failed to save uploaded file")
        raise HTTPException(
            status_code=500, detail="Could not save the uploaded file. Please try again."
        )

    try:
        # Run blocking I/O in threadpool so the event loop isn't blocked
        markdown, image_chunks = await run_in_threadpool(
            parse_document, str(temp_path)
        )
    except Exception:
        logger.exception("Failed to parse document '%s'", original_name)
        raise HTTPException(
            status_code=500,
            detail="Failed to parse the document. The file may be corrupted or password-protected.",
        )
    finally:
        temp_path.unlink(missing_ok=True)  # always clean up, even if parsing fails

    if not markdown.strip():
        raise HTTPException(
            status_code=422, detail="No extractable text found in the document."
        )

    chunks = chunk_document(markdown)

    if not chunks:
        raise HTTPException(status_code=422, detail="Document produced no chunks.")

    try:
        uploaded_count = await run_in_threadpool(
            embed_and_upload,
            chunks,
            original_name,
            user_id,
            course_id,
        )
    except RuntimeError as e:
        # RuntimeError from embedder means retries were exhausted + rollback done
        logger.error("Embedding failed for '%s': %s", original_name, e)
        raise HTTPException(
            status_code=504,
            detail="Upload timed out while indexing. Please try again.",
        )
    except Exception:
        logger.exception("Embedding failed for '%s'", original_name)
        raise HTTPException(
            status_code=500,
            detail="Upload timed out while indexing. Please try again.",
        )

    return {
        "status": "completed",
        "filename": original_name,
        "chunks_created": uploaded_count,
        "images_found": len(image_chunks),
    }
