"""Authored lesson plus an outline. Outline rows are not pretend course text."""

LESSON = """
Practice capital, a new epoch, and a brain that only learns from your own paper record

Two figures sit next to each other on this desk, and they are not the same figure.

Practice capital is the cash allocated to the open paper epoch. It starts at the amount configured for the account. A later reset sets it to a starting amount you choose. The default shown in the reset preview is that configured amount. You can type a different nonnegative amount in the account currency. The template refuses floats, blank text, NaN, infinity, negative numbers, extra decimal places, and amounts above the template bound, instead of rounding them into a nearby cent.

This epoch's performance P&L is the realized result of fills in the open epoch only. When a new epoch opens, that P&L is zero because the epoch has no fills yet. Zero P&L is not a statement that capital is zero. If you reset practice capital to 10,000, the new epoch still shows 0.00 of P&L until a fill realizes a gain or a loss. If you choose 0 as the starting capital, the preview states a separate warning: new entries are blocked until a later paper-funds action. Exits remain available. Choosing zero does not delete the learning brain, strategy versions, or the old ledger.

Historical realized P&L is the sum of fill results across epochs. A reset appends an epoch-close marker and a balance adjustment. Those rows are labeled as not P&L. They do not rewrite earlier fills, cost basis, currency, or the FX rate stored on a fill. A loss in the closed epoch is still a loss. The chart reads the ledger. It does not draw a smoother path than the fills you supplied. Prices on this desk are caller-supplied paper prices, not a market-data print and not a performance advertisement.

The reset refuses to run while a position is open or an order is pending. Leaving those in place would either hide a holding or invent a close. Settling at marked prices is a separate confirmation. It writes the close onto the old epoch, so the gain or loss stays in historical P&L, and only then can a reset open the next epoch. Canceling a pending order is also separate. Neither action is implied by the reset button.

New risk uses equity, which is practice capital plus the marked value of open positions. When equity is zero or negative, a new buy is refused. The check stops there. It does not divide by equity. An exit does not need spare equity. After a zero-capital reset, the explicit paper-funds action can raise practice capital again. That credit is labeled paper funds, not revenue and not a broker deposit, and it does not change historical P&L.

Each subscription has its own learning brain on disk. A worker appends lessons from three kinds of evidence: realized paper outcomes, research you submit, and teachings you explicitly approve. A run that finds nothing new records an idle result and does not invent a lesson. Candidate lessons are not trading behavior. Accepting one records a validated paper hypothesis and still does not place an order, move a stop, enable a short, or edit the sealed risk rails. If no model provider is configured, the brain says the model is not running. A spend cap of zero blocks a paid call. This template does not wire an outbound model call at all.

Submitted text is stored as data. It is not a system instruction. Wording that tries to change rails, reveal a secret, or override the desk is quarantined and cannot be promoted. The brain statement records that a submission existed. It does not copy the raw text into a prompt, a log line, or another tenant's store.

Sector opt-out removes a sector from new entries only. An open holding in that sector stays on the holdings list, and its exit still works.

Nothing on this screen is a live brokerage. Manual, approval, and automatic broker modes can be named only after a separate permission, and even then this build will not submit a live order.
""".strip()

CATALOG = [
    {
        "id": "capital-epoch-and-brain",
        "title": "Practice capital, epoch P&L, and the learning brain",
        "status": "authored",
        "minutes": 12,
        "body": LESSON,
    },
    {
        "id": "orders-and-fills",
        "title": "Orders, fills, and caller-supplied paper prices",
        "status": "outline_only",
        "minutes": None,
        "outline": [
            "A pending order is an intention. It does not move capital.",
            "A fill needs an explicit paper price. This template has no market-data print behind that price.",
            "A sell closes a long quantity already held. It does not open a short.",
        ],
    },
    {
        "id": "spreads-and-costs",
        "title": "Spreads, fees, and why a paper fill can omit them",
        "status": "outline_only",
        "minutes": None,
        "outline": [
            "A later milestone can record a fee as its own ledger row.",
            "This template does not invent a spread and then call the result a market.",
        ],
    },
    {
        "id": "corporate-actions",
        "title": "Corporate actions and account currency",
        "status": "outline_only",
        "minutes": None,
        "outline": [
            "Account currency is chosen when the paper account is opened and a reset does not change it.",
            "Splits, dividends, and FX conversion are outline topics only. No vendor text is included.",
        ],
    },
    {
        "id": "what-a-license-is",
        "title": "What would have to be licensed before a feed is shown",
        "status": "outline_only",
        "minutes": None,
        "outline": [
            "A feed name, a license reference, and a delivery path are separate facts.",
            "This template keeps news blocked and does not redistribute headlines.",
        ],
    },
]


def catalog():
    return json_copy(CATALOG)


def lesson(lesson_id):
    for row in CATALOG:
        if row["id"] == lesson_id:
            return json_copy(row)
    return None


def json_copy(value):
    import json
    return json.loads(json.dumps(value))
