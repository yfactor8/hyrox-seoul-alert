#!/usr/bin/env python3
"""
HYROX Seoul ticket-availability monitor.

Renders the REAL vivenu ticket shop in a headless browser and reads the exact
per-division "SOLD OUT" state a human would see, then pushes an ntfy.sh
notification the moment a watched division flips from sold-out -> buyable
(i.e. a cancellation reopens it).

Why a browser and not the JSON API: vivenu computes per-division sold-out state
client-side. The public JSON endpoints only return an optimistic event-level
answer (checkout.allowed=true whenever ANY category has stock), which produced
false "available" readings. Reading the rendered shop is what actually matches
what you see when you click through.

Read-only: it navigates the shop's category/filter buttons but never adds to
cart, reserves, or buys anything.

For each watched division we open the checkout, click through its filter path
(e.g. Singles -> Open -> Women), locate the specific ticket card by a unique
name fragment, and mark it BUYABLE unless the card shows a "SOLD OUT" badge.

State is persisted in state.json so we only alert on a *change*, not every run.
"""

import json
import os
import sys
import time
import datetime
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
# Optional, git-ignored overlay holding the real ntfy topic for local runs, so no
# secret is committed. Cloud runs get the topic from the NTFY_TOPIC env/secret.
CONFIG_LOCAL_PATH = os.path.join(HERE, "config.local.json")
# STATE_PATH / topic can be overridden by env so the same script runs in a hosted
# cron (e.g. GitHub Actions) with a cached state file and a secret topic.
STATE_PATH = os.environ.get("HYROX_STATE_PATH", os.path.join(HERE, "state.json"))
LOG_PATH = os.path.join(HERE, "monitor.log")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# JS run in the page to locate a specific ticket card by a unique name fragment
# and report whether it is sold out. Climbs a few ancestors to catch the badge
# but stops before pulling in an excluded neighbour card (e.g. the Adaptive row).
CARD_JS = r"""
(args) => {
  const {contains, excludes} = args;
  const all = Array.from(document.querySelectorAll('div,li,article,section,button'));
  let best = null;
  for (const n of all) {
    const t = n.innerText || '';
    if (t.includes(contains) && !excludes.some(x => t.includes(x)) && t.includes('₩')) {
      if (!best || t.length < best.len) best = {el: n, len: t.length, txt: t};
    }
  }
  if (!best) return {found: false};
  let node = best.el, soldout = false;
  for (let k = 0; k < 3 && node; k++) {
    const t = node.innerText || '';
    if (excludes.some(x => t.includes(x))) break;
    if (/sold\s*out/i.test(t)) { soldout = true; break; }
    node = node.parentElement;
  }
  return {found: true, soldout: soldout, snippet: best.txt.slice(0, 120)};
}
"""


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    line = f"[{now()}] {msg}"
    # Windows consoles are often cp1252 and choke on emoji; never let that crash us.
    try:
        print(line)
    except UnicodeEncodeError:
        enc = (sys.stdout.encoding or "ascii")
        print(line.encode(enc, "replace").decode(enc, "replace"))
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_config():
    """Load config.json, then shallow-merge config.local.json over it (one level
    deep for nested dicts like 'ntfy'). The local file is git-ignored so the real
    ntfy topic never gets committed."""
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        return None
    overlay = load_json(CONFIG_LOCAL_PATH, {})
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(val)
        else:
            cfg[key] = val
    return cfg


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


_PRIORITY_MAP = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5, "max": 5}


