# NoteLens 🎙️ ➔ 📝 ➔ 💬

> Transform YouTube technical videos and lectures into high-fidelity, structured study guides and interact with them using conversational AI.

---

## 🏛️ Architecture & Technologies (Abstract Overview)

NoteLens is structured as a decoupled full-stack platform:

* **Backend Engine (FastAPI & Python)**:
  * **LangGraph Orchestrator**: Manages stateful pipelines for both ingestion and multi-turn conversational RAG.
  * **ChromaDB Vector Store**: Embedded vector database storing semantic chunk embeddings with temporal (`[start_sec, end_sec]`) metadata.
  * **Embedding Model**: Fast local embedding generation via `BAAI/bge-small-en-v1.5` (sentence-transformers).
  * **LLM Engine & Redundancy**: Primary generation powered by `openai/gpt-oss-120b` via Groq, featuring **automatic key rotation** and **seamless failover** between Groq and HuggingFace to prevent downtime or rate limits.

* **Frontend UI (React & TypeScript)**:
  * **Vite & React 18**: High-performance Single Page Application (SPA).
  * **Tailwind CSS v4**: Modern, responsive styling with dedicated mobile navigation drawer and desktop views.
  * **Server-Sent Events (SSE)**: Live streaming pipeline visualization showing stage-by-stage progress in real time.

---

## ✨ Features

* ⚡ **Lazy-Loaded Search & Instant URL Preview**:
  * Query YouTube with user-controlled result batches (6, 12, 18, 24, 30).
  * Instant URL metadata resolution and lazy thumbnail loading.
  * In-memory TTL caching for instant (<1ms) repeated lookups.

* 📑 **Semantic & Chapter-Aware Chunking**:
  * Utilizes creator video chapters as anchor boundaries where available.
  * Automatically calculates embedding distance peaks for natural topic shifts.
  * Merges short segments to ensure high-density, context-rich notes.

* 🔄 **Parallel Ingestion with Multi-Job Switcher**:
  * Process up to **2 videos concurrently** in the background.
  * Real-time progress bars and stage logs for each job.
  * One-click job switcher tabs in the build view to toggle between active jobs without interruption.
  * Dedicated cancellation controls (✕) to safely stop unwanted builds.

* 📚 **Publication-Ready Study Guides**:
  * Executive overview and actionable key takeaways.
  * Granular sections with exact clickable YouTube timestamp links (`▶ mm:ss`).
  * Faithfully preserved and syntax-highlighted code snippets, shell commands, and configs.
  * One-click "Save PDF" print support.

* 🛡️ **Resilient LLM Failover**:
  * **Multi-key rotation**: Distribute requests across backup Groq API keys automatically.
  * **Cross-provider failover**: If Groq encounters rate limits or service interruptions, requests automatically fail over to HuggingFace (and vice-versa).
  * **TPM token budget guards**: Outline-driven reduce phase prevents 413 token overflow.

* 💬 **Conversational RAG Chat**:
  * Scope questions to a single video or your entire video library.
  * Multi-query expansion and timestamp citation (`[#1 mm:ss]`).

---

## 🚀 Deployment Guide

### 1. Deploy Backend on Render

1. Fork or push this repository to GitHub.
2. Sign in to [Render](https://render.com) and click **New → Blueprint** (or create a **Web Service** manually).
3. Connect your repository. Render will automatically read [`render.yaml`](render.yaml).
4. Configure the environment variables in your Render Dashboard:
   * `GROQ_API_KEY`: Your primary Groq API key (`gsk_...`).
   * `GROQ_API_KEYS` *(optional)*: Comma-separated backup keys (`gsk_1,gsk_2`).
   * `HUGGINGFACEHUB_API_TOKEN` *(optional)*: For HuggingFace failover.
   * `FRONTEND_ORIGIN`: Your Vercel frontend URL (or `*`).
5. Click **Deploy**. Note your backend URL (e.g., `https://notelens-backend.onrender.com`).

---

### 2. Deploy Frontend on Vercel

1. Sign in to [Vercel](https://vercel.com) and click **Add New → Project**.
2. Select your repository.
3. In the project setup:
   * **Root Directory**: Select `frontend`
   * **Framework Preset**: `Vite`
4. In **Environment Variables**, add:
   * `VITE_API_BASE_URL`: The URL of your deployed Render backend:
     ```
     https://notelens-backend.onrender.com
     ```
5. Click **Deploy**. Vercel will build and host the React SPA with automatic routing managed by [`vercel.json`](frontend/vercel.json).

---

## 💻 Local Development

### Backend Setup:
```bash
# 1. Create and activate virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and insert your GROQ_API_KEY

# 4. Start backend
python main.py
# Server runs on http://localhost:8000
```

### Frontend Setup:
```bash
cd frontend
npm install
npm run dev
# Frontend runs on http://localhost:5173
```
