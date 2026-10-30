#!/usr/bin/env python3
"""Crypto news collector — public RSS, forward-accumulating.

Polls public crypto news feeds, deduplicates, timestamps on arrival, tags mentioned
symbols, and scores headline sentiment with a finance-oriented lexicon. Writes
one JSONL row per NEW item to reports/news/news_YYYYMMDD.jsonl.

WHY A COLLECTOR AND NOT A BACKTEST
----------------------------------
RSS feeds serve only recent items, so there is no historical archive to mine here.
Like the microstructure collectors, this accumulates forward. It cannot answer
"does news predict returns?" today — it can only start being able to. Expect weeks,
not hours. Anyone reading a result off a few days of this is fooling themselves,
and news_research.py refuses to report on samples that are too small.

SECURITY — headlines are DATA, never instructions
-------------------------------------------------
Every string collected here is untrusted text written by strangers. It is stored,
counted and scored. It is NEVER interpreted as a command, and nothing downstream
may act on an instruction found inside a headline ("buy X now", "ignore previous
rules"). The collector strips markup and truncates; the research layer only ever
consumes the numeric sentiment score and symbol tags, never free text as logic.

Usage:
    python3 news_collector.py                # one poll
    python3 news_collector.py --loop 300     # poll every 5 minutes
    python3 news_collector.py --stats        # what has been collected so far
"""
import argparse
import hashlib
import html
import json
import os
import re
import time
import urllib.request
from xml.etree import ElementTree

BASE = os.path.dirname(os.path.abspath(__file__))
NEWS_DIR = os.path.join(BASE, "reports", "news")
SEEN_FILE = os.path.join(NEWS_DIR, "seen.json")

FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss",
    "cointelegraph": "https://cointelegraph.com/rss",
    "decrypt": "https://decrypt.co/feed",
    "bitcoinmagazine": "https://bitcoinmagazine.com/feed",
    "theblock": "https://www.theblock.co/rss.xml",
    "cryptoslate": "https://cryptoslate.com/feed/",
    "bitcoinist": "https://bitcoinist.com/feed/",
    "newsbtc": "https://www.newsbtc.com/feed/",
    # Retail-crowd tier — deliberately separate sources from the professional
    # outlets above, so the future event study can test the tiers independently
    # (does pro news lead retail chatter, and does either lead price?).
    "reddit_cryptocurrency": "https://www.reddit.com/r/CryptoCurrency/.rss",
    "reddit_bitcoin": "https://www.reddit.com/r/Bitcoin/.rss",
    "reddit_wallstreetbets": "https://www.reddit.com/r/wallstreetbets/.rss",
    # --- CM-062 (2026-08-30): ALT-COIN crowd sources ------------------------
    # A 12-mechanism study returned UNTESTABLE on r/wallstreetbets: over 24 days
    # it produced THREE genuine coin posts, and 7 of its 11 Kraken-symbol hits
    # were ticker collisions with equities (a 64% false-positive rate). WSB is an
    # equities room; it is kept for the pro-vs-retail comparison but it cannot
    # carry an alt-coin signal on its own.
    # These subs discuss the small and mid-cap coins Kraken actually lists, which
    # is where a "rumour on threads" effect would live if it exists anywhere.
    # All verified reachable 2026-08-30 (the 429s on first attempt were Reddit
    # rate-limiting, not missing feeds — the 2.0s inter-feed throttle handles it).
    "reddit_cryptomarkets": "https://www.reddit.com/r/CryptoMarkets/.rss",
    "reddit_altcoin": "https://www.reddit.com/r/altcoin/.rss",
    "reddit_satoshistreetbets": "https://www.reddit.com/r/SatoshiStreetBets/.rss",
    "reddit_cryptomoonshots": "https://www.reddit.com/r/CryptoMoonShots/.rss",
    "reddit_ethereum": "https://www.reddit.com/r/ethereum/.rss",
    "reddit_solana": "https://www.reddit.com/r/solana/.rss",
    "reddit_defi": "https://www.reddit.com/r/defi/.rss",
}

