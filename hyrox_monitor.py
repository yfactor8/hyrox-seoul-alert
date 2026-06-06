#!/usr/bin/env python3
"""
HYROX Seoul ticket-availability monitor.

Polls the same public vivenu endpoints the official ticket page uses, decides
whether each watched division is BUYABLE, and pushes an ntfy.sh notification the
moment one flips from unavailable -> available (i.e. a cancellation reopens it).

Read-only: it never adds to cart, reserves, or buys anything.

Signal (per ticket):
  buyable = event.saleStatus == "onSale"
            AND availabilities.checkout.allowed == True
            AND ticket_id NOT in availabilities.tickets[]   (that list = blocked/sold-out)
            AND ticket.active == True                        (organizer enable flag)

State is persisted in state.json so we only alert on a *change*, not every run.
"""

import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
# STATE_PATH / topic can be overridden by env so the same script runs in a hosted
# cron (e.g. GitHub Actions) with a cached state file and a secret topic.
STATE_PATH = os.environ.get("HYROX_STATE_PATH", os.path.join(HERE, "state.json"))
LOG_PATH = os.path.join(HERE, "monitor.log")

LISTINGS_URL = "https://vivenu.com/api/events/public/listings/{event_id}"
AVAIL_URL = "https://vivenu.com/api/public/events/{event_id}/availabilities"
INFO_URL = "https://vivenu.com/api/events/info/{event_id}"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


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


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def http_get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


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


def evaluate(cfg):
    """Return (results, raw) where results maps ticket_id -> dict(buyable, reason, label)."""
    eid = cfg["event_id"]
    listings = http_get_json(LISTINGS_URL.format(event_id=eid))
    avail = http_get_json(AVAIL_URL.format(event_id=eid))

    sale_status = listings.get("saleStatus")
    indicator = listings.get("availabilityIndicator")
    checkout_allowed = bool(((avail or {}).get("checkout") or {}).get("allowed"))
    blocked = {t.get("id") or t.get("_id") for t in (avail.get("tickets") or [])}

    # active flags (organizer enable). info is a touch heavier; fetch best-effort.
    active_by_id = {}
    try:
        info = http_get_json(INFO_URL.format(event_id=eid))
        for t in info.get("tickets", []):
            tid = t.get("id") or t.get("_id")
            active_by_id[tid] = bool(t.get("active"))
    except Exception as e:  # noqa: BLE001
        log(f"info fetch failed (non-fatal): {e}")

    results = {}
    for w in cfg["watch"]:
        tid = w["ticket_id"]
        reasons = []
        if sale_status != "onSale":
            reasons.append(f"saleStatus={sale_status}")
        if not checkout_allowed:
            reasons.append("checkout not allowed")
        if tid in blocked:
            reasons.append("ticket in sold-out/blocked list")
        if tid in active_by_id and not active_by_id[tid]:
            reasons.append("ticket inactive")
        buyable = len(reasons) == 0
        results[tid] = {
            "label": w["label"],
            "buyable": buyable,
            "reason": "OK" if buyable else "; ".join(reasons),
        }

    raw = {
        "saleStatus": sale_status,
        "indicator": indicator,
        "checkout_allowed": checkout_allowed,
        "blocked_count": len(blocked),
    }
    return results, raw


def main():
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        log("FATAL: cannot read config.json")
        sys.exit(1)

    state = load_json(STATE_PATH, {})
    prev = state.get("tickets", {})       # ticket_id -> last buyable bool
    first_run = "tickets" not in state

    try:
        results, raw = evaluate(cfg)
    except urllib.error.HTTPError as e:
        log(f"HTTP error {e.code} polling vivenu: {e}")
        maybe_error_alert(cfg, state, f"HTTP {e.code}")
        save_json(STATE_PATH, state)
        return
    except Exception as e:  # noqa: BLE001
        log(f"poll error: {e}")
        maybe_error_alert(cfg, state, str(e))
        save_json(STATE_PATH, state)
        return

    # clear any error-cooldown once polling works again
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


def maybe_error_alert(cfg, state, detail):
    cooldown = cfg.get("error_alert_cooldown_minutes", 120) * 60
    last = state.get("last_error_alert", 0)
    if time.time() - last >= cooldown:
        ntfy_push(cfg, "⚠️ Hyrox monitor error",
                  f"Polling failed: {detail}. Will keep retrying.",
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
