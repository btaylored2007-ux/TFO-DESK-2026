"""Conservative shared engineering-intent boundary. Pure functions; no I/O.

Classify original requests, never generated plans. Trading instructions and safety
restrictions do not authorize code work. Ambiguous requests stay chat/questions.
This routing boundary does not authorize changes to protected risk controls.
"""
import re

Q_START = re.compile(r"^\s*(is|are|was|were|can|could|what|why|how|when|where|does|do|did|should|will|would|who|which|any|has|have|am)\b", re.I)
STATUS_ONLY = re.compile(r"^\s*(team\s+)?(update|status|report)(\s+(?:me|us))?(\s+(?:on|about|regarding|with)\b.*)?\s*[.!?]?\s*$", re.I)
VERB = r"(?:fix|repair|patch|deploy|update|continue|address|implement|build|rebuild|redesign|rewrite|restore|install|migrate|wire|create|add|change|make|move|remove|rename|replace|re-?enable|switch|set(?:\s+up)?|put|turn)"
ADDRESS = r"(?:(?:Marcus|Simon|Sam|Brett|fixer|team|engineer)\s*[:,]?\s+)?"
IMPERATIVE = re.compile(r"^\s*" + ADDRESS + r"(?:(?:please|pls)\s+)?" + VERB + r"\b", re.I)
REQUEST = re.compile(r"^\s*" + ADDRESS + r"(?:I\s+(?:want|need)\s+(?:you\s+)?to|we\s+need\s+to)\s+" + VERB + r"\b", re.I)
DOMAIN = re.compile(r"\b(?:code|software|engine|dashboard|dash|feed(?:s|[- ]health)?|collector(?:s)?|ingestion|ingest|API|endpoint|upload|download|frontend|backend|router|routing|classifier|website|UI|layout|button|panel|chip|bug|test(?:s|\s+suite)?|deployment|service|worker|queue|cache|caching|database|parser|backtester|function|module|script|configuration|config|desk\s+health|(?:CFO|engineer|admin|trader)\s+seat)\b|\b[\w/.-]+\.(?:py|js|ts|tsx|html|css|sh)\b", re.I)
FAULT = re.compile(r"\b(?:not\s+working|broken|wrong|missing|down|stale|fails?|failing|needs?\s+(?:fixing|repair)|should\s+look)\b", re.I)
NEGATED = re.compile(r"\b(?:not|never|no|without|avoid|mustn't|shouldn't|can't|cannot|don't|doesn't|won't|wouldn't)\b|\b(?:keep|leave)\b.*\b(?:unchanged|intact|as\s+is)\b", re.I)
CONDITIONAL = re.compile(r"\b(?:if|unless|until|hypothetically|suppose|example|quoted?|quotation)\b", re.I)
GENERIC_FIX = re.compile(r"^\s*" + ADDRESS + r"(?:(?:please|pls)\s+)?(?:(?:fix|repair)\s+(?:it|this|that|them|everything|all)|get\s+(?:them|it|this|everything)(?:\s+all)?\s+fixed\s*(?:up)?)\s*[.!?]*$", re.I)
GET_REPAIR = re.compile(r"^\s*" + ADDRESS + r"(?:(?:please|pls)\s+)?get\b.*\b(?:fixed|repaired|working|restored)\b", re.I)
OBSERVATION = re.compile(r"^\s*" + ADDRESS + r"(?:(?:please|pls)\s+)?(?:report|show|tell|check|inspect|explain|review|describe|summari[sz]e|find|list|read|look|research|confirm|verify)\b|\b(?:said|says|reported|reports|quoted|mentioned)\b", re.I)
NO_CODE = re.compile(r"\b(?:no|without|avoid)\s+(?:(?:any|further|more)\s+)?(?:(?:code|software|engineering)\s+)?(?:changes?|edits?|modifications?|patches?|deployments?)\b|\b(?:do\s+not|don't|never|must\s+not)\s+(?:change|edit|modify|patch|fix|deploy|touch)\s+(?:(?:any|the)\s+)?(?:code|software)\b|\b(?:do\s+not|don't|never|must\s+not)\s+(?:deploy|release|apply|publish|execute|implement)\b|\b(?:plan|draft|analysis|proposal)[- ]only\b|\b(?:only|just)\s+(?:a\s+)?(?:plan|draft|analysis|proposal)\b", re.I)
OUTPUT_ONLY = re.compile(r"^\s*" + ADDRESS + r"(?:(?:please|pls)\s+)?(?:make|create|build|add)\s+(?:me\s+)?(?:a|an|the)?\s*(?:list|shortlist|report|summary|diagram|plan|chart|proposal|example)\b", re.I)

