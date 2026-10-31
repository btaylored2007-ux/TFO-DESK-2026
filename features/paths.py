"""Output locations for the advisory store. Cache lives beside the files and is regenerable."""
import os, re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def state_dir(base=None):
    return os.path.join(base or REPO, "state")


def features_dir(base=None):
    return os.path.join(state_dir(base), "features")


def cache_dir(base=None):
    return os.path.join(features_dir(base), "_cache")


def regime_path(base=None):
    return os.path.join(state_dir(base), "regime.json")


def ticker_path(ticker, base=None):
    return os.path.join(features_dir(base), ticker_filename(ticker))


def ticker_filename(ticker):
    raw = str(ticker).strip()
    if not raw or raw in (".", "..") or any(c in raw for c in "/\\"):
        raise ValueError("bad ticker")
    name = re.sub(r"[^A-Za-z0-9._=+^-]", "_", raw)
    if not name or name in (".", ".."):
        raise ValueError("bad ticker")
    return name + ".json"
