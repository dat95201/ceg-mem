#!/bin/bash
# Tien do run_eval.py.  Dung: bash logs/watch_eval.sh [logfile]
LOG=${1:-logs/E1.log}
PID=$(pgrep -f 'run_eval.py' | head -1)
[ ! -f "$LOG" ] && { echo "chua co $LOG"; exit 1; }
TOTAL=$(grep -o '^\[ *[0-9]*/[0-9]*\]' "$LOG" | tail -1 | tr -d '[]' | cut -d/ -f2)
REP=$(grep -c ' - repaired@' "$LOG"); EXH=$(grep -c ' - exhausted' "$LOG")
SKIP=$(grep -c 'already complete, skipping' "$LOG"); DONE=$((REP+EXH+SKIP))
SPENT=$(grep -o 'spent=\$[0-9.]*' "$LOG" | tail -1 | tr -d 'spent=$')
if [ -z "$PID" ]; then
  echo ">>> run_eval DA DUNG"; tail -3 "$LOG"
else
  ET=$(ps -p "$PID" -o etime= | tr -d ' ' | awk -F'[-:]' '{n=NF;s=$n;if(n>1)s+=$(n-1)*60;if(n>2)s+=$(n-2)*3600;if(n>3)s+=$(n-3)*86400;print s}')
  printf 'pid %s | chay %dh%02dm | cell %d/%s\n' "$PID" $((ET/3600)) $(((ET%3600)/60)) "$DONE" "${TOTAL:-?}"
  [ "$DONE" -gt 0 ] && [ -n "$TOTAL" ] && awk -v d="$DONE" -v t="$TOTAL" -v e="$ET" 'BEGIN{
    r=d/(e/3600); printf "nhip %.1f cell/gio | ETA con ~%.1f gio (~%.1f ngay)\n", r, (t-d)/r, (t-d)/r/24}'
fi
if [ $((REP+EXH)) -gt 0 ]; then
  awk -v r="$REP" -v x="$EXH" 'BEGIN{
    printf "\nrepaired %d | exhausted %d  ->  success@B = %.3f", r, x, r/(r+x)
    if(r/(r+x)<0.10) print "   <- QUA THAP: gan het task nam o `dead`, band pi vo nghia (chon A)"
    else if(r/(r+x)>0.70) print "   <- QUA CAO: corpus de hon du kien"
    else print "   <- co trai, band pi kha thi (chon B)"}'
fi
[ -n "$SPENT" ] && echo "TIEN: \$$SPENT / cap $(grep -o '[0-9.]*' <<< $(grep '^BUDGET_USD_CAP' .env))"
echo "--- 3 dong cuoi ---"; tail -3 "$LOG"
