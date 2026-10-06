# Notely 

An AI-powered study platform that turns your notes, PDFs, and slides into flashcards, summaries, and question-answerable knowledge — grounded entirely in your own material, not general LLM knowledge.

Upload a document, ask questions about it, generate flashcards on demand, or just chat — all scoped to a course/workspace with full multi-tenant isolation.

---

## ✨ Features

- **Multi-format document ingestion** — PDF, PPTX, DOCX with text, table, and image OCR extraction
- **RAG-grounded Q&A** — answers are generated only from retrieved course material, with source citations, so you always know what's from your notes vs. general knowledge
- **AI-generated flashcards** — on-demand, topic-scoped flashcard decks with a flip-card review UI
- **Plain chat mode** — for general questions that don't need document retrieval
- **Course-based organization** — every conversation and upload lives inside a course workspace, fully isolated per user
- **Collaboration & sharing** — invite collaborators by email, or generate a public read-only link
- **Light / dark / system theme** — accessible, low-contrast dark mode (no pure black/white)
- **Background processing** — uploads are parsed and embedded asynchronously so the UI never blocks

---

## 🏗️ Architecture

```
┌──────────────┐      ┌───────────────┐      ┌────────────────────┐
│   Next.js     │────▶│   API Routes   │────▶│   FastAPI (Python)  │
│  (Frontend)   │◀────│  (Node/Edge)   │◀────│   RAG Pipeline      │
└──────────────┘      └───────┬───────┘      └─────────┬──────────┘
                               │                          │
                        ┌──────▼──────┐          ┌────────▼────────┐
                        │  MongoDB     │          │     Qdrant       │
                        │ (users,      │          │  (vector store,  │
                        │  courses,    │          │   multi-tenant   │
                        │  chat        │          │   filtering)     │
                        │  history)    │          └──────────────────┘
                        └─────────────┘
```

**Ingestion pipeline (per uploaded document):**

```
Upload → Docling + RapidOCR (parse: text, tables, scanned pages)
       → LangChain text splitter (markdown-aware chunking)
       → Qdrant Cloud (server-side embedding + storage,
                        tagged with user_id / course_id)
```

**Generation pipeline (per query):**

```
Query → Qdrant retrieval (filtered by user_id + course_id)
      → LangChain + Gemini (grounded generation, Pydantic-validated output)
      → Structured response (answer + sources, or flashcard array)
```

---

## 🧰 Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui |
| Auth | Auth0 |
| Backend (app) | Next.js API Routes, MongoDB (native driver) |
| Backend (RAG) | FastAPI, LangChain, Google Gemini |
| Document parsing | Docling, RapidOCR (OCR) |
| Vector database | Qdrant Cloud (with payload-indexed multi-tenant filtering) |
| Async processing | BullMQ + Redis (Upstash) |
| Package management | `uv` (Python), `npm` (Node) |

---

## 📁 Project Structure

```
notely/
├── src/                        # Next.js frontend + API routes
│   ├── app/
│   │   ├── dashboard/
│   │   │   ├── page.tsx        # New conversation entry point
│   │   │   └── [courseId]/     # Course-scoped chat page
│   │   └── api/
│   │       ├── chat/           # Routes messages to FastAPI, saves history
│   │       └── courses/        # Course CRUD
│   ├── components/
│   │   ├── Composer.tsx        # Chat input, mode selector, file upload
│   │   ├── ChatHistory.tsx     # Message rendering, flashcard deck
│   │   ├── MarkdownContent.tsx # Custom lightweight markdown renderer
│   │   └── CourseSidebar.tsx   # Navigation, search, project management
│   └── lib/
│       ├── mongodb.ts
│       ├── auth0.ts
│       └── Types.ts
│
└── rag_pipeline/                # FastAPI RAG service
    ├── main.py
    ├── parser.py                # Docling + RapidOCR document parsing
    ├── chunker.py                # Markdown-aware text splitting
    ├── embedder.py                # Qdrant embedding + retrieval
    ├── generator.py                # LangChain + Gemini generation chains
    ├── qdrant_client_setup.py
    └── routes/
        ├── upload.py
        └── generation.py
```

