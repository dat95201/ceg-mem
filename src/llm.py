"""Model client: on-disk cache + cost meter. Every call is logged.

The wire format is OpenAI's chat-completions, which is what both providers this
project uses speak: api.openai.com for the reported runs, and a local Ollama at
http://localhost:11434/v1 for smoke-testing the pipeline without spending. They
differ only in LLM_BASE_URL, LLM_API_KEY and MODEL - there is one code path, not
a provider switch, so a smoke run exercises the same code the real run does.

The cache key covers a caller-supplied `nonce`, not just the prompt. Without
one, two calls with byte-identical prompts replay each other - which quietly
destroys the experiment, because mode="no_memory" builds the *same* prompt
every round of every seed (src.proposer never puts history in a no-memory
prompt). The nonce is deterministic - src.loop derives it from (task, seed,
round) - so re-running a cell still replays from cache for free, while rounds
that are meant to be independent draws really are.

Sampling temperature is passed explicitly instead of left to the SDK default,
and recorded in the cache key and in data/calls.jsonl, so the distribution
every reported number was drawn from is on the record. The rest of the sampling
configuration is not sendable over this endpoint - Ollama's num_ctx, top_k and
repeat_penalty are server-side, and so reach neither the cache key nor the
metrics row. The local runs therefore take the model's own shipped defaults,
which is what makes `qwen2.5-coder:7b` mean the same thing to anyone who pulls
it; the one exception is the context window, which is not a default anybody can
look up. See LLM_CONTEXT_TOKENS below - it is the setting that can corrupt a run
silently.
"""
import os, json, time, math, hashlib, pathlib
from dotenv import load_dotenv

load_dotenv()

CACHE = pathlib.Path(os.environ.get("CACHE_DIR", "cache")); CACHE.mkdir(parents=True, exist_ok=True)
LOG = pathlib.Path(os.environ.get("CALLS_LOG", "data/calls.jsonl")); LOG.parent.mkdir(parents=True, exist_ok=True)

MODEL = os.environ.get("MODEL", "")
PRICE_IN = float(os.environ.get("PRICE_IN_PER_MTOK", 0))
PRICE_OUT = float(os.environ.get("PRICE_OUT_PER_MTOK", 0))
CAP = float(os.environ.get("BUDGET_USD_CAP", 100))
TEMPERATURE = float(os.environ.get("TEMPERATURE", 1.0))

# Empty base url means the SDK's own default, api.openai.com. Point it at
# http://localhost:11434/v1 for Ollama. The key is unused by Ollama but the SDK
# refuses to construct a client without one, hence the placeholder.
BASE_URL = os.environ.get("LLM_BASE_URL", "") or None
API_KEY = os.environ.get("LLM_API_KEY", "") or "unused"

# The SDK's default is 600s. A local 7B at ~16 tok/s needs ~1000s to fill the
# 16000-token ceiling src.proposer.budget_for_source hands out for the longest
# corpus programs, so the default would abort a legitimate call and record it as
# a harness failure.
TIMEOUT = float(os.environ.get("LLM_TIMEOUT_SEC", 1800))
MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", 5))

# Total context the server is actually serving, or 0 to disable the check.
#
# This exists because Ollama truncates rather than refuses. The window is a
# server-side default picked from available VRAM - "4k/32k/256k", per `ollama
# serve --help` - and the OpenAI-compatible endpoint has no field to raise it,
# so an over-long prompt silently loses its head and the call returns a
# plausible answer to a question that was never asked. Worse, the window reaches
# neither the cache key nor the report, so the same model id on a machine that
# happened to serve 4096 is a different instrument and nothing says so.
#
# That failure is worst exactly where it does the most damage: the typed and
# untyped arms carry accumulated evidence and so have the longest prompts, so
# the truncation is differential across arms and lands on the comparison the
# experiment exists to make.
#
# Pin the server with OLLAMA_CONTEXT_LENGTH and verify it at /api/ps -
# scripts/screen_shard.sh does both and refuses to start on a mismatch. This
# check is the client-side half: it catches a prompt that would not fit before
# the server can crop it.
CONTEXT_TOKENS = int(os.environ.get("LLM_CONTEXT_TOKENS", 0))