# --- CM-062: the ticker-collision blocklist, applied A PRIORI ----------------
# Kraken lists coins whose symbols collide with well-known equities and with
# ordinary English. Naive "$TICKER -> Kraken base" extraction gave a 64%
# false-positive rate on WSB. Any analysis that maps text to Kraken symbols must
# apply this first, and must require the $ prefix or a coin-name co-mention for
# anything on it. Published here so every study uses the SAME list rather than
# each inventing its own.
TICKER_COLLISIONS = {
    # Kraken symbol : what it usually means in a retail thread
    "GME": "GameStop", "AMC": "AMC Entertainment", "TSLA": "Tesla",
    "NVDA": "Nvidia", "SPY": "S&P 500 ETF", "QQQ": "Nasdaq ETF",
    "META": "Meta Platforms", "MSTR": "MicroStrategy", "COIN": "Coinbase stock",
    "HOOD": "Robinhood", "PLTR": "Palantir", "INTC": "Intel", "AMD": "AMD",
    "T": "AT&T / the word", "A": "the article", "I": "the pronoun",
    "IT": "the pronoun", "ALL": "the word", "ANY": "the word", "NOW": "the word",
    "ONE": "the word", "TWO": "the word", "UP": "the word", "DOWN": "the word",
    "NEW": "the word", "OK": "the word", "GO": "the word", "ME": "the pronoun",
    "USD": "the currency", "EUR": "the currency", "CEO": "the title",
    "ATH": "all-time high", "FUD": "the slang", "DYOR": "the slang",
    "USA": "the country", "SEC": "the regulator", "ETF": "the instrument",
}

# Sources whose text is raw crowd chatter rather than edited journalism.
RETAIL_SOURCES = {"reddit_cryptocurrency", "reddit_bitcoin", "reddit_wallstreetbets",
                  "reddit_cryptomarkets", "reddit_altcoin", "reddit_satoshistreetbets",
                  "reddit_cryptomoonshots", "reddit_ethereum", "reddit_solana",
                  "reddit_defi"}
# CM-062: WSB is an EQUITIES room. Kept for the pro-vs-retail comparison, but any
# alt-coin study should treat it separately and never pool it with the crypto subs.
EQUITY_CROWD_SOURCES = {"reddit_wallstreetbets"}

UA = {"User-Agent": "Mozilla/5.0 (compatible; local-research-desk/1.0)"}

# Symbols the desk cares about, and the words that signal them. Deliberately
# conservative: a false tag is worse than a missed one, because it injects noise
# into the very series we are trying to measure.
SYMBOL_PATTERNS = {
    "BTC": r"\b(bitcoin|btc)\b",
    "ETH": r"\b(ethereum|ether|eth)\b",
    "SOL": r"\b(solana|sol)\b",
    "XRP": r"\b(ripple|xrp)\b",
    "ADA": r"\b(cardano|ada)\b",
    "DOGE": r"\b(dogecoin|doge)\b",
    "LINK": r"\b(chainlink|link)\b",
    "AVAX": r"\b(avalanche|avax)\b",
    "DOT": r"\b(polkadot|dot)\b",
    "LTC": r"\b(litecoin|ltc)\b",
    "XLM": r"\b(stellar|xlm)\b",
    "SUI": r"\bsui\b",
    "TRX": r"\b(tron|trx)\b",
    "SHIB": r"\b(shiba|shib)\b",
    "PEPE": r"\bpepe\b",
}

# Finance-oriented sentiment lexicon. A general-purpose lexicon mis-scores market
# text badly ("bear", "crash", "rally" carry domain meaning), so this is hand-built
# for crypto headlines. Weights are deliberately coarse — the point is a signed
# score with a defensible sign, not false precision.
POSITIVE = {
    "surge": 2, "surges": 2, "soar": 2, "soars": 2, "rally": 2, "rallies": 2,
    "jump": 1.5, "jumps": 1.5, "gain": 1, "gains": 1, "rise": 1, "rises": 1,
    "climb": 1, "climbs": 1, "up": 0.5, "high": 1, "record": 1.5, "ath": 2,
    "breakout": 1.5, "bullish": 2, "bull": 1.5, "adoption": 1.5, "approve": 2,
    "approved": 2, "approval": 2, "partnership": 1, "launch": 1, "upgrade": 1,
    "inflow": 1.5, "inflows": 1.5, "accumulate": 1, "buy": 1, "buying": 1,
    "institutional": 1, "etf": 0.5, "milestone": 1, "outperform": 1.5,
    "recover": 1, "recovery": 1, "rebound": 1.5, "optimism": 1.5, "boost": 1.5,
}
NEGATIVE = {
    "crash": -2.5, "crashes": -2.5, "plunge": -2.5, "plunges": -2.5,
    "plummet": -2.5, "slump": -2, "slumps": -2, "tumble": -2, "tumbles": -2,
    "fall": -1.5, "falls": -1.5, "drop": -1.5, "drops": -1.5, "sink": -2,
    "decline": -1.5, "declines": -1.5, "down": -0.5, "low": -1, "bearish": -2,
    "bear": -1.5, "selloff": -2, "sell-off": -2, "dump": -2, "liquidation": -2,
    "liquidated": -2, "hack": -2.5, "hacked": -2.5, "exploit": -2.5,
    "breach": -2, "scam": -2, "fraud": -2.5, "lawsuit": -1.5, "sue": -1.5,
    "sued": -1.5, "ban": -2, "banned": -2, "crackdown": -2, "reject": -2,
    "rejected": -2, "outflow": -1.5, "outflows": -1.5, "warning": -1.5,
    "risk": -1, "fear": -1.5, "panic": -2, "collapse": -2.5, "bankruptcy": -2.5,
    "delay": -1, "delayed": -1, "halt": -1.5, "halted": -1.5, "probe": -1.5,
}
NEGATORS = {"not", "no", "never", "without", "denies", "denied", "avoids"}