def ntfy_push(cfg, title, message, priority, click=None, tags=None):
    """Publish via ntfy's JSON format (POST to server root) so emoji/Unicode in
    the title work -- HTTP headers are latin-1 only and would choke on them."""
    n = cfg["ntfy"]
    server = os.environ.get("NTFY_SERVER", n["server"]).rstrip("/")
    topic = os.environ.get("NTFY_TOPIC", n["topic"])
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": _PRIORITY_MAP.get(str(priority).lower(), 3),
    }
    if click:
        payload["click"] = click
    if tags:
        payload["tags"] = tags if isinstance(tags, list) else [tags]
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        server, data=data,
        headers={"Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        log(f"ntfy sent ({priority}): {title}")
        return True
    except Exception as e:  # noqa: BLE001 - never let a push failure kill the run
        log(f"ntfy FAILED: {e}")
        return False


def _dismiss_cookies(page):
    for label in ("Only necessary", "Accept all", "Accept"):
        try:
            b = page.get_by_role("button", name=label)
            if b.count():
                b.first.click(timeout=3000)
                return
        except Exception:  # noqa: BLE001
            pass


def _check_one(page, checkout_url, w):
    """Navigate the shop to one watched division and return its state dict.
    Raises on navigation failure so the caller can treat it as an error (rather
    than silently reporting a wrong answer)."""
    page.goto(checkout_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3500)
    _dismiss_cookies(page)
    page.wait_for_timeout(800)

    # Click the category, then each filter step (e.g. "Singles" -> "Open" -> "Women").
    path = [w["nav"]["category"]] + list(w["nav"].get("steps", []))
    for label in path:
        page.get_by_text(label, exact=True).first.click(timeout=15000)
        page.wait_for_timeout(1500)

    # Wait for the target card to render, then read its sold-out state.
    contains = w["name_contains"]
    excludes = w.get("name_excludes", [])
    page.get_by_text(contains, exact=False).first.wait_for(timeout=15000)
    info = page.evaluate(CARD_JS, {"contains": contains, "excludes": excludes})
    if not info.get("found"):
        raise RuntimeError(f"target card not found for {w['label']!r} "
                           f"(nav {path}); shop layout may have changed")
    buyable = not info.get("soldout")
    return {
        "label": w["label"],
        "buyable": buyable,
        "reason": "buyable (no SOLD OUT badge)" if buyable else "SOLD OUT badge present",
    }


def _ensure_browsers_path(cfg):
    """Windows Task Scheduler launches processes with a stripped environment that
    can lack %LOCALAPPDATA%, so Playwright fails to locate the Chromium we
    installed under %LOCALAPPDATA%\\ms-playwright. Resolve it explicitly. On Linux
    (cloud) none of these exist, so we leave Playwright's own default alone."""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    candidates = []
    if cfg.get("playwright_browsers_path"):
        candidates.append(cfg["playwright_browsers_path"])
    for base in (os.environ.get("LOCALAPPDATA"),
                 os.path.join(os.environ["USERPROFILE"], "AppData", "Local")
                 if os.environ.get("USERPROFILE") else None,
                 os.path.join(os.path.expanduser("~"), "AppData", "Local")):
        if base:
            candidates.append(os.path.join(base, "ms-playwright"))
    for cand in candidates:
        if cand and os.path.isdir(cand):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = cand
            log(f"using browsers path: {cand}")
            return
    log(f"WARN: no ms-playwright dir found; tried {candidates}")


def _evaluate_once(cfg):
    from playwright.sync_api import sync_playwright

    checkout_url = cfg["checkout_url"]
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(locale="en-US", user_agent=UA)
            page = ctx.new_page()
            page.set_default_timeout(20000)
            for w in cfg["watch"]:
                results[w["ticket_id"]] = _check_one(page, checkout_url, w)
        finally:
            browser.close()
    return results, {"method": "browser-render", "checked": len(results)}


def evaluate(cfg):
    """Render the real shop and return (results, raw). results maps ticket_id ->
    dict(label, buyable, reason). Retries a few times so a single transient blip
    (a slow render, a dropped connection) self-heals within one run instead of
    surfacing as an error. Raises only if every attempt fails."""
    _ensure_browsers_path(cfg)
    attempts = int(cfg.get("check_attempts", 3))
    last_err = None
    for i in range(1, attempts + 1):
        try:
            return _evaluate_once(cfg)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if i < attempts:
                log(f"check attempt {i}/{attempts} failed ({type(e).__name__}); retrying")
                time.sleep(4)
    raise last_err


def main():
    cfg = load_config()
    if not cfg:
        log("FATAL: cannot read config.json")
        sys.exit(1)

    state = load_json(STATE_PATH, {})
    prev = state.get("tickets", {})       # ticket_id -> last buyable bool
    first_run = "tickets" not in state

    try:
        results, raw = evaluate(cfg)
    except Exception as e:  # noqa: BLE001
        fails = state.get("consec_failures", 0) + 1
        state["consec_failures"] = fails
        log(f"check error (consecutive #{fails}): {e}")
        maybe_error_alert(cfg, state, str(e), fails)
        save_json(STATE_PATH, state)
        return

    # a success clears the failure streak and any error cooldown
    state["consec_failures"] = 0
    state.pop("last_error_alert", None)

    summary = ", ".join(f"{r['label']}={'BUY' if r['buyable'] else 'no'}"
                        for r in results.values())
    log(f"poll OK | {raw} | {summary}")

    new_state = {}
    for tid, r in results.items():
        was = prev.get(tid)
        is_buy = r["buyable"]
        new_state[tid] = is_buy

        became_available = (was is False and is_buy)
        first_seen_available = (first_run and is_buy and cfg.get("alert_on_first_run_if_available"))

        if became_available or first_seen_available:
            tag = "rotating_light"
            when = "is AVAILABLE right now" if first_seen_available else "JUST OPENED UP"
            ntfy_push(
                cfg,
                title=f"🎟️ {r['label']} {when}!",
                message=(f"{cfg['event_name']}\n{r['label']} is buyable now. "
                         f"Tap to open the ticket page and check out FAST."),
                priority=cfg["ntfy"]["priority_available"],
                click=cfg["shop_url"],
                tags=tag,
            )
        elif was is True and not is_buy:
            log(f"{r['label']} went back to unavailable ({r['reason']})")

    state["tickets"] = new_state
    state["last_poll"] = now()
    state["last_raw"] = raw

    maybe_heartbeat(cfg, state, results)
    save_json(STATE_PATH, state)


def maybe_error_alert(cfg, state, detail, fails):
    """Only ping when the check has failed on several *consecutive* runs — a
    sustained breakage worth attention — not on a single transient network/render
    blip that recovers on the next run. A cooldown then avoids repeat spam."""
    threshold = int(cfg.get("error_alert_after_consecutive_failures", 5))
    if fails < threshold:
        return
    cooldown = cfg.get("error_alert_cooldown_minutes", 120) * 60
    last = state.get("last_error_alert", 0)
    if time.time() - last >= cooldown:
        ntfy_push(cfg, "⚠️ Hyrox monitor error",
                  f"The check has failed {fails} times in a row: {detail}. "
                  f"Still retrying — you may want to check it.",
                  cfg["ntfy"]["priority_info"], tags="warning")
        state["last_error_alert"] = time.time()


def maybe_heartbeat(cfg, state, results):
    hours = cfg.get("send_heartbeat_every_hours", 0)
    if not hours:
        return
    last = state.get("last_heartbeat", 0)
    if time.time() - last >= hours * 3600:
        summary = ", ".join(f"{r['label']}: {'available' if r['buyable'] else 'sold out'}"
                            for r in results.values())
        ntfy_push(cfg, "✅ Hyrox monitor alive",
                  f"Still watching. Current status: {summary}",
                  cfg["ntfy"]["priority_info"], tags="heartbeat")
        state["last_heartbeat"] = time.time()


if __name__ == "__main__":
    main()
