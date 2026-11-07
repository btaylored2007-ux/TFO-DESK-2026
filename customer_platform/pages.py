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
            "<a href=\"/teach\">Teach</a><a href=\"/ask\">Ask</a><a href=\"/board\">Board</a><a href=\"/account\">Account</a></nav>"
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
      <p class="eyebrow">The Transparent Trading School</p>
      <h1>[[TITLE]]</h1>
    </div>
  </a>
  [[NAV]]
</header>
<main>
[[BODY]]
</main>
<footer>
  Private to this login. Practice figures stay on this subscription.
  This template does not publish a leaderboard and it is not the owner desk.
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
          <h2>Create a subscription</h2>
          <p>This creates an isolated practice desk on this loopback app. It does not open a public website.</p>
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
    later = ", ".join(status.get("later_not_built") or [])
    body = """
    {notice}
    <section class="banner">
      <p>Hosted customer web app. Browser login and an isolated dashboard. Not a desktop install.</p>
      <p>Later, and not built here: {later}. Private performance is not published.</p>
    </section>
    <section class="card">
      <h2>Log in</h2>
      <form method="post" action="/login">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Email <input name="email" autocomplete="username" required></label>
        <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
        <button type="submit">Log in</button>
      </form>
    </section>
    {signup}
    """.format(notice=notice, later=esc(later), csrf=esc(csrf), signup=signup)
    return page("Practice desk", body, csrf=csrf)


def desk(model, csrf):
    figures = """
    <section class="figures">
      <article><span>{cap_label}</span><strong>{cap} {cur}</strong></article>
      <article><span>{pnl_label}</span><strong>{pnl} {cur}</strong></article>
      <article><span>{hist_label}</span><strong>{hist} {cur}</strong></article>
    </section>
    """.format(
        cap_label=esc(model["practice_capital_label"]),
        cap=esc(model["practice_capital"]),
        pnl_label=esc(model["session_pnl_label"]),
        pnl=esc(model["session_pnl"]),
        hist_label=esc(model["historical_realized_pnl_label"]),
        hist=esc(model["historical_realized_pnl"]),
        cur=esc(model["currency"]),
    )
    banner = ""
    if model["chart"].get("fixture") or model.get("fixture"):
        banner = "<p class=\"warning\">Fixture prices. Not a market print.</p>"
    holdings = "".join(
        "<tr><td>{symbol}</td><td>{qty}</td><td>{sector}</td><td>{mark}</td><td>Shown even if this sector is opted out. Exit stays available.</td></tr>".format(
            symbol=esc(row["symbol"]), qty=esc(row["qty"]), sector=esc(row["sector"]), mark=esc(row.get("mark") or "unmarked"),
        )
        for row in model["open_positions"]
    ) or "<tr><td colspan=\"5\">No open position.</td></tr>"
    pending = "".join(
        "<tr><td>{symbol}</td><td>{side}</td><td>{qty}</td><td>{price}</td></tr>".format(
            symbol=esc(row["symbol"]), side=esc(row["side"]), qty=esc(row["qty"]), price=esc(row["limit_price"]),
        )
        for row in model["pending_orders"]
    ) or "<tr><td colspan=\"4\">No pending order.</td></tr>"
    body = """
    {banner}
    <section class="hero" aria-live="polite">
      <p class="state">{headline}</p>
      <p>{notice}</p>
      {action}
    </section>
    {figures}
    <section class="card">
      <h2>Paper ledger</h2>
      {chart}
      <p>Configured starting capital for the next reset default: {start} {cur}.</p>
    </section>
    <section class="card">
      <h2>Learning brain</h2>
      <p>Model notes stay off this page. <a href="/brain">Notes</a></p>
    </section>
    <section class="card">
      <h2>Open holdings</h2>
      <table><thead><tr><th>Symbol</th><th>Qty</th><th>Sector</th><th>Mark</th><th></th></tr></thead><tbody>{holdings}</tbody></table>
      <p>Opted out of new entries: {opt}. Opt-out does not hide a holding and does not disable an exit.</p>
      <form method="post" action="/desk/optout">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Sectors to opt out, comma separated <input name="sectors" value="{opt}"></label>
        <button type="submit">Save opt-out</button>
      </form>
    </section>
    <section class="card">
      <h2>Pending orders</h2>
      <table><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Limit</th></tr></thead><tbody>{pending}</tbody></table>
    </section>
    <section class="card">
      <h2>Reset practice capital</h2>
      <p>The new epoch's performance P&amp;L starts at 0. Capital becomes the starting figure you choose. The default is this account's configured starting amount. Old fills stay on the ledger.</p>
      <form method="post" action="/desk/reset/preview">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Chosen starting capital <input name="amount" value="{start}" inputmode="decimal"></label>
        <button type="submit">Preview reset</button>
      </form>
    </section>
    """.format(
        banner=banner, figures=figures, chart=chart_svg(model["chart"]),
        start=esc(model["configured_starting_capital"]), cur=esc(model["currency"]),
        headline=esc(model["trading"]["headline"]),
        notice=esc(model["trading"].get("notice") or ""),
        action=_trade_action(model["trading"], csrf),
        holdings=holdings, pending=pending,
        opt=esc(", ".join(model["sector_opt_out"])), csrf=esc(csrf),
    )
    return page("Practice desk", body, logged_in=True)


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