# Same figures src.proposer sizes max_tokens with - 3.5 chars/token measured on
# this corpus's Python, 15% margin for formatting. Duplicated rather than
# imported because src.proposer imports this module.
_CHARS_PER_TOKEN = 3.5
_BUDGET_MARGIN = 1.15


class BudgetExceeded(RuntimeError):
    pass


class ContextOverflow(RuntimeError):
    """Prompt + max_tokens would not fit the server's context window."""


_client = None


def client():
    """The one client, built on first use.

    Built once rather than per call: against a local server every construction
    is a fresh connection pool, and the SDK's retry budget is per-client.
    """
    global _client
    if _client is None:
        import openai
        _client = openai.OpenAI(
            base_url=BASE_URL, api_key=API_KEY,
            timeout=TIMEOUT, max_retries=MAX_RETRIES,
        )
    return _client


_spent = None


def spent() -> float:
    """USD charged so far, per data/calls.jsonl.

    Read from the file once, then kept in memory and advanced by complete().
    Re-reading on every call made this O(n^2) over a sweep that logs tens of
    thousands of them. The consequence is that spend by a *concurrently*
    running driver is invisible here - PLAN.md already requires the billable
    steps be run one at a time, for the same reason.
    """
    global _spent
    if _spent is None:
        if not LOG.exists():
            _spent = 0.0
        else:
            _spent = sum(json.loads(l)["usd"] for l in LOG.read_text().splitlines() if l)
    return _spent


def cache_key(prompt: str, model: str, temperature: float, max_tokens: int, nonce: str) -> str:
    """Identity of one model call. `nonce` is what separates two draws that
    happen to share a prompt - see this module's docstring."""
    parts = (model, repr(float(temperature)), str(max_tokens), nonce, prompt)
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def complete(
    prompt: str,
    model: str = None,
    max_tokens: int = 1024,
    *,
    nonce: str = "",
    temperature: float = None,
) -> str:
    global _spent
    model = model or MODEL
    temperature = TEMPERATURE if temperature is None else temperature
    if not model:
        raise ValueError("no model configured - set MODEL in .env or pass model=...")

    key = cache_key(prompt, model, temperature, max_tokens, nonce)
    hit = CACHE / f"{key}.json"
    if hit.exists():
        return json.loads(hit.read_text())["text"]

    if spent() >= CAP:
        raise BudgetExceeded(f"cap {CAP} USD reached")

    if CONTEXT_TOKENS:
        estimate = math.ceil(len(prompt) / _CHARS_PER_TOKEN * _BUDGET_MARGIN)
        if estimate + max_tokens > CONTEXT_TOKENS:
            raise ContextOverflow(
                f"~{estimate} prompt + {max_tokens} output tokens exceeds the "
                f"{CONTEXT_TOKENS}-token window (LLM_CONTEXT_TOKENS). Raise the "
                f"server's own window - OLLAMA_CONTEXT_LENGTH for Ollama - rather "
                f"than letting it truncate the prompt."
            )

    t0 = time.time()
    r = client().chat.completions.create(
        model=model, max_tokens=max_tokens, temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    choice = r.choices[0]
    # `or ""`: content is None, not "", when the model emits no text at all.
    # src.proposer runs a regex over this and would raise TypeError instead of
    # the TruncatedResponse the loop knows how to log.
    text = choice.message.content or ""
    tin, tout = r.usage.prompt_tokens, r.usage.completion_tokens
    usd = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT

    hit.write_text(json.dumps({
        "text": text, "model": model, "temperature": temperature, "nonce": nonce,
    }))
    with LOG.open("a") as f:
        f.write(json.dumps({
            "model": model, "temperature": temperature, "nonce": nonce,
            "cache_key": key, "in": tin, "out": tout,
            # finish_reason distinguishes "the model ran out of output budget"
            # from "the model answered in the wrong format": both reach
            # src.proposer as a missing closing fence and neither is visible in
            # the text alone.
            "finish_reason": choice.finish_reason,
            "usd": round(usd, 6), "sec": round(time.time() - t0, 2),
        }) + "\n")

    _spent = spent() + usd
    return text


if __name__ == "__main__":
    print(complete("Reply with the single word: ok", nonce="smoke-test"))
    print(f"spent so far: {spent():.4f} USD")
