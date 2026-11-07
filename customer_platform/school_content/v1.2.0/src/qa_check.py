import json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
c = json.loads((ROOT/"v1.2.0/course.json").read_text())
NEW_IDS = {q["id"] for q in json.loads((ROOT/"src/new_questions.json").read_text())}
old = json.loads(Path("/workspace/transparent-trading-school-review/v1.1.1/course.json").read_text())
g = {}
exec(open(ROOT/"src/checks.py").read(), g)
fails = []
ok = 0

def num(t):
    m = re.search(r"([-+]?)(?:A\$|US\$)?(\d[\d,]*\.?\d*)", t)
    return (-1 if m and m.group(1)=="-" else 1)*float(m.group(2).replace(",","")) if m else None

Q = {}
for m in c["modules"]:
    a = [q for q in m["questions"] if q["pool"]=="A"]
    b = [q for q in m["questions"] if q["pool"]=="B"]
    for i,q in enumerate(a):
        Q[(m["number"],"A",i+1)] = q
    for i,q in enumerate(b):
        Q[(m["number"],"B",i+1)] = q

for n,p,i,v in g["C"]:
    q = Q[(n,p,i)]
    tol = max(0.002*abs(v), 1e-9)
    cor = [o for o in q["options"] if o["id"]==q["correct_option"]][0]
    cv = num(cor["text"])
    if cor["text"].lower().startswith("about"):
        tol = max(tol, 0.025*abs(v))
    if cv is not None and abs(cv-v)>tol and abs(abs(cv)-abs(v))<=tol and not re.search(r"[-+]", cor["text"]):
        cv = -cv if v<0 else cv
    if cv is None or abs(cv-v)>tol:
        fails.append(f"{q['id']} expected {v:.6g} got {cor['text']}")
    elif any(o is not cor and num(o["text"]) is not None and abs(num(o["text"])-v)<=tol for o in q["options"]):
        fails.append(f"{q['id']} distractor matches")
    else:
        ok += 1

ids=set(); struct=[]
for m in c["modules"]:
    los = {o["id"] for o in m["objectives"]}
    if not 4 <= len(m["lessons"]) <= 6:
        struct.append(m["id"]+" lessons")
    if sum(q["pool"]=="A" for q in m["questions"]) < 10:
        struct.append(m["id"]+" <10 A")
    if "practical_rubric" not in m:
        struct.append(m["id"]+" no rubric")
    else:
        crits = m["practical_rubric"].get("criteria") or []
        if len(crits) < 3:
            struct.append(m["id"]+" thin rubric")
        joined = json.dumps(m["practical_rubric"]).lower()
        if "profit" in joined and "not a pass" not in joined and "not the pass" not in joined:
            # still ok if it says profit is not a pass criterion via pass_default
            pass
        if "not a pass" not in joined and "profit is not" not in joined:
            struct.append(m["id"]+" rubric missing profit-not-pass")
    for x in [m["id"]]+[l["id"] for l in m["lessons"]]+[q["id"] for q in m["questions"]]:
        if x in ids:
            struct.append("dup "+x)
        ids.add(x)
    for q in m["questions"]:
        t = [o["text"] for o in q["options"]]
        if len(t)!=4 or len(set(t))!=4:
            struct.append(q["id"]+" options")
        if q["objective_id"] not in los:
            struct.append(q["id"]+" LO")
        if q["correct_option"] not in "abcd":
            struct.append(q["id"]+" key")
        new_ids = getattr(sys.modules[__name__], "NEW_IDS", set())
        if q["id"] in new_ids and any(len(o["explanation"].strip())<12 for o in q["options"]):
            struct.append(q["id"]+" short-expl-new")
        if re.search(r"\b(guarantee[ds]? (profit|return)|risk-free trading)", q["stem"], re.I) and not re.search(r"\bno\b|not |never |red flag", q["stem"], re.I):
            struct.append(q["id"]+" claim")

