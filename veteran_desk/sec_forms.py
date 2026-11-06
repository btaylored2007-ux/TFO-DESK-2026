"""newstags_20261103 (master fixer F23; banks ch-1793715460-12ba + ch-1793717050-eb02, news_filings ch-1793712178-11a1 B).
ONE place that names SEC form types for the news paths (news_desk -> news.json / news_alerts.jsonl, catalyst_stream, feeds.tech_pack).

ROOT CAUSE: EDGAR's browse-edgar?action=getcurrent&type=4 feed filters by PREFIX, so the desk's "Form 4" feed also returns 424B2,
424B3, 424B5, 425, 40-F, 485APOS, 497, 497J, 497K ... (3 Nov 2026 23:08 WITA: 35 of 40 entries were 424B2 structured-note prospectus
supplements, 2 were real Form 4s). news_desk stamped every entry with the FEED name 'SEC Form 4' and catalyst_stream with kind 'form4';
the JPM/BAC/C 424B2s then tagged the asset (news.json asset_tags) -> seat 'news hit', engine news count, and (for a held name) a
news_alerts.jsonl line = an input to book.rail_giveback_news.

RULES: the form type is read from the entry itself (atom <category label="form type" term=...>, else EDGAR's 'FORM - NAME (CIK) (Role)'
title). Only '4' and '4/A' are labelled 'SEC Form 4' (exact string kept for existing consumers); every other form keeps its own name
('SEC 424B2', 'SEC FWP', 'SEC 425', 'SEC 40-F'). Routine offering paperwork (prospectus supplements, free-writing prospectuses, shelf /
plan registrations, fund prospectus updates) is NOT a catalyst: it never tags an asset, never counts as a news hit, never writes
news_alerts.jsonl and so never feeds rail_giveback_news. Real events keep their status (8-K, 6-K, Form 4, SC 13D, 425, earnings,
headlines). A form that cannot be read keeps the old behaviour (treated as news) and the caller logs a warning."""
import re

ATOM = "{http://www.w3.org/2005/Atom}"
FORM4 = frozenset(("4", "4/A"))
FORM4_LABEL = "SEC Form 4"
# Routine, non-event offering paperwork (normalized: upper-case, single spaces, trimmed). Dealer banks file 424B2/FWP structured notes
# many times a day (EDGAR 3 Nov 2026 to ~23:13 WITA: 424B2 x124 - JPM 71, BAC 9, C 4; FWP x87 - WFC 11, C 6, JPM 3).
ROUTINE_FILING_FORMS = frozenset((
    "424A", "424B1", "424B2", "424B3", "424B4", "424B5", "424B7", "424B8", "424H", "424H/A", "424I",   # Rule 424 prospectuses
    "FWP",                                                                                             # free-writing prospectus
    "S-3", "S-3/A", "S-3ASR", "S-3D", "S-3DPOS", "S-3MEF", "F-3", "F-3/A", "F-3ASR", "F-3D", "F-3DPOS", "F-3MEF",   # shelf registrations
    "S-8", "S-8 POS", "POS AM", "POSASR", "POS EX",                                                    # plan registration / post-effective
    "485APOS", "485BPOS", "485BXT", "497", "497AD", "497H2", "497J", "497K", "N-2", "N-2/A", "N-2ASR", "N-2MEF", "POS 8C"))   # fund prospectus
ROUTINE_PREFIXES = ("424", "485", "497")   # whole Rule-424 / fund-prospectus families, incl. variants not listed above
# Dealer-bank structured-note issuers (banks universe). Local news data 31 Oct - 3 Nov (catalyst queues + news.json) has no other banks-
# universe name with >= 20 424B2/FWP rows, so the list is the six named by the banks seat. Used for the plain-English reason only:
# the non-catalyst rule above is by FORM for every issuer (a real offering is announced by an 8-K / press release, which still counts).
DEALER_BANKS = frozenset(("BAC", "C", "JPM", "GS", "MS", "WFC"))
STRUCTURED_NOTE_RE = re.compile(r"notes?\s+linked\s+to|market[- ]linked|auto-?callable|contingent\s+(interest|coupon)|buffered|barrier|"
                                r"pricing\s+supplement|equity[- ]linked|index[- ]linked", re.I)
