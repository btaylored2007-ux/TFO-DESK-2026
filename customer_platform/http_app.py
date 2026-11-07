"""Loopback HTTP app for the customer web template.

Signup and login are browser flows. The listener refuses any host other than
loopback. There is no desktop shell and no public leaderboard route.
"""

import hmac
import os
import secrets
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from veteran_desk.tenant_foundation import Denied as FoundationDenied

from customer_platform.errors import (
    BrainError,
    Denied,
    IdempotencyConflict,
    InvalidAmount,
    LiveDisabled,
    PaymentRejected,
    RailsIntegrity,
    ResetBlocked,
)
from customer_platform.pages import (
    admin_document,
    ask_page,
    brain_page,
    desk,
    education_page,
    esc,
    exercise_page,
    home,
    lesson_page,
    membership_document,
    page,
    preview_page,
    result_page,
    sandbox_page,
    school_page,
    setup_page,
    teach_page,
)
from customer_platform.platform import FUNDS_PHRASE

LOOPBACK = {"127.0.0.1", "::1"}
BRAND = os.path.join(os.path.dirname(__file__), "brand", "brand.css")


def worker_loop(platform, stop, interval=30):
    platform.worker_enabled = True
    try:
        while not stop.is_set():
            try:
                platform.brain_tick()
            except Exception as exc:
                platform.log("brain worker " + type(exc).__name__)
            if stop.wait(interval):
                break
    finally:
        platform.worker_enabled = False


def serve(platform, host, port):
    if host not in LOOPBACK:
        raise RuntimeError("customer web app refuses a non-loopback host")
    httpd = ThreadingHTTPServer((host, port), handler_for(platform))
    httpd.serve_forever()


def bind(platform, host="127.0.0.1", port=0):
    if host not in LOOPBACK:
        raise RuntimeError("customer web app refuses a non-loopback host")
    httpd = ThreadingHTTPServer((host, port), handler_for(platform))
    thread = threading.Thread(target=httpd.serve_forever, name="customer-web", daemon=True)
    thread.start()
    return httpd


