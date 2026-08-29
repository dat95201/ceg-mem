"""Model client: on-disk cache + cost meter. Every call is logged.

The wire format is OpenAI's chat-completions, which is what both providers this
project uses speak: api.openai.com for the reported runs, and a local Ollama at
http://localhost:11434/v1 for smoke-testing the pipeline without spending. They
differ only in LLM_BASE_URL, LLM_API_KEY and MODEL - there is one code path, not
a provider switch, so a smoke run exercises the same code the real run does.

The one exception to "one code path" is the o-series. A reasoning model is not a
drop-in over this endpoint: it rejects `max_tokens` in favour of
`max_completion_tokens`, rejects any `temperature` but its own default, and
spends hidden reasoning tokens that are billed as output. `_is_reasoning` below
routes those three differences and nothing else. REASONING_EFFORT then becomes
part of the protocol in the same way MODEL is - it changes the proposal
distribution, so it is in the cache key, in the metrics row and in the
experiment cell key, or two efforts are two instruments sharing one name.

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

# o-series only. Empty for every chat model, and that emptiness is load-bearing:
# cache_key() appends this field only when it is set, so switching the code in
# does not re-key the responses already on disk for qwen2.5-coder / gpt-4o-mini.
REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "")

# Model ids that take `max_completion_tokens` and refuse `temperature`. Matched
# on the leading token of the id so o4-mini, o4-mini-2025-04-16 and o3-mini all
# route the same way, while gpt-4o-mini - whose id starts with "gpt" - does not.
_REASONING_PREFIXES = ("o1", "o3", "o4", "o5")


def _is_reasoning(model: str) -> bool:
    return model.split("-", 1)[0] in _REASONING_PREFIXES

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
    running driver is invisible here - DESIGN.md already requires the billable
    steps be run one at a time, for the same reason.
    """
    global _spent
    if _spent is None:
        if not LOG.exists():
            _spent = 0.0
        else:
            _spent = sum(json.loads(l)["usd"] for l in LOG.read_text().splitlines() if l)
    return _spent


# ── recovering token counts from an old cache entry ─────────────────────────
# Every blob written before the ledger join existed holds {text, model,
# temperature, nonce} and no counts - 18.5k of them here, of which the ones
# carrying the loop's own episode nonce will be REPLAYED by the grid. Left
# alone they hole #13/#16/#6, and they hole them differentially: the
# unconditioned arms share E1's draws and are the cached ones, while the
# the steered typed arm's prompts are all new and complete. A comparison of token cost
# between a holed arm and a complete one is not a comparison.
#
# The blob does not store the prompt - the cache key is its hash - so this
# cannot be done as an offline sweep over cache/. It has to happen HERE, on the
# hit, which is the one moment the prompt is in hand again. The recovered counts
# are written back so the next hit is free, and every consumer is told HOW they
# were obtained: `usage` means the server said so, anything else is arithmetic.
_TOKENIZER = None
_TOKENIZER_TRIED = False


def _tokenizer(model: str):
    """The model's own tokenizer if transformers can supply it, else None.

    Optional on purpose: an exact count is better, but requiring a HF download
    to run the grid would be a new failure mode on a Colab runtime, and the
    heuristic below preserves the SHAPE of #16 (flat vs linear in the round
    index) even where it misses the level.
    """
    global _TOKENIZER, _TOKENIZER_TRIED
    if _TOKENIZER_TRIED:
        return _TOKENIZER
    _TOKENIZER_TRIED = True
    repo = os.environ.get("TOKENIZER_ID", "")
    if not repo:
        return None
    try:
        from transformers import AutoTokenizer
        _TOKENIZER = AutoTokenizer.from_pretrained(repo)
    except Exception as exc:
        print(f"    TOKENIZER_ID={repo!r} unavailable ({type(exc).__name__}); "
              f"falling back to the character heuristic", flush=True)
        _TOKENIZER = None
    return _TOKENIZER


