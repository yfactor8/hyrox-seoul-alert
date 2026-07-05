# Hyrox Seoul — Singles Women's Open ticket alert

Watches the **HYROX Seoul Season 26/27** ticket shop and pushes a phone
notification the instant the **Singles / Women / Open (Sunday)** division becomes
buyable — e.g. when a cancellation reopens a spot.

- **Race:** 14–15 Nov 2026, KINTEX (Goyang, Korea)
- **Shop:** https://korea.hyrox.com/event/hyrox-seoul-season-26-27-vthaza
- **Ticket watched:** `HYROX WOMEN 여자 오픈 | Sunday` (internal key `SOLO_OPEN_W`)

It is **read-only**: it navigates the shop's category/filter buttons to reach the
ticket, but never adds to cart, reserves, or buys anything.

---

## How it decides "available"

It renders the **real ticket shop** in a headless browser (Playwright/Chromium)
and reads the exact per-division state a human sees. Each check:

1. Opens the checkout: `…/checkout/{eventId}`
2. Clicks the filter path for the division (Women's Open = **Singles → Open → Women**)
3. Finds the specific ticket card by a unique name fragment (`여자 오픈`, excluding
   the Adaptive row `어뎁티브`)
4. Marks it **BUYABLE** unless that card shows a **SOLD OUT** badge

> Why a browser instead of the JSON API: vivenu computes per-division sold-out
> state **client-side**. The public JSON endpoints only return an optimistic
> event-level answer (`checkout.allowed=true` whenever *any* category has stock),
> which gave false "available" readings. Reading the rendered shop is the only
> thing that matches what you actually see.

It remembers the last status in `state.json` and only pushes when the status
**changes** (so no per-check spam), plus a rate-limited error ping if the shop
can't be read. (The old daily "alive" heartbeat is off by default —
`send_heartbeat_every_hours: 0`.)

**Dependency:** Playwright + Chromium. Install once locally:
```powershell
pip install -r requirements.txt
python -m playwright install chromium
```
(The cloud workflow installs these automatically.)

---

## Alerts — ntfy.sh

Notifications go to a private ntfy **topic name that acts as a password** — anyone
who knows it can read your alerts or spam them. Because this repo is **public**,
the topic is **never committed**. It lives in two places only:

- **Local watcher:** `config.local.json` (git-ignored) — see `config.local.example.json`.
- **Cloud watcher:** the `NTFY_TOPIC` GitHub Actions secret.

Set up your phone once:
1. Install the **ntfy** app ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/us/app/ntfy/id1625396347)).
2. Tap **+ Subscribe to topic** and enter your topic (the value in `config.local.json`).
3. Done. Tapping an alert opens the ticket page so you can check out fast.

> If the topic ever leaks, rotate it: pick a new string, update `config.local.json`
> **and** the `NTFY_TOPIC` secret (`gh secret set NTFY_TOPIC --body "<new>"`), and
> re-subscribe your phone.

Send yourself a test anytime:
```powershell
python -c "import json,hyrox_monitor as m; c=m.load_config(); m.ntfy_push(c,'Test','It works','default')"
```

---

## Runtime 1 — this PC (already installed, ~1-minute checks)

A Windows Scheduled Task named **`HyroxSeoulTicketMonitor`** runs the script every
minute with `pythonw.exe` (no console window). It runs whenever the PC is on and
you're logged in.

Manage it:
```powershell
Get-ScheduledTask  -TaskName HyroxSeoulTicketMonitor          # status
Get-ScheduledTaskInfo -TaskName HyroxSeoulTicketMonitor       # last run / next run
Start-ScheduledTask   -TaskName HyroxSeoulTicketMonitor       # run now
Disable-ScheduledTask -TaskName HyroxSeoulTicketMonitor       # pause
Enable-ScheduledTask  -TaskName HyroxSeoulTicketMonitor       # resume
Unregister-ScheduledTask -TaskName HyroxSeoulTicketMonitor -Confirm:$false   # remove
```

Activity is logged to `monitor.log`.

## Runtime 2 — cloud backup for when the PC is off (free)

The local task only runs while your computer is on. For true 24/7 coverage,
`.github/workflows/monitor.yml` runs the **same script on GitHub's servers every
5 minutes**, independent of your machine.

This repo is **public** specifically so GitHub Actions minutes are free and
unlimited (private repos cap free minutes at 2,000/month, which an every-5-min
cron would blow through in about a week). The only sensitive value, the ntfy
topic, is kept out of the repo and supplied via the `NTFY_TOPIC` secret:

```powershell
gh secret set NTFY_TOPIC --body "<your-topic>"
```

State is cached between runs so the cloud watcher also only alerts on changes.

> GitHub auto-disables scheduled workflows after **60 days with no repo commits**.
> Push any small change (or hit **Run workflow** in the Actions tab) occasionally
> to keep it alive until race day.

---

## Files

| File | Purpose |
|---|---|
| `hyrox_monitor.py` | The watcher (renders the shop via Playwright). |
| `requirements.txt` | Python deps (Playwright). |
| `config.json` | Checkout URL, watched ticket(s) + filter path, alert settings. **No secrets.** |
| `config.local.json` | Git-ignored. Holds the real ntfy topic for local runs. |
| `config.local.example.json` | Template — copy to `config.local.json` and fill in your topic. |
| `state.json` | Last-seen status (auto-created; safe to delete to re-baseline). |
| `monitor.log` | Run history. |
| `.github/workflows/monitor.yml` | GitHub Actions cloud backup. |

### Watch more divisions
Add entries to `watch[]` in `config.json`. Each needs the shop **filter path** and
a **unique name fragment** for the ticket card. Example (the one being watched):

```json
{
  "label": "Singles Women's Open (Sunday)",
  "ticket_id": "69fafdfb399575a4b35692a4",
  "nav": { "category": "Singles", "steps": ["Open", "Women"] },
  "name_contains": "여자 오픈",
  "name_excludes": ["ADAPTIVE", "어뎁티브"]
}
```

- `nav.category` = the category card to click; `nav.steps` = the filter buttons
  after it (Class then Gender for Singles: `Open`/`Pro`, then `Men`/`Women`).
- `name_contains` must uniquely match the ticket card's text; `name_excludes`
  guards against matching a similarly-named neighbour (e.g. the Adaptive row).
