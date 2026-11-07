"""Customer platform facade. Reuses the dormant tenant store for passwords and sessions.

Nothing here imports the owner book, gate, desk, or owner learning loop.
"""

import json
import os
import time
from datetime import datetime, timezone

from decimal import Decimal

from veteran_desk.tenant_foundation import (
    RANK,
    Denied as FoundationDenied,
    FoundationOff,
    Store,
    _check_password,
    _hash_password,
    _reject_password,
    _token_hash,
)

from customer_platform.brain import Brain
from customer_platform.control import Control
from customer_platform.education import catalog, lesson
from customer_platform.errors import Denied, InvalidAmount, LiveDisabled, NewsBlocked, PaymentRejected, ResetBlocked
from customer_platform.files import ensure_dir, read_json, write_json
from customer_platform.journal import Journal
from customer_platform.money import money_text, parse_money, parse_signed_money
from customer_platform.paperbook import PaperBook, format_signed
from customer_platform.school import (
    School,
    load_author_draft,
    load_package,
    synthetic_exercise,
    synthetic_exercise_file,
)
from customer_platform.skills import Skills
from customer_platform.payments import Payments
from customer_platform.requests import Requests
from customer_platform.redact import redact
from customer_platform.reset import RESET_PHRASE, ResetService
from customer_platform.starter import starter_pack
from customer_platform.teaching import PROMOTE_PHRASE, TeachingStore, public_idea

FUNDS_PHRASE = "PAPER FUNDS NOT REAL MONEY"
SETTLE_PHRASE = "SETTLE PAPER POSITIONS AT MARKS"
PROVIDERS = ("anthropic", "deepseek", "grok", "openai")
BROKER_MODES = ("approval", "auto", "manual")


