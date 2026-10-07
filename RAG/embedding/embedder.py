import os
import uuid
import time
import logging

from qdrant_client import models
from qdrant_client.models import PointStruct, Document

from qdrant_store.qdrant_client import (
    get_qdrant_client,
    ensure_collection,
    COLLECTION_NAME,
)

logger = logging.getLogger(__name__)

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Configurable batch size — smaller batches avoid Qdrant Cloud timeouts
# when server-side inference is used.
BATCH_SIZE = int(os.getenv("QDRANT_BATCH_SIZE", "32"))

# Retry settings for transient errors (timeout / connection)
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds


def _is_retryable(exc: Exception) -> bool:
    """Return True for timeout and connection errors worth retrying."""
    msg = str(exc).lower()
    return any(
        keyword in msg
        for keyword in ("timeout", "timed out", "connection", "unavailable")
    )


def _rollback_document(
    client,
    document_name: str,
    user_id: str,
    course_id: str,
) -> None:
    """Delete all points already uploaded for this document to avoid partial data."""
    try:
        client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document",
                            match=models.MatchValue(value=document_name),
                        ),
                        models.FieldCondition(
                            key="user_id",
                            match=models.MatchValue(value=user_id),
                        ),
                        models.FieldCondition(
                            key="course_id",
                            match=models.MatchValue(value=course_id),
                        ),
                    ]
                )
            ),
        )
        logger.info(
            "Rolled back partial upload for document='%s', user='%s', course='%s'",
            document_name,
            user_id,
            course_id,
        )
    except Exception as rollback_exc:
        logger.error("Rollback failed: %s", rollback_exc)


def embed_and_upload(
    chunks: list[str],
    document_name: str,
    user_id: str,
    course_id: str,
) -> int:
    client = get_qdrant_client()
    ensure_collection(client)

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=Document(text=chunk, model=EMBED_MODEL),
            payload={
                "text": chunk,
                "document": document_name,
                "chunk_index": i,
                "user_id": user_id,
                "course_id": course_id,
                "uploaded_at": time.time(),
            },
        )
        for i, chunk in enumerate(chunks)
    ]

    # Upload in batches with retry + exponential backoff
    batch_size = BATCH_SIZE
    total_uploaded = 0

    for batch_start in range(0, len(points), batch_size):
        batch = points[batch_start : batch_start + batch_size]
        batch_num = (batch_start // batch_size) + 1
        total_batches = (len(points) + batch_size - 1) // batch_size

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                client.upsert(
                    collection_name=COLLECTION_NAME,
                    points=batch,
                    wait=True,
                )
                total_uploaded += len(batch)
                logger.info(
                    "Batch %d/%d uploaded (%d points)",
                    batch_num,
                    total_batches,
                    len(batch),
                )
                break  # success — move to next batch
            except Exception as exc:
                if attempt < MAX_RETRIES and _is_retryable(exc):
                    delay = RETRY_BACKOFF_BASE ** attempt
                    logger.warning(
                        "Batch %d/%d attempt %d failed (%s) — retrying in %ds",
                        batch_num,
                        total_batches,
                        attempt,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    # All retries exhausted or non-retryable error —
                    # roll back already-uploaded points for this document
                    logger.error(
                        "Batch %d/%d failed after %d attempts: %s",
                        batch_num,
                        total_batches,
                        attempt,
                        exc,
                    )
                    _rollback_document(client, document_name, user_id, course_id)
                    raise RuntimeError(
                        f"Upload failed at batch {batch_num}/{total_batches} "
                        f"after {attempt} attempts. "
                        f"Already-uploaded chunks have been cleaned up. "
                        f"Please try again."
                    ) from exc

    return total_uploaded


def query_documents(query_text: str, user_id: str, course_id: str, limit: int = 5):
    client = get_qdrant_client()
    return client.query_points(
        collection_name=COLLECTION_NAME,
        query=Document(text=query_text, model=EMBED_MODEL),
        query_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="user_id", match=models.MatchValue(value=user_id)
                ),
                models.FieldCondition(
                    key="course_id", match=models.MatchValue(value=course_id)
                ),
            ]
        ),
        limit=limit,
    )


def fetch_ordered_document_chunks(
    user_id: str,
    course_id: str,
    document_name: str | None = None,
    limit: int = 50,
):
    """
    Fetch chunks for a specific document or the latest document of a course,
    ordered by chunk_index ascending.
    """
    client = get_qdrant_client()
    must_conditions = [
        models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
        models.FieldCondition(key="course_id", match=models.MatchValue(value=course_id)),
    ]
    if document_name:
        must_conditions.append(
            models.FieldCondition(key="document", match=models.MatchValue(value=document_name))
        )

    records, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=models.Filter(must=must_conditions),
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )

    if not records:
        return []

    # If document_name was not specified, group records by document and pick the latest
    if not document_name:
        doc_records: dict[str, list] = {}
        for r in records:
            p = getattr(r, "payload", {}) or {}
            d = p.get("document", "")
            if d:
                doc_records.setdefault(d, []).append(r)

        if doc_records:
            target_doc = max(
                doc_records.keys(),
                key=lambda d_name: max(
                    (getattr(rec, "payload", {}) or {}).get("uploaded_at", 0)
                    for rec in doc_records[d_name]
                ),
            )
            records = doc_records[target_doc]

    # Sort chunks in original document reading order
    records.sort(key=lambda r: (getattr(r, "payload", {}) or {}).get("chunk_index", 0))
    return records

