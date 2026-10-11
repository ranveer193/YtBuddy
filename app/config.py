import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
_b = lambda k, d: os.getenv(k, str(d)).lower() in ("1", "true", "yes")


class Settings:
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    llm_provider = os.getenv("LLM_PROVIDER", "groq")
    groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    groq_api_key = os.getenv("GROQ_API_KEY", "")
    _keys_raw = os.getenv("GROQ_API_KEYS", "")
    groq_api_keys = [k.strip() for k in ([os.getenv("GROQ_API_KEY", "")] + _keys_raw.split(",")) if k.strip()]
    hf_token = os.getenv("HUGGINGFACEHUB_API_TOKEN", "")
    hf_llm_repo = os.getenv("HF_LLM_REPO", "openai/gpt-oss-120b")
    frontend_origins = [o.strip() for o in os.getenv("FRONTEND_ORIGIN", "*").split(",") if o.strip()]
    reasoning_effort = os.getenv("REASONING_EFFORT", "low")
    structured_method = os.getenv("STRUCTURED_METHOD", "function_calling")
    embed_model = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    # semantic chunking
    unit_words = int(os.getenv("UNIT_WORDS", 45))
    sim_window = int(os.getenv("SIM_WINDOW", 3))
    breakpoint_percentile = float(os.getenv("BREAKPOINT_PERCENTILE", 88))
    chunk_min_tokens = int(os.getenv("CHUNK_MIN_TOKENS", 300))
    chunk_max_tokens = int(os.getenv("CHUNK_MAX_TOKENS", 1500))
    # map / reduce
    short_tokens = int(os.getenv("SHORT_VIDEO_TOKENS", 8000))
    context_parts = int(os.getenv("CONTEXT_PARTS", 3))
    concurrency = int(os.getenv("LLM_CONCURRENCY", 4))
    reduce_budget = int(os.getenv("REDUCE_TOKEN_BUDGET", 3500))
    # chat
    memory_limit = int(os.getenv("MEMORY_TOKEN_LIMIT", 1500))
    memory_keep = int(os.getenv("MEMORY_KEEP_LAST", 4))
    llm_compression = _b("LLM_COMPRESSION", True)
    n_queries = int(os.getenv("MULTI_QUERY_COUNT", 3))
    per_query_k = int(os.getenv("PER_QUERY_K", 8))

    @property
    def notes_dir(self):
        p = self.data_dir / "notes"; p.mkdir(parents=True, exist_ok=True); return p

    @property
    def chroma_dir(self):
        p = self.data_dir / "chroma"; p.mkdir(parents=True, exist_ok=True); return str(p)


settings = Settings()
