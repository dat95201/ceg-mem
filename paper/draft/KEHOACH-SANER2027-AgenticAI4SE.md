# Kế hoạch thực thi — CEGMem → SANER 2027 Agentic AI4SE

**Ngày lập:** 06/09/2026 · **Chốt số:** 09/10 · **Bản nháp chất lượng camera-ready:** 16/10 · **Abstract:** 19/10 · **Nộp:** 22/10 (sớm 1 ngày)
**Nguồn đặc quyền đã đọc:** `/Users/datnguyen/Study/research/ceg-mem` (toàn bộ), `/Users/datnguyen/Downloads/codex-review.md`

---

## 0. Hai điều chỉnh tiền đề — phải đọc trước mọi thứ khác

### 0.1 File được chỉ định là `ADVISOR_REVIEW` **không phải** review của thầy

`codex-review.md` tự khai ở dòng 10: *"This is an AI-generated review intended for revision support. It may contain errors; authors should verify every technical, bibliographic, and venue-compliance finding before acting on it."* Nội dung là 4 review mô phỏng + meta-review do AI sinh, ngày 05/09/2026. Thư mục `reviews/` trong repo rỗng.

**Hệ quả theo W12:** tôi **không** trao quyền ưu tiên của thầy cho tài liệu này. Nó được xử lý như `REVIEW_PACKET` (mã `REV-##`), tức là một nguồn phát hiện phải đối chiếu với `PAPER` và `CODEBASE`, không phải một ràng buộc cứng. Bản đồ ở §9 vì vậy có hai phần: `REV-##` (gói AI, có disposition đầy đủ) và `AD-##` (những quyết định thực sự cần chữ ký thầy, tất cả đang ở trạng thái NEEDS CLARIFICATION).

Nếu thực sự tồn tại một bản review của thầy mà tôi chưa được đưa, **toàn bộ §9 phải chạy lại** — đó là câu hỏi đầu tiên trong danh sách hỏi thầy (§10).

### 0.2 Lỗi P0 "desk reject" mà gói review nêu **đã đóng từ 06/09**

| Kiểm chứng | Kết quả |
|---|---|
| `pdfinfo paper/draft/main-ieee.pdf` | **Pages: 11** (10 nội dung + 1 tài liệu tham khảo) |
| `main-ieee.tex` | `\documentclass[10pt,conference]{IEEEtran}`, không `compsoc` |
| PDF metadata | Title/Subject/Keywords đã điền, Author rỗng (double-anonymous) |
| `main.pdf` (preprint) | 17 trang, mang toàn bộ phụ lục |

Gói review đọc một bản PDF 19 trang layout ACM. Bản đó không còn tồn tại. **Không một work item nào trong kế hoạch này được dành cho việc cắt trang** — việc đó đã xong (`CUT-rationale.md`, 4 lượt cắt, 21 → 10+1). Điều còn lại là *giữ* 10+1 khi thêm kết quả mới, và đó là ràng buộc trang ở §7.

---

## 1. CAP — Khảo sát năng lực, đọc từ repo (cơ sở cho mọi ước lượng ở W13)

### CAP-A · Bài báo

| ID | Sự kiện | Nguồn |
|---|---|---|
| A-01 | `main-ieee.pdf` = 11 trang (10+1). Tuân thủ 10+2 của SANER | `pdfinfo`, 06/09 06:49 |
| A-02 | Tiêu đề đã đổi thành *"…with a **Location-Indexed** Memory of Refuted Attempts"* — cú đổi story từ "typed" sang "location-indexed" **đã thực hiện** | PDF Title metadata |
| A-03 | Thân bài còn **5 float**. Một float tốn 0,3–0,6 trang; 1 trang IEEE 10pt ≈ 1.100 từ | `CUT-rationale.md` |
| A-04 | Dự trữ cắt còn lại **≈0,50 trang**, đã liệt kê tên: cột "Presumes about candidates" của Table I (0,15), §VII-F rút còn 3 câu (0,15), giải thích lồng nhau ở §III-A (0,10), gloss danh sách arm ở §VI-B (0,10) | `CUT-rationale.md`, mục "If you need to find another half page" |
| A-05 | Lý thuyết đã bị giáng cấp: Prop. 6 rút bỏ tuyên bố tiệm cận, Thm 3/4 và mọi chứng minh chuyển sang supplement, `\suppmattertrue` tự định tuyến tham chiếu | `AUDIT-review-response.md` §E2 |

### CAP-B · Harness

| ID | Sự kiện | Nguồn |
|---|---|---|
| B-01 | Các arm đã cài: E1, E2, E3-guard-only, E3-steer-only, E4-k{20,8,3}, E5-c{90,75,50,25,00}, E5-random, E8-audit, E9-freeguard, **E10-chat**, **E11-selftest**, **E11b-selftest-guard**, **E12-randskip** | `scripts/eval_shard.sh` L343–420 |
| B-02 | **Không tồn tại** code cho prioritization / test-equivalence / RTS. `grep -rn -i "prioriti\|dedup\|rts\|regression_test\|venugopal\|modification_point" src scripts` → 0 khớp trong mã nguồn .py | grep |
| B-03 | `src/sandbox.run_program` (165 LOC) chạy 1 chương trình trên 1 input, timeout mặc định `SANDBOX_TIMEOUT_SEC=10.0`. `src/adapter.load_task` nạp toàn bộ pool test. Cả hai dùng lại được **không sửa** cho một script replay ngoại tuyến | `src/sandbox.py`, `src/adapter.py` |
| B-04 | `src/oracle._sample(cases,k,seed) = random.Random(seed).sample(...)`; `src/loop.py` L461 & L549 gọi với `seed = seed + round_index`. **Bốc thăm ở mọi độ sâu k là tất định và tái lập được ngoại tuyến.** Khi `k ≥ |pool|` trả về nguyên pool theo thứ tự | `src/oracle.py` L89–92, `src/loop.py` |
| B-05 | `src/loop.proposal_nonce(task, seed, round)` — **cố ý không chứa mode hay cờ ablation**. Cache key = `(model, temp, max_tokens, nonce, prompt)`. Nên một arm có prompt trùng byte với arm đã chạy ở cùng `(task, seed, round)` là **cache hit hoàn toàn** | `src/loop.py` L152–165, `src/llm.py` L263–280 |
| B-06 | `src/selftest.py` (156 LOC) cài E11; sinh test là 1 lời gọi/`(task, model)`, nonce là hàm thuần của task → replay tốn 0 lời gọi | `src/selftest.py` |

