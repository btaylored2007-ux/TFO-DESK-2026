#!/bin/bash
export PATH=/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin
cd ~/Applications/MultiVenueDesk; L=logs/deploy_fastllm4.log
grep -q tfo_fastllm4 veteran_desk/local_llm.py && { echo already >> $L; launchctl remove com.tfo.fastllm4-deploy-once; exit 0; }
for i in $(seq 1 400); do M=$((10#$(date +%M))); Q=$((M%15))
  if [ ! -s tools/deploy.lock ] && [ $Q -ge 4 ] && [ $Q -le 5 ]; then break; fi; sleep 15; done
if [ "$(/sbin/md5 -q veteran_desk/llm_backend.py tools/queue_answer_daemon.py veteran_desk/local_llm.py)" != "$(cat staging/fastllm4/live.md5)" ]; then echo "ABORT: live changed since staging" >> $L; launchctl remove com.tfo.fastllm4-deploy-once; exit 1; fi
tools/safe_deploy.sh --tag fastllm4 --note "tfo_fastllm4: daemon skips items whose direct call already failed (fast_failed flag -> box routine, no 2nd 120s try); cursor-agent cap live-tunable via state/llm_cap.json (PAPER)" staging/fastllm4/llm_backend.py:veteran_desk/llm_backend.py staging/fastllm4/queue_answer_daemon.py:tools/queue_answer_daemon.py staging/fastllm4/local_llm.py:veteran_desk/local_llm.py >> $L 2>&1
echo "EXIT=$?" >> $L; launchctl kickstart -k gui/$(id -u)/com.tfo.queue-answerer >> $L 2>&1
launchctl remove com.tfo.fastllm4-deploy-once
