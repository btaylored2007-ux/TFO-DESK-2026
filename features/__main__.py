"""Write the advisory store for the tickers on the command line. PAPER ONLY."""
import json, sys
from features.store import run


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    tickers = argv[1:]
    if not tickers:
        sys.stderr.write("usage: python -m features TICKER [TICKER...]\n")
        return 2
    out = run(tickers)
    sys.stdout.write(json.dumps({"tickers": out["tickers"], "regime": out["regime"], "errors": out["errors"]}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