TAG_RE = re.compile(r"<[^>]+>")
WORD_RE = re.compile(r"[a-z][a-z\-]+")

# Several of these feeds carry general tech coverage, not just crypto. On the very
# first poll the three most "negative crypto headlines" were about Meta, OpenAI and
# a macOS exploit — the lexicon fired correctly on "hacked"/"exploit", but none of
# it has anything to do with a coin price. Feeding that into an event study injects
# pure noise into the series being measured, so relevance is decided at collection
# time and recorded on the row.
CRYPTO_TERMS = re.compile(
    r"\b(crypto|cryptocurrency|cryptoasset|blockchain|defi|token|tokens|"
    r"altcoin|altcoins|stablecoin|stablecoins|exchange|onchain|on-chain|"
    r"halving|mining|miner|miners|wallet|wallets|staking|nft|web3|etf|etfs|"
    r"sec|cftc|mica|binance|coinbase|kraken|gemini-exchange|bitstamp|"
    r"tether|usdt|usdc|satoshi|satoshis|hodl|memecoin|memecoins|perpetual|"
    r"perps|futures|spot|custody|self-custody|cold-storage|coldcard|ledger|"
    r"seed|airdrop|dao|layer-2|l2|rollup|validator|validators|node|nodes|"
    r"tvl|dex|cex|protocol|whale|whales|liquidity|chain|degen|bull-run|"
    r"galaxy-digital|grayscale|microstrategy|strategy|treasury|spot-etf)\b")

# Non-crypto tech subjects that these feeds cover and that must not be scored as
# crypto sentiment unless a crypto term also appears.
OFFTOPIC_TERMS = re.compile(
    r"\b(openai|anthropic|claude|chatgpt|gemini|llama|copilot|meta|apple|"
    r"microsoft|nvidia|macos|windows|android|iphone)\b")


def strip_markup(s):
    """Untrusted text in -> plain, bounded text out."""
    if not s:
        return ""
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()[:400]


def score_sentiment(text):
    """Signed sentiment for one headline, with simple negation handling.

    Returns (score, n_hits). Score is normalised by hit count so a long headline
    with many weak words does not outweigh a short emphatic one.
    """
    words = WORD_RE.findall(text.lower())
    total, hits = 0.0, 0
    for i, w in enumerate(words):
        v = POSITIVE.get(w) or NEGATIVE.get(w)
        if v is None:
            continue
        # flip if negated within the preceding two words
        if any(words[j] in NEGATORS for j in range(max(0, i - 2), i)):
            v = -v
        total += v
        hits += 1
    return (round(total / hits, 3) if hits else 0.0), hits


def tag_symbols(text):
    low = text.lower()
    return sorted(s for s, pat in SYMBOL_PATTERNS.items() if re.search(pat, low))


def classify_relevance(text, symbols):
    """Decide whether a headline is crypto-relevant, and how specifically.

    Returns (relevance, reason):
      "symbol"      names a coin we track — the only tier usable for a
                    symbol-level event study
      "market"      crypto-general but no specific coin — usable only for a
                    market-wide sentiment series
      "offtopic"    general tech/other. Kept for auditability, EXCLUDED from
                    every study.
    """
    low = text.lower()
    has_crypto = bool(CRYPTO_TERMS.search(low))
    has_offtopic = bool(OFFTOPIC_TERMS.search(low))
    if symbols and not (has_offtopic and not has_crypto):
        return "symbol", "names %s" % ",".join(symbols)
    if has_crypto and not has_offtopic:
        return "market", "crypto-general"
    if has_crypto and has_offtopic:
        return "market", "crypto term present alongside off-topic subject"
    return "offtopic", "no crypto term"


