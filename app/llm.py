import json
import logging
from langchain_core.runnables import RunnableLambda
from .config import settings as s

logger = logging.getLogger(__name__)


def _extract_failed_generation(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", {})
        if isinstance(err, dict):
            failed = err.get("failed_generation")
            if failed and isinstance(failed, str):
                return failed
    return None


def _create_groq_llm(key: str | None = None, temperature: float = 0.2):
    from langchain_groq import ChatGroq
    kwargs = {
        "model": s.groq_model,
        "temperature": temperature,
        "max_retries": 2,
        "reasoning_effort": s.reasoning_effort,
    }
    if key:
        kwargs["api_key"] = key
    return ChatGroq(**kwargs)


def _create_hf_llm(temperature: float = 0.2):
    from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint
    kwargs = {
        "repo_id": s.hf_llm_repo,
        "task": "text-generation",
        "temperature": max(temperature, 0.01),
        "max_new_tokens": 2048,
    }
    if s.hf_token:
        kwargs["huggingfacehub_api_token"] = s.hf_token
    return ChatHuggingFace(llm=HuggingFaceEndpoint(**kwargs))


def get_candidate_llms(temperature: float = 0.2):
    """Returns candidate LLM instances in failover priority order."""
    groq_keys = list(dict.fromkeys(s.groq_api_keys or ([s.groq_api_key] if s.groq_api_key else [""])))
    groq_candidates = []
    for k in groq_keys:
        try:
            groq_candidates.append(("groq", _create_groq_llm(key=k if k else None, temperature=temperature)))
        except Exception as e:
            logger.warning(f"Could not initialize Groq LLM: {e}")

    hf_candidate = None
    if s.hf_token or s.llm_provider == "hf":
        try:
            hf_candidate = ("hf", _create_hf_llm(temperature=temperature))
        except Exception as e:
            logger.warning(f"Could not initialize HF LLM: {e}")

    candidates = []
    if s.llm_provider == "hf":
        if hf_candidate: candidates.append(hf_candidate)
        candidates.extend(groq_candidates)
    else:
        candidates.extend(groq_candidates)
        if hf_candidate: candidates.append(hf_candidate)

    if not candidates:
        candidates.append(("groq", _create_groq_llm(temperature=temperature)))
    return candidates


def get_llm(temperature: float = 0.2):
    """Primary LLM."""
    candidates = get_candidate_llms(temperature)
    return candidates[0][1]


def structured(schema, temperature: float = 0.2):
    """Returns a RunnableLambda that automatically fails over between alternate API keys
    and providers (Groq <-> HuggingFace) on error / rate limit."""
    def _invoke_with_failover(inputs, config=None):
        candidates = get_candidate_llms(temperature)
        last_exc = None

        for idx, (provider_name, llm_inst) in enumerate(candidates):
            try:
                bound = llm_inst.with_structured_output(schema, method=s.structured_method)
                return bound.invoke(inputs, config=config)
            except Exception as e:
                last_exc = e
                failed_gen = _extract_failed_generation(e)
                if failed_gen:
                    # 1. Check if the failed generation itself is valid JSON matching schema
                    try:
                        cleaned = failed_gen.strip()
                        if cleaned.startswith("```json"):
                            cleaned = cleaned[7:].rstrip("`").strip()
                        elif cleaned.startswith("```"):
                            cleaned = cleaned[3:].rstrip("`").strip()
                        data = json.loads(cleaned)
                        if isinstance(data, dict):
                            return schema.model_validate(data)
                    except Exception:
                        pass

                    # 2. Rescue by asking model to format failed_gen into schema
                    try:
                        bound = llm_inst.with_structured_output(schema, method=s.structured_method)
                        recovery_prompt = (
                            f"Format the following content into the required {schema.__name__} structured fields:\n\n{failed_gen}"
                        )
                        return bound.invoke(recovery_prompt, config=config)
                    except Exception:
                        pass

                # If another candidate exists, log and failover
                if idx < len(candidates) - 1:
                    next_provider = candidates[idx + 1][0]
                    print(f"⚠️ [Failover] Candidate {idx + 1} ({provider_name}) error: {type(e).__name__}. Failing over to candidate {idx + 2} ({next_provider})...")
                    continue

        if last_exc:
            raise last_exc

    return RunnableLambda(_invoke_with_failover)
