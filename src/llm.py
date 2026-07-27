"""Model client: on-disk cache + cost meter. Every call is logged."""
import os, json, time, hashlib, pathlib
from dotenv import load_dotenv

load_dotenv()

CACHE = pathlib.Path(os.environ.get("CACHE_DIR", "cache")); CACHE.mkdir(exist_ok=True)
LOG = pathlib.Path(os.environ.get("CALLS_LOG", "data/calls.jsonl")); LOG.parent.mkdir(exist_ok=True)

MODEL = os.environ.get("MODEL", "")
PRICE_IN = float(os.environ.get("PRICE_IN_PER_MTOK", 0))
PRICE_OUT = float(os.environ.get("PRICE_OUT_PER_MTOK", 0))
CAP = float(os.environ.get("BUDGET_USD_CAP", 100))


class BudgetExceeded(RuntimeError):
    pass


def spent() -> float:
    if not LOG.exists():
        return 0.0
    return sum(json.loads(l)["usd"] for l in LOG.read_text().splitlines() if l)


def complete(prompt: str, model: str = None, max_tokens: int = 1024) -> str:
    model = model or MODEL
    key = hashlib.sha256(f"{model}\x00{prompt}".encode()).hexdigest()
    hit = CACHE / f"{key}.json"
    if hit.exists():
        return json.loads(hit.read_text())["text"]

    if spent() >= CAP:
        raise BudgetExceeded(f"cap {CAP} USD reached")

    import anthropic
    t0 = time.time()
    r = anthropic.Anthropic().messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text = r.content[0].text
    tin, tout = r.usage.input_tokens, r.usage.output_tokens
    usd = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT

    hit.write_text(json.dumps({"text": text}))
    with LOG.open("a") as f:
        f.write(json.dumps({
            "model": model, "in": tin, "out": tout,
            "usd": round(usd, 6), "sec": round(time.time() - t0, 2),
        }) + "\n")
    return text


if __name__ == "__main__":
    print(complete("Reply with the single word: ok"))
    print(f"spent so far: {spent():.4f} USD")
