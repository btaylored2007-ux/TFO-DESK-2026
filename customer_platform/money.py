"""Exact currency amounts. Floats, NaN, and silent rounding are refused."""

import re
from decimal import Decimal, InvalidOperation

from customer_platform.errors import InvalidAmount

EXPONENT = {"USD": 2, "EUR": 2, "GBP": 2, "AUD": 2, "JPY": 0}
MAX_PAPER = {
    "USD": Decimal("1000000.00"),
    "EUR": Decimal("1000000.00"),
    "GBP": Decimal("1000000.00"),
    "AUD": Decimal("1000000.00"),
    "JPY": Decimal("100000000"),
}
_NONNEG = re.compile(r"^(0|[1-9]\d*)(\.(\d+))?$")
_SIGNED = re.compile(r"^-?(0|[1-9]\d*)(\.(\d+))?$")
_FX = re.compile(r"^(0|[1-9]\d*)(\.(\d{1,8}))?$")


def _quant(currency):
    if currency not in EXPONENT:
        raise InvalidAmount("currency is not supported")
    return Decimal(1).scaleb(-EXPONENT[currency])


def money_text(amount, currency):
    quant = _quant(currency)
    rendered = amount.quantize(quant)
    if rendered != amount:
        raise InvalidAmount("amount is not an exact currency unit")
    return format(rendered, "f")


def _as_text(value):
    if isinstance(value, bool) or isinstance(value, float):
        raise InvalidAmount("amount must be a decimal string, not a float")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise InvalidAmount("amount is not finite")
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def _parse(value, currency, pattern):
    text = _as_text(value)
    if text.lower() in {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}:
        raise InvalidAmount("amount is not finite")
    if not pattern.match(text):
        raise InvalidAmount("amount must be a decimal")
    frac = text.split(".", 1)[1] if "." in text else ""
    if len(frac) > EXPONENT[currency]:
        raise InvalidAmount("amount has more decimal places than the currency allows")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise InvalidAmount("amount is not finite") from exc
    if not amount.is_finite():
        raise InvalidAmount("amount is not finite")
    quant = _quant(currency)
    rendered = amount.quantize(quant)
    if rendered != amount:
        raise InvalidAmount("amount is not an exact currency unit")
    return rendered


def parse_money(value, currency):
    """Nonnegative currency amount, used for capital, funds, and prices."""
    amount = _parse(value, currency, _NONNEG)
    if amount < 0:
        raise InvalidAmount("amount must be nonnegative")
    if amount > MAX_PAPER[currency]:
        raise InvalidAmount("amount exceeds the template paper bound")
    return amount


def parse_signed_money(value, currency):
    """Realized P&L may be negative. The same exact-decimal rules apply."""
    amount = _parse(value, currency, _SIGNED)
    if abs(amount) > MAX_PAPER[currency]:
        raise InvalidAmount("amount exceeds the template paper bound")
    return amount


def parse_fx(value):
    if value is None:
        return Decimal("1")
    text = _as_text(value)
    if not _FX.match(text):
        raise InvalidAmount("fx rate is not an exact decimal")
    rate = Decimal(text)
    if not rate.is_finite() or rate <= 0 or rate > Decimal("1000000"):
        raise InvalidAmount("fx rate is out of range")
    return rate


def parse_exact(amount, currency):
    """Accept a Decimal that lands on a currency unit, including trailing zeros."""
    if isinstance(amount, bool) or isinstance(amount, float) or not isinstance(amount, Decimal):
        return parse_money(amount, currency)
    if not amount.is_finite():
        raise InvalidAmount("amount is not finite")
    quant = _quant(currency)
    rendered = amount.quantize(quant)
    if rendered != amount:
        raise InvalidAmount("amount is not an exact currency unit")
    if rendered < 0 or rendered > MAX_PAPER[currency]:
        raise InvalidAmount("amount is out of range")
    return rendered


def exact_notional(qty, price, fx, currency):
    total = qty * price * fx
    if total >= 0:
        return parse_exact(total, currency)
    rendered = parse_exact(abs(total), currency)
    return -rendered
