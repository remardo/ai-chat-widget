"""Main FastAPI application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import os

from .config import settings, validate_settings
from .services.storage.json_storage import JSONStorage
from .services.storage.sqlite_storage import SQLiteStorage
from .services.storage.postgres_storage import PostgresStorage
from .services.knowledge import KnowledgeBase
from .api import chat

# Validate configuration
validate_settings()

# Initialize storage based on config
if settings.STORAGE_TYPE == "json":
    storage = JSONStorage(settings.DATA_PATH)
elif settings.STORAGE_TYPE == "sqlite":
    db_path = os.path.join(settings.DATA_PATH, "chatbot.db")
    storage = SQLiteStorage(db_path)
elif settings.STORAGE_TYPE == "postgres":
    storage = PostgresStorage(settings.DATABASE_URL)
else:
    raise ValueError(f"Invalid STORAGE_TYPE: {settings.STORAGE_TYPE}")

# Load knowledge base
knowledge_base = KnowledgeBase(
    settings.KNOWLEDGE_PATH,
    include_live_supabase_knowledge=bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY),
    enable_rag=settings.ENABLE_RAG,
    top_k=settings.RAG_TOP_K,
    chunk_size=settings.RAG_CHUNK_SIZE,
    chunk_overlap=settings.RAG_CHUNK_OVERLAP,
    embedding_model=settings.RAG_EMBEDDING_MODEL,
    use_zvec=settings.RAG_USE_ZVEC,
    fallback_max_chars=settings.RAG_FALLBACK_MAX_CHARS,
)

# Create FastAPI app
app = FastAPI(
    title="AI Chat Widget",
    description="Universal AI chatbot for any website",
    version="1.0.0",
    debug=settings.DEBUG,
)

# CORS middleware
origins = settings.CORS_ORIGINS.split(",") if settings.CORS_ORIGINS != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chat.router)


# Serve widget files
_app_dir = Path(__file__).resolve().parent  # backend/app locally, /app/app in Docker
_widget_candidates = [
    _app_dir.parent / "widget",       # /app/widget in Docker
    _app_dir.parent.parent / "widget" # <repo>/widget locally
]
for candidate in _widget_candidates:
    if candidate.exists():
        app.mount("/widget", StaticFiles(directory=str(candidate)), name="widget")
        break


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "AI Chat Widget",
        "version": "1.0.0",
        "status": "running",
        "storage": settings.STORAGE_TYPE,
        "ai_model": settings.AI_MODEL,
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "storage": settings.STORAGE_TYPE}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.PORT)