def _unquoted(text):
    text = re.sub(r"```[\s\S]*?(?:```|$)", "", text)
    return re.sub(r"\"[^\"]*(?:\"|$)|[“][^”]*(?:[”]|$)|`[^`]*(?:`|$)|(?<!\w)'[^']*'(?!\w)", "", text)

CLAUSES = re.compile(r"(?:[.!?](?=\s|$)|[;\n]|\s+(?:but|however)\s+|\s+(?=without\b)|\s+and\s+(?=(?:do\s+not|don't|never|keep|leave)\b))", re.I)

def original_text(text):
    """Extract known legacy envelopes; new callers pass source_text explicitly."""
    t = str(text or "").replace("’", "'").strip()
    if re.match(r"^(?:ChatGPT|SuperGrok|Grok|Claude) plan(?: unavailable)? -> ", t, re.I):
        m = re.search(r"Brett: (.*?)(?: \|\| |$)", t, re.S)
        if not m:
            m = re.search(r"FIX_RULES: (.*)$", t, re.S)
        return m.group(1).strip() if m else ""
    if t.startswith("ACTING FOR SEAT "):
        return t.split("\n", 1)[1].strip() if "\n" in t else ""
    if t.startswith("SOFTWARE ENGINEER ("):
        return t.split("\nOriginal request: ", 1)[1].strip() if "\nOriginal request: " in t else ""
    if re.match(r"^(?:FROM SEAT .*?\b)?Brett said:", t, re.I):
        t = re.sub(r"^.*?Brett said:\s*", "", t, count=1, flags=re.I)
        t = t.split(" || ", 1)[0]
    t = re.sub(r"^(?:[^\n]{1,80}:\s*\n)?From Brett:\s*", "", t, count=1, flags=re.I)
    return t.strip()

def _core(text):
    return original_text(text)

def _question_core(text):
    return re.sub(r"^" + ADDRESS, "", _core(text), count=1, flags=re.I).strip()

def is_question(text):
    c = _question_core(text)
    if not c:
        return False
    if re.match(r"^(?:do\s+not|don't|never)\b", c, re.I):
        return False
    return c.endswith("?") or bool(Q_START.match(c)) or bool(STATUS_ONLY.match(c))

def _affirmative_engineering(clause, images=None):
    c = clause.strip(" \t-:")
    if not c:
        return False
    c = _unquoted(c)
    if CONDITIONAL.search(c) or OBSERVATION.search(c) or OUTPUT_ONLY.search(c):
        return False
    negative_view = re.sub(r"\b(?:(?:does?\s+)?not\s+(?:work(?:ing)?|updat(?:e|ed|ing)|load(?:ing)?|refresh(?:ing)?|respond(?:ing)?))\b", "broken", c, flags=re.I)
    if NEGATED.search(negative_view):
        return False
    if GENERIC_FIX.fullmatch(c):
        return True
    if not DOMAIN.search(c):
        return False
    if GET_REPAIR.match(c):
        return True
    if IMPERATIVE.match(c) or REQUEST.match(c):
        if re.search(r"\bcontinue\b", c, re.I):
            return bool(re.search(r"\bcontinue\s+(?:(?:the|existing|current)\s+)*(?:THF\s+)?(?:fix(?:ing|es)?|repair(?:ing|s)?|patch(?:ing|es)?|deploy(?:ing|ment)?|implementation|implementing|coding|(?:engineering|feed[- ]health|code|software|dashboard)\s+work)\b", c, re.I))
        if re.search(r"\bupdate\s+(?:me|us|them|Brett)\b", c, re.I):
            return False
        return True
    if re.search(r"\bneeds?\s+(?:fixing|repair)|\b(?:please|pls)\s+(?:fix|repair)\b", c, re.I):
        return True
    return bool(images and FAULT.search(c))

def wants_fix(text, images=None):
    """Require a positive engineering clause, not an image, noun or restriction."""
    c = _core(text)
    if not c or STATUS_ONLY.match(_question_core(c)) or NO_CODE.search(_unquoted(c)):
        return False
    if is_question(c) and not IMPERATIVE.match(c):
        return False
    return any(_affirmative_engineering(clause, images) for clause in CLAUSES.split(_unquoted(c)))

def kind(text, images=None):
    if wants_fix(text, images):
        return "fix"
    return "question" if is_question(text) else "other"

def allows_code(text, images=None, command_mode=None):
    """Single admission rule for generated engineering entry paths."""
    return command_mode not in ("ask", "learn") and wants_fix(text, images)
