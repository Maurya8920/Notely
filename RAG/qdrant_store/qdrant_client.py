import os
import logging
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

load_dotenv()

logger = logging.getLogger(__name__)

COLLECTION_NAME = "documents"
_client: QdrantClient | None = None
_collection_initialized = False


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        timeout = int(os.getenv("QDRANT_TIMEOUT", "120"))
        _client = QdrantClient(
            url=os.getenv("QDRANT_ENDPOINT"),
            api_key=os.getenv("QDRANT_API_KEY"),
            cloud_inference=True,
            timeout=timeout,
        )
    return _client


def ensure_collection(client: QdrantClient):
    """Create collection and payload indexes if not already done in this process."""
    global _collection_initialized
    if _collection_initialized:
        return

    created = False
    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=384, distance=Distance.COSINE),
        )
        created = True

    # Create payload indexes — Qdrant requires an explicit index before you
    # can use a field in query_filter, otherwise it throws a 400 Bad Request.
    # Only create them when the collection was just created, or if the indexes
    # don't exist yet.  `create_payload_index` is idempotent (no-op if the
    # index already exists), so we can safely call it on first startup.
    if created or not _collection_initialized:
        for field in ("user_id", "course_id", "document"):
            try:
                client.create_payload_index(
                    collection_name=COLLECTION_NAME,
                    field_name=field,
                    field_schema="keyword",
                )
            except Exception as exc:
                # Index might already exist — log and continue
                logger.debug("Index creation for '%s' skipped: %s", field, exc)

    _collection_initialized = True
