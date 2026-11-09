"""Reset practice capital to a chosen starting figure.

The new epoch's performance P&L starts at 0. Capital is the chosen amount,
which defaults to the account's configured starting capital. The old ledger
is appended, not rewritten.
"""

import os

from customer_platform.errors import Crash, IdempotencyConflict, InvalidAmount, ResetBlocked, StalePreview
from customer_platform.files import (
    file_lock,
    hash_tree,
    jsonl_line,
    process_lock,
    read_bytes,
    read_json,
    sha256_bytes,
    write_json,
    write_json_exclusive,
)
from customer_platform.money import money_text, parse_money
from customer_platform.paperbook import format_signed, new_id

RESET_PHRASE = "RESET PAPER CAPITAL"
ZERO_WARNING = (
    "Chosen starting capital is 0. New entries are blocked until a later paper-funds action. "
    "Exits still work. This does not erase the learning brain, strategies, or old ledger."
)


class ResetService:
    def __init__(self, book, brain_dir, knowledge_dir, assert_rails):
        self.book = book
        self.brain_dir = brain_dir
        self.knowledge_dir = knowledge_dir
        self.assert_rails = assert_rails
        self.journal = os.path.join(book.dir, "journal")
        self.snapshots = os.path.join(book.dir, "snapshots")
        self.idem_path = os.path.join(book.dir, "idempotency.json")
        self.lock_path = os.path.join(book.dir, "lock")

    def preview(self, amount=None):
        state = self.book.load()
        self.book.assert_paper(state)
        chosen, is_default = self._chosen(state, amount)
        currency = state["currency"]
        epoch = self.book.epoch(state)
        positions = [row for row in state["positions"] if row["status"] == "open"]
        pending = [row for row in state["orders"] if row["status"] == "pending"]
        warning = ZERO_WARNING if chosen == money_text(parse_money("0", currency), currency) else None
        blocked = None
        if positions or pending:
            blocked = "open positions or pending orders must be settled or canceled before a reset"
        performance = self.book.performance()
        token = self._token(state)
        return {
            "amount_is_default": is_default,
            "blocked": blocked,
            "brain_sha256": hash_tree(self.brain_dir),
            "chosen_starting_capital": chosen,
            "configured_starting_capital": state["configured_starting_capital"],
            "currency": currency,
            "current_capital_balance": epoch["capital_balance"],
            "current_epoch_id": epoch["epoch_id"],
            "current_epoch_session_pnl": epoch["session_realized_pnl"],
            "historical_realized_pnl": performance["historical_realized_pnl"],
            "knowledge_sha256": hash_tree(self.knowledge_dir),
            "new_capital_label": "Practice capital will be set to the chosen starting figure.",
            "new_epoch_capital": chosen,
            "new_epoch_session_pnl": money_text(parse_money("0", currency), currency),
            "new_epoch_session_pnl_label": "New performance epoch P&L starts at 0. This is not the capital figure.",
            "open_positions": positions,
            "pending_orders": pending,
            "preserved": [
                "learning brain",
                "strategy versions",
                "research",
                "append-only ledger",
                "currency",
                "cost basis and fx on old fills",
            ],
            "preview_token": token,
            "zero_warning": warning,
        }

    def apply(self, *, amount, preview_token, confirmation, idempotency_key, acknowledge_zero=False, crash_at=None):
        with process_lock(self.lock_path):
            with file_lock(self.lock_path):
                return self._apply(
                    amount=amount,
                    preview_token=preview_token,
                    confirmation=confirmation,
                    idempotency_key=idempotency_key,
                    acknowledge_zero=acknowledge_zero,
                    crash_at=crash_at,
                )

    def recover(self):
        with process_lock(self.lock_path):
            with file_lock(self.lock_path):
                return self._recover()

    def _apply(self, *, amount, preview_token, confirmation, idempotency_key, acknowledge_zero, crash_at):
        self._key(idempotency_key)
        state = self.book.load()
        self.book.assert_paper(state)
        self.assert_rails()
        chosen, _is_default = self._chosen(state, amount)
        request_hash = self._request_hash(state, chosen, acknowledge_zero, confirmation)
        record = self._idem_get(idempotency_key)
        if record and record["status"] == "committed":
            if record["request_hash"] != request_hash:
                raise IdempotencyConflict()
            return record["result"]
        if record and record["status"] == "intent":
            if record["request_hash"] != request_hash:
                raise IdempotencyConflict()
            return self._resume(record, crash_at)
        if record and record["status"] == "rolled_back":
            if record["request_hash"] != request_hash:
                raise IdempotencyConflict()
        if confirmation != RESET_PHRASE:
            raise ResetBlocked("confirmation_required", "type the reset confirmation before practice capital changes")
        preview = self.preview(chosen)
        if preview["blocked"]:
            raise ResetBlocked("open_risk", preview["blocked"])
        if preview["zero_warning"] and acknowledge_zero is not True:
            raise ResetBlocked("zero_acknowledgement_required", preview["zero_warning"])
        if preview_token != preview["preview_token"]:
            raise StalePreview()
        reset_id = new_id("rs_")
        before_state = read_bytes(self.book.state_path)
        before_ledger = self.book.ledger_bytes()
        after_state, extra = self._plan(state, chosen, reset_id)
        after_ledger = before_ledger + b"".join(jsonl_line(row) for row in extra)
        snapshot = os.path.join(self.snapshots, reset_id)
        write_json_exclusive(os.path.join(snapshot, "before_state.json"), {"raw_sha256": sha256_bytes(before_state)})
        _seal(os.path.join(snapshot, "state.before"), before_state)
        _seal(os.path.join(snapshot, "ledger.before"), before_ledger)
        _seal(os.path.join(snapshot, "state.after"), after_state)
        _seal(os.path.join(snapshot, "ledger.after"), after_ledger)
        result = self._result(state, chosen, reset_id, preview)
        intent = {
            "acknowledge_zero": acknowledge_zero is True,
            "after_ledger_sha256": sha256_bytes(after_ledger),
            "after_state_sha256": sha256_bytes(after_state),
            "before_ledger_sha256": sha256_bytes(before_ledger),
            "before_state_sha256": sha256_bytes(before_state),
            "brain_sha256": preview["brain_sha256"],
            "confirmation": confirmation,
            "idempotency_key": idempotency_key,
            "knowledge_sha256": preview["knowledge_sha256"],
            "request_hash": request_hash,
            "reset_id": reset_id,
            "result": result,
            "status": "intent",
        }
        write_json_exclusive(os.path.join(self.journal, reset_id + ".json"), intent)
        self._idem_put(idempotency_key, intent)
        self._fault(crash_at, "after_intent")
        return self._resume(intent, crash_at)

    def _resume(self, intent, crash_at):
        snapshot = os.path.join(self.snapshots, intent["reset_id"])
        before_state = read_bytes(os.path.join(snapshot, "state.before"))
        before_ledger = read_bytes(os.path.join(snapshot, "ledger.before"))
        after_state = read_bytes(os.path.join(snapshot, "state.after"))
        after_ledger = read_bytes(os.path.join(snapshot, "ledger.after"))
        current_state = read_bytes(self.book.state_path)
        current_ledger = self.book.ledger_bytes()
        if current_state == after_state and current_ledger == after_ledger:
            return self._commit(intent)
        if current_state != before_state or current_ledger != before_ledger:
            self._restore(before_state, before_ledger)
            self._mark(intent, "rolled_back")
            raise ResetBlocked("rolled_back", "partial reset was restored to the pre-reset paper book")
        self._fault(crash_at, "before_replace")
        _replace(self.book.state_path, after_state)
        self._fault(crash_at, "between_replaces")
        _replace(self.book.ledger_path, after_ledger)
        self._fault(crash_at, "before_commit")
        committed = self._commit(intent)
        self._unchanged(intent)
        return committed

    def _recover(self):
        actions = []
        if not os.path.isdir(self.journal):
            return actions
        idem = read_json(self.idem_path, {})
        for name in sorted(os.listdir(self.journal)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.journal, name)
            intent = read_json(path, None)
            if not intent or intent.get("status") == "committed":
                continue
            snapshot = os.path.join(self.snapshots, intent["reset_id"])
            before_state = read_bytes(os.path.join(snapshot, "state.before"))
            before_ledger = read_bytes(os.path.join(snapshot, "ledger.before"))
            after_state = read_bytes(os.path.join(snapshot, "state.after"))
            after_ledger = read_bytes(os.path.join(snapshot, "ledger.after"))
            current_state = read_bytes(self.book.state_path)
            current_ledger = self.book.ledger_bytes()
            if current_state == after_state and current_ledger == after_ledger:
                self._commit(intent)
                actions.append({"reset_id": intent["reset_id"], "action": "commit"})
            else:
                if current_state != before_state or current_ledger != before_ledger:
                    self._restore(before_state, before_ledger)
                self._mark(intent, "rolled_back")
                actions.append({"reset_id": intent["reset_id"], "action": "rolled_back"})
            idem[intent["idempotency_key"]] = read_json(path)
        if actions:
            write_json(self.idem_path, idem)
        return actions

    def _plan(self, state, chosen, reset_id):
        currency = state["currency"]
        epoch = self.book.epoch(state)
        closed_pnl = epoch["session_realized_pnl"]
        ending = epoch["capital_balance"]
        epoch["status"] = "closed"
        epoch["closed_utc"] = self.book.now()
        epoch["ending_capital"] = ending
        epoch["closed_session_pnl"] = closed_pnl
        new_epoch = new_id("ep_")
        state["current_epoch_id"] = new_epoch
        state["epochs"].append({
            "capital_balance": chosen,
            "closed_utc": None,
            "ending_capital": None,
            "epoch_id": new_epoch,
            "opened_utc": self.book.now(),
            "opening_capital": chosen,
            "session_realized_pnl": money_text(parse_money("0", currency), currency),
            "status": "open",
        })
        extra = [
            {
                "closed_session_pnl": closed_pnl,
                "currency": currency,
                "ending_capital": ending,
                "epoch_id": epoch["epoch_id"],
                "id": new_id("lg_"),
                "kind": "epoch_close",
                "label": "Epoch closed. Its fill P&L stays on the ledger.",
                "not_pnl": True,
                "reset_id": reset_id,
                "utc": self.book.now(),
            },
            {
                "amount": chosen,
                "currency": currency,
                "epoch_id": new_epoch,
                "id": new_id("lg_"),
                "kind": "balance_adjustment",
                "label": "Practice capital set to the chosen starting figure. Not a P&L entry.",
                "not_pnl": True,
                "prior_epoch_id": epoch["epoch_id"],
                "reset_id": reset_id,
                "utc": self.book.now(),
            },
        ]
        raw = write_json(self.book.state_path + ".plan", state)
        # The plan file is not the live state. Callers replace from the sealed snapshot.
        try:
            os.remove(self.book.state_path + ".plan")
        except OSError:
            pass
        return raw, extra

    def _result(self, state, chosen, reset_id, preview):
        currency = state["currency"]
        return {
            "brain_sha256": preview["brain_sha256"],
            "chosen_starting_capital": chosen,
            "currency": currency,
            "historical_realized_pnl": preview["historical_realized_pnl"],
            "knowledge_sha256": preview["knowledge_sha256"],
            "new_epoch_capital": chosen,
            "new_epoch_session_pnl": money_text(parse_money("0", currency), currency),
            "new_epoch_session_pnl_label": preview["new_epoch_session_pnl_label"],
            "prior_epoch_id": preview["current_epoch_id"],
            "prior_epoch_session_pnl": preview["current_epoch_session_pnl"],
            "reset_id": reset_id,
            "zero_warning": preview["zero_warning"],
        }

    def _commit(self, intent):
        path = os.path.join(self.journal, intent["reset_id"] + ".json")
        intent = dict(intent)
        intent["status"] = "committed"
        write_json(path, intent)
        record = self._idem_get(intent["idempotency_key"]) or {}
        record.update(intent)
        self._idem_put(intent["idempotency_key"], record)
        self._unchanged(intent)
        return intent["result"]

    def _mark(self, intent, status):
        path = os.path.join(self.journal, intent["reset_id"] + ".json")
        intent = dict(intent)
        intent["status"] = status
        write_json(path, intent)
        self._idem_put(intent["idempotency_key"], intent)

    def _restore(self, state_bytes, ledger_bytes):
        _replace(self.book.state_path, state_bytes)
        _replace(self.book.ledger_path, ledger_bytes)

    def _unchanged(self, intent):
        if hash_tree(self.brain_dir) != intent["brain_sha256"]:
            raise ResetBlocked("brain_changed", "reset stopped because the learning brain changed")
        if hash_tree(self.knowledge_dir) != intent["knowledge_sha256"]:
            raise ResetBlocked("knowledge_changed", "reset stopped because knowledge changed")

    def _chosen(self, state, amount):
        currency = state["currency"]
        if amount is None:
            return state["configured_starting_capital"], True
        try:
            chosen = money_text(parse_money(amount, currency), currency)
        except InvalidAmount:
            raise
        return chosen, chosen == state["configured_starting_capital"]

    def _token(self, state):
        raw = read_bytes(self.book.state_path) + b"\0" + self.book.ledger_bytes()
        raw += hash_tree(self.brain_dir).encode("utf-8")
        return sha256_bytes(raw)

    def _request_hash(self, state, chosen, acknowledge_zero, confirmation):
        payload = "|".join([
            state["currency"],
            chosen,
            "1" if acknowledge_zero is True else "0",
            str(confirmation or ""),
        ])
        return sha256_bytes(payload.encode("utf-8"))

    def _idem_get(self, key):
        return read_json(self.idem_path, {}).get(key)

    def _idem_put(self, key, record):
        body = read_json(self.idem_path, {})
        body[key] = record
        write_json(self.idem_path, body)

    def _key(self, key):
        text = str(key or "")
        if len(text) < 8 or len(text) > 80 or any(ch.isspace() for ch in text):
            raise ResetBlocked("idempotency", "idempotency key is not usable")

    def _fault(self, crash_at, point):
        if crash_at == point:
            raise Crash(point)


def _seal(path, data):
    from customer_platform.files import write_exclusive
    write_exclusive(path, data)


def _replace(path, data):
    temporary = path + ".replace"
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