def _recover_counts(blob: dict, prompt: str, path) -> dict:
    """{'in', 'out', 'method'} for a cache hit, filling the blob if it was old."""
    if blob.get("in") is not None and blob.get("out") is not None:
        return {"in": blob["in"], "out": blob["out"],
                "method": blob.get("tokens_method", "usage")}
    tok = _tokenizer(blob.get("model") or "")
    if tok is not None:
        n_in = len(tok.encode(prompt))
        n_out = len(tok.encode(blob.get("text") or ""))
        method = "tokenizer"
    else:
        n_in = math.ceil(len(prompt) / _CHARS_PER_TOKEN)
        n_out = math.ceil(len(blob.get("text") or "") / _CHARS_PER_TOKEN)
        method = "heuristic"
    blob = {**blob, "in": n_in, "out": n_out, "tokens_method": method}
    try:
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(blob))
        os.replace(tmp, path)
    except OSError:
        pass   # a read-only or full cache is not a reason to fail the call
    return {"in": n_in, "out": n_out, "method": method}


def cache_key(prompt: str, model: str, temperature: float, max_tokens: int, nonce: str,
              reasoning_effort: str = "") -> str:
    """Identity of one model call. `nonce` is what separates two draws that
    happen to share a prompt - see this module's docstring.

    `reasoning_effort` is *appended* rather than always present, and that is
    deliberate rather than tidy. It is empty for every chat model, and an empty
    field still changes a hash - so writing it into the tuple unconditionally
    would have re-keyed all ~18k responses already cached under
    qwen2.5-coder:7b and made the pi screen re-buy every draw it has paid for.
    The two key spaces cannot collide, because a run that sets an effort is by
    construction on an o-series model that no chat-model key was ever built for.
    """
    parts = [model, repr(float(temperature)), str(max_tokens)]
    if reasoning_effort:
        parts.append(f"effort={reasoning_effort}")
    parts += [nonce, prompt]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def complete(
    prompt: str,
    model: str = None,
    max_tokens: int = 1024,
    *,
    nonce: str = "",
    temperature: float = None,
    reasoning_effort: str = None,
    meta: dict | None = None,
) -> str:
    """One model call, cached on disk.

    `meta`, when given, is filled in with what this call cost - cache_key,
    prompt/completion/reasoning tokens, seconds, finish_reason - so the caller
    can put them on its own record. This is the only way the per-round token
    and latency metrics can exist: data/calls.jsonl has the numbers but
    src.metrics.RoundRecord had no key to join on, and the draw nonce is not
    one (src.loop.proposal_nonce deliberately omits the mode, so the arms that
    build identical prompts share a draw - which means one nonce maps to
    several ledger rows the moment the arms diverge). cache_key is that key.

    A cache hit fills `meta` too, from the blob, so a replayed cell reports the
    same token counts a live one did. Blobs written before this field existed
    do not carry them; those come back as None and are recoverable from
    data/calls_*.jsonl by cache_key.
    """
    global _spent
    model = model or MODEL
    temperature = TEMPERATURE if temperature is None else temperature
    effort = REASONING_EFFORT if reasoning_effort is None else reasoning_effort
    if not model:
        raise ValueError("no model configured - set MODEL in .env or pass model=...")
    if effort and not _is_reasoning(model):
        raise ValueError(
            f"REASONING_EFFORT={effort!r} was set but {model!r} is not an o-series "
            f"model. The effort would go into the cache key and the metrics row "
            f"while changing nothing about the call, so every arm would look like "
            f"a distinct protocol for no reason. Unset it, or pass an o-series id."
        )

    key = cache_key(prompt, model, temperature, max_tokens, nonce, effort)
    if meta is not None:
        meta.update(cache_key=key, model=model, max_tokens=max_tokens,
                    reasoning_effort=effort or None)
    hit = CACHE / f"{key}.json"
    if hit.exists():
        blob = json.loads(hit.read_text())
        counts = _recover_counts(blob, prompt, hit)
        if meta is not None:
            meta.update(cached=True, prompt_tokens=counts["in"],
                        completion_tokens=counts["out"],
                        reasoning_tokens=blob.get("reasoning_out"),
                        llm_sec=blob.get("sec"), finish_reason=blob.get("finish_reason"),
                        tokens_method=counts["method"])
        return blob["text"]

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

    kwargs: dict = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if _is_reasoning(model):
        # Two renames, not a different call. `max_completion_tokens` has to cover
        # the hidden reasoning tokens *as well as* the visible answer, which is
        # why src.proposer._MAX_BUDGET is raised for this profile - reasoning
        # spent first leaves a truncated program, and a truncated program is
        # recorded as a harness failure on exactly the longest corpus tasks.
        kwargs["max_completion_tokens"] = max_tokens
        if effort:
            kwargs["reasoning_effort"] = effort
        # temperature is deliberately not sent: the o-series accepts only its
        # own default and errors on anything else, including an explicit 1.0 on
        # some snapshots. It stays in the cache key, where it records what the
        # protocol asked for.
    else:
        kwargs["max_tokens"] = max_tokens
        kwargs["temperature"] = temperature

    t0 = time.time()
    r = client().chat.completions.create(**kwargs)
    choice = r.choices[0]
    # `or ""`: content is None, not "", when the model emits no text at all.
    # src.proposer runs a regex over this and would raise TypeError instead of
    # the TruncatedResponse the loop knows how to log.
    text = choice.message.content or ""
    tin, tout = r.usage.prompt_tokens, r.usage.completion_tokens
    # Reasoning tokens are already inside completion_tokens, so `usd` is correct
    # without them; they are broken out only so the token metrics can separate
    # "what the model wrote" from "what the model thought". 0 on a chat model.
    details = getattr(r.usage, "completion_tokens_details", None)
    treason = getattr(details, "reasoning_tokens", 0) or 0
    usd = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
    sec = round(time.time() - t0, 2)

    # Written via a temp file in the same directory and renamed, because two
    # shards run in parallel against one CACHE_DIR will both miss the same key
    # and both write it. os.replace is atomic on POSIX, so a reader can only
    # ever see the whole blob or no file at all - never the half-written JSON a
    # plain write_text can expose.
    tmp = hit.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({
        "text": text, "model": model, "temperature": temperature, "nonce": nonce,
        # Recorded so a cache hit can report what the call cost without going
        # back to the ledger - see complete()'s docstring.
        "in": tin, "out": tout, "reasoning_out": treason,
        # "usage": the server reported these. Anything else in this field is a
        # reconstruction (see _recover_counts) and must not be pooled with them
        # without saying so.
        "tokens_method": "usage",
        "finish_reason": choice.finish_reason, "sec": sec,
    }))
    os.replace(tmp, hit)
    with LOG.open("a") as f:
        f.write(json.dumps({
            "model": model, "temperature": temperature, "nonce": nonce,
            "reasoning_effort": effort or None,
            "cache_key": key, "in": tin, "out": tout, "reasoning_out": treason,
            # finish_reason distinguishes "the model ran out of output budget"
            # from "the model answered in the wrong format": both reach
            # src.proposer as a missing closing fence and neither is visible in
            # the text alone.
            "finish_reason": choice.finish_reason,
            "usd": round(usd, 6), "sec": sec,
        }) + "\n")

    if meta is not None:
        meta.update(cached=False, prompt_tokens=tin, completion_tokens=tout,
                    reasoning_tokens=treason, llm_sec=sec,
                    finish_reason=choice.finish_reason, usd=round(usd, 6))

    _spent = spent() + usd
    return text


if __name__ == "__main__":
    print(complete("Reply with the single word: ok", nonce="smoke-test"))
    print(f"spent so far: {spent():.4f} USD")