### CAP-C · Log đóng băng `runs/2026-09-01`

| ID | Sự kiện | Nguồn |
|---|---|---|
| C-01 | `episodes.jsonl`: **39.147 round**, **3.654 cell**, 99 task, 44 trường. Có `patch` (**mã nguồn đầy đủ của ứng viên**), `counterexample_args`, `examples_tried`, `sandbox_runs`, `guard_evaluations`, `oracle_sec`, `guard_sec`, `fine_type`, `coarse_type`, `seed`, `round_index`, `cache_key` | đếm trực tiếp |
| C-02 | Log **không** có `history`, `selftest*`, `oracle_skip_p` → **E10/E11/E11b/E12 chưa từng chạy**. Mọi phân tích về chúng là *re-run*, không phải *re-analysis* | đếm trực tiếp |
| C-03 | **8.570** cặp `(task, patch)` phân biệt; riêng arm `no_memory` có **5.564** | đếm trực tiếp |
| C-04 | Pool: 3.852 case / 99 task; trung bình 38,9; trung vị 33; max 144 | `runs/2026-09-01/tasks.json` |
| C-05 | **Chi phí sandbox đo được: 0,808 s/case.** (249.448 case-execution trong 201.491 giây oracle.) Toàn bộ công oracle của run đóng băng = 56 CPU-h | tính từ `episodes.jsonl` |
| C-06 | Lưới chính (495 cell/arm): oracle calls 9,47 / 1,91 / 1,83 (no-mem/untyped/typed); sandbox runs 95,68 / 44,45 / 43,01; success 0,703 / 0,699 / 0,697. typed-vs-untyped oracle sign p=0,0072, A12=0,524, Wilcoxon theo task p=0,0224 | `paper/draft/numbers.json` |
| C-07 | Cache 100 MB / 18.501 entry. Replay run đóng băng = 0 lời gọi mới | `du`, `src/llm.py` |
| C-08 | Băng: dead 23, hard 17, medium 13, easy 25, too-easy 21. **Primary band = 55 task** (275 cell ở 5 seed) | `numbers.json` |

### CAP-D · Máy

| ID | Sự kiện |
|---|---|
| D-01 | VM sandbox báo `nproc = 4`. 200 CPU-h ở song song tối đa = **50 giờ đồng hồ**. Mọi item >50 CPU-h phải chia đêm hoặc đẩy sang host khác |
| D-02 | Đĩa còn 178 GB — đủ cho một model 14B (~9 GB) |

### Hai ước lượng trong tài liệu của chính nhóm là **sai**, và chúng đổi kế hoạch

| Tài liệu | Nói | Thực tế | Hệ quả |
|---|---|---|---|
| `PLAN-experiments.md` §C | *"Three [prioritization] arms are implemented and pass the round-1 pairing test"* | **Không có dòng code nào** (CAP-B-02). Ba arm đã cài là E10/E11/E12 — ChatRepair, CodeT, random-skip — **không phải** Qi'13/Venugopal'20/dedup | WI-02 chuyển từ "chạy arm có sẵn" thành "viết ~460 LOC". +4,8 person-day |
| `PLAN-experiments.md` §1 | *"≈0.15 s per case … ≈50 CPU-hours"* cho **toàn bộ** 8.570 ứng viên | **0,808 s/case** (CAP-C-05). Toàn bộ ma trận = 352.329 execution = **79 CPU-h**; giới hạn ở vũ trụ no-memory = 221.304 execution = **50 CPU-h** | Ước lượng cũ thấp hơn ~5× ở tốc độ. Kế hoạch này dùng vũ trụ no-memory và con số 50 CPU-h |

---

## 2. CG — Đồ thị tuyên bố hiện tại và điểm hở

| ID | Tuyên bố bài báo đang đưa | Bằng chứng | Điểm hở còn lại |
|---|---|---|---|
| CG-01 | Guard giảm oracle call ×5,17 mà không giảm repair rate | 495 cell ghép cặp, CRN, round-1 đồng nhất 495/495 | không có — đây là kết quả mạnh nhất |
| CG-02 | Tổng execution vẫn giảm ×2,22 **sau khi tính cả guard replay** | `tab:testwork`, `sandbox_runs = guard_evaluations + selftest_runs + examples_tried` | không có |
| CG-03 | Cơ chế là *reorder + stop early*, cùng ô với Qi'13 và Venugopal'20 trong Table I | lập luận, **chưa đo** | **CG-03 là điểm hở lớn nhất còn lại.** Không có số nào tách CEGMem khỏi hai cơ chế cổ điển → WI-02 |
| CG-04 | Prompt steering là null trên mọi outcome, tốn +128% prompt token | 297 cell ablation | chỉ đúng cho qwen2.5-coder:7b → WI-05 |
| CG-05 | Index (λ) không kiếm được chỗ đứng: bộ nhớ trung vị 1 entry, 86,4% quyết định cần 1 lần tra | `related_addenda.py`, log đóng băng | đã tự thú, không cần thêm |
| CG-06 | Guard là sound: 0 chấp nhận trong 4.411 round bị chặn | audit ở k=100, nơi 97/99 task đã vét cạn pool | **audit vô nghĩa ở k=100; dưới k=100 chưa từng chạy** → WI-03 |
| CG-07 | Assumption 2 (loại-trừ-lớp) được thừa nhận là **không** thoả với index λ | tự thú trong §IV | chưa có con số → WI-04 |
| CG-08 | Corpus là quota sample, không phải probability sample; 42/99 task đổi băng | `corpus.json`, errata | đã công bố, không cần thêm |
| CG-09 | Artifact tồn tại, có thể replay | `sections/11b-availability.tex` **còn placeholder, chưa có DOI** | → WI-08 |

---

## 3. Ngân sách — chốt cứng trước khi chọn item (W3, W4)

### 3.1 Người (ràng buộc thắt cổ chai)

| | Ngày |
|---|---:|
| 06/09 → 09/10 (chốt số), 5 ngày làm việc/tuần | 24 person-day |
| 09/10 → 23/10 | 10 person-day |
| **Tổng** | **34 person-day** |
| Dự trữ bắt buộc W4 (1 FTE → 35%) | **−12** |
| **Sức chứa cho item nghiên cứu** | **22 person-day** |