def parse_feed(name, url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return []
    out = []
    # RSS <item> and Atom <entry>
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        get = lambda t: next((c.text for c in node
                              if c.tag.split("}")[-1] == t and c.text), "")
        title = strip_markup(get("title"))
        if not title:
            continue
        link = strip_markup(get("link")) or strip_markup(
            next((c.attrib.get("href", "") for c in node
                  if c.tag.split("}")[-1] == "link"), ""))
        pub = strip_markup(get("pubDate") or get("published") or get("updated"))
        summary = strip_markup(get("description") or get("summary"))
        out.append({"source": name, "title": title, "link": link,
                    "published": pub, "summary": summary[:300]})
    return out


def item_id(it):
    return hashlib.sha1(
        ("%s|%s" % (it["source"], it["title"])).encode("utf-8")).hexdigest()[:16]


# Words too common in headlines to identify a story.
_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "has",
    "its", "his", "her", "you", "your", "will", "can", "how", "why", "what",
    "new", "now", "says", "said", "after", "amid", "over", "into", "out",
    "not", "but", "all", "more", "than", "one", "two", "here", "have",
}


def story_key_tokens(title):
    """Significant tokens for near-duplicate detection."""
    return {w for w in WORD_RE.findall(title.lower())
            if len(w) > 2 and w not in _STOP}


def cluster_stories(rows, threshold=4.0, min_shared=2, window_hours=36):
    """Group headlines covering the SAME underlying event.

    On the first poll, four of the five most negative BTC headlines were the same
    Coldcard exploit reported by four outlets. Counted naively that is four
    independent bearish signals; it is one. Any event study that misses this
    overstates its sample exactly the way pooling correlated assets did.

    Plain word overlap does NOT work here — outlets describe one event in different
    words ("Ledger Says Coldcard Exploit Shows..." vs "Bitcoin Red Team Finds 85
    Critical Flaws... After Coldcard"), giving a Jaccard around 0.2. What identifies
    a story is sharing a RARE token: "coldcard" is decisive, "bitcoin" is worthless.
    So similarity is the summed inverse-document-frequency of shared tokens, and
    `threshold` is in IDF units — roughly "one distinctive word in common".

    Returns rows with `story_id` added, plus a summary.
    """
    import math as _math
    ordered = sorted(rows, key=lambda r: r.get("collected_epoch", 0))
    n_docs = max(1, len(ordered))

    df = {}
    tok_by_row = []
    for r in ordered:
        toks = story_key_tokens(r.get("title", ""))
        tok_by_row.append(toks)
        for t in toks:
            df[t] = df.get(t, 0) + 1
    idf = {t: _math.log(n_docs / c) for t, c in df.items()}

    def sim(a, b):
        """IDF mass of shared tokens, requiring more than one word in common.

        `min_shared` guards against a single coincidental rare token welding two
        unrelated stories together.
        """
        shared = a & b
        if len(shared) < min_shared:
            return 0.0
        return sum(idf.get(t, 0.0) for t in shared)

    clusters = []
    for r, toks in zip(ordered, tok_by_row):
        if not toks:
            r["story_id"] = r["id"]
            continue
        best, best_score = None, 0.0
        for c in clusters:
            if abs(r.get("collected_epoch", 0) - c["epoch"]) > window_hours * 3600:
                continue
            s = sim(toks, c["tokens"])
            if s > best_score:
                best, best_score = c, s
        if best is not None and best_score >= threshold:
            r["story_id"] = best["id"]
            best["members"] += 1
            # NOTE: the cluster's token set is deliberately NOT grown. Absorbing
            # each new member's vocabulary turns a cluster into a magnet that
            # matches everything (single-linkage chaining) — an earlier version
            # did exactly that and produced one 28-headline "story" containing an
            # NFT fraud, BlackRock and Meta's AI agent. Compare against the seed.
        else:
            clusters.append({"tokens": set(toks), "epoch": r.get("collected_epoch", 0),
                             "id": r["id"], "members": 1})
            r["story_id"] = r["id"]

    sizes = [c["members"] for c in clusters]
    return ordered, {
        "headlines": len(ordered),
        "distinct_stories": len(clusters),
        "largest_cluster": max(sizes) if sizes else 0,
        "duplication_factor": round(len(ordered) / len(clusters), 2) if clusters else 1.0,
    }


def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen):
    os.makedirs(NEWS_DIR, exist_ok=True)
    keep = list(seen)[-20000:]
    tmp = SEEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(keep, f)
    os.replace(tmp, SEEN_FILE)


