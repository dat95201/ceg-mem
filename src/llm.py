"""Model client: on-disk cache + cost meter. Every call is logged.

The cache key covers a caller-supplied `nonce`, not just the prompt. Without
one, two calls with byte-identical prompts replay each other - which quietly
destroys the experiment, because mode="no_memory" builds the *same* prompt
every round of every seed (src.proposer never puts history in a no-memory
prompt). The nonce is deterministic - src.loop derives it from (task, seed,
round) - so re-running a cell still replays from cache for free, while rounds
that are meant to be independent draws really are.

Sampling temperature is passed explicitly instead of left to the SDK default,
and recorded in the cache key and in data/calls.jsonl, so the distribution
every reported number was drawn from is on the record.
"""
import os, json, time, hashlib, pathlib
from dotenv import load_dotenv

load_dotenv()

CACHE = pathlib.Path(os.environ.get("CACHE_DIR", "cache")); CACHE.mkdir(parents=True, exist_ok=True)
LOG = pathlib.Path(os.environ.get("CALLS_LOG", "data/calls.jsonl")); LOG.parent.mkdir(parents=True, exist_ok=True)

MODEL = os.environ.get("MODEL", "")
PRICE_IN = float(os.environ.get("PRICE_IN_PER_MTOK", 0))
PRICE_OUT = float(os.environ.get("PRICE_OUT_PER_MTOK", 0))
CAP = float(os.environ.get("BUDGET_USD_CAP", 100))
TEMPERATURE = float(os.environ.get("TEMPERATURE", 1.0))


class BudgetExceeded(RuntimeError):
    pass


def spent() -> float:
    if not LOG.exists():
        return 0.0
    return sum(json.loads(l)["usd"] for l in LOG.read_text().splitlines() if l)


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

    import anthropic
    t0 = time.time()
    r = anthropic.Anthropic().messages.create(
        model=model, max_tokens=max_tokens, temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    # Join the text blocks rather than indexing content[0]: a response with a
    # non-text leading block would otherwise IndexError out of a multi-hour sweep.
    text = "".join(b.text for b in r.content if getattr(b, "type", None) == "text")
    tin, tout = r.usage.input_tokens, r.usage.output_tokens
    usd = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT

    hit.write_text(json.dumps({
        "text": text, "model": model, "temperature": temperature, "nonce": nonce,
    }))
    with LOG.open("a") as f:
        f.write(json.dumps({
            "model": model, "temperature": temperature, "nonce": nonce,
            "cache_key": key, "in": tin, "out": tout,
            "usd": round(usd, 6), "sec": round(time.time() - t0, 2),
        }) + "\n")
    return text


if __name__ == "__main__":
    print(complete("Reply with the single word: ok", nonce="smoke-test"))
    print(f"spent so far: {spent():.4f} USD")