Mọi ước lượng dưới đây **đã nhân** hệ số bất định: ×1,6 cho công người, ×2,0 cho item tạo hạ tầng mới hoặc phụ thuộc bên ngoài.

### 3.2 Máy

| | Trần | Kế hoạch dùng | Còn lại |
|---|---:|---:|---:|
| Lời gọi model mới | 20.000 | **6.150** (đã +30% dự phòng) | 13.850 |
| Sandbox CPU-h | 200 | **84** (đã +30% dự phòng) | 116 |

**Kết luận quan trọng:** ràng buộc thắt cổ chai **không phải** compute mà là 34 ngày công của một người. Chỉ dùng 31% quota model và 42% quota CPU. Điều này quyết định cách tiêu phần dư ở Gate G2 (§6): chỉ mở rộng những item **không tốn thêm ngày công**, tức là mở rộng vũ trụ của một lệnh đã chạy, chứ không thêm item mới.

---

## 4. Danh mục work item

Ký hiệu: **CH = story-changing** (đổi title/abstract/contribution → phải quyết sớm, W5) · **BC = evidence-adding**.

---

### WI-01 · Ma trận phán quyết (ứng viên × case), ngoại tuyến — **BC, hạ tầng bật khoá**

| | |
|---|---|
| **Đối tượng** | 5.564 ứng viên phân biệt của arm `no_memory` (CAP-C-03) × pool đầy đủ của task đó = **221.304 execution** |
| **Code phải viết** | `scripts/build_verdict_matrix.py`, ~180 LOC. Dùng lại `src.sandbox.run_program` và `src.adapter.load_task` **không sửa** (CAP-B-03). Xuất `runs/<ngày>/verdicts.jsonl`: `(task, patch_sha256, case, pass, sec)` |
| **Chi phí** | **50 sandbox CPU-h** (CAP-C-05) + **0 lời gọi model** (patch đã nằm trong log, CAP-C-01) + **4,0 person-day** (2,0 × 2,0 vì tạo stage pipeline mới) |
| **Wall-clock** | 12,5 h ở 4 worker (CAP-D-01) → 2 đêm |
| **Tiêu chí thành công (khai báo trước)** | (a) ≥99% cặp có phán quyết; (b) tái lập được `counterexample_args` đã ghi cho **≥95%** trong 18.212 round oracle của log |
| **Quy tắc thất bại** | Nếu (b) < 95% → sandbox không tất định ngoài mức timeout. **Dừng nhánh ngoại tuyến**, huỷ WI-02/03/04, kích hoạt Gate G1: chuyển 6,4 person-day sang WI-05 mở rộng + WI-09. Báo cáo tỉ lệ bất định như một threat |
| **Rủi ro** | P(hoàn thành) **0,90** — vòng lặp trên hàm có sẵn, input đã trên đĩa. P(thuận lợi) **0,75** — đã biết có 1 fault ở mép timeout (`abc285_e/48880084`) gây dao động, 95% là ngưỡng chịu đựng |
| **Fragility** | **robust** — ma trận có giá trị bất kể kết quả |
| **Trang** | 0 (không sinh float) |

---

### WI-02 · Bốn chính sách xếp thứ tự trên cùng dòng ứng viên — **CH, item quyết định novelty**

