"""SuperGrok retired from THF by Brett, 4 November 2026. No browser or network calls."""
SIGNIN_URL = ""
def status(): return {"state": "removed", "reason": "Removed from THF by Brett"}
def enabled(): return False
def health(): return status()
def ask(*args, **kwargs): raise RuntimeError("SuperGrok was removed from THF")