_TITLE_FORM = re.compile(r"^\s*([A-Z0-9][A-Z0-9 ./-]{0,15}?)\s+-\s+\S")
_ACC = re.compile(r"(\d{10}-\d{2}-\d{6})|/(\d{18})/")


def norm_form(form):
    """Upper-case, single-spaced, trimmed form type ('' for None)."""
    return re.sub(r"\s+", " ", str(form or "")).strip().upper()


def form_from_title(title):
    """EDGAR atom titles read 'FORM - NAME (CIK) (Role)'. Returns the FORM, or '' when the title carries no form prefix."""
    m = _TITLE_FORM.match(str(title or "").upper())
    return norm_form(m.group(1)) if m else ""


def edgar_form(term=None, title=None):
    """Real form type of one EDGAR entry: EDGAR's own form-type field (atom category term), else the title prefix; '' = unknown."""
    return norm_form(term) or form_from_title(title)


def atom_form(entry, title=None):
    """Form type of an atom <entry> Element (category term, else its title or the given title)."""
    try:
        c = entry.find(ATOM + "category")
        term = c.get("term") if c is not None else None
        return edgar_form(term, title if title is not None else (entry.findtext(ATOM + "title") or entry.findtext("title")))
    except Exception:
        return form_from_title(title)


def accession(url):
    """Accession number from an EDGAR URL ('0001918704-26-034024'), or None."""
    m = _ACC.search(str(url or ""))
    if not m: return None
    if m.group(1): return m.group(1)
    d = m.group(2); return "%s-%s-%s" % (d[:10], d[10:12], d[12:])


def is_form4(form):
    """Exact match only: '4' and '4/A' (never 424B2, 425, 40-F, 497 ...)."""
    return norm_form(form) in FORM4


def sec_label(form):
    """'SEC Form 4' for 4 and 4/A; 'SEC <form>' for every other form; 'SEC filing (form unknown)' when the form cannot be read."""
    f = norm_form(form)
    if f in FORM4: return FORM4_LABEL
    return ("SEC " + f) if f else "SEC filing (form unknown)"


def is_routine_filing(form):
    """True for routine offering paperwork (424B*, FWP, S-3/F-3/S-8 shelves, POS AM, 485/497 fund prospectuses, N-2)."""
    f = norm_form(form)
    return bool(f) and (f in ROUTINE_FILING_FORMS or f.startswith(ROUTINE_PREFIXES))


def is_structured_note_filing(symbol, form, title="", summary=""):
    """True when (a) form is 424B2/FWP and the issuer is a dealer bank, or (b) any 424B*/FWP whose title/summary reads like a structured note."""
    f = norm_form(form)
    if not (f.startswith("424B") or f == "FWP"): return False
    if f in ("424B2", "FWP") and re.sub(r"-P[A-Z]*$", "", str(symbol or "").upper()) in DEALER_BANKS: return True   # SEC's CIK map can give the preferred line (BAC-PS, JPM-PM)
    return bool(STRUCTURED_NOTE_RE.search("%s %s" % (title or "", summary or "")))


def routine_reason(form, symbol=None, title="", summary=""):
    """Plain-English why a routine filing is not a catalyst; None when the form is not routine."""
    f = norm_form(form)
    if not is_routine_filing(f): return None
    if is_structured_note_filing(symbol, f, title, summary):
        return "SEC %s = structured-note prospectus paperwork (dealer banks file many a day): not a catalyst" % f
    if f.startswith("424") or f == "FWP":
        return "SEC %s = prospectus supplement / free-writing prospectus for securities sold off a shelf: not a catalyst" % f
    if f.startswith(("485", "497", "N-2", "POS 8C")):
        return "SEC %s = fund prospectus update: not a catalyst" % f
    return "SEC %s = shelf / plan registration paperwork: not a catalyst" % f


def classify(form, symbol=None, title="", summary=""):
    """dict(form, label, form4, routine, catalyst, why) for one SEC item. Unknown form -> catalyst True (old behaviour), why says so."""
    f = norm_form(form)
    why = routine_reason(f, symbol, title, summary)
    amendment=f.endswith("/A")
    return dict(form=f,label=sec_label(f),form4=f in FORM4,routine=why is not None,amendment=amendment,catalyst=why is None and not amendment,
                why=why or ("amendment: new event not independently verified" if amendment else "form type unknown: kept as news (old behaviour)" if not f else None))