class Platform:
    def __init__(self, root, *, enabled=False, webhook_secret=None, clock=None,
                 signup_enabled=False, allow_fixtures=False):
        if enabled is not True:
            raise FoundationOff("customer foundation is off")
        self.root = root
        self.store = Store(root, enabled=True)
        self.clock_fn = clock
        self.webhook_secret = webhook_secret
        self.signup_enabled = bool(signup_enabled)
        self.allow_fixtures = bool(allow_fixtures)
        self.logs = []
        self.worker_enabled = False
        self._probes = {}
        self._ask_fixture = {}
        self._provider_transport = {}
        self.pass_mark = os.environ.get("CUSTOMER_PLATFORM_PASS_MARK", "0.80")
        self.school_draft = load_author_draft()
        self.incidents = None
        self.school_package = load_package(os.environ.get("CUSTOMER_PLATFORM_SCHOOL_PACKAGE"))
        ensure_dir(self.root)
        self._blank("operators.json", {})
        self._blank("operator_sessions.json", {})
        self.recover()
        from customer_platform.incidents import Incidents
        self.incidents = Incidents(self.store.root, self.clock)

    def clock(self):
        if self.clock_fn is None:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        value = self.clock_fn()
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return str(value)

    def log(self, message):
        self.logs.append(redact(message, self._needles()))

    def provision_tenant(self, email, password, display_name, *, starting_capital, currency="USD", fixture=False):
        if fixture and not self.allow_fixtures:
            raise Denied("fixture accounts are off")
        parse_money(starting_capital, currency)
        created = self.store.provision_tenant(email, password, display_name)
        tenant_id = created["tenant_id"]
        directory = self._tenant_dir(tenant_id)
        ensure_dir(directory)
        PaperBook(directory, self.clock).init_account(
            currency=currency, starting_capital=starting_capital, fixture=bool(fixture),
        )
        Brain(directory, self.clock, lambda: "disabled", self.log).init(currency)
        TeachingStore(os.path.join(directory, "knowledge"), lambda: set(), self.clock).load()
        self._save_entitlements(tenant_id, _blank_entitlements())
        write_json(os.path.join(directory, "secrets.json"), {})
        self._audit(tenant_id, created["user_id"], "provision_paper", True)
        return created

    def signup(self, email, password, display_name, *, starting_capital, currency="USD"):
        """Create one customer subscription from the web app. Closed unless this process enabled it."""
        if not self.signup_enabled:
            raise Denied("signup is closed")
        return self.provision_tenant(
            email, password, display_name, starting_capital=starting_capital, currency=currency,
        )

    def login(self, email, password):
        email = str(email or "").strip().lower()
        if self._rate_blocked(email):
            self.log("login refused")
            raise Denied("login refused")
        try:
            token = self.store.login(email, password)
        except FoundationDenied as exc:
            self._rate_fail(email)
            self.log("login refused")
            raise Denied("login refused") from exc
        self._rate_clear(email)
        return token

    def add_member(self, token, email, password, role):
        return self.store.add_member(token, email, password, role)

    def create_operator(self, email, password, display_name):
        _reject_password(password)
        email = str(email or "").strip().lower()
        operators = read_json(self._path("operators.json"), {})
        if email in operators:
            raise Denied("operator already exists")
        import uuid
        operator_id = "op_" + uuid.uuid4().hex[:16]
        operators[email] = {
            "display_name": str(display_name or "")[:80],
            "operator_id": operator_id,
            "password": _hash_password(password),
        }
        write_json(self._path("operators.json"), operators)
        return {"email": email, "operator_id": operator_id}

    def operator_login(self, email, password):
        operators = read_json(self._path("operators.json"), {})
        row = operators.get(str(email or "").strip().lower())
        if not row or not _check_password(password, row.get("password")):
            self.log("operator login refused")
            raise Denied("login refused")
        import secrets as _secrets
        token = _secrets.token_urlsafe(32)
        sessions = read_json(self._path("operator_sessions.json"), {})
        sessions[_token_hash(token)] = {
            "expires": time.time() + 12 * 3600,
            "operator_id": row["operator_id"],
        }
        write_json(self._path("operator_sessions.json"), sessions)
        return token

    def desk(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        book = self._book(ctx["tenant_id"])
        state = book.load()
        epoch = book.epoch(state)
        performance = book.performance()
        equity = book.equity(state)
        return {
            "brain": self.brain_status(token),
            "broker_mode": state["broker_mode"],
            "chart": book.chart(),
            "configured_starting_capital": state["configured_starting_capital"],
            "currency": state["currency"],
            "equity": equity,
            "fixture": bool(state.get("fixture")),
            "historical_realized_pnl": performance["historical_realized_pnl"],
            "historical_realized_pnl_label": performance["historical_realized_pnl_label"],
            "live_execution_enabled": False,
            "membership": self.membership(token),
            "open_positions": [row for row in state["positions"] if row["status"] == "open"],
            "pending_orders": [row for row in state["orders"] if row["status"] == "pending"],
            "positions": state["positions"],
            "practice_capital": epoch["capital_balance"],
            "practice_capital_label": "Practice capital of the open epoch. Not P&L.",
            "sector_opt_out": list(state["sector_opt_out"]),
            "session_pnl": epoch["session_realized_pnl"],
            "session_pnl_label": performance["current_epoch_session_pnl_label"],
            "tenant_id": ctx["tenant_id"],
            "trading": self._control(ctx["tenant_id"]).view(),
            "stop_distance": state.get("stop_distance") or "",
            "venue": state["venue"],
        }

    def performance(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return self._book(ctx["tenant_id"]).performance()

    def add_paper_funds(self, token, amount, acknowledgement, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        if acknowledgement != FUNDS_PHRASE:
            raise ResetBlocked("confirmation_required", "paper funds require an explicit acknowledgement")
        row = self._book(ctx["tenant_id"]).add_funds(amount)
        self._audit(ctx["tenant_id"], ctx["user_id"], "paper_funds", True)
        return row

    def set_starting_capital(self, token, amount, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        state = self._book(ctx["tenant_id"]).set_starting_default(amount)
        self._audit(ctx["tenant_id"], ctx["user_id"], "set_starting_capital", True)
        return {"configured_starting_capital": state["configured_starting_capital"], "currency": state["currency"]}

    def place_order(self, token, **kwargs):
        claimed = kwargs.pop("claimed_tenant", None)
        grade = kwargs.pop("grade", "A")
        trader_id = kwargs.pop("trader_id", None) or "desk"
        side = kwargs.get("side")
        action = "trade_close" if side == "sell" else "trade_open"
        ctx = self._require(token, action, claimed)
        kwargs["trader_id"] = trader_id
        claimed_account = kwargs.pop("account_id", None)
        if claimed_account not in (None, "", ctx["tenant_id"]):
            raise Denied("account does not match the session")
        kwargs["account_id"] = ctx["tenant_id"]
        kwargs.setdefault("strategy_id", "unattributed")
        kwargs.setdefault("strategy_version", "unknown")
        kwargs.setdefault("decision", "customer_order")
        armed = self._control(ctx["tenant_id"]).load().get("replay_armed")
        kwargs.setdefault("feed", "synthetic_replay" if armed else "unknown")
        if side == "buy":
            kwargs["stop"] = self._entry_stop(ctx["tenant_id"], kwargs.get("limit_price"), kwargs.get("stop"))
        if side == "sell":
            order = self._book(ctx["tenant_id"]).place_order(**kwargs)
        else:
            def _place():
                return self._book(ctx["tenant_id"]).place_order(**kwargs)
            order, _seq = self._control(ctx["tenant_id"]).enter(_place, grade=grade, trader_id=trader_id)
        self._audit(ctx["tenant_id"], ctx["user_id"], "place_order", True)
        return order

    def _entry_stop(self, tenant_id, limit_price, stop):
        if stop not in (None, ""):
            return stop
        distance = self._book(tenant_id).load().get("stop_distance") or ""
        if not distance:
            raise ResetBlocked("stop", "a new entry needs a stop")
        from decimal import Decimal
        price = Decimal(str(limit_price))
        width = Decimal(str(distance))
        if width <= 0 or width >= 1:
            raise ResetBlocked("stop", "stop distance is not valid")
        computed = (price * (Decimal("1") - width)).quantize(Decimal("0.01"))
        return format(computed, "f")

    def set_stop_distance(self, token, distance, claimed_tenant=None):
        from decimal import Decimal, InvalidOperation
        ctx = self._require(token, "admin", claimed_tenant)
        try:
            width = Decimal(str(distance))
        except InvalidOperation as exc:
            raise ResetBlocked("stop", "stop distance is not valid") from exc
        if not width.is_finite() or width <= 0 or width >= 1:
            raise ResetBlocked("stop", "stop distance is not valid")
        book = self._book(ctx["tenant_id"])
        state = book.load()
        state["stop_distance"] = format(width, "f")
        book.save(state)
        return {"stop_distance": state["stop_distance"]}

    def set_position_stop(self, token, symbol, stop, claimed_tenant=None):
        ctx = self._require(token, "trade_close", claimed_tenant)
        return self._book(ctx["tenant_id"]).set_stop(symbol, stop)

    def use_test_feed_probe(self, token, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        self._probes[ctx["tenant_id"]] = "test"
        return {"live_feed": False, "probe": "test"}
        ctx = self._require(token, "admin", claimed_tenant)
        self._probes[ctx["tenant_id"]] = "test"
        return {"live_feed": False, "probe": "test"}

    def start_trading(self, token, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        result = self._control(ctx["tenant_id"]).start()
        if not result["started"]:
            self.incidents.detect("prices_unavailable", ctx["tenant_id"], evidence=["prices not available"])
        self._audit(ctx["tenant_id"], ctx["user_id"], "start_trading", result["started"])
        return result

    def stop_trading(self, token, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        result = self._control(ctx["tenant_id"]).stop()
        self._audit(ctx["tenant_id"], ctx["user_id"], "stop_trading", True)
        return result

    def enqueue_entry(self, token, payload, *, grade="A", trader_id="desk", claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        return self._control(ctx["tenant_id"]).enqueue(payload, grade=grade, trader_id=trader_id)

    def commit_queued(self, token, claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        control = self._control(ctx["tenant_id"])

        def _place(item):
            body = dict(item["payload"])
            body["trader_id"] = item["trader_id"]
            return self._book(ctx["tenant_id"]).place_order(**body)

        return control.commit_queue(_place)

    def set_activity(self, token, level, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        return self._control(ctx["tenant_id"]).set_activity(level)

    def set_allow_b(self, token, allowed, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        return self._control(ctx["tenant_id"]).set_allow_b(allowed)

    def set_trader_entries(self, token, trader_id, enabled, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        return self._control(ctx["tenant_id"]).set_entries(trader_id, enabled)

    def school_catalog(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return self._school(ctx["tenant_id"]).catalog()

    def school_progress(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return self._school(ctx["tenant_id"]).progress()

    def school_begin(self, token, module_id, *, review_ack=False, claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        return self._school(ctx["tenant_id"]).begin_attempt(module_id, review_ack=review_ack)

    def school_submit(self, token, attempt_id, answers, claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        if not isinstance(answers, dict):
            raise ResetBlocked("answers", "Answers are required")
        return self._school(ctx["tenant_id"]).submit(attempt_id, answers)

    def school_lesson(self, token, module_id, lesson_id, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return self._school(ctx["tenant_id"]).read_lesson(module_id, lesson_id)

    def school_rubric(self, token, module_id, marks, *, profit_claimed=False, claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        return self._school(ctx["tenant_id"]).save_rubric(module_id, marks, profit_claimed=profit_claimed)

    def school_exercise(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        self._school(ctx["tenant_id"])
        return synthetic_exercise()

    def school_exercise_file(self, token, name, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        self._school(ctx["tenant_id"])
        return synthetic_exercise_file(name)

    def teach_list(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return self._skills(ctx["tenant_id"]).list_skills()

    def teach_save(self, token, *, trader_ids, text="", rules=None, claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        return self._skills(ctx["tenant_id"]).save_description(
            user_id=ctx["user_id"], trader_ids=trader_ids, text=text, rules=rules,
        )

    def teach_upload(self, token, *, trader_ids, filename, data, claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        return self._skills(ctx["tenant_id"]).save_upload(
            user_id=ctx["user_id"], trader_ids=trader_ids, filename=filename, data=data,
        )

    def teach_status(self, token, skill_id, status, evidence_ids=None, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        before = self._rails_bytes(ctx["tenant_id"])
        result = self._skills(ctx["tenant_id"]).set_status(skill_id, status, evidence_ids=evidence_ids, known_evidence=[])
        if self._rails_bytes(ctx["tenant_id"]) != before:
            raise ResetBlocked("rails", "a skill cannot change hard rails")
        return result

    def teach_run_test(self, token, skill_id, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        before = self._rails_bytes(ctx["tenant_id"])
        result = self._skills(ctx["tenant_id"]).run_paper_test(skill_id)
        if self._rails_bytes(ctx["tenant_id"]) != before:
            raise ResetBlocked("rails", "a skill cannot change hard rails")
        return result

    def teach_attest(self, token, skill_id, claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        return self._skills(ctx["tenant_id"]).attest(skill_id)

    def teach_rollback(self, token, skill_id, version, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        return self._skills(ctx["tenant_id"]).rollback(skill_id, version)

    def ask(self, token, text, idempotency_key, claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        row = self._requests(ctx["tenant_id"]).submit(
            user_id=ctx["user_id"], text=text, idempotency_key=idempotency_key,
        )
        return self._route_ask(ctx["tenant_id"], row)

    def ask_retry(self, token, request_id, claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        box = self._requests(ctx["tenant_id"])
        current = box.customer_view(request_id)
        fixture = self._ask_fixture.get(ctx["tenant_id"])
        if current["status"] != "pending" or not fixture:
            box.note_unconnected_retry(request_id)
            view = box.customer_view(request_id)
            view["calls"] = 0
            view["job"] = "unconnected"
            return view
        return box.deliver(
            request_id, sender="synthetic_fixture", body_text=fixture,
            kind="lead", delivery_key="fixture-" + request_id,
        )

    def ask_cancel(self, token, request_id, claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        return self._requests(ctx["tenant_id"]).cancel(request_id)

    def _route_ask(self, tenant_id, row):
        from customer_platform.ai_vault import public_keys, vault_status
        from customer_platform.provider_jobs import ProviderJobs
        status = vault_status()
        keys = public_keys(self._key_path(tenant_id)) if status["available"] else []
        provider = keys[0]["provider"] if keys else "openai"
        transport = self._provider_transport.get(tenant_id)
        jobs = ProviderJobs(
            os.path.join(self._tenant_dir(tenant_id), "requests", "provider_jobs.json"),
            self.clock,
            transport=transport,
        )
        job = jobs.open_job(request_id=row["id"], provider=provider, has_credential=bool(keys))
        secret = ""
        if keys and transport is not None:
            secret = self._customer_secret(tenant_id, provider)
        result = jobs.run(job["id"], secret)
        view = dict(row)
        view["calls"] = 0
        view["job"] = result["status"]
        view["provenance"] = result["error"] or "not_sent"
        return view

    def _customer_secret(self, tenant_id, provider):
        from cryptography.fernet import Fernet
        from customer_platform.ai_vault import vault_status
        if not vault_status()["available"]:
            return ""
        body = read_json(self._key_path(tenant_id), {})
        row = body.get(provider) or {}
        token = row.get("ciphertext") or ""
        if not token:
            return ""
        return Fernet(os.environ["CUSTOMER_PLATFORM_DATA_KEY"].encode("utf-8")).decrypt(token.encode("utf-8")).decode("utf-8")

    def use_mock_provider(self, token, transport, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        self._provider_transport[ctx["tenant_id"]] = transport
        return {"network": False, "tenant_id": ctx["tenant_id"]}

    def _ask_provenance(self, tenant_id):
        from customer_platform.ai_vault import public_keys, vault_status
        status = vault_status()
        if not status["available"]:
            return "not_sent"
        if not public_keys(self._key_path(tenant_id)):
            return "not_sent"
        return "key stored; outbound call is off"

    def use_synthetic_reply(self, token, body, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        self._ask_fixture[ctx["tenant_id"]] = str(body)
        return {"calls": 0, "fixture": True, "provider_call": False}

    def key_status(self, token, claimed_tenant=None):
        from customer_platform.ai_vault import public_keys, vault_status
        ctx = self._require(token, "read", claimed_tenant)
        status = dict(vault_status())
        reason = status.get("reason") or ""
        if "CUSTOMER_PLATFORM" in reason or "not set" in reason or "not installed" in reason:
            status["reason"] = "Key storage is not ready on this server."
        rows = public_keys(self._key_path(ctx["tenant_id"])) if status["available"] else []
        return {"keys": rows, "status": status}

    def key_save(self, token, provider, secret, claimed_tenant=None):
        from customer_platform.ai_vault import save_key
        ctx = self._require(token, "admin", claimed_tenant)
        saved = save_key(self._key_path(ctx["tenant_id"]), provider, secret)
        self._audit(ctx["tenant_id"], ctx["user_id"], "key_save", True)
        return saved

    def key_remove(self, token, provider, claimed_tenant=None):
        from customer_platform.ai_vault import remove_key
        ctx = self._require(token, "admin", claimed_tenant)
        return remove_key(self._key_path(ctx["tenant_id"]), provider)

    def key_test(self, token, provider, claimed_tenant=None):
        from customer_platform.ai_vault import connection_test
        ctx = self._require(token, "admin", claimed_tenant)
        return connection_test(self._key_path(ctx["tenant_id"]), provider, transport=None)

    def _key_path(self, tenant_id):
        return os.path.join(self._tenant_dir(tenant_id), "vault", "keys.json")

    def teach_model(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        fills = []
        for line in self._book(ctx["tenant_id"]).ledger():
            if line.get("kind") == "fill":
                fills.append(line.get("id") or line.get("fill_id") or "")
        return {"fills": [item for item in fills if item][-12:], "skills": self._skills(ctx["tenant_id"]).list_skills()}

    def operator_incident_action(self, operator_token, incident_id, action, *, learning="", outcome="blocked"):
        operator = self._operator(operator_token)
        owner = operator.get("email") or "operator"
        if action == "ack":
            return self.incidents.assign(incident_id, owner)
        if action == "repair":
            return self.incidents.repair(incident_id, "recheck_prices", owner, lambda: [])
        if action == "verify":
            return self.incidents.verify(incident_id, owner, lambda: [])
        if action == "close":
            return self.incidents.close(incident_id, owner, outcome, learning=learning)
        if action == "propose":
            return self.incidents.propose(
                incident_id, owner, priority="medium", test="operator reviewed the prices gate",
                text=learning or "proposed from the operator queue",
            )
        raise Denied("incident action is not valid")

    def ask_view(self, token, request_id, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return self._requests(ctx["tenant_id"]).customer_view(request_id)

    def ask_reviews(self, token, request_id, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return self._requests(ctx["tenant_id"]).reviews(request_id)

    def ask_list(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return self._requests(ctx["tenant_id"]).list_for(ctx["user_id"])

    def ask_consider(self, token, text, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return self._requests(ctx["tenant_id"]).consider_text(text)

    def _requests(self, tenant_id):
        return Requests(self._tenant_dir(tenant_id), self.clock)

    def journal_add(self, token, *, kind, text, module_id="", claimed_tenant=None):
        ctx = self._require(token, "trade_open", claimed_tenant)
        return self._journal(ctx["tenant_id"]).add(user_id=ctx["user_id"], kind=kind, text=text, module_id=module_id)

    def journal_list(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return self._journal(ctx["tenant_id"]).entries()

    def private_board(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        book = self._book(ctx["tenant_id"])
        state = book.load()
        currency = state["currency"]
        rows = {}
        for line in book.ledger():
            if line.get("kind") != "fill" or line.get("side") != "sell":
                continue
            trader = line.get("trader_id") or "desk"
            bucket = rows.setdefault(trader, {"net": Decimal("0"), "samples": 0})
            bucket["net"] += parse_signed_money(line["realized_pnl"], currency)
            bucket["samples"] += 1
        samples = sum(item["samples"] for item in rows.values())
        return {
            "costs": "Net is sale proceeds minus cost basis. No separate fee was recorded.",
            "empty": samples == 0,
            "fixture": bool(state.get("fixture")),
            "private": True,
            "results": [
                {"net": format_signed(item["net"], currency), "samples": item["samples"], "trader_id": trader}
                for trader, item in rows.items()
            ],
            "samples": samples,
            "window": "all closed paper fills on this login",
        }

    def fill_order(self, token, order_id, *, price, fx=None, fixture=False, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        state = self._book(ctx["tenant_id"]).load()
        pending = [row for row in state["orders"] if row["order_id"] == order_id and row["status"] == "pending"]
        if not pending:
            raise ResetBlocked("order", "pending order was not found")
        action = "trade_close" if pending[0]["side"] == "sell" else "trade_open"
        ctx = self._require(token, action, claimed_tenant)
        if fixture and not state.get("fixture"):
            raise Denied("fixture fills are only available on a fixture account")
        if pending[0]["side"] == "buy":
            def _fill():
                return self._book(ctx["tenant_id"]).fill_order(order_id, price=price, fx=fx, fixture=fixture)
            row, _seq = self._control(ctx["tenant_id"]).enter(_fill, trader_id=pending[0].get("trader_id") or "desk")
        else:
            row = self._book(ctx["tenant_id"]).fill_order(order_id, price=price, fx=fx, fixture=fixture)
        self._brain(ctx["tenant_id"]).mark_pending()
        self._audit(ctx["tenant_id"], ctx["user_id"], "fill_order", True)
        return row

    def cancel_order(self, token, order_id, *, confirmation, claimed_tenant=None):
        ctx = self._require(token, "trade_close", claimed_tenant)
        if confirmation != "CANCEL PENDING PAPER ORDERS":
            raise ResetBlocked("confirmation_required", "cancel requires an explicit confirmation")
        return self._book(ctx["tenant_id"]).cancel_order(order_id)

    def mark_position(self, token, symbol, mark, claimed_tenant=None):
        ctx = self._require(token, "trade_close", claimed_tenant)
        book = self._book(ctx["tenant_id"])
        state = book.mark_position(symbol, mark)
        for position in list(state["positions"]):
            if position["status"] != "open" or not position.get("stop"):
                continue
            if parse_money(position["mark"], state["currency"]) > parse_money(position["stop"], state["currency"]):
                continue
            order = book.place_order(
                symbol=position["symbol"], side="sell", qty=position["qty"],
                limit_price=position["mark"], sector=position["sector"],
                idempotency_key="stop-" + position["position_id"], trader_id=position.get("trader_id") or "desk",
            )
            book.fill_order(order["order_id"], price=position["mark"])
        return book.load()

    def set_stop(self, token, symbol, stop, claimed_tenant=None):
        ctx = self._require(token, "trade_close", claimed_tenant)
        return self._book(ctx["tenant_id"]).set_stop(symbol, stop)

    def settle_flat(self, token, *, confirmation, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        if confirmation != SETTLE_PHRASE:
            raise ResetBlocked("confirmation_required", "settling positions is separate from a capital reset")
        result = self._book(ctx["tenant_id"]).settle_flat()
        self._brain(ctx["tenant_id"]).mark_pending()
        self._audit(ctx["tenant_id"], ctx["user_id"], "settle_flat", True)
        return result

    def preview_reset(self, token, amount=None, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        return self._reset(ctx["tenant_id"]).preview(amount)

    def reset_capital(self, token, *, amount=None, preview_token, confirmation, idempotency_key,
                      acknowledge_zero=False, claimed_tenant=None, crash_at=None):
        ctx = self._require(token, "admin", claimed_tenant)
        result = self._reset(ctx["tenant_id"]).apply(
            amount=amount,
            preview_token=preview_token,
            confirmation=confirmation,
            idempotency_key=idempotency_key,
            acknowledge_zero=acknowledge_zero,
            crash_at=crash_at,
        )
        self._audit(ctx["tenant_id"], ctx["user_id"], "reset_capital", True)
        self.log("paper capital reset " + result["reset_id"])
        return result

    def set_sector_opt_out(self, token, sectors, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        state = self._book(ctx["tenant_id"]).set_opt_out(sectors)
        return {"sector_opt_out": state["sector_opt_out"], "open_positions": state["positions"]}

    def submit_idea(self, token, *, kind, body, idempotency_key, claimed_tenant=None):
        ctx = self._require(token, "submit", claimed_tenant)
        idea = self._teaching(ctx["tenant_id"]).submit(
            user_id=ctx["user_id"], kind=kind, body=body, idempotency_key=idempotency_key,
        )
        self._brain(ctx["tenant_id"]).mark_pending()
        self._audit(ctx["tenant_id"], ctx["user_id"], "submit_idea", True)
        return public_idea(idea)

    def record_paper_test(self, token, idea_id, ledger_ids, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        idea = self._teaching(ctx["tenant_id"]).record_paper_test(idea_id, ledger_ids)
        return public_idea(idea)

    def promote_idea(self, token, idea_id, confirmation, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        before = self._rails_bytes(ctx["tenant_id"])
        idea = self._teaching(ctx["tenant_id"]).promote(idea_id, confirmation)
        self._brain(ctx["tenant_id"]).assert_rails()
        if self._rails_bytes(ctx["tenant_id"]) != before:
            raise ResetBlocked("rails", "promotion must not change hard rails")
        self._brain(ctx["tenant_id"]).mark_pending()
        self._audit(ctx["tenant_id"], ctx["user_id"], "promote_idea", True)
        return public_idea(idea)

    def rollback_idea(self, token, idea_id, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        return public_idea(self._teaching(ctx["tenant_id"]).rollback(idea_id))

    def ideas(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        store = self._teaching(ctx["tenant_id"]).load()
        return [public_idea(idea) for idea in store["ideas"]]

    def starter(self):
        return starter_pack()

    def education(self):
        return catalog()

    def education_lesson(self, lesson_id):
        return lesson(lesson_id)

    def brain_status(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        brain = self._brain(ctx["tenant_id"])
        return brain.status(
            worker_enabled=self.worker_enabled,
            provider_status=self._provider_summary(ctx["tenant_id"]),
        )

    def brain_lessons(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        rows = self._brain(ctx["tenant_id"]).lesson_rows()
        return [
            {
                "evidence_ids": row["evidence_ids"],
                "kind": row["kind"],
                "lesson_id": row["lesson_id"],
                "statement": row["statement"],
                "status": row["status"],
                "trading_effect": row["trading_effect"],
            }
            for row in rows
        ]

    def run_brain(self, token, *, trigger, idempotency_key, claimed_tenant=None, crash_at=None):
        ctx = self._require(token, "admin", claimed_tenant)
        if trigger not in {"manual", "event", "schedule"}:
            raise Denied("unknown brain trigger")
        return self._run_brain_tenant(ctx["tenant_id"], trigger=trigger, key=idempotency_key, crash_at=crash_at)

    def brain_tick(self):
        results = []
        now = self.clock()
        for tenant_id in self._tenant_ids():
            cursor = read_json(os.path.join(self._tenant_dir(tenant_id), "brain", "cursor.json"), None)
            if not cursor:
                continue
            due = cursor["next_due_utc"] <= now
            if not cursor["pending_event"] and not due:
                continue
            trigger = "event" if cursor["pending_event"] else "schedule"
            key = "tick-{0}-{1}-{2}-{3}".format(
                tenant_id, trigger, cursor["ledger_index"], cursor["teaching_index"],
            )
            results.append(self._run_brain_tenant(tenant_id, trigger=trigger, key=key))
        return results

    def accept_lesson(self, token, lesson_id, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        before = self._rails_bytes(ctx["tenant_id"])
        accepted = self._brain(ctx["tenant_id"]).accept_lesson(lesson_id)
        if self._rails_bytes(ctx["tenant_id"]) != before:
            raise ResetBlocked("rails", "validating a lesson must not change hard rails")
        self._audit(ctx["tenant_id"], ctx["user_id"], "accept_lesson", True)
        return {
            "activation": accepted.get("activation", "not_activated"),
            "lesson_id": accepted["lesson_id"],
            "status": accepted["status"],
            "trading_effect": accepted["trading_effect"],
        }

    def skip_brain_line(self, token, ledger_index, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        self._brain(ctx["tenant_id"]).skip_line(ledger_index)
        self._audit(ctx["tenant_id"], ctx["user_id"], "skip_brain_line", True)

    def set_brain_due(self, token, utc, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        self._brain(ctx["tenant_id"]).set_due(utc)

    def set_spend_cap(self, token, amount, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        text = money_text(parse_money(amount, "USD"), "USD")
        self._brain(ctx["tenant_id"]).set_spend_cap(text)
        return {"spend_cap": text, "model_running": False, "outbound_ai": "not_wired"}

    def declare_secret_ref(self, token, provider, claimed_tenant=None, **extra):
        if extra:
            raise Denied("raw provider keys are not accepted")
        ctx = self._require(token, "admin", claimed_tenant)
        if provider not in PROVIDERS:
            raise Denied("provider is not configurable")
        import secrets as _secrets
        ref = "sec_" + _secrets.token_hex(8)
        path = os.path.join(self._tenant_dir(ctx["tenant_id"]), "secrets.json")
        body = read_json(path, {})
        body[provider] = {"declared_utc": self.clock(), "ref": ref}
        write_json(path, body)
        self._audit(ctx["tenant_id"], ctx["user_id"], "declare_secret_ref", True)
        return self.provider_status(token)

    def provider_status(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return self._provider_rows(ctx["tenant_id"])

    def start_checkout(self, token, claimed_tenant=None):
        ctx = self._require(token, "checkout", claimed_tenant)
        session = Payments(self._tenant_dir(ctx["tenant_id"]), self.webhook_secret).start_checkout(ctx["tenant_id"])
        entitlements = self._entitlements(ctx["tenant_id"])
        if entitlements["sandbox_payment_state"] == "none":
            entitlements["sandbox_payment_state"] = "checkout_open"
            self._save_entitlements(ctx["tenant_id"], entitlements)
        return session

    def apply_webhook(self, raw_body, signature):
        def entitlements_for(tenant_id):
            if tenant_id not in self._tenant_ids():
                raise PaymentRejected("unknown_tenant")
            return self._entitlements(tenant_id)

        def persist(tenant_id, body):
            self._save_entitlements(tenant_id, body)

        return Payments(self.root, self.webhook_secret).apply_webhook(
            raw_body, signature, entitlements_for, persist,
        )

    def membership(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        body = self._entitlements(ctx["tenant_id"])
        return {
            "blocked": bool(body["blocked"]),
            "commercial_paid": False,
            "commercial_paid_reason": "no payment provider is configured in this foundation",
            "news": body["news"],
            "sandbox_payment_state": body["sandbox_payment_state"],
        }

    def news(self, token, claimed_tenant=None):
        ctx = self._require(token, "read", claimed_tenant)
        return dict(self._entitlements(ctx["tenant_id"])["news"])

    def fetch_news(self, token, claimed_tenant=None):
        self._require(token, "read", claimed_tenant)
        raise NewsBlocked()

    def note_license(self, token, feed_id, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        text = str(feed_id or "").strip()
        if not text or len(text) > 40:
            raise Denied("feed id is not usable")
        body = self._entitlements(ctx["tenant_id"])
        body["news"]["feeds"] = [{
            "delivery": "not_wired",
            "feed_id": text,
            "redistribution": False,
            "state": "unconfigured",
        }]
        body["news"]["state"] = "unconfigured"
        body["news"]["redistribution"] = False
        body["news"]["delivery"] = "not_wired"
        self._save_entitlements(ctx["tenant_id"], body)
        return self.news(token)

    def grant_broker_mode(self, token, mode, confirmation, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        if mode not in BROKER_MODES:
            raise Denied("broker mode is not configurable")
        if confirmation != "GRANT BROKER MODE " + mode.upper():
            raise ResetBlocked("confirmation_required", "broker mode permission is explicit")
        book = self._book(ctx["tenant_id"])
        state = book.load()
        state["broker_permissions"][mode] = True
        state["live_execution_enabled"] = False
        book.save(state)
        self._audit(ctx["tenant_id"], ctx["user_id"], "grant_broker_mode", True)
        return {"broker_mode": state["broker_mode"], "live_execution_enabled": False, "permissions": state["broker_permissions"]}

    def set_broker_mode(self, token, mode, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        book = self._book(ctx["tenant_id"])
        state = book.load()
        if mode == "paper":
            state["broker_mode"] = "paper"
        elif mode in BROKER_MODES and state["broker_permissions"].get(mode):
            state["broker_mode"] = mode
        else:
            raise Denied("that broker mode has no explicit permission")
        state["live_execution_enabled"] = False
        book.save(state)
        return {"broker_mode": state["broker_mode"], "live_execution_enabled": False, "venue": state["venue"]}

    def submit_live_order(self, token, **_ignored):
        self._require(token, "admin")
        raise LiveDisabled("customer live execution is not implemented")

    def admin_overview(self, operator_token):
        self._operator(operator_token)
        rows = []
        accounts = read_json(self._path("accounts.json"), {})
        names = {}
        for row in accounts.values():
            names[row["tenant_id"]] = row.get("display_name") or ""
        for tenant_id in self._tenant_ids():
            entitlements = self._entitlements(tenant_id)
            brain = self._brain(tenant_id).status(
                worker_enabled=self.worker_enabled,
                provider_status=self._provider_summary(tenant_id),
            )
            rows.append({
                "brain_last_error_code": brain["last_error_code"],
                "brain_persisted_lessons": brain["persisted_lesson_count"],
                "brain_updated": brain["brain_updated"],
                "commercial_paid": False,
                "display_name": names.get(tenant_id, ""),
                "model_running": False,
                "news": entitlements["news"]["state"],
                "providers": self._provider_summary(tenant_id),
                "sandbox_payment_state": entitlements["sandbox_payment_state"],
                "tenant_id": tenant_id,
            })
        return {"commercial_paid": False, "tenants": rows}

    def block_tenant(self, operator_token, tenant_id, blocked=True):
        self._operator(operator_token)
        if tenant_id not in self._tenant_ids():
            raise Denied("tenant was not found")
        body = self._entitlements(tenant_id)
        body["blocked"] = bool(blocked)
        body["commercial_paid"] = False
        self._save_entitlements(tenant_id, body)
        return {"blocked": body["blocked"], "tenant_id": tenant_id}

    def setup_status(self):
        return {
            "ai_outbound": "not_wired",
            "commercial_paid_possible": False,
            "hosted_checkout": False,
            "listen": "loopback only when the process is started with CUSTOMER_PLATFORM_ENABLE=1",
            "live_broker": "interface only; submit_live_order is disabled",
            "news": "blocked unless a license is recorded, and delivery stays unwired",
            "owner_desk_mounted": False,
            "payments": "signed webhook contract and test fixtures; no provider credentials and no charges",
            "platform_enabled": True,
            "public_signup": False,
            "web_signup_enabled": self.signup_enabled,
            "web_signup": "loopback web app only, and only when this process enables signup; not a public website",
            "reset": "practice capital can be set to a chosen starting amount; new epoch P&L starts at 0",
            "product": "hosted customer web app",
            "distribution": "browser sessions only; no desktop installer and no self-host package",
            "later_not_built": ["marketing website integration", "public leaderboard statistics"],
            "private_performance_published": False,
        }

    def recover(self):
        actions = []
        for tenant_id in self._tenant_ids():
            if os.path.isdir(os.path.join(self._tenant_dir(tenant_id), "paper")):
                actions.extend(self._reset(tenant_id).recover())
        return actions

    def tree_hashes(self, tenant_id):
        directory = self._tenant_dir(tenant_id)
        from customer_platform.files import hash_tree
        return {
            "brain": hash_tree(os.path.join(directory, "brain")),
            "knowledge": hash_tree(os.path.join(directory, "knowledge")),
            "ledger": self._book(tenant_id).ledger_bytes(),
        }

    def _run_brain_tenant(self, tenant_id, *, trigger, key, crash_at=None):
        directory = self._tenant_dir(tenant_id)
        brain = self._brain(tenant_id)
        ledger = self._book(tenant_id).ledger()
        teachings = TeachingStore(os.path.join(directory, "knowledge"), self._ledger_ids(tenant_id), self.clock).history()
        return brain.run(
            trigger=trigger,
            idempotency_key=key,
            tenant_id=tenant_id,
            ledger=ledger,
            teachings=teachings,
            crash_at=crash_at,
        )

    def _require(self, token, action, claimed=None):
        try:
            ctx = self.store.context(token)
        except FoundationDenied as exc:
            raise Denied("session refused") from exc
        if claimed not in (None, "", ctx["tenant_id"]):
            self._audit(ctx["tenant_id"], ctx["user_id"], "cross_tenant", False)
            raise Denied("tenant does not match the session")
        minimum = {
            "admin": "tenant_admin",
            "checkout": "tenant_admin",
            "promote": "tenant_admin",
            "read": "viewer",
            "submit": "member",
            "trade_close": "member",
            "trade_open": "member",
        }[action]
        if RANK[ctx["role"]] < RANK[minimum]:
            self._audit(ctx["tenant_id"], ctx["user_id"], action, False)
            raise Denied("role cannot " + action)
        if self._entitlements(ctx["tenant_id"])["blocked"] and action in {"checkout", "promote", "submit", "trade_open"}:
            raise Denied("membership is blocked")
        return ctx

    def operator_incidents(self, operator_token):
        self._operator(operator_token)
        return self.incidents.list_for_operator()

    def _operator(self, token):
        sessions = read_json(self._path("operator_sessions.json"), {})
        row = sessions.get(_token_hash(str(token or "")))
        if not row or float(row.get("expires") or 0) < time.time():
            raise Denied("operator session refused")
        return row

    def _book(self, tenant_id):
        return PaperBook(self._tenant_dir(tenant_id), self.clock)

    def _control(self, tenant_id):
        book = self._book(tenant_id)
        control = Control(self._tenant_dir(tenant_id), self.clock, book.cancel_pending_buys)
        if self._probes.get(tenant_id) == "test" or control.load().get("replay_armed"):
            control.use_test_probe()
            self._probes[tenant_id] = "test"
        return control

    def arm_synthetic_replay(self, token, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        self._probes[ctx["tenant_id"]] = "test"
        result = self._control(ctx["tenant_id"]).arm_replay()
        self._audit(ctx["tenant_id"], ctx["user_id"], "arm_replay", True)
        return result

    def set_replay_print(self, token, price, claimed_tenant=None):
        ctx = self._require(token, "admin", claimed_tenant)
        if claimed_tenant not in (None, "", ctx["tenant_id"]):
            raise Denied("account does not match the session")
        return self._control(ctx["tenant_id"]).set_replay_print(price)

    def paper_tick_tenant(self, tenant_id):
        from customer_platform.paper_engine import tick_tenant
        return tick_tenant(self, tenant_id)

    def paper_tick_all(self):
        base = os.path.join(self.store.root, "tenants")
        if not os.path.isdir(base):
            return []
        return [self.paper_tick_tenant(tenant_id) for tenant_id in os.listdir(base) if not tenant_id.startswith(".")]

    def _journal(self, tenant_id):
        return Journal(self._tenant_dir(tenant_id), self.clock)

    def _skills(self, tenant_id):
        return Skills(self._tenant_dir(tenant_id), self.clock, ai_connected=False)

    def _school(self, tenant_id):
        return School(
            self._tenant_dir(tenant_id), self.clock,
            pass_mark=self.pass_mark, draft=self.school_draft, published=False,
        )

    def _rate_blocked(self, email):
        row = read_json(self._path("rate.json"), {}).get(email) or {}
        return int(row.get("fails") or 0) >= 8 and float(row.get("until") or 0) > time.time()

    def _rate_fail(self, email):
        body = read_json(self._path("rate.json"), {})
        row = body.get(email) or {"fails": 0}
        row["fails"] = int(row.get("fails") or 0) + 1
        row["until"] = time.time() + 60
        body[email] = row
        write_json(self._path("rate.json"), body)

    def _rate_clear(self, email):
        body = read_json(self._path("rate.json"), {})
        if email in body:
            del body[email]
            write_json(self._path("rate.json"), body)

    def backup_store(self, dest):
        import hashlib
        import shutil
        os.makedirs(dest, mode=0o700, exist_ok=True)
        manifest = []
        for dirpath, _dirs, names in os.walk(self.store.root):
            for name in sorted(names):
                if name.endswith(".tmp") or name == "lock":
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, self.store.root)
                target = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
                shutil.copy2(full, target)
                digest = hashlib.sha256(open(full, "rb").read()).hexdigest()
                manifest.append({"path": rel, "sha256": digest})
        write_json(os.path.join(dest, "BACKUP_MANIFEST.json"), {"files": manifest})
        return {"files": len(manifest), "dest": dest}

    def restore_store(self, dest):
        import hashlib
        import shutil
        manifest = read_json(os.path.join(dest, "BACKUP_MANIFEST.json"), None)
        if not manifest:
            raise ResetBlocked("backup", "backup manifest is missing")
        for row in manifest["files"]:
            full = os.path.join(dest, row["path"])
            digest = hashlib.sha256(open(full, "rb").read()).hexdigest()
            if digest != row["sha256"]:
                raise ResetBlocked("backup", "backup hash does not match")
        for row in manifest["files"]:
            target = os.path.join(self.store.root, row["path"])
            os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
            shutil.copy2(os.path.join(dest, row["path"]), target)
        return {"restored": len(manifest["files"])}

    def _brain(self, tenant_id):
        return Brain(
            self._tenant_dir(tenant_id),
            self.clock,
            lambda: self._provider_summary(tenant_id),
            self.log,
        )

    def _reset(self, tenant_id):
        directory = self._tenant_dir(tenant_id)
        return ResetService(
            self._book(tenant_id),
            os.path.join(directory, "brain"),
            os.path.join(directory, "knowledge"),
            self._brain(tenant_id).assert_rails,
        )

    def _teaching(self, tenant_id):
        return TeachingStore(
            os.path.join(self._tenant_dir(tenant_id), "knowledge"),
            self._ledger_ids(tenant_id),
            self.clock,
        )

    def _ledger_ids(self, tenant_id):
        def ids():
            return {row["id"] for row in self._book(tenant_id).ledger()}
        return ids

    def _tenant_dir(self, tenant_id):
        return os.path.join(self.store.root, "tenants", tenant_id)

    def _tenant_ids(self):
        accounts = read_json(self._path("accounts.json"), {})
        return sorted({row["tenant_id"] for row in accounts.values() if row.get("tenant_id")})

    def _entitlements(self, tenant_id):
        path = os.path.join(self._tenant_dir(tenant_id), "entitlements.json")
        body = read_json(path, None)
        if body is None:
            body = _blank_entitlements()
        body["commercial_paid"] = False
        return body

    def _save_entitlements(self, tenant_id, body):
        body["commercial_paid"] = False
        news = body.setdefault("news", {})
        news["redistribution"] = False
        if news.get("delivery") != "not_wired":
            news["delivery"] = "not_wired"
        write_json(os.path.join(self._tenant_dir(tenant_id), "entitlements.json"), body)

    def _provider_rows(self, tenant_id):
        declared = read_json(os.path.join(self._tenant_dir(tenant_id), "secrets.json"), {})
        rows = []
        for provider in PROVIDERS:
            ref = (declared.get(provider) or {}).get("ref")
            present = bool(ref) and bool(os.environ.get("CUSTOMER_PLATFORM_SECRET_" + ref))
            if not ref:
                state = "disabled"
            elif present:
                state = "secret_ref"
            else:
                state = "unconfigured"
            rows.append({
                "calls_enabled": False,
                "outbound": "not_wired",
                "provider": provider,
                "state": state,
            })
        return rows

    def _provider_summary(self, tenant_id):
        rows = self._provider_rows(tenant_id)
        if any(row["state"] == "secret_ref" for row in rows):
            return "secret_ref"
        if any(row["state"] == "unconfigured" for row in rows):
            return "unconfigured"
        return "disabled"

    def _rails_bytes(self, tenant_id):
        path = os.path.join(self._tenant_dir(tenant_id), "brain", "rails.json")
        with open(path, "rb") as handle:
            return handle.read()

    def _audit(self, tenant_id, user_id, action, allowed):
        try:
            self.store._audit(tenant_id, user_id, action, allowed)
        except Exception:
            self.log("audit unavailable")

    def _needles(self):
        needles = []
        if self.webhook_secret:
            secret = self.webhook_secret.decode("utf-8", "ignore") if isinstance(self.webhook_secret, bytes) else str(self.webhook_secret)
            if secret:
                needles.append(secret)
        for tenant_id in self._tenant_ids():
            declared = read_json(os.path.join(self._tenant_dir(tenant_id), "secrets.json"), {})
            for row in declared.values():
                ref = (row or {}).get("ref")
                if not ref:
                    continue
                value = os.environ.get("CUSTOMER_PLATFORM_SECRET_" + ref)
                if value:
                    needles.append(value)
        return needles

    def _path(self, name):
        return os.path.join(self.store.root, name)

    def _blank(self, name, value):
        path = self._path(name)
        if not os.path.exists(path):
            write_json(path, value)


def _blank_entitlements():
    return {
        "blocked": False,
        "commercial_paid": False,
        "news": {
            "delivery": "not_wired",
            "feeds": [],
            "redistribution": False,
            "state": "blocked",
        },
        "sandbox_payment_state": "none",
    }


def canonical(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)
