"""Explicit delayed-PAPER admission and chronological receipt fields."""
import datetime as dt


def eligible(quote):
    return isinstance(quote, dict) and (quote.get('executable') is True or quote.get('paper_executable') is True)


def receipt(quote):
    return {'price_class': quote.get('paper_price_class') or 'CURRENT PAPER',
            'price_source_utc': quote.get('quote_utc'),
            'price_received_utc': quote.get('fetched_at'),
            'price_model': quote.get('paper_price_model') or 'Native registered quote/cost model',
            'live_execution': False}


def chronological(quote, position):
    """A delayed exit cannot consume a pre-entry or already-filled bar."""
    if quote.get('paper_price_class') != 'DELAYED PAPER':
        return True
    try:
        parse = lambda s: dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
        stamp = parse(quote['quote_utc'])
        entry_evidence = position.get('entry_price_evidence') or {}
        source_entry = entry_evidence.get('price_source_utc') or entry_evidence.get('quote_utc')
        entry = source_entry or position.get('entry_utc') or position.get('opened_utc')
        if entry:
            opened = parse(entry)
        elif position.get('entry_epoch') is not None:
            opened = float(position['entry_epoch'])
        else:
            return False
        last = position.get('last_paper_exit_source_utc')
        return stamp > opened and (not last or stamp > parse(last))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
