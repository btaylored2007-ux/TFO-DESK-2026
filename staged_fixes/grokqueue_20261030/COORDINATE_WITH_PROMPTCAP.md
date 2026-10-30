# grokqueue <-> promptcap (22:13 WITA 30 Oct)
grokqueue adds a hook in desk._llm right AFTER the SECRET_RX line (block marked `tfo_grokqueue_20261030`).
When backend()=="grok_queue" it returns BEFORE any codex cap -> full prompts to Grok (Brett 22:11).
If you deploy staged_fixes/promptcap_20261030/desk.py, re-base on the live veteran_desk/desk.py first or paste that block in, else Grok routing is lost.
Do NOT trim DATA when veteran_desk.llm_backend.backend()=="grok_queue".
