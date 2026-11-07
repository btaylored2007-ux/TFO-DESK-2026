"""HTML for the customer web app. Server-rendered. No public leaderboard."""

from customer_platform.reset import RESET_PHRASE, ZERO_WARNING
from customer_platform.platform import FUNDS_PHRASE


def esc(value):
    text = str(value if value is not None else "")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def page(title, body, *, csrf="", logged_in=False):
    nav = ""
    if logged_in:
        nav = (
            "<nav aria-label=\"Primary\"><a href=\"/school\">School</a><a href=\"/desk\">Desk</a>"
            "<a href=\"/teach\">Teach</a><a href=\"/ask\">Ask</a><a href=\"/board\">Board</a><a href=\"/keys\">Keys</a></nav>"
        )
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>[[TITLE]]</title>
<link rel="stylesheet" href="/assets/brand.css">
</head>
<body>
<header>
  <a class="brand" href="/desk">
    <svg class="mark" viewBox="0 0 36 36" aria-hidden="true">
      <rect x="3" y="5" width="22" height="26" rx="3" fill="none" stroke="#143d34" stroke-width="2"/>
      <rect x="11" y="3" width="22" height="26" rx="3" fill="#f3eee4" stroke="#6e2430" stroke-width="2"/>
    </svg>
    <div>
      <p class="eyebrow">Transparent Trading</p>
      <h1>[[TITLE]]</h1>
    </div>
  </a>
  [[NAV]]
</header>
<main>
[[BODY]]
</main>
<footer>
  Private to this login. Practice figures stay here. Not a public leaderboard.