---

## 🚀 Getting Started

### Prerequisites

- Node.js 18+, `npm`
- Python 3.11+, [`uv`](https://github.com/astral-sh/uv)
- MongoDB Atlas account
- Qdrant Cloud account
- Auth0 application
- Google Gemini API key
- Upstash Redis database (for background job processing)
- Tesseract OCR (optional fallback) — [install guide](https://github.com/UB-Mannheim/tesseract/wiki)

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd notely

# Frontend dependencies
npm install

# RAG pipeline dependencies (requires Python 3.12+ and uv)
cd RAG
uv pip install -r requirements.txt
cd ..
```

### 2. Environment variables

**Root `.env.local`** (copy from `.env.example`):
```bash
MONGODB_URI=mongodb+srv://<user>:<password>@cluster0.xxxxx.mongodb.net/notely?appName=Cluster0
AUTH0_SECRET=<run: node -e "console.log(require('crypto').randomBytes(32).toString('hex'))">
AUTH0_DOMAIN=<your-tenant>.us.auth0.com
AUTH0_CLIENT_ID=
AUTH0_CLIENT_SECRET=
FASTAPI_URL=http://127.0.0.1:8000
UPSTASH_REDIS_URL=rediss://default:<password>@<host>.upstash.io:6379
APP_BASE_URL=http://localhost:3000
```

**`RAG/.env`** (copy from `RAG/.env.example`):
```bash
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash-lite
QDRANT_ENDPOINT=https://<id>.<region>.gcp.cloud.qdrant.io
QDRANT_API_KEY=
```

### 3. Auth0 application settings

In Auth0 → Applications → Settings, set:
- **Allowed Callback URLs:** `http://localhost:3000/auth/callback`
- **Allowed Logout URLs:** `http://localhost:3000`

### 4. Run the services

```bash
# Terminal 1 — Next.js frontend (http://localhost:3000)
npm run dev

# Terminal 2 — FastAPI RAG service (http://127.0.0.1:8000)
cd RAG
.venv\Scripts\uvicorn main:app --reload --reload-exclude ".venv" --port 8000
```

Visit `http://localhost:3000/dashboard`.

---

## 🔑 Key Design Decisions

- **Course-scoped conversations, not chat threads.** Every message — chat, ask, or flashcard-generation — belongs to a course. This mirrors how the app's data (Qdrant filtering, uploads) is already organized, avoiding a parallel, disconnected conversation model.
- **Multi-tenant isolation via payload filtering, not separate collections.** One Qdrant collection, indexed and filtered by `user_id` and `course_id`, rather than per-user collections — scales cleanly and supports future cross-user features.
- **Grounded vs. general responses are explicitly distinguished.** Answers generated from retrieved notes are cited with source numbers; when no relevant material is found, the system says so rather than silently falling back to potentially incorrect general knowledge.
- **RapidOCR over EasyOCR for full-page OCR.** Chosen after directly comparing performance on CPU-only hardware — EasyOCR's PyTorch-based inference was significantly slower without a GPU.
- **Files are processed, not persisted, by default.** Uploaded documents are parsed in memory and discarded after chunking/embedding, avoiding unnecessary cloud storage costs and ephemeral-disk deployment issues.

---

## 🗺️ Roadmap

- [ ] Spaced repetition scheduling for flashcard review
- [ ] Semantic deduplication for shared/group notes
- [ ] Source traceability (link generated flashcards back to originating page/chunk)
- [ ] "Ask your notes" semantic search bar across all uploads
- [ ] Public share links and collaborator invites (in progress)

---

## 📄 License

This project was built as a personal/academic project. License terms TBD.