def handler_for(platform):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def log_message(self, fmt, *args):
            return

        def _dispatch(self, method):
            try:
                parsed = urlparse(self.path)
                path = parsed.path
                if path.startswith("/leaderboard") or "leaderboard" in path:
                    self._html(404, page("Not available", "<p>A public leaderboard is not part of this template.</p>"))
                    return
                if method == "GET":
                    self._get(path)
                else:
                    self._post(path)
            except (Denied, FoundationDenied) as exc:
                self._html(403, page("Refused", "<p class=\"warning\">{0}</p>".format(esc(exc))))
            except (ResetBlocked, InvalidAmount, BrainError, RailsIntegrity, IdempotencyConflict, LiveDisabled, PaymentRejected) as exc:
                self._html(400, page("Not applied", "<p class=\"warning\">{0}</p>".format(esc(exc))))
            except Exception:
                self.server_platform().log(traceback.format_exc())
                self._html(500, page("Error", "<p class=\"warning\">The template hit an unexpected error.</p>"))

        def _get(self, path):
            if path == "/assets/brand.css":
                with open(BRAND, "rb") as handle:
                    body = handle.read()
                self._bytes(200, body, "text/css; charset=utf-8")
                return
            if path in {"/health", "/setup-status", "/diagnostics"}:
                self._html(404, page("Not found", "<p>That page is not part of the customer web app.</p>"))
                return
            if path in {"/", "/login"}:
                self._html(200, home(self.server_platform().setup_status(), self._csrf()))
                return
            if path == "/admin":
                token = self._cookies().get("cp_operator")
                overview = None
                try:
                    if token:
                        overview = self.server_platform().admin_overview(token)
                except Denied:
                    overview = None
                self._html(200, admin_document(self._csrf(), overview))
                return
            token = self._customer()
            if path == "/desk":
                self._html(200, desk(self.server_platform().desk(token), self._csrf()))
            elif path == "/school":
                self._html(200, school_page(self.server_platform().school_catalog(token)))
            elif path == "/school/exercise":
                self._html(200, exercise_page(self.server_platform().school_exercise(token)))
            elif path.startswith("/school/exercise/"):
                name = path.rsplit("/", 1)[-1]
                payload = self.server_platform().school_exercise_file(token, name)
                self._bytes(200, payload, "text/csv; charset=utf-8")
            elif path.startswith("/school/tts.m"):
                parts = [part for part in path.split("/") if part]
                if len(parts) != 3 or not parts[2].startswith(parts[1] + "."):
                    self._html(404, page("Not found", "<p>That lesson is not in this draft.</p>", logged_in=True))
                else:
                    self._html(200, lesson_page(self.server_platform().school_lesson(token, parts[1], parts[2])))
            elif path == "/ask":
                self._html(200, ask_page(self.server_platform().ask_list(token), self._csrf()))
            elif path == "/teach":
                self._html(200, teach_page(self.server_platform().teach_list(token), self._csrf()))
            elif path == "/journal":
                rows = self.server_platform().journal_list(token)
                body = "<section class=\"card\"><h2>Practice journal</h2><p>Saved notes are not scored.</p>{items}</section>".format(
                    items="".join("<p>{0}</p>".format(esc(row["text"])) for row in rows) or "<p>No notes yet.</p>"
                )
                self._html(200, page("Practice journal", body, logged_in=True))
            elif path == "/board":
                board = self.server_platform().private_board(token)
                lines = "".join(
                    "<li>{0}: {1} from {2} closes</li>".format(esc(row["trader_id"]), esc(row["net"]), esc(row["samples"]))
                    for row in board["results"]
                ) or "<li>No closed paper results in this window.</li>"
                body = "<section class=\"card\"><h2>This login only</h2><p>{0}</p><ul>{1}</ul><p>Not a public leaderboard.</p></section>".format(
                    esc(board["costs"]), lines)
                self._html(200, page("Results", body, logged_in=True))
            elif path == "/brain":
                platform = self.server_platform()
                self._html(200, brain_page(
                    platform.brain_status(token), platform.brain_lessons(token), platform.starter(), self._csrf(),
                ))
            elif path == "/education":
                self._html(200, education_page(self.server_platform().education()))
            elif path == "/membership":
                self._html(200, membership_document(self.server_platform().membership(token), self._csrf()))
            elif path.startswith("/membership/sandbox/"):
                session_id = path.rsplit("/", 1)[-1]
                found = None
                for row in self._sessions():
                    if row.get("session_id") == session_id:
                        found = row
                if not found:
                    raise Denied("checkout session was not found")
                self._html(200, sandbox_page(found))
            else:
                self._html(404, page("Not found", "<p>That page is not part of the customer web app.</p>", logged_in=bool(token)))

        def _post(self, path):
            form = self._form()
            if path != "/payments/webhook":
                self._check_csrf(form)
            platform = self.server_platform()
            if path == "/signup":
                created = platform.signup(
                    form.get("email", ""), form.get("password", ""), form.get("display_name", ""),
                    starting_capital=form.get("starting_capital", ""), currency=form.get("currency") or "USD",
                )
                token = platform.login(form.get("email", ""), form.get("password", ""))
                self._redirect("/desk", session=token)
                return
            if path == "/login":
                token = platform.login(form.get("email", ""), form.get("password", ""))
                self._redirect("/desk", session=token)
                return
            if path == "/admin/login":
                token = platform.operator_login(form.get("email", ""), form.get("password", ""))
                self._redirect("/admin", operator=token)
                return
            if path == "/payments/webhook":
                raw = self._raw()
                signature = self.headers.get("X-Customer-Platform-Signature", "")
                result = platform.apply_webhook(raw, signature)
                self._bytes(200, str(result["sandbox_payment_state"]).encode("utf-8"), "text/plain; charset=utf-8")
                return
            token = self._customer()
            if path == "/desk/reset/preview":
                amount = form.get("amount") or None
                preview = platform.preview_reset(token, amount)
                preview["idempotency_key"] = "web-" + secrets.token_hex(8)
                self._html(200, preview_page(preview, self._csrf()))
            elif path == "/desk/reset":
                result = platform.reset_capital(
                    token,
                    amount=form.get("amount") or None,
                    preview_token=form.get("preview_token", ""),
                    confirmation=form.get("confirmation", ""),
                    idempotency_key=form.get("idempotency_key", ""),
                    acknowledge_zero=form.get("acknowledge_zero") == "yes",
                )
                self._html(200, result_page(result))
            elif path == "/desk/start":
                result = platform.start_trading(token)
                self._html(200, desk(platform.desk(token), self._csrf()) if False else page(
                    "Practice desk",
                    "<p class=\"state\">{0}</p><p>{1}</p><p><a href=\"/desk\">Back</a></p>".format(
                        esc(result["headline"]), esc("; ".join(result.get("reasons") or [])),
                    ),
                    logged_in=True,
                ))
            elif path == "/desk/stop":
                result = platform.stop_trading(token)
                self._html(200, page("Practice desk", "<p class=\"state\">{0}</p><p><a href=\"/desk\">Back</a></p>".format(esc(result["headline"])), logged_in=True))
            elif path == "/ask":
                platform.ask(token, form.get("text", ""), form.get("idempotency_key") or ("web-" + secrets.token_hex(8)))
                self._redirect("/ask")
            elif path == "/teach/save":
                platform.teach_save(
                    token,
                    trader_ids=[part.strip() for part in form.get("traders", "desk").split(",") if part.strip()],
                    text=form.get("text", ""),
                    rules={
                        "entry": form.get("entry", ""),
                        "exit": form.get("exit", ""),
                        "risk": form.get("risk", ""),
                        "market": form.get("market", ""),
                    },
                )
                self._redirect("/teach")
            elif path == "/journal/save":
                platform.journal_add(token, kind=form.get("kind") or "mistake_review", text=form.get("text", ""))
                self._redirect("/journal")
            elif path == "/desk/optout":
                sectors = [part.strip() for part in form.get("sectors", "").split(",") if part.strip()]
                platform.set_sector_opt_out(token, sectors)
                self._redirect("/desk")
            elif path == "/desk/order":
                platform.place_order(
                    token, symbol=form.get("symbol", ""), side=form.get("side", ""), qty=form.get("qty", ""),
                    limit_price=form.get("limit_price", ""), sector=form.get("sector") or None,
                    idempotency_key=form.get("idempotency_key") or ("web-" + secrets.token_hex(8)),
                )
                self._redirect("/desk")
            elif path == "/desk/fill":
                platform.fill_order(token, form.get("order_id", ""), price=form.get("price", ""))
                self._redirect("/desk")
            elif path == "/desk/funds":
                platform.add_paper_funds(token, form.get("amount", ""), FUNDS_PHRASE if form.get("acknowledge") == "yes" else "")
                self._redirect("/desk")
            elif path == "/brain/idea":
                platform.submit_idea(
                    token, kind="natural_language", body=form.get("body", ""),
                    idempotency_key="web-" + secrets.token_hex(8),
                )
                self._redirect("/brain")
            elif path == "/brain/run":
                platform.run_brain(token, trigger="manual", idempotency_key="web-" + secrets.token_hex(8))
                self._redirect("/brain")
            elif path == "/membership/checkout":
                session = platform.start_checkout(token)
                self._redirect("/membership/sandbox/" + session["session_id"])
            else:
                self._html(404, page("Not found", "<p>That action is not part of the customer web app.</p>"))

        def server_platform(self):
            return platform

        def _customer(self):
            token = self._cookies().get("cp_session", "")
            platform.store.context(token)
            return token

        def _sessions(self):
            from customer_platform.files import read_json
            rows = []
            root = platform.store.root
            tenants = os.path.join(root, "tenants")
            if not os.path.isdir(tenants):
                return rows
            token = self._customer()
            tenant_id = platform.store.context(token)["tenant_id"]
            body = read_json(os.path.join(tenants, tenant_id, "payments.json"), {"sessions": []})
            return body.get("sessions") or []

        def _cookies(self):
            found = {}
            for part in (self.headers.get("Cookie") or "").split(";"):
                if "=" in part:
                    key, value = part.strip().split("=", 1)
                    found[key] = value
            return found

        def _csrf(self):
            current = self._cookies().get("cp_csrf")
            if current and len(current) >= 16:
                return current
            issued = secrets.token_urlsafe(24)
            self._pending_csrf = issued
            return issued

        def _check_csrf(self, form):
            cookie = self._cookies().get("cp_csrf", "")
            posted = form.get("csrf", "")
            if len(cookie) < 16 or not hmac.compare_digest(cookie, posted):
                raise Denied("session check failed")

        def _form(self):
            raw = self._raw()
            parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
            return {key: values[-1] for key, values in parsed.items()}

        def _raw(self):
            length = int(self.headers.get("Content-Length") or "0")
            if length < 0 or length > 262144:
                raise Denied("request is too large")
            return self.rfile.read(length) if length else b""

        def _redirect(self, location, session=None, operator=None):
            self.send_response(303)
            self._common(session=session, operator=operator)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _html(self, status, html):
            body = html.encode("utf-8")
            self.send_response(status)
            self._common()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, status, body, content_type):
            self.send_response(status)
            self._common()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _common(self, session=None, operator=None):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'")
            self.send_header("Cache-Control", "no-store")
            csrf = getattr(self, "_pending_csrf", None)
            if csrf:
                self.send_header("Set-Cookie", "cp_csrf={0}; HttpOnly; SameSite=Strict; Path=/".format(csrf))
            if session:
                self.send_header("Set-Cookie", "cp_session={0}; HttpOnly; SameSite=Strict; Path=/".format(session))
            if operator:
                self.send_header("Set-Cookie", "cp_operator={0}; HttpOnly; SameSite=Strict; Path=/".format(operator))

    return Handler