</footer>
</body>
</html>
"""
    return html.replace("[[TITLE]]", esc(title)).replace("[[NAV]]", nav).replace("[[BODY]]", body)


def home(status, csrf, message=""):
    notice = "<p class=\"warning\">{0}</p>".format(esc(message)) if message else ""
    signup = ""
    if status.get("web_signup_enabled"):
        signup = """
        <section class="card">
          <h2>Create practice account</h2>
          <p>This opens a private practice desk. It is not a paid subscription.</p>
          <form method="post" action="/signup">
            <input type="hidden" name="csrf" value="{csrf}">
            <label>Email <input name="email" autocomplete="username" required></label>
            <label>Password <input name="password" type="password" autocomplete="new-password" required></label>
            <label>Display name <input name="display_name" required></label>
            <label>Starting practice capital <input name="starting_capital" value="10000.00" inputmode="decimal" required></label>
            <label>Currency
              <select name="currency"><option>USD</option><option>EUR</option><option>GBP</option><option>AUD</option><option>JPY</option></select>
            </label>
            <button type="submit">Create and enter</button>
          </form>
        </section>
        """.format(csrf=esc(csrf))
    else:
        signup = "<section class=\"card\"><h2>Signup is closed</h2><p>This process was started without web signup. Login still works for accounts that already exist.</p></section>"
    later = ""
    body = """
    {notice}
    <section class="card">
      <h2>Log in</h2>
      <p>Practice only. Live prices, news, and charges are not available.</p>
      <form method="post" action="/login">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Email <input name="email" autocomplete="username" required></label>
        <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
        <button type="submit">Log in</button>
      </form>
    </section>
    {signup}
    """.format(notice=notice, csrf=esc(csrf), signup=signup)
    return page("Practice desk", body, csrf=csrf)


def desk(model, csrf):
    trading = model["trading"]
    figures = """
    <section class="figures">
      <article><span>Practice capital</span><strong>{cap} {cur}</strong></article>
      <article><span>This epoch's result</span><strong>{pnl} {cur}</strong></article>
      <article><span>Ledger result</span><strong>{hist} {cur}</strong></article>
    </section>
    """.format(
        cap=esc(model["practice_capital"]), pnl=esc(model["session_pnl"]),
        hist=esc(model["historical_realized_pnl"]), cur=esc(model["currency"]),
    )
    holdings = "".join(
        "<tr><td>{symbol}</td><td>{qty}</td><td>{stop}</td></tr>".format(
            symbol=esc(row["symbol"]), qty=esc(row["qty"]), stop=esc(row.get("stop") or "none"),
        )
        for row in model["open_positions"]
    ) or "<tr><td colspan=\"3\">No open position on this login.</td></tr>"
    points = (model["chart"].get("points") or [])
    chart = chart_svg(model["chart"]) if len(points) > 1 else "<p>No ledger history on this login yet.</p>"
    distance = model.get("stop_distance") or ""
    policy = "Saved stop distance {0}.".format(esc(distance)) if distance else "No saved stop distance. A new buy needs a stop."
    body = """
    <section class="hero" aria-live="polite">
      <p class="state">{headline}</p>
      <p>{notice}</p>
      {strategy}
      {action}
    </section>
    {figures}
    <section class="card">
      <h2>This login</h2>
      {chart}
      <p>{policy}</p>
      <form method="post" action="/desk/stop-distance">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Stop distance, as a fraction below the limit <input name="distance" value="{distance}" placeholder="0.05"></label>
        <button type="submit">Save stop distance</button>
      </form>
    </section>
    <section class="card">
      <h2>Open positions</h2>
      <table><thead><tr><th>Symbol</th><th>Qty</th><th>Stop</th></tr></thead><tbody>{holdings}</tbody></table>
      <form method="post" action="/desk/position-stop">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Symbol <input name="symbol"></label>
        <label>New stop <input name="stop"></label>
        <button type="submit">Update stop</button>
      </form>
    </section>
    <section class="card">
      <h2>Trader</h2>
      <form method="post" action="/desk/activity">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Activity <select name="level"><option {a1}>1</option><option {a2}>2</option><option {a3}>3</option></select></label>
        <button type="submit">Save activity</button>
      </form>
      <form method="post" action="/desk/trader">
        <input type="hidden" name="csrf" value="{csrf}">
        <input type="hidden" name="trader_id" value="desk">
        <label>Desk entries <select name="enabled"><option value="yes">on</option><option value="no">off</option></select></label>
        <button type="submit">Save trader</button>
      </form>
    </section>
    <details class="card">
      <summary>Advanced paper order</summary>
      <form method="post" action="/desk/order">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Symbol <input name="symbol" required></label>
        <label>Side <select name="side"><option>buy</option><option>sell</option></select></label>
        <label>Qty <input name="qty" required></label>
        <label>Limit <input name="limit_price" required></label>
        <label>Stop <input name="stop"></label>
        <button type="submit">Paper order</button>
      </form>
    </details>
    """.format(
        headline=esc(trading["headline"]),
        notice=esc(trading.get("notice") or ""),
        action=_trade_action(trading, csrf),
        strategy=(
            "<p>A reviewed provider schema can place one paper order from the synthetic print. The model does not set the price, the stop, or the approval.</p>"
            if model.get("provider_strategies") else
            "<p>An active written rule can place one paper order from the synthetic print. This is not AI trading.</p>"
            if model.get("active_strategies") else ""
        ),
        figures=figures, chart=chart, holdings=holdings, policy=policy,
        distance=esc(distance), csrf=esc(csrf),
        a1="selected" if str(trading.get("activity")) == "1" else "",
        a2="selected" if str(trading.get("activity")) == "2" else "",
        a3="selected" if str(trading.get("activity")) == "3" else "",
    )
    return page("Desk", body, logged_in=True)

def preview_page(preview, csrf, message=""):
    warning = "<p class=\"warning\">{0}</p>".format(esc(preview["zero_warning"])) if preview.get("zero_warning") else ""
    blocked = "<p class=\"warning\">{0}</p>".format(esc(preview["blocked"])) if preview.get("blocked") else ""
    notice = "<p class=\"warning\">{0}</p>".format(esc(message)) if message else ""
    confirm = ""
    if not preview.get("blocked"):
        confirm = """
        <form method="post" action="/desk/reset">
          <input type="hidden" name="csrf" value="{csrf}">
          <input type="hidden" name="preview_token" value="{token}">
          <input type="hidden" name="amount" value="{amount}">
          <input type="hidden" name="idempotency_key" value="{idem}">
          <label>Type {phrase} <input name="confirmation" autocomplete="off" required></label>
          {zero}
          <button type="submit">Reset practice capital</button>
        </form>
        """.format(
            csrf=esc(csrf), token=esc(preview["preview_token"]), amount=esc(preview["chosen_starting_capital"]),
            idem=esc(preview.get("idempotency_key") or ""), phrase=esc(RESET_PHRASE),
            zero=("<label><input type=\"checkbox\" name=\"acknowledge_zero\" value=\"yes\"> " + esc(ZERO_WARNING) + "</label>") if preview.get("zero_warning") else "",
        )
    rows = "".join("<li>{0}</li>".format(esc(item)) for item in preview["preserved"])
    body = """
    {notice}{blocked}{warning}
    <section class="card">
      <h2>Reset preview</h2>
      <p>Chosen starting capital: <strong>{chosen} {cur}</strong>. Default configured amount: {default}. Using the default: {is_default}.</p>
      <p>{new_cap}</p>
      <p>{new_pnl} <strong>{pnl}</strong></p>
      <p>Current practice capital {current}. Current epoch P&amp;L {session}. Historical realized P&amp;L {hist}, unchanged.</p>
      <p>Open positions: {positions}. Pending orders: {orders}.</p>
      <p>Preserved:</p><ul>{preserved}</ul>
      {confirm}
      <p><a href="/desk">Back to the desk</a></p>
    </section>
    """.format(
        notice=notice, blocked=blocked, warning=warning, chosen=esc(preview["chosen_starting_capital"]),
        cur=esc(preview["currency"]), default=esc(preview["configured_starting_capital"]),
        is_default=esc(preview["amount_is_default"]), new_cap=esc(preview["new_capital_label"]),
        new_pnl=esc(preview["new_epoch_session_pnl_label"]), pnl=esc(preview["new_epoch_session_pnl"]),
        current=esc(preview["current_capital_balance"]), session=esc(preview["current_epoch_session_pnl"]),
        hist=esc(preview["historical_realized_pnl"]), positions=esc(len(preview["open_positions"])),
        orders=esc(len(preview["pending_orders"])), preserved=rows, confirm=confirm,
    )
    return page("Reset preview", body, logged_in=True)


def result_page(result):
    warning = "<p class=\"warning\">{0}</p>".format(esc(result["zero_warning"])) if result.get("zero_warning") else ""
    body = """
    {warning}
    <section class="card">
      <h2>Practice capital was reset</h2>
      <p>New practice capital: <strong>{capital} {cur}</strong>.</p>
      <p>{label} <strong>{pnl}</strong></p>
      <p>Prior epoch {prior} keeps session P&amp;L {prior_pnl}. Historical realized P&amp;L remains {hist}.</p>
      <p>The learning brain was not part of this change. This was the local template, not the owner desk.</p>
      <p><a href="/desk">Back to the desk</a></p>
    </section>
    """.format(
        warning=warning, capital=esc(result["new_epoch_capital"]), cur=esc(result["currency"]),
        label=esc(result["new_epoch_session_pnl_label"]), pnl=esc(result["new_epoch_session_pnl"]),
        prior=esc(result["prior_epoch_id"]), prior_pnl=esc(result["prior_epoch_session_pnl"]),
        hist=esc(result["historical_realized_pnl"]),
    )
    return page("Reset result", body, logged_in=True)


def brain_page(status, lessons, pack, csrf, ideas=None, message=""):
    notice = "<p class=\"warning\">{0}</p>".format(esc(message)) if message else ""
    rows = "".join(
        "<tr><td>{status}</td><td>{kind}</td><td>{effect}</td><td>{text}</td></tr>".format(
            status=esc(row["status"]), kind=esc(row["kind"]), effect=esc(row["trading_effect"]), text=esc(row["statement"]),
        )
        for row in lessons
    ) or "<tr><td colspan=\"4\">No persisted lesson yet.</td></tr>"
    notes = "".join(
        "<li>{0}</li>".format(esc(item.get("note") or "Saved note."))
        for item in ideas or []
    ) or "<li>No saved note yet.</li>"
    rules = "".join("<li>{0}</li>".format(esc(rule["text"])) for rule in pack["rules"])
    body = """
    {notice}
    <section class="card">
      <h2>Notes on this login</h2>
      <p>Saved notes do not place trades.</p>
      <ul>{notes}</ul>
      <form method="post" action="/brain/idea">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Note <textarea name="body" rows="4" required></textarea></label>
        <button type="submit">Save note</button>
      </form>
    </section>
    <section class="card">
      <h2>Lessons from this desk</h2>
      <p>{count} saved.</p>
      <table><thead><tr><th>Status</th><th>Kind</th><th>Trading effect</th><th>Statement</th></tr></thead><tbody>{rows}</tbody></table>
    </section>
    """.format(
        notice=notice, csrf=esc(csrf), rows=rows, notes=notes,
        count=len(lessons), rules=rules,
    )
    return page("Notes", body, logged_in=True)

def education_page(catalog):
    blocks = []
    for row in catalog:
        if row["status"] == "authored":
            blocks.append("<article class=\"card\"><h2>{title}</h2><pre class=\"prose\">{body}</pre></article>".format(
                title=esc(row["title"]), body=esc(row["body"]),
            ))
        else:
            items = "".join("<li>{0}</li>".format(esc(item)) for item in row.get("outline") or [])
            blocks.append("<article class=\"card\"><h2>{title}</h2><p>Outline only. No licensed text is included.</p><ul>{items}</ul></article>".format(
                title=esc(row["title"]), items=items,
            ))
    return page("Education", "\n".join(blocks), logged_in=True)


def membership_document(membership, csrf, checkout=None):
    if checkout:
        checkout_block = "<p class=\"warning\">{notice}</p><p>Session {session}. Provider {provider}. Charges: no. Hosted checkout: no.</p>".format(
            notice=esc(checkout["notice"]), session=esc(checkout["session_id"]), provider=esc(checkout["provider"]),
        )
    else:
        checkout_block = "<p>No checkout session is open.</p>"
    body = """
    <section class="card">
      <h2>Membership</h2>
      <p>Commercial paid: no. {reason}</p>
      <p>Sandbox payment state: {sandbox}. Blocked: {blocked}.</p>
      <p>News: {news}. Redistribution: no. Delivery: {delivery}.</p>
      {checkout}
      <form method="post" action="/membership/checkout">
        <input type="hidden" name="csrf" value="{csrf}">
        <button type="submit">Open a test checkout contract</button>
      </form>
    </section>
    """.format(
        reason=esc(membership["commercial_paid_reason"]), sandbox=esc(membership["sandbox_payment_state"]),
        blocked=esc(membership["blocked"]), news=esc(membership["news"]["state"]),
        delivery=esc(membership["news"]["delivery"]), checkout=checkout_block, csrf=esc(csrf),
    )
    return page("Membership", body, logged_in=True)


def sandbox_page(session):
    body = """
    <section class="card">
      <h2>Test checkout contract</h2>
      <p class="warning">{notice}</p>
      <p>There is no pay button. A sandbox state changes only when a signed test webhook is verified. Loading a fixture file does not create a paid claim.</p>
      <p>Session {session}. Mode {mode}.</p>
    </section>
    """.format(notice=esc(session["notice"]), session=esc(session["session_id"]), mode=esc(session["mode"]))
    return page("Test checkout", body, logged_in=True)


def admin_page(overview=None, message="", incidents=None):
    notice = "<p class=\"warning\">{0}</p>".format(esc(message)) if message else ""
    if overview is None:
        body = notice + """
        <section class="card">
          <h2>Operator login</h2>
          <p>Operators see subscription status and the incident queue. They do not see customer secrets, research text, or provider keys.</p>
          <form method="post" action="/admin/login">
            <input type="hidden" name="csrf" value="{csrf}">
            <label>Email <input name="email" autocomplete="username" required></label>
            <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
            <button type="submit">Log in</button>
          </form>
        </section>
        """
        return body
    rows = "".join(
        "<tr><td>{tenant}</td><td>{name}</td><td>{sandbox}</td><td>no</td><td>{news}</td><td>{providers}</td><td>{lessons}</td><td>{error}</td><td>no</td></tr>".format(
            tenant=esc(row["tenant_id"]), name=esc(row["display_name"]), sandbox=esc(row["sandbox_payment_state"]),
            news=esc(row["news"]), providers=esc(row["providers"]), lessons=esc(row["brain_persisted_lessons"]),
            error=esc(row["brain_last_error_code"]),
        )
        for row in overview["tenants"]
    )
    incident_rows = ""
    report = incidents or {}
    for row in report.get("incidents") or []:
        incident_rows += (
            "<article><p>{code} · {status} · tenant {tenant}</p><p>Root cause: {cause}. Verification: {verify}. Learning: {learning}.</p>"
            "<p>Engineer running: no. External notification sent: no.</p>"
            "<form method=\"post\" action=\"/admin/incident\"><input type=\"hidden\" name=\"csrf\" value=\"{{csrf}}\">"
            "<input type=\"hidden\" name=\"incident_id\" value=\"{iid}\">"
            "<button name=\"action\" value=\"ack\" type=\"submit\">Ack</button>"
            "<button name=\"action\" value=\"repair\" type=\"submit\">Recheck prices</button>"
            "<button name=\"action\" value=\"verify\" type=\"submit\">Verify</button>"
            "<button name=\"action\" value=\"propose\" type=\"submit\">Propose</button>"
            "<input name=\"learning\" placeholder=\"lesson\">"
            "<button name=\"action\" value=\"close\" type=\"submit\">Escalate or block</button>"
            "<input type=\"hidden\" name=\"outcome\" value=\"escalated\"></form></article>"
        ).format(
            code=esc(row["code"]), status=esc(row["status"]), tenant=esc(row["tenant_id"]),
            cause=esc(row.get("root_cause") or "UNKNOWN"), verify=esc(row.get("verification") or ""),
            learning=esc(row.get("learning") or ""), iid=esc(row["id"]),
        )
    body = notice + """
    <section class="card">
      <h2>Subscriptions</h2>
      <table><thead><tr><th>Tenant</th><th>Name</th><th>Sandbox state</th><th>Commercial paid</th><th>News</th><th>Providers</th><th>Lessons</th><th>Brain error</th><th>Model running</th></tr></thead>
      <tbody>{rows}</tbody></table>
    </section>
    <section class="card">
      <h2>Incident queue</h2>
      <p>External notification is off. No engineer is running.</p>
      {incidents}
    </section>
    """.format(rows=rows or "<tr><td colspan=\"9\">No subscription yet.</td></tr>", incidents=incident_rows or "<p>No incident stored.</p>")
    return body


def admin_document(csrf, overview=None, message="", incidents=None):
    body = admin_page(overview, message, incidents)
    if overview is None:
        body = body.format(csrf=esc(csrf))
    else:
        body = body.replace("{csrf}", esc(csrf))
    return page("Admin", body, logged_in=False)


def setup_page(status):
    rows = "".join("<tr><td>{0}</td><td>{1}</td></tr>".format(esc(key), esc(value)) for key, value in sorted(status.items()))
    body = "<section class=\"card\"><h2>What this template actually does</h2><table>{0}</table></section>".format(rows)
    return page("Setup", body, logged_in=False)


def chart_svg(chart):
    from decimal import Decimal
    points = chart.get("points") or []
    if not points:
        return "<p>No ledger points yet.</p>"
    values = [Decimal(point["cash"]) for point in points]
    low = min(values)
    high = max(values)
    span = high - low or Decimal("1")
    width = 640
    height = 96
    step = Decimal(width) / Decimal(max(len(values) - 1, 1))
    coords = []
    for index, value in enumerate(values):
        x = (Decimal(index) * step).quantize(Decimal("0.01"))
        y = (Decimal(height) - ((value - low) / span) * Decimal(height - 24) - Decimal(12)).quantize(Decimal("0.01"))
        coords.append("{0},{1}".format(x, y))
    label = points[-1]["label"]
    return """
    <svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{label}">
      <polyline fill="none" stroke="#143d34" stroke-width="2" points="{points}"/>
    </svg>
    <p>{label}. {count} stored points. {cur}.</p>
    """.format(width=width, height=height, points=" ".join(coords), label=esc(label), count=len(points), cur=esc(chart["currency"]))


def teach_page(model, csrf, notice=""):
    skills = model.get("skills") if isinstance(model, dict) else model
    fills = model.get("fills") if isinstance(model, dict) else []
    blocks = []
    if not skills:
        blocks.append("<p>No skill saved yet.</p>")
    evidence = "".join("<option>{0}</option>".format(esc(item)) for item in fills or [])
    for skill in skills:
        rules = "".join("<li>{0}: {1}</li>".format(esc(key), esc(value)) for key, value in skill["rules"].items())
        notes = "".join("<li>{0}</li>".format(esc(item)) for item in skill["clarifications"])
        versions = "".join(
            "<li>v{0} {1}</li>".format(esc(item["version"]), esc(item["status"])) for item in skill["versions"]
        )
        nxt = _next_skill_action(skill["status"])
        action = (
            "<form method=\"post\" action=\"/teach/test\"><input type=\"hidden\" name=\"csrf\" value=\"{csrf}\">"
            "<input type=\"hidden\" name=\"skill_id\" value=\"{sid}\">"
            "<button type=\"submit\">Run synthetic paper test</button></form>"
            "<p>This test is for this version only. Another fill on the account is not evidence. A profit is not a pass.</p>"
        ).format(csrf=esc(csrf), sid=esc(skill["id"]))
        if nxt and nxt != "paper_testing":
            action += (
                "<form method=\"post\" action=\"/teach/status\"><input type=\"hidden\" name=\"csrf\" value=\"{csrf}\">"
                "<input type=\"hidden\" name=\"skill_id\" value=\"{sid}\"><input type=\"hidden\" name=\"status\" value=\"{status}\">"
                "<button type=\"submit\">Mark {status}</button></form>"
            ).format(csrf=esc(csrf), sid=esc(skill["id"]), status=esc(nxt))
        rollback = "".join(
            "<form method=\"post\" action=\"/teach/rollback\"><input type=\"hidden\" name=\"csrf\" value=\"{csrf}\">"
            "<input type=\"hidden\" name=\"skill_id\" value=\"{sid}\"><input type=\"hidden\" name=\"version\" value=\"{version}\">"
            "<button type=\"submit\">Roll back to v{version}</button></form>".format(
                csrf=esc(csrf), sid=esc(skill["id"]), version=esc(item["version"]),
            )
            for item in skill["versions"]
        )
        blocks.append(
            "<article class=\"card\"><h2>{status}</h2><p>Traders: {traders}. Source: {source}. AI: {ai}. Evidence: {evidence_ids}.</p>"
            "<ul>{rules}</ul><ul>{notes}</ul><ul>{versions}</ul>{action}{rollback}"
            "<p>This is not a promise of results. AI interpretation is {ai}.</p></article>".format(
                status=esc(skill["status"]), traders=esc(", ".join(skill["trader_ids"])),
                source=esc(skill["rules_source"]), ai=esc(skill["ai"]),
                evidence_ids=esc(", ".join(skill.get("evidence_ids") or []) or "none"),
                rules=rules or "<li>No rules interpreted.</li>", notes=notes or "<li>No clarifications.</li>",
                versions=versions, action=action, rollback=rollback,
            )
        )
    warning = "<p class=\"warning\">{0}</p>".format(esc(notice)) if notice else ""
    body = """
    <section class="card">
      <h2>Upload skills</h2>
      <p>Describe a strategy in your own words, or upload a plain text file. This is separate from the School. A saved draft is not a completed test.</p>
      {warning}
      <form method="post" action="/teach/save">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Traders <input name="traders" value="desk"></label>
        <label>Entry <input name="entry"></label>
        <label>Exit <input name="exit"></label>
        <label>Risk <input name="risk"></label>
        <label>Market <input name="market"></label>
        <label>Or describe it <textarea name="text" rows="4"></textarea></label>
        <button class="tap" type="submit">Save draft</button>
      </form>
      <form method="post" action="/teach/upload" enctype="multipart/form-data">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Traders <input name="traders" value="desk"></label>
        <label>Text file <input type="file" name="upload" accept=".txt,.md"></label>
        <button class="tap" type="submit">Upload file</button>
      </form>
    </section>
    {blocks}
    """.format(warning=warning, csrf=esc(csrf), blocks="".join(blocks))
    return page("Teach my traders", body, logged_in=True)


def _next_skill_action(status):
    return {"draft": "paper_testing", "needs_clarification": "paper_testing", "paper_testing": "ready_for_review", "ready_for_review": "active", "active": "paused"}.get(status, "")


def _trade_action(trading, csrf):
    if trading.get("state") == "replay":
        return (
            '<form method="post" action="/desk/stop"><input type="hidden" name="csrf" value="{csrf}">'
            '<button class="tap stop" type="submit">Stop new entries</button></form>'
        ).format(csrf=esc(csrf))
    if trading.get("can_start"):
        return (
            '<form method="post" action="/desk/start"><input type="hidden" name="csrf" value="{csrf}">'
            '<button class="tap start" type="submit">Start paper replay</button></form>'
        ).format(csrf=esc(csrf))
    return (
        '<form method="post" action="/desk/replay"><input type="hidden" name="csrf" value="{csrf}">'
        '<button class="tap" type="submit">Arm synthetic replay</button></form>'
        '<form method="post" action="/desk/start"><input type="hidden" name="csrf" value="{csrf}">'
        '<button class="tap start" type="submit" disabled>Start</button></form>'
    ).format(csrf=esc(csrf))


def keys_page(model, csrf, notice=""):
    status = model["status"]
    rows = "".join(
        "<li>{0} ····{1}</li>".format(esc(row["provider"]), esc(row.get("last4") or ""))
        for row in model.get("keys") or []
    ) or "<li>No key stored for this login.</li>"
    warning = "<p class=\"warning\">{0}</p>".format(esc(notice or status.get("reason") or ""))
    body = """
    <section class="card">
      <h2>Your provider keys</h2>
      {warning}
      <p>Keys belong to this login. They are not an operator subscription and they are not sent to the browser after save. Available: {available}.</p>
      <p>The provider client is part of this app. Outbound send stays off until you confirm it and paper replay is started. A missing key is not a missing client. Public egress stays off on this server.</p>
      <ul>{rows}</ul>
      <form method="post" action="/keys">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Provider <select name="provider"><option>openai</option><option>anthropic</option><option>grok</option><option>deepseek</option></select></label>
        <label>Key <input name="secret" type="password" autocomplete="off"></label>
        <button type="submit">Save key</button>
      </form>
      <form method="post" action="/keys/test">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Provider <select name="provider"><option>openai</option><option>anthropic</option><option>grok</option><option>deepseek</option></select></label>
        <button type="submit">Test connection</button>
      </form>
      <form method="post" action="/keys/remove">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Provider <select name="provider"><option>openai</option><option>anthropic</option><option>grok</option><option>deepseek</option></select></label>
        <button type="submit">Remove key</button>
      </form>
    </section>
    """.format(warning=warning, available=esc(status.get("available")), rows=rows, csrf=esc(csrf))
    return page("Keys", body, logged_in=True)


def ask_page(rows, csrf):
    cards = []
    for row in rows:
        events = "".join(
            "<p><strong>{0}</strong> {1}</p>".format(esc(item.get("sender", "")), esc(item.get("body", "")))
            for item in row.get("events") or []
        )
        reviews = ""
        if row.get("reviews"):
            reviews = "<details><summary>Independent reviews. Not a combined answer.</summary>" + "".join(
                "<p><strong>{0}</strong> {1}</p>".format(esc(item.get("sender", "")), esc(item.get("body", "")))
                for item in row["reviews"]
            ) + "</details>"
        history = "".join(
            "<li>{0} {1} {2}{3}</li>".format(
                esc(item.get("sequence")), esc(item.get("sender")), esc(item.get("kind")),
                " late" if item.get("late") else "",
            )
            for item in row.get("history") or []
        )
        cards.append(
            "<article><p>{0} · {1} · {2}</p><p>{3}</p><p>{4}</p><p>Owner {5}. Provenance {6}.</p>{7}{8}"
            "<details><summary>Raw record</summary><ul>{9}</ul></details>"
            "<form method=\"post\" action=\"/ask/retry\"><input type=\"hidden\" name=\"csrf\" value=\"{csrf}\">"
            "<input type=\"hidden\" name=\"request_id\" value=\"{rid}\"><button type=\"submit\">Retry</button></form>"
            "<form method=\"post\" action=\"/ask/cancel\"><input type=\"hidden\" name=\"csrf\" value=\"{csrf}\">"
            "<input type=\"hidden\" name=\"request_id\" value=\"{rid}\"><button type=\"submit\">Cancel</button></form>"
            "</article>".format(
                esc(row.get("status", "")), esc(row.get("provider", "")), esc(row.get("job") or "unconnected"),
                esc(row.get("text", "")), esc(row.get("answer", "")), esc(row.get("owner", "")), esc(row.get("provenance") or "not_sent"),
                events, reviews, history or "<li>No delivery yet.</li>", csrf=esc(csrf), rid=esc(row.get("id")),
            )
        )
    body = (
        "<section class=\"card\"><h2>Ask</h2>"
        "<p>No outbound provider call is made from this page. A saved question can be retried or cancelled. Nothing is sent until this login has its own key and a connection is enabled.</p>"
        "<form method=\"post\" action=\"/ask\"><input type=\"hidden\" name=\"csrf\" value=\"{csrf}\">"
        "<label>Question <textarea name=\"text\" rows=\"4\" required></textarea></label>"
        "<button class=\"tap\" type=\"submit\">Save question</button></form>"
        "{cards}</section>"
    ).format(csrf=esc(csrf), cards="".join(cards) or "<p>No question saved yet.</p>")
    return page("Ask", body, logged_in=True)


def quiz_page(attempt, csrf):
    rows = []
    for question in attempt.get("questions") or []:
        options = "".join(
            "<label><input type=\"radio\" name=\"answer-{0}\" value=\"{1}\"> {2}</label>".format(
                esc(question["question_id"]), esc(option["id"]), esc(option["text"]),
            )
            for option in question["options"]
        )
        rows.append("<fieldset><legend>{0}</legend>{1}</fieldset>".format(esc(question["stem"]), options))
    body = """
    <section class="card">
      <p class="warning">Synthetic fixture course. Not the unpublished school.</p>
      <form method="post" action="/school/grade">
        <input type="hidden" name="csrf" value="{csrf}">
        <input type="hidden" name="attempt_id" value="{attempt}">
        {rows}
        <button type="submit">Submit</button>
      </form>
    </section>
    """.format(csrf=esc(csrf), attempt=esc(attempt["attempt_id"]), rows="".join(rows))
    return page("Practice question", body, logged_in=True)


def school_page(catalog):
    items = []
    for module in catalog["modules"]:
        lessons = ""
        if module.get("study") and module.get("lessons"):
            lessons = "<ul class=\"school\">" + "".join(
                "<li><a href=\"/school/{module}/{lesson}\">{title}</a></li>".format(
                    module=esc(module["module_id"]), lesson=esc(row["lesson_id"]), title=esc(row["title"]),
                )
                for row in module["lessons"]
            ) + "</ul>"
        else:
            lessons = "<p>Locked until the previous module is cleared on the server.</p>"
        items.append("<li><span>{label}</span> <small>Unpublished draft</small>{lessons}</li>".format(
            label=esc(module["label"]), lessons=lessons,
        ))
    body = """
    <section class="card">
      <p class="warning">Unpublished draft. Not an accepted course. Fact review is unfinished.</p>
      <p>Pass mark {mark}. {credential}</p>
      <p>{note}</p>
      <p>Lessons opened on this login: {read}. Quiz keys stay on the server.</p>
      <p><a href="/school/exercise">Synthetic worked exercise</a>. It is not a market result.</p>
      <ol class="school">{items}</ol>
    </section>
    """.format(
        mark=esc(catalog["pass_mark"]), credential=esc(catalog["credential"]),
        note=esc(catalog["coverage_note"]), read=esc(catalog.get("lessons_read", 0)),
        items="".join(items),
    )
    return page("The Transparent Trading School", body, logged_in=True)


def exercise_page(item):
    body = """
    <section class="card">
      <p class="warning">Synthetic exercise. Not market data, not an order book, and not a performance claim.</p>
      <p>{disclaimer}</p>
      <p>Seed {seed}. Chosen in-sample lookback {lookback}. In-sample net {insample}. Out of sample {oos}. Look-ahead in sample {ahead}.</p>
      <p>Split-day raw return {raw}. Split-adjusted return {adjusted}.</p>
      <p>These figures are the synthetic CSV for the lookback chosen in sample. They are not a walk-forward Sharpe example from a lesson, and they are not the best out-of-sample row.</p>
      <p><a href="/school/exercise/synthetic_mini_backtest.csv">Synthetic price file</a></p>
      <p><a href="/school/exercise/synthetic_corporate_actions.csv">Synthetic split file</a></p>
      <p><a href="/school">Back to modules</a></p>
    </section>
    """.format(
        disclaimer=esc(item["disclaimer"]), seed=esc(item["seed"]), lookback=esc(item["lookback"]),
        insample=esc(item["in_sample_net"]), oos=esc(item["out_of_sample_net"]),
        ahead=esc(item["look_ahead_in_sample"]), raw=esc(item["raw_split_return"]),
        adjusted=esc(item["adjusted_split_return"]),
    )
    return page("Synthetic exercise", body, logged_in=True)


def lesson_page(lesson):
    text = "".join("<p>{0}</p>".format(esc(part.strip())) for part in lesson["body"].split("\n") if part.strip())
    body = """
    <section class="card">
      <p class="warning">{warning}</p>
      <p><a href="/school">Back to modules</a></p>
      <h2>{title}</h2>
      {text}
      <p>{credential} The quiz is not open on this draft.</p>
    </section>
    """.format(
        warning=esc(lesson["warning"]), title=esc(lesson["title"]), text=text,
        credential=esc(lesson["credential"]),
    )
    return page(lesson["title"], body, logged_in=True)


FUNDS_ACK = FUNDS_PHRASE