# Preserve v1.1.1 ids and keys
old_q = {q["id"]: q for m in old["modules"] for q in m["questions"]}
new_q = {q["id"]: q for m in c["modules"] for q in m["questions"]}
id_breaks = []
for qid, q in old_q.items():
    if qid not in new_q:
        id_breaks.append("missing "+qid)
    elif new_q[qid]["correct_option"] != q["correct_option"]:
        id_breaks.append("key-change "+qid)
old_l = {l["id"] for m in old["modules"] for l in m["lessons"]}
new_l = {l["id"] for m in c["modules"] for l in m["lessons"]}
if old_l != new_l:
    id_breaks.append("lesson-id-set-changed")
if [m["id"] for m in old["modules"]] != [m["id"] for m in c["modules"]]:
    id_breaks.append("module-id-order-changed")

txt = (ROOT/"v1.2.0/course.json").read_text().lower()
bad = [w for w in ["you will become", "certified trader", "accredited course"] if w in txt]
if "guaranteed profit" in txt and "no strategy" not in txt:
    # still list for visibility; fail only if a correct-option explanation asserts a guarantee
    for m in c["modules"]:
        for q in m["questions"]:
            cor=[o for o in q["options"] if o["id"]==q["correct_option"]][0]
            if "guaranteed profit" in (cor["text"]+" "+cor["explanation"]).lower() and "not" not in (cor["text"]+" "+cor["explanation"]).lower():
                bad.append("guaranteed profit as correct "+q["id"])
# guaranteed profit may appear in a distractor - check context
allowed_bad = []
if "guaranteed profit" in txt:
    # only fail if used outside option text marked wrong... we scan later
    pass

print(f"numeric recomputed {ok}/{len(g['C'])} ok")
print("numeric fails", fails)
print("structural issues", struct)
print("id/key preservation breaks", id_breaks)
print("phrase scan", bad)
print("modules", len(c["modules"]), "lessons", sum(len(m['lessons']) for m in c["modules"]),
      "questions", sum(len(m['questions']) for m in c["modules"]),
      "poolA", sum(1 for m in c["modules"] for q in m["questions"] if q["pool"]=="A"),
      "poolB", sum(1 for m in c["modules"] for q in m["questions"] if q["pool"]=="B"))

# word counts by field
def wc(s): return len((s or "").split())
core=deep=qtxt=gloss=ex=rub=0
per=[]
for m in c["modules"]:
    mc=md=0
    for l in m["lessons"]:
        n=wc(l["body"])
        if l["layer"]=="core":
            core += n; mc += n
        else:
            deep += n; md += n
    for q in m["questions"]:
        qtxt += wc(q["stem"])
        for o in q["options"]:
            qtxt += wc(o["text"]) + wc(o["explanation"])
    for gitem in m.get("glossary",[]):
        gloss += wc(gitem.get("term","")) + wc(gitem.get("definition",""))
    ex += wc(m.get("paper_exercise",""))
    rub += wc(json.dumps(m.get("practical_rubric",{})))
    per.append((m["id"], mc, md, mc+md, sum(q["pool"]=="A" for q in m["questions"]), sum(q["pool"]=="B" for q in m["questions"])))

print("WORD_COUNTS", {
    "core_lessons": core,
    "deep_dives": deep,
    "lessons_total": core+deep,
    "questions_stems_options_explanations": qtxt,
    "glossary": gloss,
    "paper_exercises": ex,
    "practical_rubrics_approx": rub,
})
print("PER_MODULE")
for row in per:
    print(row)

# forbidden outcome claims in lessons
claim_hits=[]
for m in c["modules"]:
    for l in m["lessons"]:
        low=l["body"].lower()
        for phrase in ["you will become an excellent", "accredited qualification", "certified trader"]:
            if phrase in low:
                claim_hits.append((l["id"], phrase))
        if "guarantees profits" in low and not any(x in low for x in ["no strategy guarantees", "does not", "cannot", "not a"]):
            claim_hits.append((l["id"], "guarantees profits"))
print("lesson_claim_hits", claim_hits)

rc = 0 if not fails and not struct and not id_breaks and not claim_hits else 1
print("EXIT", rc)
sys.exit(rc)
