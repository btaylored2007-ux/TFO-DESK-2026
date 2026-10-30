#!/bin/bash
export PATH=/usr/local/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
cd ~/Applications/MultiVenueDesk
for i in $(seq 1 400); do
  M=$((10#$(date +%M))); Q=$((M%15))
  if [ ! -s tools/deploy.lock ] && [ $Q -ge 4 ] && [ $Q -le 5 ]; then break; fi; sleep 20; done
python3 staging/fastllm/apply_patch.py >> logs/deploy_fastllm2.log 2>&1 || exit 1
tools/safe_deploy.sh --tag fastllm2 --note "tfo_fastllm_20261030: local Grok fast path via Cursor CLI (grok-4.7-low-fast, 120s, 1 quick retry) in llm_backend.grok_call for VD+THF+hunt_clearance; tools/queue_answer_daemon.py (lanes vd3/thf2/other2); compact seat prompts (~150KB->~17KB, SEAT_MEM_MODE summary default)" staging/fastllm/local_llm.py:veteran_desk/local_llm.py staging/fastllm/queue_answer_daemon.py:tools/queue_answer_daemon.py staging/fastllm/llm_backend.py:veteran_desk/llm_backend.py staging/fastllm/desk.py:veteran_desk/desk.py staging/fastllm/llm_queue_cli.py:tools/llm_queue_cli.py >> logs/deploy_fastllm2.log 2>&1
echo "EXIT=$?" >> logs/deploy_fastllm2.log; launchctl remove com.tfo.fastllm-deploy-once
