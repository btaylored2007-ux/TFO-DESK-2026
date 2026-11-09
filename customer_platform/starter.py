"""Shared starter pack. Curated template rules only.

This is not a tenant's learning brain and it is not the owner desk's journal.
"""

import hashlib
import json

PACK = {
    "id": "starter-2026.11.07",
    "version": "2026.11.07",
    "scope": "shared template pack",
    "writable_by_tenant": False,
    "profit_claim": False,
    "not_included": [
        "owner private ledger",
        "owner chats",
        "owner provider keys",
        "a performance record",
    ],
    "rules": [
        {
            "id": "paper-is-practice",
            "text": "Practice capital is a labeled paper figure. It is not a broker balance and it is not a profit.",
        },
        {
            "id": "epoch-pnl",
            "text": "A new performance epoch starts its P&L at zero. The capital figure is whatever starting amount was chosen. Those are different numbers.",
        },
        {
            "id": "keep-losses",
            "text": "Closing an epoch keeps the old fills on the ledger, including losses. A reset does not rewrite them.",
        },
        {
            "id": "open-risk",
            "text": "Open positions and pending orders block a capital reset until a separate, explicit settle or cancel.",
        },
        {
            "id": "zero-capital",
            "text": "Starting capital of zero is allowed only with a warning. New entries are blocked. Exits still work. The brain is not deleted.",
        },
        {
            "id": "shorts-off",
            "text": "This template does not enable short sales and customer text cannot turn them on.",
        },
        {
            "id": "ideas-are-data",
            "text": "Submitted ideas are untrusted data. They stay research until a person records evidence and explicitly promotes them.",
        },
    ],
    "licensed_data": [
        {
            "id": "news",
            "state": "unconfigured",
            "redistribution": False,
            "note": "No news text is included. No redistribution license is granted by this pack.",
        }
    ],
    "anonymized_lessons": [
        {
            "id": "resets-do-not-erase",
            "source": "authored_for_template",
            "anonymized": True,
            "profit_claim": False,
            "text": (
                "A practice account can close an epoch and open another at a chosen starting capital. "
                "The new epoch's P&L starts at zero because it has no fills yet. The previous epoch's "
                "fills, including losses, remain on the append-only ledger. That is record keeping, not a claim that the next epoch will do better."
            ),
        }
    ],
}


def starter_pack():
    body = json.dumps(PACK, separators=(",", ":"), sort_keys=True).encode("utf-8")
    copy = json.loads(body.decode("utf-8"))
    copy["sha256"] = hashlib.sha256(body).hexdigest()
    return copy