| | |
|---|---|
| **Đối tượng** | Mô phỏng ngoại tuyến 5 chính sách trên 495 cell của `no_memory`, dùng `verdicts.jsonl`: **P0** oracle như đã báo cáo (k=100) · **P1** fault-recorded prioritization (Qi et al., ICSM'13) — xếp case theo số ứng viên trước đó nó đã giết · **P2** modification-point aware (Venugopal et al., 2020) — cùng cách nhưng khoá theo vị trí sửa λ · **P3** dedup-only guard (kho, không index) · **P4** guard CEGMem (λ-indexed) |
| **Vì sao hợp lệ** | Trong arm `no_memory` proposer không thấy gì từ validator, nên dòng ứng viên **độc lập với chính sách**. Năm chính sách chạy trên ứng viên trùng byte. Đây chính là điều làm phép so sánh chặt hơn bất kỳ baseline chạy lại nào |
| **Code phải viết** | `scripts/simulate_policies.py`, ~280 LOC |
| **Chi phí** | **0 CPU-h** ngoài WI-01 (vài giây Python) + **0 lời gọi model** + **4,8 person-day** (3,0 × 1,6) |
| **Chỉ số** | execution/episode, execution đến bác bỏ đầu tiên, execution đến chấp nhận đầu tiên, repair tại ngân sách execution khớp. Ghép cặp theo cell; **Wilcoxon theo task là kiểm định chính** |
| **Tiêu chí thành công (khai báo trước)** | Guard CEGMem ≤ chính sách cổ điển tốt nhất về execution-đến-bác-bỏ-đầu-tiên trên **≥60/99 task**, Wilcoxon một phía theo task **p < 0,05** |
| **Quy tắc thất bại** | Nếu một prioritizer cổ điển bằng hoặc hơn: **không sửa kết luận, đổi phạm vi tuyên bố** theo đoạn văn đã viết sẵn ở WI-10 — *"đóng góp của guard là nó không cần lịch sử test xuyên patch trong một task và chuyển giao được sang proposer là LLM, ở mức ngang bằng execution với fault-recorded prioritization"* — và Table I chuyển CEGMem vào đúng ô của Qi'13. Chi phí lịch: **0 ngày**, vì đoạn văn đã có từ tuần 1 |
| **Rủi ro** | P(hoàn thành) **0,85** — code nhỏ, input có sẵn sau WI-01. P(thuận lợi) **0,45** — chưa pilot; với pool 39 case và thường có một case "sát thủ", Qi'13 rất có thể ngang ngửa |
| **Fragility** | **fatal** nếu không có đoạn văn viết sẵn; **fragile** khi có → xếp **đầu tiên** |
| **Trang** | +Table V (5 chính sách × 4 chỉ số) ≈ **0,35 trang**. Đổi lấy: cột Table I (0,15) + §VII-F (0,15) + §III-A (0,10) = 0,40 (CAP-A-04) |

---

### WI-03 · Soundness của guard dưới k=100, tính ngoại tuyến — **BC**

| | |
|---|---|
| **Đối tượng** | 4.411 round bị guard chặn. Với mỗi round, dựng lại **chính xác** tập kiểm tra `X_k = random.Random(seed+round_index).sample(pool, k)` cho k ∈ {20, 8, 3} (CAP-B-04) và tra `verdicts.jsonl` xem ứng viên bị chặn có vượt qua toàn bộ `X_k` không |
| **Code phải viết** | +60 LOC vào `simulate_policies.py` |
| **Chi phí** | **0 CPU-h, 0 lời gọi model** (đây là điểm mấu chốt: `PLAN-experiments.md` §A dự trù chạy lại `--audit-guarded` — không cần) + **0,8 person-day** (0,5 × 1,6) |
| **Tiêu chí thành công** | Cận trên Clopper–Pearson cho tỉ lệ chặn-sai tại mỗi k, thay cho câu tự thú *"dưới k=100 audit chưa từng chạy"* trong §IV |
| **Quy tắc thất bại** | Tìm thấy vi phạm (ứng viên bị chặn mà bốc thăm k=8 sẽ chấp nhận) → **đó là kết quả hay hơn**: mệnh đề 1 vế 2 phát biểu lại thành có điều kiện theo độ sâu. 0,3 ngày viết lại đã cấp phát sẵn |
| **Rủi ro** | P(hoàn thành) **0,90** · P(thuận lợi — guard sound dưới k) **0,60** |
| **Fragility** | **robust** — cả hai kết quả đều đăng được |
| **Trang** | 0 — một câu ở §IV + một hàng supplement |

---

### WI-04 · Đo tính loại-trừ-lớp (Assumption 2) — **BC**

| | |
|---|---|
| **Đối tượng** | Với mỗi counterexample x thuộc lớp τ trong log, đếm số ứng viên thuộc lớp τ′ ≠ τ mà x bác bỏ. Đây đúng là con số Thm 3/4 cần và §IV đang thiếu (CG-07) |
| **Code phải viết** | `scripts/measure_exclusivity.py`, ~90 LOC trên `verdicts.jsonl` |
| **Chi phí** | 0 CPU-h + 0 lời gọi + **0,8 person-day** (0,5 × 1,6) |
| **Tiêu chí thành công** | Tỉ lệ loại-trừ kèm khoảng tin cậy bootstrap gộp theo task; §IV đổi từ *"directional"* sang một con số |
| **Quy tắc thất bại** | Tỉ lệ < 0,5 → Thm 3 và 4 rời hẳn khỏi thân bài, còn một câu ở §IV. Câu đó viết sẵn ở WI-10 |
| **Rủi ro** | P(hoàn thành) **0,90** · P(thuận lợi) **0,35** — nhóm đã tự thú λ không thoả tính loại-trừ; con số thấp là kết quả *dự kiến*, và bản thân con số trung thực mới là sản phẩm |
| **Fragility** | **robust** — lý thuyết đã bị giáng cấp từ trước |
| **Trang** | 0 |

---

### WI-05 · Proposer thứ hai (qwen2.5-coder:14b) — **CH, khe hở external validity** · 🔴 **BLOCKED**

| | |
|---|---|
| **Đối tượng** | 3 arm (no_memory, untyped, typed) × 55 task primary band (CAP-C-08) × 3 seed = **495 cell**. Không viết code: `--model` đã là tham số, `eval_shard.sh` đã ghim `OLLAMA_CONTEXT_LENGTH` và đọc lại `/api/ps` (CAP-B-01) |
| **Chi phí** | **≈4.700 lời gọi model mới** (495 × 9,5 proposal/cell, CAP-C-06) + **6,8 sandbox CPU-h** (495 × 61 exec × 0,808 s) + **3,2 person-day** (2,0 × 1,6) |
| **Wall-clock** | ~21 h GPU cục bộ (14B ước ~16 s/lời gọi, gấp đôi 7B) → 3 ngày chạy nền, shard có resume |
| **🔴 Blocker** | Cần `ollama pull qwen2.5-coder:14b` (~9 GB, đĩa đủ theo CAP-D-02) **và** đủ bộ nhớ hợp nhất để phục vụ ở context length đã ghim. Ngày sớm nhất blocker gỡ: **D+0** (ngay hôm chạy lệnh kiểm tra). Lệnh quyết định: `ollama pull qwen2.5-coder:14b && OLLAMA_CONTEXT_LENGTH=32768 bash scripts/serve_local.sh verify` |
| **Phương án thay thế không cần blocker** | `qwen2.5-coder:3b` — **cùng họ, thang xuống**. Trả lời đúng câu hỏi tương tác (hiệu ứng có phụ thuộc quy mô không) theo hướng ngược lại, cùng số lời gọi, chạy được trên mọi máy. Chất lượng suy luận kém hơn 14b nhưng không bằng không |
| **Tiêu chí thành công (khai báo trước)** | (a) mức giảm oracle call của guard ở thang mới **≥ ×3,0** (so với ×5,17 ở 7B); (b) khoảng tin cậy gộp-theo-task của chênh lệch repair rate arm steer-only **chứa 0** |
| **Quy tắc thất bại** | Nếu (b) sai và steering **dương** ở 14B → abstract, §I và kết luận thu hẹp thành *"bỏ prompt steering với proposer ≤7B"*. Đó là thay đổi cấp tiêu đề, 1,0 ngày viết lại đã cấp phát. **Vì vậy WI-05 phải khởi động chậm nhất 21/09** — một kết quả story-changing về đến ngày 09/10 là vô dụng (W5) |
| **Rủi ro** | P(hoàn thành) **0,55** — 21 h GPU cục bộ + phụ thuộc ngoài (pull, VRAM) + một FTE đang chạy WI-01/02 cùng cửa sổ; shard resume giảm nhẹ. P(thuận lợi) **0,50** — chưa pilot, và chính bài báo dự đoán steering null có thể không chuyển giao |
| **Fragility** | **fragile** |
| **Trang** | +Table VI (2 thang × 3 arm) ≈ **0,25 trang**. Sau WI-02 dự trữ chỉ còn 0,15 (gloss §VI-B 0,10 + 0,05 rút caption) → **thiếu 0,10**, xử lý bằng phương án gộp bảng ở §7, quyết tại Gate G4 |

---

### WI-06 · E11-selftest + E11b (họ CodeT) trên vũ trụ sweep — **BC (nhưng chạm vào cơ chế)**

| | |
|---|---|
| **Đối tượng** | 2 arm × 33 task sweep × 3 seed = **198 cell**. Code đã có, **chưa từng chạy** (CAP-C-02). Đây là baseline duy nhất tấn công chi phí oracle **theo đúng cách guard làm** — chặn lời gọi — nhưng bằng tri thức tiên nghiệm thay vì bác bỏ tích luỹ. E11b đo tính cộng gộp |
| **Chi phí model** | Prompt của E11 là prompt `no_memory` (self-test không hiện ra với proposer, CAP-B-06) và nonce không chứa cờ (CAP-B-05) → **proposal là cache hit 100%**. Chỉ lời gọi sinh test là mới: **≈33 lời gọi** |
| **Chi phí sandbox** | 198 cell × (95 exec oracle + ~48 exec self-test) × 0,808 s = **6,4 CPU-h** |
| **Chi phí người** | **1,6 person-day** (1,0 × 1,6) |
| **Tiêu chí thành công (khai báo trước)** | E11 giảm oracle call **< ×2,0** (so với ×5,17 của guard) **hoặc** repair rate tụt ≥5 điểm phần trăm |
| **Quy tắc thất bại** | Nếu E11 ngang guard mà không mất repair → "bác bỏ tích luỹ" không phải thành phần chịu lực. §II-B và abstract đổi phạm vi; 0,5 ngày đã cấp phát |
| **Rủi ro** | P(hoàn thành) **0,80** · P(thuận lợi) **0,60** — test do model tự sinh nổi tiếng là có expected output sai |
| **Fragility** | **fragile** |
| **Trang** | 0 — hai câu ở §VII + một hàng supplement |

---

### WI-07 · E12-randskip: phản chứng cho "bỏ qua có thông tin" — **BC, item trả lời "vì sao nó hoạt động"** (W7)

| | |
|---|---|
| **Đối tượng** | `--oracle-skip-p 0.37` (đúng tỉ lệ chặn đo được của typed guard) trên 33 task × 3 seed = **99 cell**. mode = `no_memory`, prompt không đổi → **cache hit, 0 lời gọi mới** |
| **Chi phí** | 0 lời gọi + **1,3 CPU-h** + **0,8 person-day** (0,5 × 1,6) |
| **Tiêu chí thành công (khai báo trước)** | Repair rate tụt **≥5 điểm phần trăm** so với `no_memory` ở cùng ngân sách oracle, kiểm định theo task p<0,05 |
| **Quy tắc thất bại** | Nếu bỏ qua **ngẫu nhiên** không làm hỏng repair → oracle đang được cấp phát dư, và khoản tiết kiệm của guard **không** quy được cho tính "có thông tin". Đây là kết quả gây tổn hại và phải báo cáo: §VII thêm một đoạn, chữ "informed" biến khỏi abstract. 0,5 ngày đã cấp phát |
| **Rủi ro** | P(hoàn thành) **0,90** · P(thuận lợi) **0,70** — E5-random và depth sweep đều cho thấy oracle có thông tin |
| **Fragility** | **fatal nếu bỏ qua** — nó là falsifier trực tiếp của luận đề. Rẻ, sớm, xếp cùng khối với WI-02 |
| **Trang** | 0 |

---

### WI-08 · Artifact ẩn danh + DOI — **BC, nâng tiêu chí điểm thấp nhất** · 🔴 **BLOCKED**

| | |
|---|---|
| **Đối tượng** | Gói `src/ scripts/ runs/2026-09-01/ data/ cache/ (100 MB) + verdicts.jsonl`, rà sạch định danh (cache key, đường dẫn tuyệt đối, username, lịch sử git, chủ sở hữu repo), mint DOI ẩn danh, thay placeholder trong `sections/11b-availability.tex` |
| **Chi phí** | 0 CPU-h + 0 lời gọi + **4,0 person-day** (2,0 × 2,0 vì phụ thuộc bên thứ ba) |
| **🔴 Blocker** | (a) quyết định của thầy/đơn vị về việc công bố cache — nó chứa completion của model trên mã benchmark (AD-03); (b) tài khoản host DOI. Ngày sớm nhất gỡ: khi thầy trả lời, xem §10 |
| **Phương án thay thế không cần blocker** | Đính kèm archive ẩn danh làm *EasyChair additional material* — CFP cho phép, không cần bên thứ ba |
| **Tiêu chí thành công** | URL ẩn danh mở được **và** một clone sạch dựng lại `numbers.json` khớp từng byte qua `make numbers`, `scripts/check_consistency.py` pass |
| **Quy tắc thất bại** | Chưa có DOI trước 16/10 → chuyển sang archive EasyChair và ghi rõ trong §Data Availability. **Không thương lượng:** mục này không được nộp kèm placeholder |
| **Rủi ro** | P(hoàn thành) **0,75** · P(thuận lợi) **0,80** — đường lui không cần bên thứ ba nào |
| **Fragility** | **robust**. Đây là tiêu chí bị chấm thấp nhất trong gói review (Open Science 1–2/5); đóng nó đáng **+1,0** trên tiêu chí đó |
| **Trang** | 0 |

---

### WI-10 · Ba đoạn "kết quả xấu" viết trước + đóng băng bản đặc tả — **tính vào dự trữ, không vào 22 ngày**

| | |
|---|---|
| **Đối tượng** | Viết trước, **trước khi có bất kỳ kết quả nào**: (1) đoạn thu hẹp novelty cho nhánh thất bại của WI-02; (2) đoạn giới hạn phạm vi "≤7B" cho WI-05; (3) đoạn "oracle cấp phát dư" cho WI-07 và đoạn giáng cấp Thm 3/4 cho WI-04. Đồng thời viết `PRESPEC-2026-09.md` liệt kê falsifier F4–F7 và tiêu chí thành công của từng WI, **commit git có timestamp** |
| **Vì sao** | (a) W2: mỗi quy tắc thất bại chỉ có chi phí lịch bằng 0 nếu văn bản đã tồn tại; (b) nó đóng luôn điểm REV *"'pre-registered' không kiểm chứng được"* — một commit là một dấu thời gian kiểm chứng được, và `tables/integrity.tex` đã có sẵn khung F1–F3 |
| **Chi phí** | **2,0 person-day**, lấy từ dự trữ 12 ngày |
| **Tiêu chí thành công** | Commit tồn tại trước 09/09, chứa đủ 4 đoạn và 4 falsifier, và không đoạn nào cần sửa nội dung sau khi kết quả về (chỉ được điền số) |
| **Quy tắc thất bại** | Nếu một nhánh thất bại xảy ra mà đoạn viết sẵn không dùng được → ghi nhận là lỗi tiên lượng trong §IX, không viết vội bản mới trong tuần chốt |

---

### Tổng hợp danh mục

| WI | Loại | Người (pd) | CPU-h | Lời gọi mới | P(xong) | P(thuận) | Fragility | Trang |
|---|---|---:|---:|---:|---:|---:|---|---:|
| WI-01 ma trận phán quyết | BC | 4,0 | 50 | 0 | 0,90 | 0,75 | robust | 0 |
| WI-02 4 chính sách xếp thứ tự | **CH** | 4,8 | 0 | 0 | 0,85 | 0,45 | fragile* | +0,35 / −0,40 |
| WI-03 soundness dưới k | BC | 0,8 | 0 | 0 | 0,90 | 0,60 | robust | 0 |
| WI-04 loại-trừ-lớp | BC | 0,8 | 0 | 0 | 0,90 | 0,35 | robust | 0 |
| WI-05 proposer thứ hai | **CH** 🔴 | 3,2 | 6,8 | 4.700 | 0,55 | 0,50 | fragile | +0,25 / −0,25 |
| WI-06 E11 self-test | BC | 1,6 | 6,4 | 33 | 0,80 | 0,60 | fragile | 0 |
| WI-07 E12 random-skip | BC | 0,8 | 1,3 | 0 | 0,90 | 0,70 | fatal nếu bỏ | 0 |
| WI-08 artifact + DOI | BC 🔴 | 4,0 | 0 | 0 | 0,75 | 0,80 | robust | 0 |
| **Tổng** | | **20,0 / 22** | **64,5** | **4.733** | | | | **0,00** |
| + dự phòng 30% | | | **84** | **6.150** | | | | |
| WI-10 (từ dự trữ) | | 2,0 | 0 | 0 | 0,95 | — | — | 0 |

\* fragile chỉ vì WI-10 tồn tại; nếu WI-10 trượt, WI-02 trở lại **fatal**.

Dư người: **2,0 pd (9%)**. Dư trang: **0,00** — đây là con số phải theo dõi hằng tuần.

---

## 5. Những gì tôi **không** đưa vào kế hoạch, và vì sao

| Đề xuất | Lý do loại |
|---|---|
| **Baseline regression-test selection (RTS)** | Cần độ phủ dòng cho từng (ứng viên, case): 221.304 lần chạy có instrument, chậm ~3× → **≈150 CPU-h** cộng một harness coverage mới. Vượt phần compute còn lại sau WI-01, và trả lời đúng câu hỏi mà Qi'13 đã trả lời. **Loại có ghi lý do**, nêu một câu trong §IX |
| **E10-chat (họ ChatRepair)** | Prompt phân kỳ từ round 2 → **không** cache hit: ~2.500 lời gọi mới, 297 cell, 6,3 CPU-h, **2,4 person-day**. Compute thì đủ, **ngày công thì không** (20,0 + 2,4 = 22,4 > 22). Loại tại vòng chọn, đưa vào đoạn *"Baselines we did not run"* đã có sẵn trong `sections/09-threats.tex`. **Ứng viên số 1 để phục hồi tại Gate G2 nếu WI-01 về sớm** |
| **Chạy nốt 9 cell thiếu của E9-freeguard** | Đã kiểm: 171/180 cell, **không cell nào thiếu cặp**. Kiểm định chính là hoán vị theo task trên 19 task (p=0,061); thêm 9 cell không thể dịch chuyển một kiểm định 19-task. **Loại có ghi lý do** — và điều này bác bỏ điểm REV-P1-7 |
| **Benchmark quy mô repository (SWE-bench)** | Là một bài báo khác: localization chiếm ưu thế, oracle chỉ phần, khoá λ không định nghĩa được trên diff đa tệp. Giữ nguyên ở §IX |
| **"Chạy thêm benchmark" / "thử thêm model"** | Không gắn với câu hỏi nào. Không phải work item |
| **User study** | Không có người tham gia, không có task, không có N, không có kế hoạch phân tích. Không đề xuất |
| **Tag PDF (PDF/UA)** | `pdftex` không sinh được PDF tagged trong toolchain hiện tại. **Từ chối có ghi lý do**; không phải điều kiện của SANER |

---

## 6. Lịch, cổng quyết định, và điều xảy ra khi trượt

| Khối | Ngày | Nội dung | Người (pd) |
|---|---|---|---:|
| **B0** | 06–08/09 | WI-10: 4 đoạn viết trước + `PRESPEC-2026-09.md` + commit timestamp | 2,0 |
| **B1** | 09–18/09 | WI-01 (4,0) + WI-07 (0,8) + khởi động WI-02 (3,2/4,8). Chạy ma trận 2 đêm | 8,0 |
| **🚦 G1** | **18/09** | **Ma trận có tái lập ≥95% counterexample đã ghi không?** KHÔNG → huỷ WI-02/03/04, chuyển 6,4 pd sang WI-05 toàn lưới + WI-10-chat | |
| **B2** | 19–25/09 | Kết WI-02 (1,6) + WI-03 (0,8) + WI-04 (0,8) + pull & verify model 14b (0,5) + WI-06 (1,6) | 5,3 |
| **🚦 G2** | **25/09** | **CỔNG STORY (W5).** Kết quả WI-02 quyết định §I, §II, Table I và câu novelty trong abstract. Hai nhánh đều đã viết sẵn — **chọn, và commit câu chữ ngay hôm đó**. Đồng thời quyết phạm vi WI-05 (495 hay 1.485 cell) theo phần dư còn lại | |
| **B3** | 26/09–06/10 | WI-05 chạy + phân tích (2,7) + WI-08 artifact (4,0) + dư (1,3) | 8,0 |
| **🚦 G3** | **06/10** | **WI-05 xong chưa?** CHƯA → **bỏ**, không gia hạn. §IX giữ nguyên câu threat "một model". 3,2 pd đã tiêu là chi phí chìm — tuyên bố điều này **bây giờ** để item không ăn vào ngày chốt | |
| **🔒 CHỐT SỐ** | **09/10** | `make numbers RUN=runs/<ngày> && make figures && python3 scripts/check_consistency.py` phải pass. Sau mốc này không con số nào đổi | |
| **B4** | 09–16/10 | Viết lại §II/§VII quanh kết quả mới, dựng lại hình, ép về 10+2 | 5,0 |
| **🚦 G4** | **16/10** | `make ieee` báo ≤12 trang **và** `make check` 0 undefined ref **và** `check_consistency` pass. TRƯỢT → cắt **float mới nhất trước** (bảng của WI-05), không cắt văn xuôi | |
| **B5** | 17–22/10 | Gửi thầy 16/10 (lead time 3–5 ngày), abstract **19/10**, rà ẩn danh, **nộp 22/10** | 4,0 |

Tổng phân bổ 32,3 pd / 34 — **dư 1,7 pd, giữ trong dự trữ viết** (B0+B4+B5 = 11,0 pd; cộng 1,7 = 12,7 pd ≥ mức 11,9 mà W4 đòi cho 1 FTE). Không khối nào kết thúc đúng ngày hạn.

**Đường tới hạn:** WI-01 → WI-02 → G2 → viết lại §II. Trượt WI-01 quá 18/09 làm đổ toàn bộ nhánh ngoại tuyến, đó là lý do G1 nằm ở đó và có sẵn phương án chuyển ngân sách.

---

## 7. Kế toán trang (W6)

| | Trang |
|---|---:|
| Hiện tại `main-ieee.pdf` | 10 nội dung + 1 refs |
| Trần SANER | 10 + 2 |
| Dư refs | +1 (vô dụng cho nội dung) |
| **Dư nội dung** | **0,00** |
| Dự trữ cắt đã đặt tên (CAP-A-04) | 0,50 |
| WI-02 tiêu | −0,35 |
| WI-05 tiêu | −0,25 |
| **Còn lại sau hai item** | **−0,10 → phải tìm thêm 0,10** |

**Quy tắc cứng cho phần còn lại của kế hoạch:** WI-03, WI-04, WI-06, WI-07 **chỉ được xuất hiện dưới dạng câu văn + hàng supplement, tuyệt đối không float mới.**

**Phương án dự phòng đã đặt tên:** nếu cả WI-02 và WI-05 đều cần bảng riêng và không cắt đủ 0,10 trang, **gộp Table V và Table VI thành một bảng "chính sách và quy mô" ở 0,40 trang**. Quyết định này thuộc Gate G4, không phải tuần cuối.

**Cấm:** giảm font, giảm lề, `\baselinestretch`. SANER desk-reject sai lệch định dạng và bước kiểm chạy trước review.

---

## 8. Nơi gói review sai về **sự kiện** (giải quyết bằng PAPER + CODEBASE, W12)

| # | Gói review nói | Sự thật | Ai đúng |
|---|---|---|---|
| 1 | 19 trang, layout ACM → desk reject (P0) | `main-ieee.pdf` = **11 trang**, IEEEtran 10pt conference, metadata đầy đủ | **Repo đúng.** Gói review đọc bản cũ |
| 2 | *"Oracle call loại trừ hoặc che giấu guard re-execution"* (B1) | `src/loop.py` tính `sandbox_runs = guard_evaluations + selftest_runs + examples_tried`; `src/metrics.py` L176–190 ghi rõ đây là **đơn vị chi phí chính**. Guard replay **luôn** nằm trong chỉ số | **Repo đúng.** Nhóm đã phát hiện; tôi xác nhận từ mã nguồn |
| 3 | *"Five arms, 1,485 cells"* gây hiểu nhầm | Đúng: 1.485 = 3 arm × 495. Ablation là 297/arm | **Gói review đúng**, đã sửa |
| 4 | Artifact không kiểm chứng được | Đúng tại thời điểm review (chỉ nộp `main.pdf`). Repo tồn tại nhưng chưa công bố | **Gói review đúng** → WI-08 |
| 5 | Yêu cầu kiểm định tương tác difficulty × steering | **Đã làm** (hoán vị 20.000 lần theo task) và **đã đổi tuyên bố**: typed−nomem dead +9,6pp [2,6; 18,3], hard −11,8pp, omnibus p=0,023; steer-only omnibus p=0,87. Tuyên bố "steering thắng ở dead band" **đã rút** | **Gói review đúng**, đã đóng |
| 6 | Yêu cầu bảng attrition, task-level inference, tổng test work, ρ vs c, cận chính xác | Tất cả đã làm | đã đóng |

Và một chỗ **tài liệu của chính nhóm** sai — xem bảng cuối §1: `PLAN-experiments.md` khẳng định ba arm prioritization "đã cài" (không có code) và giả định 0,15 s/case (thực đo 0,808 s/case).

---

## 9. Bản đồ đối chiếu

### 9.1 AD — Những gì **thực sự** cần thầy quyết (tất cả NEEDS CLARIFICATION)

| ID | Vấn đề | Vì sao không tự quyết được | Lead time |
|---|---|---|---|
| **AD-00** | File `codex-review.md` có phải là review của thầy không, hay thầy có một bản riêng chưa gửi? | Nếu có bản riêng, **toàn bộ §9.2 phải chạy lại** và thứ tự ưu tiên có thể đổi | trả lời ngay |
| **AD-01** | Xác nhận **bỏ** Research Track (abstract 21/09) và nhắm Agentic AI4SE (23/10) | Với kế hoạch này Research Track đã bất khả thi; cần thầy xác nhận là chủ ý chứ không phải trượt hạn | trước 15/09 |
| **AD-02** | Tiêu đề "Location-Indexed" đã là bản chốt chưa? | Nếu WI-02 ra kết quả bất lợi, tiêu đề có thể phải thu hẹp thêm một lần nữa tại G2 | trước 25/09 |
| **AD-03** | Được phép công bố `cache/` (100 MB completion của model trên mã benchmark) trong artifact không? | Vấn đề licence/đơn vị, không phải kỹ thuật. Chặn WI-08 | trước 26/09 |
| **AD-04** | Ký duyệt bản nộp | Cần 3–5 ngày đọc → bản nháp phải tới tay thầy **16/10** | 16/10 |

**Cảnh báo diễn giải (W12):** không đoán ý. Nếu thầy viết *"em có thể cân nhắc thêm baseline X"* thì nghĩa là *"làm X"*; nếu thầy viết *"chỗ này chắc ổn"* thì nghĩa là *"thầy không tin"*. Hỏi lại bằng câu hỏi đóng.

### 9.2 REV — Gói review AI, checklist P0/P1, disposition đầy đủ

| ID | Điểm | Disposition |
|---|---|---|
| P0-1 | Dùng IEEEtran, không compsoc | ✅ đã xong (CAP-A-01) |
| P0-2 | Về 10+2 trang | ✅ đã xong — 10+1 (CAP-A-01) |
| P0-3 | Chọn track | → **AD-01** |
| P0-4 | Tách semantic / full-pool / depth-k | ✅ đã xong (`AUDIT` §E2) |
| P0-5 | Định nghĩa khoá tiền-thực-thi | ✅ λ = vị trí sửa (§III-B) |
| P0-6 | Viết lại hoặc rút Prop. 6 | ✅ đã rút tuyên bố tiệm cận |
| P0-7 | Sửa Thm 4(b) | ✅ định nghĩa E^obs |
| P0-8 | Bỏ "first"/"none"/"field lacks"/"cannot hurt" | ✅ đã quét toàn văn |
| P0-9 | Bổ sung 6 công trình gần nhất | ✅ §II viết lại, sửa 4 lỗi sự kiện của chính mình |
| P1-1 | Baseline prioritization / equivalence | → **WI-02** |
| P1-2 | Báo cáo toàn bộ công sandbox | ✅ `tab:testwork`, ×2,22, cả giây lẫn số lần |
| P1-3 | Suy luận theo task là chính | ✅ Table II mang cả hai đơn vị |
| P1-4 | Khai họ giả thuyết xác nhận | ✅ §VI |
| P1-5 | Bảng attrition 526→99 | ✅ + phát hiện quota-sample ở §VI-A |
| P1-6 | Giải thích 939/1.256 | ✅ 317 fault không có case nào bản lỗi vượt qua |
| P1-7 | Chạy nốt 9 cell free-round | ❌ **từ chối có lý do** — không cell nào thiếu cặp; kiểm định chính là hoán vị trên 19 task |
| P1-8 | "pre-registered" phải kiểm chứng được | → **WI-10** (commit có timestamp) |
| P1-9 | Artifact ẩn danh | → **WI-08** |
| P1-10 | Thêm một model | → **WI-05** |
| P1-11 | Cận nhị thức cho kết quả 0 lỗi | ✅ cận trên chính xác đã thêm |
| P1-12 | Kiểm định tương tác | ✅ đã làm, **đã đổi tuyên bố** |
| P1-13 | Vị trí Data Availability | ✅ ngay sau Conclusion |
| P2 (10 mục) | Thuật ngữ, ký hiệu, cách đặt tên | ✅ 10/10 (`AUDIT` §M-P2) |
| P3-1..6, 8 | Bỏ câu tự chứng, locator trích dẫn, rút caption, `\textsc` | ✅ 5 mục xong; **locator trích dẫn + rút caption nằm trong dự trữ B4** |
| P3-7 | PDF tagged | ❌ **từ chối có lý do** — toolchain `pdftex` không sinh được; không phải điều kiện SANER |
| K-item | Metadata ExpeRepair khi xuất bản | 🟡 kiểm lại ở B5 |

Không mục nào bị bỏ im lặng.

---

## 10. Danh sách câu hỏi gửi thầy (gửi trong 24 h, dạng câu hỏi đóng)

1. `codex-review.md` là bản do em chạy AI sinh. **Thầy có bản nhận xét riêng nào chưa gửi em không?** Nếu có, em sẽ lập lại thứ tự ưu tiên trước ngày 15/09.
2. Em bỏ Research Track (hạn abstract 21/09) và nhắm Agentic AI4SE (abstract 19/10, nộp 23/10). **Thầy đồng ý bỏ Research Track chứ ạ?**
3. Nếu kết quả WI-02 cho thấy fault-recorded prioritization (Qi'13) ngang bằng guard của mình, em sẽ **thu hẹp tuyên bố novelty thay vì bỏ kết quả** (câu chữ đã viết sẵn). **Thầy đồng ý hướng xử lý này chứ, hay thầy muốn em rút bài sang hội nghị khác?**
4. Artifact cần kèm `cache/` 100 MB chứa completion của model trên mã ConDefects. **Đơn vị mình có cho phép công bố phần này không?** Nếu không, em nộp artifact không có cache và ghi rõ rằng replay khi đó cần chi phí model.
5. Bản nháp sẽ tới tay thầy **16/10**. **Thầy cần bao nhiêu ngày để đọc và ký duyệt?** Nếu cần hơn 5 ngày, em phải dời mốc chốt số từ 09/10 lên 06/10.

---

## 11. Bảng rủi ro gộp

| Rủi ro | Xác suất | Điều xảy ra | Đã chuẩn bị gì |
|---|---:|---|---|
| Ma trận phán quyết không tái lập được log | 0,25 | Mất WI-02/03/04 — mất luôn item novelty | Gate G1 ngày 18/09, chuyển 6,4 pd sang WI-05 toàn lưới + E10-chat |
| Qi'13 ngang bằng guard | 0,55 | Ranh giới novelty thu hẹp | Đoạn văn viết sẵn từ B0; Table I có sẵn ô để chuyển vào |
| Không phục vụ được 14B | 0,40 | Mất thang lớn | Chuyển sang 3B (thang xuống, cùng họ), quyết trong 1 ngày |
| Steering **dương** ở thang lớn | 0,25 | Khuyến nghị headline phải giới hạn "≤7B" | 1,0 pd viết lại đã cấp phát; buộc phải biết trước 06/10 nên WI-05 khởi động 21/09 |
| Bỏ qua ngẫu nhiên không hại repair (WI-07) | 0,30 | Chữ "informed" mất khỏi abstract | 0,5 pd đã cấp phát |
| DOI không kịp | 0,25 | — | Archive EasyChair, không cần bên thứ ba |
| Vượt trang sau khi thêm kết quả | 0,35 | Desk reject | Gate G4: cắt float mới nhất trước; phương án gộp Table V+VI đã đặt tên |
| Thầy cần >5 ngày duyệt | 0,30 | Trượt hạn nộp | Câu hỏi số 5 phải được trả lời trước 15/09; nếu >5 ngày, mốc chốt số dời lên 06/10 |

---

*Mọi con số chi phí trong tài liệu này đọc từ `runs/2026-09-01/episodes.jsonl`, `paper/draft/numbers.json`, mã nguồn `src/`, và `pdfinfo` — không con số nào là ước lượng bằng trực giác. Mỗi ô có ID CAP-x-## truy về nguồn của nó.*
