#!/bin/bash
# Chay pipeline end-to-end tren LLM local (Ollama), khong ton tien.
#   bash scripts/smoke_local.sh
#
# Muc dich: bat loi harness - flag sai, oracle hong, prompt vo - truoc khi tieu
# tien that. KHONG phai mot arm cua thi nghiem: qwen2.5-coder:7b co pi khac han
# model duoc bao cao, va o ~16 tok/s thi ca grid mat hon mot thang wall clock.
#
# Ghi vao data/calls_smoke.jsonl + data/episodes_smoke.jsonl, khong dung vao so
# chinh ma llm.spent() va scripts/watch_eval.sh doc.
set -euo pipefail

MODEL=${SMOKE_MODEL:-cegmem-qwen2.5-coder-7b}
CTX=32768
PY=$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)

command -v ollama >/dev/null || { echo "chua cai ollama"; exit 1; }
curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null \
  || { echo "ollama chua chay: \`ollama serve\`"; exit 1; }

# Model phai duoc BUILD tu ollama/Modelfile, khong phai pull. Ban pull thang
# chay o num_ctx 4096 va cat bot prompt trong im lang - xem src/llm.py.
if ! ollama list | grep -q "^${MODEL}"; then
  echo ">>> tao $MODEL tu ollama/Modelfile"
  ollama create "$MODEL" -f ollama/Modelfile
fi

export LLM_BASE_URL=http://localhost:11434/v1
export LLM_API_KEY=ollama
export MODEL LLM_CONTEXT_TOKENS=$CTX
export PRICE_IN_PER_MTOK=0 PRICE_OUT_PER_MTOK=0
export CALLS_LOG=data/calls_smoke.jsonl

echo ">>> 1/3 client"
$PY -m src.llm

# Kiem tra num_ctx that su dang duoc phuc vu. Guard trong src/llm.py chi so voi
# LLM_CONTEXT_TOKENS ma ta tu khai; day moi la con so cua server.
SERVED=$(curl -s --max-time 5 http://localhost:11434/api/ps \
  | $PY -c "import sys,json;m=json.load(sys.stdin)['models'];print(max([x.get('context_length',0) for x in m]+[0]))")
echo ">>> 2/3 context server dang phuc vu: $SERVED"
[ "$SERVED" -ge "$CTX" ] || { echo "MONG DOI >= $CTX. ollama/Modelfile chua co hieu luc."; exit 1; }

echo ">>> 3/3 grid nho (2 task x 3 mode x 1 seed, budget 3)"
$PY scripts/run_eval.py \
  --programs abc226_a/46153240 abc239_a/45807134 \
  --modes no_memory untyped typed --seeds 1 --budget 3 \
  --episodes-path data/episodes_smoke.jsonl

echo
echo "OK. Chay lai lenh nay: moi cell phai in 'already complete, skipping'."