def poll(verbose=True):
    os.makedirs(NEWS_DIR, exist_ok=True)
    seen = load_seen()
    collected_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    epoch = time.time()
    path = os.path.join(NEWS_DIR, "news_%s.jsonl" % time.strftime("%Y%m%d"))

    new_rows, errors = [], {}
    # CM-062: the flat 2.0s throttle was sized for THREE reddit feeds. With ten,
    # Reddit 429'd nine of them in one pass — the collector was adding sources
    # faster than it could politely fetch them, which is worse than not adding
    # them: it also knocked out the three that already worked.
    # Reddit gets its own longer spacing plus one backoff retry; everything else
    # keeps the original 2.0s. The poll runs every 5 minutes, so the extra
    # seconds cost nothing and a 429 costs a whole feed.
    # 10 reddit feeds at 7.0s = ~8.5 requests/min, still over Reddit's anonymous
    # ceiling (~10/min shared with everything else this host does). 12.0s = 5/min
    # sits safely under it. Ten feeds then take ~2 minutes; the poll runs every
    # 5, so it fits with room to spare.
    REDDIT_GAP, OTHER_GAP, RETRY_GAP = 12.0, 2.0, 30.0
    for name, url in FEEDS.items():
        is_reddit = "reddit.com" in url
        gap = REDDIT_GAP if is_reddit else OTHER_GAP
        try:
            items = parse_feed(name, url)
        except Exception as e:
            msg = str(e)
            if "429" in msg and is_reddit:
                # One patient retry. If it 429s twice we are genuinely being
                # throttled and the error is recorded rather than hidden.
                time.sleep(RETRY_GAP)
                try:
                    items = parse_feed(name, url)
                    print("   %s recovered after 429 backoff" % name, flush=True)
                except Exception as e2:
                    errors[name] = str(e2)[:120]
                    time.sleep(gap)
                    continue
            else:
                errors[name] = msg[:120]
                time.sleep(gap)
                continue
        time.sleep(gap)
        for it in items:
            iid = item_id(it)
            if iid in seen:
                continue
            seen.add(iid)
            text = it["title"] + " " + it["summary"]
            score, hits = score_sentiment(text)
            syms = tag_symbols(text)
            relevance, why = classify_relevance(text, syms)
            new_rows.append({
                "id": iid,
                "collected_at": collected_at,
                "collected_epoch": round(epoch, 3),
                "source": it["source"],
                "title": it["title"],
                "published": it["published"],
                "link": it["link"],
                "symbols": syms,
                "sentiment": score,
                "sentiment_hits": hits,
                "relevance": relevance,
                "relevance_reason": why,
                "tier": "retail" if name in RETAIL_SOURCES else "professional",
            })

    if new_rows:
        with open(path, "a") as f:
            for r in new_rows:
                f.write(json.dumps(r) + "\n")
        save_seen(seen)

    if verbose:
        print("%s  +%d new  (%d feeds, %d errors)"
              % (collected_at, len(new_rows), len(FEEDS), len(errors)))
        for n, e in errors.items():
            print("   feed error %s: %s" % (n, e))
    return new_rows, errors


def stats():
    if not os.path.isdir(NEWS_DIR):
        print("No news collected yet.")
        return
    files = sorted(f for f in os.listdir(NEWS_DIR) if f.startswith("news_"))
    total, by_sym, by_src, spans = 0, {}, {}, []
    for fn in files:
        with open(os.path.join(NEWS_DIR, fn)) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                total += 1
                spans.append(r.get("collected_epoch", 0))
                by_src[r["source"]] = by_src.get(r["source"], 0) + 1
                for s in r.get("symbols", []):
                    by_sym[s] = by_sym.get(s, 0) + 1
    print("NEWS COLLECTED")
    print("  files      : %d (%s)" % (len(files), ", ".join(files[-3:])))
    print("  headlines  : %d" % total)
    if spans:
        hours = (max(spans) - min(spans)) / 3600.0
        print("  span       : %.1f hours" % hours)
        if hours < 24 * 14:
            print("  !! far too short to test. Needs weeks — see news_research.py")
    print("  by source  : %s" % ", ".join("%s=%d" % kv for kv in sorted(by_src.items())))
    print("  by symbol  : %s" % ", ".join("%s=%d" % kv
                                          for kv in sorted(by_sym.items(),
                                                           key=lambda kv: -kv[1])[:12]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0,
                    help="poll every N seconds (0 = one poll and exit)")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.stats:
        stats()
        return
    if args.loop:
        print("polling every %ds — ctrl-c to stop" % args.loop)
        while True:
            try:
                poll()
            except Exception as e:
                print("poll failed: %s" % str(e)[:150])
            time.sleep(args.loop)
    else:
        poll()


if __name__ == "__main__":
    main()
