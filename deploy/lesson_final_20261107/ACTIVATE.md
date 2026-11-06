# Final lesson review

Not an activation. Do not copy this onto `/opt/tfo/live/thf` or `/opt/tfo/dash/base`. Do not restart `tfo-thf-tick`. `--apply` without `--staging` is refused. A production root is refused even with `--staging`. That refusal is unchanged.

Prior archive `629241d458c7fe4ae9a3806510861573b9f93b297cd6f6fbb677d84b2cbaa6d6` stays as the verified caller package. This archive adds the captured sampler and chat-task modules.

`veteran_desk/thf_shadow_sampler.py` base `a779a30626e765e5d6351ab851399240154802a54cdb955c309e8f97d4ad28ef` is checked and not copied. `veteran_desk/thf_chat_tasks.py` base `3ebeda8439b66b7eafa28555185670d1b38dfb804202ed4f62399e4252ca581f` is the live and dash file from capture `9046b991a889fb9ac50353c75e47dafebc2562cfd837cd42064598b7b8ea52af`.

`GENUINE_VERIFIERS` is empty. `AUTHORITY.rule` is unchanged. The book must remain `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`.

```bash
python3 deploy/lesson_final_20261107/install.py --root /path/to/staging-copy
```