def brain_page(status, lessons, pack, csrf, message=""):
    notice = "<p class=\"warning\">{0}</p>".format(esc(message)) if message else ""
    rows = "".join(
        "<tr><td>{status}</td><td>{kind}</td><td>{effect}</td><td>{text}</td></tr>".format(
            status=esc(row["status"]), kind=esc(row["kind"]), effect=esc(row["trading_effect"]), text=esc(row["statement"]),
        )
        for row in lessons
    ) or "<tr><td colspan=\"4\">No persisted lesson yet.</td></tr>"
    rules = "".join("<li>{0}</li>".format(esc(rule["text"])) for rule in pack["rules"])
    body = """
    {notice}
    <section class="card">
      <h2>This subscription's brain</h2>
      <p>Notes from this desk. They do not place trades.</p>
      <p>{count} saved.</p>
      <form method="post" action="/brain/run">
        <input type="hidden" name="csrf" value="{csrf}">
        <button type="submit">Run one brain update</button>
      </form>
    </section>
    <section class="card">
      <h2>Persisted lessons</h2>
      <table><thead><tr><th>Status</th><th>Kind</th><th>Trading effect</th><th>Statement</th></tr></thead><tbody>{rows}</tbody></table>
    </section>
    <section class="card">
      <h2>Submit research</h2>
      <p>Text is stored as data. It does not become an instruction, and it does not change risk rails.</p>
      <form method="post" action="/brain/idea">
        <input type="hidden" name="csrf" value="{csrf}">
        <label>Note <textarea name="body" rows="4" required></textarea></label>
        <button type="submit">Save as research</button>
      </form>
    </section>
    <section class="card">
      <h2>Starter pack, separate from the brain</h2>
      <p>Version {version}. This pack does not make the brain updated.</p>
      <ul>{rules}</ul>
    </section>
    """.format(
        notice=notice, statement=esc(status["statement"]), csrf=esc(csrf), rows=rows,
        count=len(lessons), version=esc(pack["version"]), rules=rules,
    )
    return page("Learning brain", body, logged_in=True)


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


def admin_page(overview=None, message=""):
    notice = "<p class=\"warning\">{0}</p>".format(esc(message)) if message else ""
    if overview is None:
        body = notice + """
        <section class="card">
          <h2>Operator login</h2>
          <p>Operators see subscription status. They do not see customer secrets, research text, or provider keys.</p>
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
    body = notice + """
    <section class="card">
      <h2>Subscriptions</h2>
      <table><thead><tr><th>Tenant</th><th>Name</th><th>Sandbox state</th><th>Commercial paid</th><th>News</th><th>Providers</th><th>Lessons</th><th>Brain error</th><th>Model running</th></tr></thead>
      <tbody>{rows}</tbody></table>
    </section>
    """.format(rows=rows or "<tr><td colspan=\"9\">No subscription yet.</td></tr>")
    return body


def admin_document(csrf, overview=None, message=""):
    body = admin_page(overview, message)
    if overview is None:
        body = body.format(csrf=esc(csrf))
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
    height = 200
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
    <p>{label}. {count} ledger points. Currency {cur}.</p>
    """.format(width=width, height=height, points=" ".join(coords), label=esc(label), count=len(points), cur=esc(chart["currency"]))


def teach_page(skills, csrf, notice=""):
    blocks = []
    if not skills:
        blocks.append("<p>No skill saved yet.</p>")
    for skill in skills:
        rules = "".join("<li>{0}: {1}</li>".format(esc(key), esc(value)) for key, value in skill["rules"].items())
        notes = "".join("<li>{0}</li>".format(esc(item)) for item in skill["clarifications"])
        blocks.append(
            "<article class=\"card\"><h2>{status}</h2><p>Traders: {traders}. Source: {source}. AI: {ai}.</p><ul>{rules}</ul><ul>{notes}</ul><p>Versions: {versions}. This is not a promise of results.</p></article>".format(
                status=esc(skill["status"]), traders=esc(", ".join(skill["trader_ids"])),
                source=esc(skill["rules_source"]), ai=esc(skill["ai"]),
                rules=rules or "<li>No rules interpreted.</li>", notes=notes, versions=len(skill["versions"]),
            )
        )
    warning = "<p class=\"warning\">{0}</p>".format(esc(notice)) if notice else ""
    body = """
    <section class="card">
      <h2>Upload skills</h2>
      <p>Describe a strategy in your own words, or upload a plain text file. This is separate from the School.</p>
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
    </section>
    {blocks}
    """.format(warning=warning, csrf=esc(csrf), blocks="".join(blocks))
    return page("Teach my traders", body, logged_in=True)


def _trade_action(trading, csrf):
    if trading.get("state") == "running":
        path, label, kind = "/desk/stop", "Stop", "stop"
    else:
        path, label, kind = "/desk/start", "Start", "start"
    return (
        '<form method="post" action="{path}"><input type="hidden" name="csrf" value="{csrf}">'
        '<button class="tap {kind}" type="submit">{label}</button></form>'
    ).format(path=path, csrf=esc(csrf), kind=kind, label=label)


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
        cards.append(
            "<article><p>{0} · {1}</p><p>{2}</p>{3}{4}</article>".format(
                esc(row.get("status", "")), esc(row.get("provider", "")), esc(row.get("answer", "")), events, reviews,
            )
        )
    body = (
        "<section class=\"card\"><h2>Ask</h2>"
        "<p>No provider is connected. A saved question is not sent out.</p>"
        "<form method=\"post\" action=\"/ask\"><input type=\"hidden\" name=\"csrf\" value=\"{csrf}\">"
        "<label>Question <textarea name=\"text\" rows=\"4\" required></textarea></label>"
        "<button class=\"tap\" type=\"submit\">Save question</button></form>"
        "{cards}</section>"
    ).format(csrf=esc(csrf), cards="".join(cards) or "<p>No question saved yet.</p>")
    return page("Ask", body, logged_in=True)


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
        items.append("<li><span>{order}. {label}</span> <small>Unpublished draft</small>{lessons}</li>".format(
            order=module["order"], label=esc(module["label"]), lessons=lessons,
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
