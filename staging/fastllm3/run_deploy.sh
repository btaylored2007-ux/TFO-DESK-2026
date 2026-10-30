#!/bin/bash
export PATH=/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin
cd ~/Applications/MultiVenueDesk; L=logs/deploy_fastllm3.log
for i in $(seq 1 400); do M=$((10#$(date +%M))); Q=$((M%15)); S=$((10#$(date +%S)))
  if [ ! -s tools/deploy.lock ] && [ $Q -ge 4 ] && [ $Q -le 5 ]; then break; fi; sleep 15; done
# rebase guard: staged must be built on current live local_llm
grep -q "tfo_fastllm3" veteran_desk/local_llm.py && { echo "already deployed" >> $L; launchctl remove com.tfo.fastllm3-deploy-once; exit 0; }
tools/safe_deploy.sh --tag fastllm3 --note "tfo_fastllm3: Mac-wide file-lock cap of 3 concurrent cursor-agent calls in local_llm.ask; waiters queue within the 120s budget, then fall back to llm_queue (PAPER)" staging/fastllm3/local_llm.py:veteran_desk/local_llm.py >> $L 2>&1
echo "EXIT=$?" >> $L; [ -n "$(launchctl list | grep queue-answerer)" ] && launchctl kickstart -k gui/$(id -u)/com.tfo.queue-answerer >> $L 2>&1
launchctl remove com.tfo.fastllm3-deploy-once
