"""Fast THF provider readiness checks. Unavailable models are advisory failures, never trade gates."""
import os,shutil,socket,time
def reason(name,check_busy=True):
    name={"chatgpt_work":"chatgpt","chatgpt_web":"chatgpt","claude_direct":"claude","deepseek":"deepseek_api"}.get(name,name)
    from veteran_desk import ai_cooldown as CD
    state=CD._load()
    info=state.get(name) or {}
    shared=state.get("grok") or {}
    if name=="seat" and float(shared.get("until") or 0)>time.time():info=shared
    if float(info.get("until") or 0)>time.time():return "%s cooldown until %s"%(info.get("kind") or "provider",info.get("until_wita") or info["until"])
    # Older records had a short clamp even when the provider supplied a dated reset.
    why=str(info.get("why") or "")
    if info.get("kind")=="quota" and CD.dated_reset_secs(why):
        return "quota until the stated reset"
    if name=="chatgpt":
        from veteran_desk import chatgpt_bridge as CG
        health=CG.health()
        if health.get("state")!="logged_in":return "not logged in or bridge unavailable"
        if check_busy and (health.get("busy") or int(health.get("queue") or 0)>0):return "busy; use another provider or the code engine"
    elif name == "supergrok":
        from veteran_desk import supergrok_bridge as SG
        return SG.block_reason()
    elif name in ("grok","seat"):
        from veteran_desk import thf_grok as G
        unavailable=G.reason()
        if unavailable:return unavailable
    elif name=="claude":
        from veteran_desk import claude_brain as CB
        cli=getattr(CB,"CLI",None)
        if isinstance(cli,str) and not os.path.isfile(cli) and not shutil.which(cli):return "CLI unavailable"
    if name=='deepseek_api':
        from veteran_desk import deepseek_bridge as DS
        return DS.reason(check_busy=check_busy)
    return None
