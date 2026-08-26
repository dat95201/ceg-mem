# PLAN — Metrics bổ sung · Đổi proposer sang OpenAI · Baseline so sánh

*Viết 2026-08-24. Nguồn đối chiếu: `~/Downloads/cegmem_related_work_and_metrics.md` (52 related works + 30 metrics).*
*Trạng thái repo lúc viết: `bash scripts/pipeline.sh` → stage `screen` đang chạy dở. Chưa có `data/tasks.json`, chưa có `data/episodes.jsonl`. **Chưa tốn một round E1–E5 nào.***

Đọc file này là đủ để implement — mỗi mục ghi rõ file, hàm, field schema, công thức, và chi phí.

---

## ✅ TRẠNG THÁI THI CÔNG (cập nhật 2026-08-24)

**Giai đoạn A, B, D1–D3 đã implement xong.** Chi tiết dưới đây giữ nguyên làm tài liệu tham chiếu; phần nào đã làm được đánh dấu.

| | Việc | Trạng thái |
|---|---|---|
| A1 | `blocked_by_type` — redundancy một định nghĩa cho mọi arm | ✅ `src/memory.py`, `src/metrics.py`, `src/loop.py` |
| A2 | Join ledger ↔ RoundRecord qua `cache_key` + token/giây | ✅ `src/llm.py` (`meta=` out-param), 8 field mới trong `RoundRecord` |
| A3 | `oracle_sec` / `guard_sec` / `llm_sec` | ✅ `src/loop.py` |
| A4 | Mode thứ tư `transcript` (ChatRepair) + `--transcript-window` | ✅ 4 file `MODES`, `_transcript_block`, `TranscriptMemory` |
| A5 | `--audit-guarded` + vào cell key | ✅ `src/loop.py`, `scripts/run_eval.py` |
| A6 | Wilcoxon `n_effective` + `paired_rate_ratio` (§7 pre-registered) | ✅ `scripts/analyze.py` |
| A7 | Cache write atomic (`os.replace`) — mở đường shard song song | ✅ `src/llm.py` |
| B2 | `--backend ollama\|cloud` + `--model` + `--reasoning-effort` | ✅ `eval_shard.sh`, `screen_shard.sh` |
| — | o-series routing (`max_completion_tokens`, bỏ temperature, reasoning token) | ✅ `src/llm.py`, `src/proposer.py` |
| D1 | `measure_redundancy.py` — #2,3,4,5,7,8,11,18,33 + pass@k | ✅ mới |
| D2 | `measure_patch_quality.py` — #21, #23 | ✅ mới |
| D3 | `measure_typing_coherence.py` — **#25 `ĉ` đo thật** | ✅ mới |
| D4 | Metric mới vào `analyze.py` + cost-of-pass (#14) | ✅ |
| C | Preset `E5-c25` `E5-c00` `E6-transcript` `E8-audit` | ✅ `eval_shard.sh`, `freeze_results.py` |
| — | RUNBOOK, DESIGN, `.env.example` | ✅ |
| D5 | Regression rate F2P/P2P (#22) | ⬜ chưa |
| D6 | `c*` + slope từ 6 mức E5 | ⬜ chưa (dữ liệu sẽ có sau khi chạy sweep) |
| D7 | Figures cho curve mới | ⬜ chưa |
| E | Confidence gate + revival (#30), Reflexion arm | ⬜ chưa |

### Hai bảo chứng đã kiểm tra bằng thực nghiệm

1. **Cache 19 219 response còn nguyên giá trị.** `cache_key()` chỉ *append* `reasoning_effort` khi nó khác rỗng, nên hash của mọi call chat-model không đổi. Kiểm chứng: prompt của cả 3 mode cũ byte-identical với bản trước khi sửa (`sha256` khớp), và replay 40 draw của một program đã screen xong cho ra **0 model call, verdict trùng từng call** với `data/screen_001_010.json`.
2. **Đường screen local không đổi một chữ.** `--backend` mặc định `ollama`; dry-run `screen_shard.sh --from 141 --to 150 --calls 40` in ra đúng report/ledger/shard-list như cũ. Tên file chỉ đổi khi `--model` khác `qwen2.5-coder:7b`.

### Chuyển đổi bằng cờ, không sửa code

```bash
# local (mặc định — không đổi gì)
bash scripts/eval_shard.sh --exp E2

# gpt-4o-mini
LLM_API_KEY=sk-... PRICE_IN_PER_MTOK=0.15 PRICE_OUT_PER_MTOK=0.60 \
BUDGET_USD_CAP=25 CONTEXT_LENGTH=128000 \
bash scripts/eval_shard.sh --exp E2 --backend cloud --model gpt-4o-mini

# o4-mini
LLM_API_KEY=sk-... PRICE_IN_PER_MTOK=1.10 PRICE_OUT_PER_MTOK=4.40 \
BUDGET_USD_CAP=60 CONTEXT_LENGTH=200000 \
bash scripts/eval_shard.sh --exp E2 --backend cloud --model o4-mini --reasoning-effort medium

# baseline ChatRepair
bash scripts/eval_shard.sh --exp E6-transcript --transcript-window 5

# audit redundancy (subset)
bash scripts/eval_shard.sh --exp E8-audit
```

Cloud path **từ chối khởi động** nếu thiếu `LLM_API_KEY`, hai giá, `BUDGET_USD_CAP`, hoặc nếu `CONTEXT_LENGTH` vẫn là 32768 của local.

---

## 0. TL;DR — 3 câu trả lời

| Câu hỏi | Trả lời ngắn |
|---|---|
| **Bổ sung metric gì?** | 4 nhóm. **P0** (2 việc, sửa lỗi định nghĩa đang có) → **P1** (9 metrics, *miễn phí*, chỉ đọc lại log đã có) → **P2** (5 metrics, cần thêm field vào schema) → **P3** (3 metrics, cần cơ chế mới). Chi tiết §1. |
| **Đổi sang gpt-4o-mini / o4-mini tốn gì?** | `gpt-4o-mini`: **config đã đủ**, `src/llm.py` không phải sửa một dòng nào. Chỉ 2 shard script bị khoá cứng vào ollama (≈80 dòng). `o4-mini` (reasoning model): **phải sửa code** — `max_tokens`→`max_completion_tokens`, bỏ `temperature`, thêm `reasoning_effort` vào cache key + cell key. Chi tiết §2. **Khuyến nghị: đừng thay qwen — thêm gpt-4o-mini làm proposer thứ hai trên subset 30 task (~$3).** |
| **Baseline nào?** | **B1 = Transcript memory (ChatRepair)** — bắt buộc, ~1 ngày, rẻ. **B2 = Reflexion** — nên có, ~2 ngày, 2× model call. **B0 = Repeated sampling (Large Language Monkeys)** — *miễn phí*, rút thẳng từ E1 đã chạy. Chi tiết §3. |

**Thời điểm hiện tại là thời điểm rẻ nhất để quyết cả ba việc** — mọi thay đổi schema / model / arm sau khi grid chạy đều là chạy lại từ đầu.

---

# §1 — METRICS

## 1.0 Đang có gì

Đọc từ `src/metrics.py:summarize_episode` + `scripts/analyze.py` + các `scripts/measure_*.py`:

| Đang đo | Ở đâu | Map sang metric # trong doc |
|---|---|---|
| `oracle_calls_to_accept` | `src/metrics.py:106` | #9, #12 |
| `redundant_attempts` = `n_guarded + guard_miss` | `src/metrics.py:174` | #1 (proxy, **định nghĩa đang sai — xem P0-1**) |
| `success_at_b` (một điểm B=20) | `src/metrics.py:180` | #11 (chưa có đường cong) |
| `guard_evaluations` | `src/metrics.py` | #15 ✓ |
| `proposals` | `src/metrics.py:183` | #10 ✓ |
| false-accept | `--check-overfit` → `data/overfit_checks.jsonl` | #20 ✓ |
| anchoring rate + by_noise/by_conflation | `scripts/measure_anchoring.py` | #24, #27 ✓ |
| cross-refutation rate + waste rate | `scripts/measure_coherence.py` | #3 (một nửa), #26 (thực chất là ρ̂) |
| theory fit `r` | `scripts/fit_theory.py` | #31 ✓ |
| equivalent-mutant rate | `scripts/measure_pool_strength.py` | (không có trong doc, giữ) |

**Kết luận:** bộ 8-metric-tối-thiểu của doc (§B7) hiện đã có **5/8**. Thiếu: #2 (Duplicate-Patch Rate), #14 (Cost-of-pass), #25 (typing coherence `c` đo thật). Thiếu cả #11 ở dạng đường cong.

## 1.1 P0 — Hai việc phải sửa trước khi chạy grid

> Cả hai đều đã được `DESIGN.md §6 Open items` ghi nhận là lỗi đã biết. Nếu chạy grid trước khi sửa, số về redundancy — *chính là đóng góp lõi* — không so sánh được giữa các arm.

### P0-1. `redundant_attempts` hiện là **hai định nghĩa khác nhau** cho hai arm

**Vấn đề (nguyên văn DESIGN.md §6):** `summarize_episode` tính mọi round bị guard là redundant. Đúng với typed guard (nó tra bucket theo location). Sai với untyped guard: untyped không có type index, nó replay counterexample cũ, và một candidate có failure type **hoàn toàn mới** vẫn thường xuyên fail một input cũ. Nên cột này đo *type repeat* ở arm này và *guard firing* ở arm kia, trong khi Theorem 4.3(b) gán cho cả hai cùng một `R`.

**Fix — 2 phần, cả hai đều rẻ:**

**(a) Log `blocked_by_type`.** `_still_refutes` (`src/memory.py:34`) biết chính xác *attempt nào* chặn. Attempt đó luôn mang `coarse_type`/`fine_type` — vì `Attempt.from_result` gọi `theta_both` ở mọi mode, kể cả untyped (`src/proposer.py:Attempt.from_result`). Nên:

```python
# src/memory.py:27
@dataclasses.dataclass(frozen=True)
class GuardResult:
    blocked: bool
    evaluations: int
    blocked_by: "Attempt | None" = None   # NEW

# UntypedMemory.guard (:98) và TypedMemory.guard (:172):
    return GuardResult(blocked=True, evaluations=evaluations, blocked_by=attempt)
```

```python
# src/metrics.py RoundRecord — thêm field
blocked_by_type: str | None = None   # fine_type.key của attempt đã chặn round này
```

```python
# src/loop.py, nhánh `if guarded:` — điền vào _record(...)
blocked_by_type=(guard_result.blocked_by.failure_type(granularity).key
                 if guard_result.blocked_by else None),
```

→ Giờ có **một** định nghĩa dùng chung: *"round bị chặn vì tái tạo một counterexample đã biết, nhãn = type của counterexample đó"*.

**(b) Shadow oracle trên guarded round (`--audit-guarded`).** Vẫn còn một lỗ: guarded round không chạy oracle nên không biết `θ` **của chính nó**, trong khi non-guarded round thì biết. So sánh redundancy giữa arm-có-guard và arm-không-guard vì thế bị *censoring bias*.

Sửa: thêm flag chạy oracle trên guarded round **chỉ để ghi**, không cho ảnh hưởng loop, không ghi vào memory:

```python
# src/loop.py, nhánh guarded, trước `continue`:
if audit_guarded:
    audit = differential_test(task, patch, program.correct_source,
                              max_examples=max_examples, seed=seed + round_index)
    audit_attempt = Attempt.from_result(task_name, program.buggy_source, patch, audit)
    # ghi vào coarse_type/fine_type như bình thường; KHÔNG gọi memory.store()
```

Thêm `audit_guarded: bool` vào `RoundRecord` **và vào cell key** (`scripts/run_eval.py:92 cell_key`, `src/loop.py:cell_signature`) — vì nó đổi *cái mà round đó là*.

**Chi phí: $0** (không thêm model call — patch đã sinh rồi, chỉ tốn sandbox). Wall clock: đúng bằng số guarded round × thời gian oracle. Đây là cái duy nhất cho phép nói *"typed guard chặn N round, và ta đã kiểm chứng M/N trong số đó thật sự là type lặp"*.

> Chạy `--audit-guarded` như một **cell riêng** trên subset 30 task, không phải trên toàn grid — nó phá đúng cái lợi ích (tiết kiệm oracle) mà E2 đang đo.

### P0-2. `wilcoxon(zero_method='wilcox')` báo `n` sai

`scripts/analyze.py:wilcoxon_paired` gọi scipy với default `zero_method='wilcox'` → **loại bỏ cặp tied**, nhưng `n` trả về là `xs.size` chưa trim. Với typed arm degenerate (nhiều cặp tied ở 0), `n` in ra có thể phóng đại lớn.

Fix: `stats.wilcoxon(xs, ys, zero_method="wilcox")` + báo cả `n_nonzero = int(np.count_nonzero(xs - ys))`, và in `n_nonzero` chứ không phải `n`. 3 dòng.

---

## 1.2 P1 — Chín metric **miễn phí**: chỉ cần một script post-hoc

> Tất cả rút được từ `data/episodes.jsonl` đã ghi (RoundRecord đã có `patch`, `fine_type`, `round_index`, `accept`, `guarded`) + `data/calls_*.jsonl`. **Không thêm một model call nào, không đổi schema.**

**Gom hết vào một file mới: `scripts/measure_redundancy.py` → `data/redundancy.json`.**

| # | Metric | Công thức, tính trên `rows` của một episode (đã sort theo `round_index`, đã cắt sau first accept như `summarize_episode` làm) |
|---|---|---|
| **#2** | **Duplicate-Patch Rate (DPR)** ⭐ | Chuẩn hoá mỗi `patch` bằng `ast.dump(ast.parse(src))` sau khi strip docstring; DPR = (số round có normal-form đã xuất hiện ở round trước) / số round. **Đo được cả trên guarded round** vì `patch` được log ở đó (`src/loop.py` nhánh guarded truyền `patch=patch`) → *không bị censoring như #1*. Đây là proxy trung thực nhất, doc §B1 khuyên đưa lên làm metric chính. Nâng cấp tuỳ chọn: alpha-rename biến local trước khi dump. |
| **#3** | Failure-Signature Revisit Rate (FSRR) | (số round có `fine_type` ∈ tập `fine_type` của các round trước) / (số round có oracle verdict). Chính là `guard_miss` nhưng ở dạng **tỉ lệ**, và tính cho *mọi* arm. |
| **#4** | Novel-Class Discovery Rate (NCDR) | (số `fine_type` distinct) / (số oracle call). Lý thuyết nói typed → 1.0. |
| **#5** | Elimination Yield | (số type bị loại) / (số proposal) = NCDR nhưng mẫu số là `proposals`. Dual của #4 phía generation. |
| **#7** | Class Revisit Distance | Với mỗi type τ xuất hiện lần ≥2: khoảng cách round giữa hai lần. Xuất **survival curve** (Kaplan–Meier, censor ở B) chứ không phải một số. |
| **#8** | Effective Proposal Ratio (EPR) | (số round "hữu ích") / `proposals`, hữu ích = lần đầu của một type **hoặc** là patch được accept. |
| **#11** | **success@B — dạng đường cong** ⭐ | `success@b = 1[first_accept_round ≤ b]` cho b = 1..B. Rút thẳng, không cần chạy thêm. **Hai trục:** proposal budget dùng `round_index`; oracle budget dùng số round non-guarded tích luỹ đến accept. Doc §B2 #11/#12 yêu cầu báo cả hai để trung thực. |
| **#18** | AUC-Budget | `mean_b(success@b)` — một số tóm tắt cả đường cong, tránh cherry-pick B=20. |
| **#33** | Proposal diversity | Entropy Shannon của phân phối `fine_type` trong một episode; + distinct-3gram trên token của `patch`. Kiểm chứng steering *có thật sự tăng đa dạng* hay chỉ đẩy sang một lớp khác. |

**Thêm 2 cái nữa cũng miễn phí, gom vào `scripts/measure_patch_quality.py`:**

| # | Metric | Công thức |
|---|---|---|
| **#23** | Patch verbosity | `difflib` giữa `patch` và `program.buggy_source` → (LOC changed, #hunks); so với gold = diff(`buggy_source`, `correct_source`). Báo tỉ lệ `patch_loc / gold_loc`. Doc gợi ý đây là metric phụ dễ thắng (đối chiếu RECAP: +121% changes). |
| **#21** | Correct/Plausible ratio | Đã có nguyên liệu: `data/overfit_checks.jsonl` (`truly_correct`). ratio = #truly_correct / #accepted. Chỉ cần in ra. **Bắt buộc đo — doc cảnh báo steering có thể đẩy agent vào lớp "plausible nhưng sai".** |

**Effort P1 tổng: ~1 ngày.** Hai script, không đụng `src/`, không chạy lại gì.

---

## 1.3 P2 — Năm metric cần thêm field vào schema (làm **trước** khi chạy grid)

### P2-1. Nối RoundRecord ↔ calls ledger — mở khoá 4 metric cùng lúc ⭐⭐

**Vấn đề:** `data/calls.jsonl` có `in` / `out` / `sec` / `finish_reason` cho từng model call. `RoundRecord` **không có khoá nào nối sang**. Join theo `nonce` không đủ: `proposal_nonce` cố tình *không* chứa mode (`src/loop.py:proposal_nonce`, để pair CRN), nên một nonce ứng với nhiều ledger row khi các arm phân kỳ prompt. Khoá đúng là `cache_key` — nhưng nó chỉ tồn tại bên trong `src/llm.complete`.

**Fix — 3 chỗ:**

```python
# src/llm.py:131 — complete() nhận thêm một dict out-param
def complete(prompt, model=None, max_tokens=1024, *, nonce="",
             temperature=None, meta: dict | None = None) -> str:
    ...
    key = cache_key(prompt, model, temperature, max_tokens, nonce)
    if meta is not None:
        meta["cache_key"] = key
        meta["max_tokens"] = max_tokens
    hit = CACHE / f"{key}.json"
    if hit.exists():
        blob = json.loads(hit.read_text())
        if meta is not None:
            meta.update(cached=True, **{k: blob[k] for k in ("in","out","sec","finish_reason") if k in blob})
        return blob["text"]
    ...
    if meta is not None:
        meta.update(cached=False, **{"in": tin, "out": tout, "sec": round(time.time()-t0,2),
                                     "finish_reason": choice.finish_reason, "usd": round(usd,6)})
```

```python
# src/llm.py:183 — cache blob lưu thêm token counts, để cache hit tự đủ dữ liệu
hit.write_text(json.dumps({"text": text, "model": model, "temperature": temperature,
                           "nonce": nonce,
                           "in": tin, "out": tout, "sec": round(time.time()-t0, 2),
                           "finish_reason": choice.finish_reason}))
```
> Backward-compatible: 18k blob cũ trong `cache/` thiếu các field mới → fallback tra `data/calls_*.jsonl` theo `cache_key`. Không invalidate cache (cache key **không** đổi).

```python
# src/proposer.py:365 propose() — nhận meta và truyền xuống
text = complete(prompt, model=model, max_tokens=max_tokens, nonce=nonce,
                temperature=temperature, meta=meta)
```

```python
# src/metrics.py RoundRecord — thêm
cache_key: str | None = None
prompt_tokens: int | None = None      # #16 context tokens per round
completion_tokens: int | None = None
reasoning_tokens: int | None = None   # chỉ khác None với reasoning model — xem §2.3
llm_sec: float | None = None          # #17 (nửa model)
oracle_sec: float | None = None       # #17 (nửa oracle) — time.time() quanh differential_test
guard_sec: float | None = None        # #17 — time.time() quanh memory.guard
```

**Mở khoá ngay:**

| # | Metric | Công thức sau khi có field |
|---|---|---|
| **#13** | Total tokens to repair (in/out tách riêng) | `sum(prompt_tokens)`, `sum(completion_tokens)` trên các round ≤ first_accept. Doc: input token >95% chi phí agentic → typed index thay transcript thắng đậm ở đây. |
| **#16** | Context tokens per round ⭐ | `mean(prompt_tokens)` theo `round_index`. Vẽ theo round → **đây là chỗ typed index đánh bại transcript rõ nhất và hiện chưa đo**. Chỉ có ý nghĩa khi có baseline transcript (§3, B1). |
| **#6** | Redundant-Token Share | `sum(completion_tokens của round redundant) / sum(completion_tokens)`, "redundant" theo định nghĩa P0-1(a). |
| **#17** | Wall-clock to repair | `sum(llm_sec + oracle_sec + guard_sec)`. Với backend local `llm_sec` là thời gian GPU thật; với cloud là latency. |

### P2-2. #14 Cost-of-pass `v = C/R`

Dùng đúng công thức của *Efficient Agents* (mục 67 trong doc), không tự chế:

```
C  = chi phí kỳ vọng một episode  = mean_episodes( Σ_round (prompt_tok·PRICE_IN + completion_tok·PRICE_OUT)/1e6 )
R  = resolve rate                  = mean(success_at_b)      ← dùng truly_correct, không dùng accept
v  = C / R                          [USD per correct patch]
```
Báo kèm CI bootstrap theo task (dùng `analyze.bootstrap_ci` sẵn có). So thẳng với EET (−31.8% cost) như doc §B2 yêu cầu. **Với backend local, PRICE=0** → phải reprice bằng rate card của một model cloud (ghi rõ trong caption là "repriced, not billed"). Đây chính là lý do §2.4 khuyên chạy một arm gpt-4o-mini thật.

### P2-3. #25 Typing coherence `c` — **đo thật** ⭐⭐⭐

> Doc §B4 gọi đây là *"đóng góp lớn nhất bạn có thể thêm"*, và §0(c) nói θ chưa kiểm chứng là **lỗ hổng chí mạng**. `measure_coherence.py` hiện chỉ đo cross-refutation rate và tự thừa nhận không tách được ρ khỏi c.

**Ground truth khả thi trên ConDefects** (đây là chỗ khoá — doc không nói cách này, nhưng nó chặt hơn stack-hash):

Với mỗi refuted patch `p`, định nghĩa **behavioral signature** `σ(p)` = tập test case của *toàn bộ pool* mà `p` fail. Hai patch cùng `σ` là tương đương quan sát được dưới oracle đầy đủ — đây đúng là "root cause partition" mà Igor / Semantic Crash Bucketing lấy làm chuẩn, chỉ khác là ta có nó *miễn phí về tiền*.

Rồi chấm điểm phân hoạch của `θ` so với phân hoạch của `σ`, đúng metric Igor dùng:

```
homogeneity(θ | σ)    = 1 − H(σ|θ)/H(σ)      ← under-counting: một θ-bucket trộn nhiều root cause
completeness(θ | σ)   = 1 − H(θ|σ)/H(θ)      ← over-counting: một root cause bị xé ra nhiều bucket
V-measure             = harmonic mean
adjusted Rand index   (báo kèm, chuẩn hơn khi #cluster lệch)
```
`ĉ = homogeneity` là ứng viên hợp lý nhất để map sang `c` của Def. 3.1 (xác suất quy đúng lớp) — **ghi rõ trong paper rằng đây là một operationalisation, không phải chính `c`**.

- Script mới: `scripts/measure_typing_coherence.py` → `data/typing_coherence.json`.
- Input: mọi refuted patch trong `data/episodes.jsonl` (arm `no_memory` là nguồn không thiên lệch nhất — nó chạy full budget).
- Chạy full pool: đã có sẵn máy móc trong `scripts/measure_pool_strength.py` và `src/oracle.is_truly_correct`.
- Báo ở **cả hai granularity** (coarse/fine) và **cả hai định nghĩa location** — đây cũng chính là bằng chứng để chọn `--granularity`.
- **Chi phí: $0, chỉ sandbox time.** Ước lượng: (#refuted patch) × (#test case trung bình). Với 115 task × 5 seed × ~15 refuted round ≈ 8.6k patch × ~50 case ≈ 430k lần chạy sandbox — **cần cap**: lấy mẫu tối đa 200 patch/task và tối đa 60 case/patch, ghi rõ cap trong output (doc §B7: *"no silent caps"*).

### P2-4. #28 crossover `c*` + #29 degradation slope

E5 hiện có 3 mức (0.9 / 0.75 / 0.5) + mức 1.0 từ E2 = 4 điểm. Đủ cho slope, **chưa đủ cho `c*`** — cần thấy typed cắt xuống dưới untyped. Thêm `E5-c25` và `E5-c00` vào preset của `scripts/eval_shard.sh:case "$EXP"`:

```bash
E5-c25) MODES="typed"; EXTRA="--typing-noise-c 0.25"; UNIVERSE="sweep" ;;
E5-c00) MODES="typed"; EXTRA="--typing-noise-c 0.0";  UNIVERSE="sweep" ;;
```
- `c*` = nghiệm của `f_typed(c) = f_untyped` bằng nội suy tuyến tính giữa hai mức kề nhau, cho mỗi metric primary.
- slope = `d(oracle_calls)/dc` và `d(anchoring)/dc` bằng OLS trên 6 mức.
- **Cảnh báo đã có sẵn trong `DESIGN.md §4`:** `TypedMemory.store` chỉ nhiễu nửa `location`, và store đầu tiên của mỗi episode không bao giờ mistype được (chưa có location khác để đổi sang). Nên trục c là **cận dưới của thiệt hại**, và `c=0.0` **không** cho tỉ lệ mistype 100%. Phải ghi con số mistype *thực đo* (`stored_type != fine_type`) bên cạnh `c` danh nghĩa — field `stored_type` đã có sẵn trong RoundRecord.
- Chi phí: 2 mức × 30 task sweep × 3 seed ≈ 970 calls. Local: vài giờ. gpt-4o-mini: ~$0.35.

### P2-5. #22 Regression rate (pass-to-pass)

ConDefects là chương trình stdin→stdout nguyên khối, không có test suite phân "pass-to-pass". **Cách thay thế:** split pool thành `F2P` (case mà `buggy_source` fail) và `P2P` (case mà `buggy_source` pass). Regression rate = % case P2P bị patch làm hỏng. Có sẵn dữ liệu — `Task.test_cases` và `run_program`. Một hàm trong `src/oracle.py`, ~20 dòng. **Đáng làm**: nó biến "accept" thành một verdict có cấu trúc thay vì nhị phân.

---

## 1.4 P3 — Ba metric cần cơ chế mới (sau grid chính, nếu còn thời gian)

| # | Metric | Việc phải làm |
|---|---|---|
| **#30** | **Recovery rate sau mis-elimination** ⭐ | Đây không phải chỉ là metric — nó là **một dòng mới trong Algorithm 1** mà doc §Phụ lục-3 khuyên thêm. Confidence-gated elimination: chỉ loại một class khi đã thấy ≥`k` counterexample cùng type (`--elim-threshold k`), **hoặc** revival: sau `N` round không tiến triển, gỡ class cũ nhất khỏi `E` (`--revive-after N`). Biến anchoring từ *terminal* thành *recoverable*. Implement trong `TypedMemory` + hai flag mới trong cell key. ~1 ngày + một sweep nhỏ. Doc nói thẳng: *"biến anchoring từ 'rủi ro chúng tôi phát hiện' thành 'rủi ro chúng tôi đã xử lý'"*. |
| **#32** | Steering shift `KL(P_steer ‖ P_base)` | Cần logprob của proposer. Ollama/OpenAI chat completions **có** `logprobs` nhưng chỉ trên token sinh ra, không phải phân phối trên patch space. Xấp xỉ khả thi: KL giữa **phân phối type thực nghiệm** của typed arm và của no_memory arm trên cùng task (dùng CRN pairing — cùng nonce, cùng seed). Rẻ, nhưng là KL trên type chứ không phải trên patch — **phải nói rõ**. |
| **#34** | Cross-task transfer | Nối CEGMem sang cross-issue (ExpeRepair/SWE-Exp territory). Cần memory sống qua nhiều episode. **Để future work**, đừng làm cho FSE 2026. |

---

## 1.5 Bảng "KHÔNG hứa thắng" — copy vào paper

Doc §B6 nói đúng, và code đã sẵn sàng chứng minh cả bốn. Ghi thẳng vào Section 6:

1. **Oracle calls / verification rounds** — Theorem 4.3(a) của chính paper nói typed ≡ untyped. Abstract hiện viết *"cuts verification rounds 2.6×"* → dễ bị đọc là công của *typing*; thực ra là công của *memory nói chung*. **Sửa câu abstract.**
2. **Resolve rate ở budget vô hạn** — hoà, không có lý do gì thắng.
3. **Anchoring rate** — typed *thua* no-memory và untyped khi `c < 1`. Đã báo trung thực, giữ.
4. **Guard evaluations trong full CEGMem = 0** — là do guard *dormant*, không phải guard rẻ. Gộp chú thích vào **ngay trong Table 4**, đừng để dưới bảng.

---

# §2 — ĐỔI PROPOSER SANG OpenAI

## 2.0 "gpt-o4-mini" là model nào?

Hai model khác hẳn nhau, và **chỉ một cái là drop-in**:

| | `gpt-4o-mini` | `o4-mini` |
|---|---|---|
| Loại | chat model | **reasoning model** (o-series) |
| Giá (in / out per Mtok) | $0.15 / $0.60 | **$1.10 / $4.40** |
| `temperature` | ✓ | ✗ (chỉ nhận default) |
| `max_tokens` | ✓ | ✗ → phải dùng `max_completion_tokens` |
| Reasoning token | không | **có, tính vào output token → tính tiền** |
| Context / max output | 128k / 16 384 | 200k / 100 000 |
| Sửa `src/llm.py`? | **KHÔNG** | **CÓ** (§2.3) |
| Chi phí full grid 115 task | **~$11** | **~$84 (naive) → $175–270 (kèm reasoning token)** |

`.env.example` hiện đã viết sẵn: *"Must be a chat model, not a reasoning model: o-series ids reject `temperature`"* — nghĩa là repo đã **cố ý** loại o-series.

**→ Nếu ý bạn là `gpt-4o-mini`: config đã đủ, chỉ vướng shard script. Nếu là `o4-mini`: phải làm §2.3 trước.**
Con số dưới đây tính cho cả hai.

## 2.1 Cái gì KHÔNG phải sửa

`src/llm.py` viết cho wire format OpenAI chat-completions từ đầu, ollama chỉ là một `LLM_BASE_URL` khác. Với `gpt-4o-mini`:

```bash
# .env — chỉ sửa 6 dòng
LLM_BASE_URL=                       # rỗng = api.openai.com
LLM_API_KEY=sk-...
MODEL=gpt-4o-mini
LLM_CONTEXT_TOKENS=128000           # hoặc 0 để tắt check
PRICE_IN_PER_MTOK=0.15
PRICE_OUT_PER_MTOK=0.60
BUDGET_USD_CAP=25.0                 # cap thật, không còn là tripwire
LLM_TIMEOUT_SEC=180                 # 1800 là cho local 7B, cloud không cần
```

Không đụng: `src/loop.py`, `src/proposer.py`, `src/memory.py`, `src/typer.py`, `src/oracle.py`, `scripts/run_eval.py`. `model` đã nằm sẵn trong cell key (`scripts/run_eval.py:92`) và `freeze_results.py:182` đã **từ chối** freeze một log chứa hai model. Hạ tầng đã sẵn sàng cho một model thứ hai.

**Lợi ích phụ:** `LLM_CONTEXT_TOKENS=128000` làm biến mất hoàn toàn `proposal_error="context_overflow"` — một threat-to-validity mà `RUNBOOK.md §9` hiện phải giải thích riêng. Prompt của memory arm dài nhất cũng chỉ ~10k token.

## 2.2 Cái gì PHẢI sửa — hai shard script bị khoá cứng vào ollama

Đây là toàn bộ công việc thật cho `gpt-4o-mini`.

`scripts/screen_shard.sh` và `scripts/eval_shard.sh` đều: `command -v ollama || exit`, gọi `serve_local.sh` để start/verify/unload, khẳng định `context_length` qua `/api/ps`, rồi ép `PRICE_*=0` và `BUDGET_USD_CAP=1`. Với cloud, cả 5 bước đó đều sai hoặc vô nghĩa.

**Thêm `--backend ollama|cloud` (default `ollama`) vào cả hai script:**

```bash
BACKEND="${BACKEND:-ollama}"
# ... trong phần server lifecycle:
if [[ "$BACKEND" == "cloud" ]]; then
  : "${LLM_API_KEY:?set LLM_API_KEY for --backend cloud}"
  export LLM_BASE_URL="${LLM_BASE_URL:-}"        # rỗng = api.openai.com
  export LLM_CONTEXT_TOKENS="${CONTEXT_LENGTH}"   # 128000
  export PRICE_IN_PER_MTOK PRICE_OUT_PER_MTOK BUDGET_USD_CAP
  RUNTIME='{"backend":"openai","context_length":'"$CONTEXT_LENGTH"',"api_base":"'"${LLM_BASE_URL:-api.openai.com}"'"}'
  STARTED_SERVER=0; STOP_MODEL=0
else
  ... # nguyên xi như hiện tại
fi
```

Ba chi tiết **không được bỏ sót**:

1. **`RUNTIME` blob vẫn phải được ghi.** `eval_shard.sh` cố tình `exit` nếu `serve_local.sh` không báo runtime (*"without the record this shard would be unauditable"*), và `consolidate_evals.py` hard-stop khi hai shard bất đồng runtime. Nhánh cloud phải sinh blob của riêng nó — chứa `api_base`, `model`, và (nếu có) `system_fingerprint` từ response đầu tiên.
2. **`BUDGET_USD_CAP` khi chạy nhiều shard song song.** `llm.spent()` (`src/llm.py:113`) đọc **chỉ file `CALLS_LOG` của process mình**, mà `eval_shard.sh` đã cấp mỗi shard một ledger riêng. Chạy 4 shard song song ⇒ mỗi shard thấy 1/4 chi tiêu ⇒ cap thủng 4×. **Quy tắc: `BUDGET_USD_CAP = tổng_cho_phép / số_shard_song_song`,** và ghi con số đó vào `.meta.json`. (Hoặc giữ nguyên luật serial của `DESIGN.md` — với cloud thì tốn wall clock vô ích.)
3. **Song song hoá — cơ hội lớn nhất.** Local: `OLLAMA_NUM_PARALLEL=1`, ~32s/call, grid 31k call ≈ **11 ngày GPU**. Cloud gpt-4o-mini: ~2s/call, 8 shard song song ⇒ **~2 giờ**. *Điều kiện:* mỗi shard ledger riêng (đã có), mỗi shard episodes-path riêng (đã có), cache dir dùng chung an toàn (content-addressed, ghi file atomic-ish — nên đổi `hit.write_text` thành write-to-temp + `os.replace` để tránh đọc file nửa vời khi hai shard cùng miss một key). **~5 dòng, làm luôn.**

## 2.3 Nếu là `o4-mini` — thêm 5 việc trong `src/llm.py`

```python
# 1. Nhận diện reasoning model
_REASONING = ("o1", "o3", "o4")
def _is_reasoning(model: str) -> bool:
    return model.split("-")[0] in _REASONING or model.startswith(_REASONING)

# 2. Đổi tên param + bỏ temperature
kwargs = {"model": model, "messages": [...]}
if _is_reasoning(model):
    kwargs["max_completion_tokens"] = max_tokens
    kwargs["reasoning_effort"] = REASONING_EFFORT      # "low"|"medium"|"high"
    # KHÔNG gửi temperature
else:
    kwargs["max_tokens"] = max_tokens
    kwargs["temperature"] = temperature
r = client().chat.completions.create(**kwargs)

# 3. reasoning_effort vào cache key — nó đổi phân phối, nên nó là protocol
def cache_key(prompt, model, temperature, max_tokens, nonce, reasoning_effort=""):
    parts = (model, repr(float(temperature)), str(max_tokens), reasoning_effort, nonce, prompt)

# 4. Log reasoning token tách riêng
details = getattr(r.usage, "completion_tokens_details", None)
reasoning = getattr(details, "reasoning_tokens", 0) or 0
# ghi vào ledger: {"out": tout, "reasoning_out": reasoning, ...}
# usd vẫn dùng tout (completion_tokens ĐÃ bao gồm reasoning token) — công thức hiện tại đúng
```

```python
# 5. src/proposer.py:325 — _MAX_BUDGET = 16000 sẽ giết reasoning model
```
> `max_completion_tokens` phải phủ **reasoning + visible output**. Chương trình dài nhất trong corpus (68 KB, cần ~22k visible) đã chạm trần 16000. Với reasoning model, reasoning token ăn trước, phần visible bị cắt ⇒ `TruncatedResponse` ⇒ ghi nhận là *harness failure* đúng trên những task dài nhất. Phải nâng `_MAX_BUDGET` lên ≥ 32000 (o4-mini cho tới 100k). **Nhưng `max_tokens` nằm trong cache key** → nâng trần sẽ invalidate cache của mọi program vượt 16000. Chấp nhận được vì đằng nào đổi model cũng invalidate hết.

**Ngoài ra, `reasoning_effort` phải vào cell key** (`scripts/run_eval.py:92`, `src/loop.py:cell_signature`, `RoundRecord`) — cùng lý do `model` và `granularity` đã ở đó, và `RUNBOOK.md §9` đã ghi sẵn triệu chứng nếu quên: *"Every cell re-runs instead of skipping"*.

**Verify trước khi code** (1 call, ~$0.001):
```bash
.venv/bin/python - <<'PY'
import openai, os; c = openai.OpenAI(api_key=os.environ["LLM_API_KEY"])
for kw in ({"max_tokens":64,"temperature":1.0}, {"max_completion_tokens":64,"temperature":1.0}, {"max_completion_tokens":64}):
    try:
        r = c.chat.completions.create(model="o4-mini", messages=[{"role":"user","content":"say ok"}], **kw)
        print("OK ", kw, "->", r.usage.completion_tokens, "out,",
              getattr(r.usage.completion_tokens_details, "reasoning_tokens", None), "reasoning")
    except Exception as e:
        print("ERR", kw, "->", type(e).__name__, str(e)[:120])
PY
```

## 2.4 ⚠️ Hệ quả khoa học — phần quan trọng hơn cả code

**π là thuộc tính của model.** `RUNBOOK.md §1` và `STATUS.md` đều nói: screen và E1–E5 phải cùng một proposer. Đổi model ⇒ **207 program đã screen ở K=40 (~5.5k call, nhiều ngày GPU) thành vô giá trị** và phải screen lại từ đầu.

Tệ hơn — phân bố band sẽ **dịch phải**, và đó là hướng sai. Đo thực từ `data/screen_*.json` (qwen2.5-coder:7b, K=40, 207 program):

| band | π range | count | quota mong muốn |
|---|---|---|---|
| `dead` | [0, 0.02) | **113** | 20 |
| `hard` | [0.02, 0.08) | **40** | 30 |
| `medium` | [0.08, 0.18) | 13 | 20 |
| `easy` | [0.18, 0.35] | 17 | 30 |
| `too_easy` | (0.35, 1] | 24 | 15 |

π̂ pooled = 0.110, median = 0.000.

Một proposer mạnh hơn hẳn sẽ rút cạn `dead` và `hard` — **đúng hai band nơi hiệu ứng được dự đoán lớn nhất**: `select_corpus.py:58-73` ghi rõ A₁₂ = 0.83/0.96/1.00 cho Easy/Medium/Hard, oracle calls 23.07→6.50 trên Hard, và Prop. 4.5 nói guard-cost gap *"grows with task difficulty"*. Thay proposer mạnh có thể làm **hiệu ứng khó thấy hơn**, không dễ hơn.

Mặt khác `DESIGN.md §6` đã tự ghi nhận điểm yếu ngược lại: *"A single small proposer... phải được declare, và lý tưởng là check với một model mạnh hơn trên một subset."*

### → Khuyến nghị: KHÔNG thay. THÊM.

Giữ `qwen2.5-coder:7b` làm proposer chính (screen đã chạy 207/320, sắp xong). Thêm **gpt-4o-mini làm proposer thứ hai trên subset 30 task phân tầng** (6/band — chính là `data/sweep_programs.txt` mà `eval_shard.sh` đã sinh sẵn).

| Việc | Calls | gpt-4o-mini | o4-mini | o4-mini +3× reasoning |
|---|---|---|---|---|
| Re-screen 320 gated task, K=10 | 3 200 | **$0.92** | $6.72 | ~$15 |
| E1+E2 trên subset 30 task, 5 seed, B=20 | 5 626 | **$1.97** | $14.41 | $31.15 |
| + E3 ablation (3 seed) | +1 575 | **+$0.66** | +$4.87 | +$9.55 |
| **Tổng subset arm** | ~10 400 | **~$3.6** | ~$26 | ~$56 |
| *(so sánh)* full grid 115 task, E1–E5 | 30 929 | ~$11 | ~$84 | ~$176–268 |

*Token profile lấy từ 6 358 call thật đã log: 557 in / 338 out trung bình cho no-memory call; memory arm +~900 input token.*

**Cái này mua được gì:**
- Vá đúng open item *"a single small proposer"* của DESIGN.md §6 — bằng số, không bằng lời.
- Cho `PRICE_*` khác 0 ⇒ **#14 cost-of-pass là tiền thật**, không phải repricing giả định.
- Cho một điểm dữ liệu về *"hiệu ứng có sống sót khi π cao hơn không"* — chính là câu hỏi reviewer sẽ hỏi.
- `freeze_results.py` đã từ chối trộn hai model, `analyze.py` phân tách theo cell key ⇒ **không có rủi ro nhiễm bẩn grid chính**.
- Rẻ hơn full grid gấp 3× và không vứt đi công screen đã làm.

**Nếu vẫn muốn thay hẳn:** dùng `gpt-4o-mini`, không dùng `o4-mini`. Lý do: `o4-mini` đắt gấp 8–25×, cần sửa code, và reasoning token là một biến ẩn không nằm trong bất kỳ cache key nào — đúng loại "hai instrument mang cùng một tên" mà cả `src/llm.py` lẫn `serve_local.sh` được viết ra để chặn.

---

# §3 — BASELINE

## 3.1 Tiêu chí chọn

Doc liệt 68 mục; hầu hết **không chạy được** trên setup này:
- ExpeRepair, SWE-Exp, ReasoningBank, RepairAgent, AutoCodeRover → repo-level, cần SWE-bench + scaffold agent + tool use. Khác benchmark, khác kiến trúc. **Chỉ cite, không chạy.**
- F1X, ExpressAPR, Angelix, SemFix → Java/C, cần enumerate patch space + dynamic analysis. **Chỉ cite** (và §0(a) của doc đã cho sẵn câu định vị).
- EET (early termination) → trục khác (dừng sớm, không phải memory). Cite làm mốc định lượng cho claim tiết kiệm.

Còn lại đúng ba thứ chạy được **trong loop hiện tại, cùng corpus, cùng oracle, cùng proposer** — nghĩa là so sánh apples-to-apples chứ không phải trích số từ paper khác:

## 3.2 B0 — Repeated sampling (*Large Language Monkeys*, doc #63) — **MIỄN PHÍ**

> Doc gọi đây là *"đối trọng lý thuyết mạnh nhất với bạn"*: họ nói "cứ sample nhiều là được"; bạn nói "phần lớn sample đó redundant". **Phải addressed head-on.**

**Không cần chạy gì thêm.** Arm `E1` chạy `--force-full-budget`: no-memory, prompt bất biến, 20 round độc lập/episode. Đó **chính là** repeated sampling với k=1..20.

```
coverage@k (pass@k) = 1 − (1 − π̂_task)^k          [ước lượng]
                    = P[ít nhất một trong k round đầu accept]   [đo trực tiếp từ round log]
```
Vẽ chồng lên `success@B` của typed arm trên cùng một trục budget (§1.2 #11). **Thông điệp:** CEGMem cắt đúng phần đuôi phẳng của đường cong repeated-sampling. Nếu hai đường trùng nhau ⇒ đóng góp không tồn tại, và biết sớm còn hơn để reviewer chỉ ra.

**Effort: 2 giờ** (một hàm trong `scripts/measure_redundancy.py`). Chi phí: $0.
**Bắt buộc làm. Không có lý do gì bỏ.**

## 3.3 B1 — Transcript memory (*ChatRepair*, doc #18) — ⭐ **BẮT BUỘC**

**Tại sao đây là baseline quan trọng nhất:**

`src/proposer.py` docstring và `DESIGN.md §4` đã tự ghi: untyped arm hiện **không cho proposer thấy gì cả**, vì Algorithm 1 vẽ `p_t ~ G(·|E)` với `E` rỗng. Về lý thuyết là đúng. Nhưng reviewer FSE sẽ đọc là **straw man** — không agent thật nào vứt transcript đi. Doc §A3 nói thẳng ChatRepair là *"hiện thân chính xác của flat transcript memory"*.

Repo đã lường trước và ghi sẵn cách vá: *"The transcript condition remains a legitimate question about real LLM agents — just not this paper's untyped baseline. To ask it, **add it as its own mode** rather than by relabelling this one."*

**Implement — mode thứ tư `transcript`:**

```python
# src/proposer.py MODES / src/memory.py MODES / src/loop.py MODES
MODES = ("no_memory", "untyped", "typed", "transcript")

# src/proposer.py:_evidence_block — thêm nhánh
if mode == "transcript":
    lines = ["Previous attempts and why each failed:"]
    for i, a in enumerate(refuted, 1):
        lines.append(f"--- attempt {i} ---\n```python\n{a.patch}\n```\n"
                     f"{_format_counterexample(a)}")
    return "\n".join(lines)
# _exclusion_block: giữ nguyên "" cho transcript (transcript không có khái niệm class)

# src/memory.py — TranscriptMemory(UntypedMemory): guard giống hệt untyped (flat scan).
#   Khác biệt duy nhất là nó CÓ reach proposer. Một dòng: `mode = "transcript"`.
# src/memory.py:build_memory — thêm nhánh.
```

**Cạm bẫy đã biết, phải xử lý ngay:**
1. **Context blow-up.** Docstring của `src/proposer.py` ghi lại lần thử trước: transcript arm re-emit gần y hệt patch vừa được cho xem, guard chặn 16–19/20 round, budgeted success **tụt xuống dưới no-memory** (0.50 vs 0.63). Đó là kết quả *thật* và đáng báo cáo — nhưng chỉ khi nó không phải artefact truncation. **Bắt buộc:** `LLM_CONTEXT_TOKENS` phải đúng (32768 local / 128000 cloud) và phải đếm `proposal_error="context_overflow"` theo arm. Đây cũng chính là chỗ metric **#16 context-tokens-per-round** thắng đẹp nhất: transcript tăng tuyến tính theo round, typed index thì phẳng.
2. **Cache.** Prompt của transcript arm khác ⇒ không share cache với E1. **Đây là arm duy nhất trong 4 arm phải trả tiền model call thật.** Ước tính bằng E2's untyped-equivalent: 115 task × 5 seed × E[rounds] ≈ 5 200 call. Local: ~2 ngày. gpt-4o-mini: ~$2.5 (input dài hơn nhiều — ước ~4 000 in/call trung bình ⇒ ~$3.5).
3. **Truncation policy phải khai báo trước.** Khi transcript vượt context, cắt thế nào? Đề xuất: giữ `k` attempt gần nhất (`--transcript-window k`, default = ∞), ghi vào cell key. Cắt là một lựa chọn thiết kế, không phải chi tiết kỹ thuật.

**Effort: ~1 ngày code + 1 ngày chạy.** ~40 dòng thật, phần còn lại là plumbing 4-mode qua `MODES` tuple ở 4 file, cộng một preset `E6-transcript` trong `eval_shard.sh`.

**Đổi lấy:** một câu trong Section 5 — *"we additionally instantiate the transcript condition that ChatRepair embodies, and report it alongside"* — đóng luôn lỗ hổng straw-man.

## 3.4 B2 — Reflexion (doc #35, **[ĐÃ CÓ]** trong bibliography) — nên có

Reflexion = self-reflection **bằng lời** sau mỗi thất bại, lưu vào buffer, đưa vào prompt kế tiếp. Đối lập hoàn hảo với CEGMem: *"reflection bằng ngôn ngữ tự nhiên, không có type lattice, không có bảo đảm non-repetition"* — đúng câu doc dùng để phân biệt với ReasoningBank (#34).

**Implement — mode `reflexion`:**
```
mỗi round sau khi bị refute:
  call #2: prompt(patch, counterexample) → "In one or two sentences, why did this fail
           and what should the next attempt do differently?"
  → append text vào reflection buffer
mỗi round trước khi propose:
  evidence_block = toàn bộ reflection buffer (chỉ text, KHÔNG có patch source)
guard: giống untyped (flat scan) — để so sánh công bằng
```
- File mới `src/reflect.py` (~50 dòng) + nhánh trong `_evidence_block` + `ReflexionMemory` trong `src/memory.py`.
- **Nonce cho reflection call phải riêng**: `f"{task}|seed{s}|r{round}|reflect"`, nếu không nó đụng cache của proposal call.
- **Chi phí gấp đôi model call**: ~10 400 call cho 115 task × 5 seed. Local ~4 ngày. gpt-4o-mini ~$4 (reflection prompt ngắn, output ngắn).
- Ghi `n_reflection_calls` vào RoundRecord — nó phải vào metric #10 (proposals) và #13 (tokens), nếu không Reflexion trông rẻ hơn thực tế. **Đây là điểm so sánh mạnh: Reflexion trả 2× model call để có memory bằng lời; CEGMem trả 1× + một lookup O(1).**

**Effort: ~2 ngày code + 3–4 ngày chạy local (hoặc ~$4 + 3 giờ trên cloud).**

## 3.5 Nếu chỉ đủ sức làm một cái

**Làm B1 (transcript).** Lý do:
- Nó vá một lỗ hổng **reviewer chắc chắn sẽ chỉ ra** ("untyped baseline của bạn là straw man"), B2 thì không — Reflexion là *nice-to-have*.
- Nó rẻ nhất trong hai (1× model call, không phải 2×).
- Nó là arm duy nhất làm metric **#16 (context tokens per round)** và **#13 (total tokens)** có ý nghĩa — mà doc gọi là *"chỗ typed index đánh bại transcript rõ nhất và bạn chưa đo"*.
- B0 thì cứ làm, nó miễn phí.

**Không chạy:** ExpeRepair / SWE-Exp / ReasoningBank / AWM. Định vị bằng lời theo trục doc đã cho: *"họ retrieve để **gợi ý**; CEGMem index để **loại trừ**. Recall vs. exclusion."* và *"cross-issue (họ) vs. intra-episode (ta) — complementary, không cạnh tranh."*

---

# §4 — THỨ TỰ THI CÔNG

Sắp theo *"cái gì sẽ đắt kinh khủng nếu làm sau"*.

### Giai đoạn A — trước khi grid chạy một round nào (bắt buộc)
| # | Việc | File | Effort |
|---|---|---|---|
| A1 | `blocked_by_type` vào GuardResult + RoundRecord | `src/memory.py:27,98,172`, `src/metrics.py`, `src/loop.py` | 2h |
| A2 | Join ledger: `meta=` out-param, cache blob lưu token, 6 field mới vào RoundRecord | `src/llm.py:131,183`, `src/proposer.py:365`, `src/metrics.py` | 3h |
| A3 | `oracle_sec` / `guard_sec` (`time.time()` quanh 2 call) | `src/loop.py` | 30m |
| A4 | Quyết mode thứ tư `transcript` — **thêm vào `MODES` NGAY BÂY GIỜ** dù chưa chạy | 4 file `MODES` tuple, `_evidence_block`, `build_memory` | 4h |
| A5 | `--audit-guarded` flag + vào cell key | `src/loop.py`, `scripts/run_eval.py:92` | 2h |
| A6 | Fix `wilcoxon` n | `scripts/analyze.py` | 15m |
| A7 | Cache write atomic (`os.replace`) — mở đường chạy shard song song | `src/llm.py:183` | 15m |

> **Tại sao A1–A5 phải trước:** mọi thứ ở đây đổi *schema hoặc cell key*. `RUNBOOK.md §9` đã cảnh báo: cell key không phủ version của `build_prompt`, nên đổi sau khi có dữ liệu ⇒ những cell cũ vẫn báo "complete" một cách âm thầm ⇒ phải xoá tay hoặc chạy lại toàn bộ.
> **Tổng giai đoạn A: ~1.5 ngày.**

### Giai đoạn B — quyết định proposer (song song với A)
| # | Việc |
|---|---|
| B1 | Xác nhận `gpt-4o-mini` hay `o4-mini` (§2.0). Chạy probe ở §2.3 nếu là o4-mini. |
| B2 | Thêm `--backend cloud` vào `screen_shard.sh` + `eval_shard.sh` (~80 dòng, §2.2) |
| B3 | **Không đổi proposer chính.** Chạy nốt screen qwen (còn ~110/320 program). |
| B4 | Sinh `data/sweep_programs.txt` (đã tự sinh), dùng làm universe cho arm gpt-4o-mini |

### Giai đoạn C — chạy grid (theo `RUNBOOK.md §7`, không đổi)
Thêm 2 preset vào `scripts/eval_shard.sh:case "$EXP"`:
```bash
E6-transcript) MODES="transcript"; EXTRA="--check-overfit"; DEF_SEEDS="1 2 3 4 5" ;;
E7-cloud)      MODES="no_memory untyped typed transcript"; EXTRA="--check-overfit"
               UNIVERSE="sweep"; DEF_SEEDS="1 2 3 4 5" ;;   # chạy với --backend cloud
E5-c25) MODES="typed"; EXTRA="--typing-noise-c 0.25"; UNIVERSE="sweep" ;;
E5-c00) MODES="typed"; EXTRA="--typing-noise-c 0.0";  UNIVERSE="sweep" ;;
```
Nhớ chạy trial trước cho mỗi preset mới (`RUNBOOK.md §7 "Verify the machine"`), và chạy lại lần hai để xác nhận mọi cell in `already complete, skipping` — đó là bài test cho resume key sau khi cell key đổi.

### Giai đoạn D — phân tích (đều $0, sau grid)
| # | Việc | Output |
|---|---|---|
| D1 | `scripts/measure_redundancy.py` — metrics #2,3,4,5,7,8,11,18,33 + B0 pass@k | `data/redundancy.json` |
| D2 | `scripts/measure_patch_quality.py` — #21, #23 | `data/patch_quality.json` |
| D3 | `scripts/measure_typing_coherence.py` — **#25 ĉ đo thật** (§1.3 P2-3) | `data/typing_coherence.json` |
| D4 | Thêm #6,#13,#14,#16,#17 vào `scripts/analyze.py:report["metrics"]` | `data/analysis.json` |
| D5 | Regression rate F2P/P2P (#22) | `src/oracle.py` + report |
| D6 | `c*` + slope từ E5 6 mức (#28,#29) | `data/theory_fit.json` |
| D7 | Figures: success@B curve chồng pass@k, context-tokens-per-round theo arm, survival curve class-revisit | `figures/` |
| D8 | `scripts/check_consistency.py` — **chạy cuối cùng và chạy lại trước khi nộp** | |

### Giai đoạn E — tuỳ chọn, nếu còn thời gian
- Confidence gate + revival (#30) — biến anchoring thành recoverable. Doc gọi đây là 1 trong 3 việc nên làm trước khi nộp.
- Reflexion arm (B2, §3.4).
- `scripts/label_tool.py` cho inter-annotator agreement trên θ (`RUNBOOK.md §8` đã đánh dấu *"optional and high-value"*).

---

# §5 — Ba việc doc yêu cầu mà KHÔNG phải code

Ghi ở đây để không quên (doc §Phụ lục):

1. **Thay motivation "empirically familiar" bằng số có nguồn.** Dùng Bouzenia & Pradel (#33, behavioral analysis: *"unproductive loops (repetitive patch-test cycles)"* tương quan mạnh với thất bại) và *When Agents go Astray* (#45, *action looping, redundant backtracking*). Một câu có citation ở Introduction đổi hoàn toàn cảm nhận rằng paper là synthetic.
2. **Bổ sung cụm A2 (semantic APR / patch-space pruning) vào Section 2** — doc §0(a) gọi việc thiếu Mechtaev (F1X) và ExpressAPR là *"lỗ hổng nghiêm trọng nhất của bản thảo hiện tại"*. Câu định vị đã có sẵn: *họ phân lớp theo semantic equivalence để tiết kiệm **test execution**; CEGMem phân lớp theo observed failure type để tiết kiệm **model call***.
3. **Bổ sung cụm A4 (memory cho repair agent)** — ExpeRepair (cùng FSE 2026) và SWE-Exp. Bắt buộc cite. Trục phân biệt: *recall vs. exclusion*, và *cross-issue vs. intra-episode → complementary*.
4. **Bổ sung A7 (crash deduplication)** vào Section 3.2 — Semantic Crash Bucketing (#51) định nghĩa gần trùng khớp failure type của bạn (*"cùng lớp = cùng patch sửa được"*). Nếu bỏ sót, reviewer sẽ hỏi. Igor (#50) cho luôn cách đo `c` — chính là §1.3 P2-3.

---

## Phụ lục — số liệu đã đo, dùng cho mọi ước tính ở trên

```
Token profile (6 358 call thật, qwen2.5-coder:7b, data/calls_screen_*.jsonl):
  prompt      mean 557   median 470   p90 1 006   max 2 134
  completion  mean 338   median 268   p90   652   max 2 048
  latency     mean 32.3s median 19.3s
  finish_reason: stop 6 356 · length 2

Grid size (corpus 115 task = quota mặc định, B=20):
  E1     11 500 calls      E2  10 336      E3  6 201      E4+E5  2 892
  TOTAL  30 929 calls

Screen hiện tại: 207/320 gated task, K=40 calls/program
  π̂ pooled 0.110 · median 0.000
  dead 113 · hard 40 · medium 13 · easy 17 · too_easy 24
  (quota cần: dead 20 · hard 30 · medium 20 · easy 30 · too_easy 15
   → medium và easy đang thiếu; band shift phải nếu đổi proposer mạnh hơn)
```

Nguồn giá: [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) · [o4-mini model card](https://developers.openai.com/api/docs/models/o4-mini)
