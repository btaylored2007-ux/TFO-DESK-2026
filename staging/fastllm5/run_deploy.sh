#!/bin/bash
export PATH=/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin
cd ~/Applications/MultiVenueDesk; L=logs/deploy_fastllm5.log
grep -q tfo_fastllm5 veteran_desk/desk.py && { echo already >> $L; launchctl remove com.tfo.fastllm5-deploy-once; exit 0; }
for i in $(seq 1 400); do M=$((10#$(date +%M))); Q=$((M%15))
  if [ ! -s tools/deploy.lock ] && [ $Q -ge 4 ] && [ $Q -le 5 ]; then break; fi; sleep 15; done
if [ "$(/sbin/md5 -q veteran_desk/desk.py veteran_desk/vd_engine.py)" != "$(cat staging/fastllm5/live.md5)" ]; then
  echo "live changed -> rebuilding staged files from live" >> $L; python3 staging/fastllm5/apply.py >> $L 2>&1 || { echo "ABORT rebuild failed" >> $L; launchctl remove com.tfo.fastllm5-deploy-once; exit 1; }; fi
tools/safe_deploy.sh --tag fastllm5 --note "tfo_fastllm5 (head trader approval 03:09 WITA): VD seat prompt slim (dedupe redundant instrument fields, 5-sig-fig numbers, compact JSON, sounding-board critiques trimmed to 240 chars; rails/stops/caps/theme text untouched) ~16K->~13.5K, replay 3 seats same decisions; THF vd_engine LLM_TIMEOUT_2L 60->120s (PAPER)" staging/fastllm5/desk.py:veteran_desk/desk.py staging/fastllm5/vd_engine.py:veteran_desk/vd_engine.py >> $L 2>&1
echo "EXIT=$?" >> $L; launchctl remove com.tfo.fastllm5-deploy-once
