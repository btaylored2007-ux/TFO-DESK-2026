"""Attribute equity rating headlines to the covered issuer, rather than the broker publishing the rating.
Pure local text/metadata parsing. No network requests and no trading authority.
"""
import re
BROKERS={
"WFC":r"wells\s+fargo(?:\s+securities)?","BAC":r"(?:bofa(?:\s+securities)?|bank\s+of\s+america(?:\s+securities)?)",
"JPM":r"j\.?\s*p\.?\s*morgan(?:\s+chase)?","GS":r"goldman\s+sachs","MS":r"morgan\s+stanley",
"C":r"citi(?:group)?","BCS":r"barclays","UBS":r"ubs","DB":r"deutsche\s+bank",
"RY":r"(?:rbc(?:\s+capital\s+markets)?|royal\s+bank\s+of\s+canada)","TD":r"td\s+(?:cowen|securities)",
"BMO":r"bmo(?:\s+capital\s+markets)?","BNP.PA":r"bnp\s+paribas","HSBC":r"hsbc","JEF":r"jefferies",
"MFG":r"mizuho","SMFG":r"smbc(?:\s+nikko)?","RJF":r"raymond\s+james","SF":r"(?:stifel|kbw|keefe\s+bruyette(?:\s*&?\s*woods)?)",
"PIPR":r"piper\s+sandler","Baird":r"(?:robert\s+w\.?\s+)?baird","Oppenheimer":r"oppenheimer",
"Rosenblatt":r"rosenblatt","Needham":r"needham","Truist":r"truist(?:\s+securities)?"}
ACTION=re.compile(r"^(?:maintains?|reiterates?|reaffirms?|upgrades?|downgrades?|initiates?|resumes?|reinstates?|assumes?|starts?|raises?|lowers?|cuts?|boosts?|lifts?|hikes?|trims?)\b",re.I)
def normalized_tickers(raw):
    if raw is None:return []
    if isinstance(raw,str):raw=re.split(r"[,;\s]+",raw)
    if isinstance(raw,dict):raw=[raw]
    out=[]
    for value in raw if isinstance(raw,(list,tuple,set)) else []:
        if isinstance(value,dict):value=value.get("ticker") or value.get("symbol") or value.get("code")
        value=str(value or "").upper().strip()
        if value.endswith(".US"):value=value[:-3]
        if re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=:_-]{0,29}",value) and value not in out:out.append(value)
    return out
def subject(title,patterns=None,tickers=()):
    text=re.sub(r"\s+"," ",str(title or "")).strip()
    text=re.sub(r"^(?:update\s*\d*|breaking|brief)\s*:\s*","",text,flags=re.I)
    broker=None;rest=None
    for symbol,pattern in BROKERS.items():
        found=re.match(r"^("+pattern+r")\b[,:]?\s+",text,re.I)
        if found and ACTION.match(text[found.end():]):
            broker=symbol;rest=text[found.end():];break
    supplied=normalized_tickers(tickers)
    if broker is None:return dict(broker_rating=False,subject_text=text,tickers=supplied,attribution="headline and provider metadata")
    # "Maintains Hold on X", "Downgrades X to Sell", or "Raises X Price Target".
    on=re.search(r"\b(?:on|of)\s+(.+)",rest,re.I)
    covered=on.group(1) if on else ACTION.sub("",rest,count=1).strip()
    covered=re.split(r",|;|\s+(?:to|from|price\s+target|shares?\b|stock\b)",covered,maxsplit=1,flags=re.I)[0].strip()
    covered=re.sub(r"^(?:coverage|a|an|at|its|rating|ratings|price\s+target|coverage\s+with)\s+","",covered,flags=re.I)
    # Some action syntax cannot identify a subject. Retain it as general wire news.
    generic=bool(re.match(r"^(?:outperform|underperform|overweight|underweight|neutral|buy|hold|sell|equal.weight|coverage|price|target|rating)\b",covered,re.I))
    if generic:covered=""
    assets=[]
    for symbol,pattern in (patterns or {}).items():
        if covered and re.search(pattern,covered,re.I):assets.append(symbol)
    source_brokers=[symbol for symbol,pattern in BROKERS.items() if re.search(r"\b(?:"+pattern+r")\b",text.split(rest,1)[0],re.I)]
    supplied=[symbol for symbol in supplied if symbol not in source_brokers or symbol in assets or (covered and re.search(r"\b"+re.escape(symbol)+r"\b",covered))]
    return dict(broker_rating=True,broker=broker,subject_text=covered,tickers=list(dict.fromkeys(supplied+assets)),attribution="covered issuer; rating broker excluded")
