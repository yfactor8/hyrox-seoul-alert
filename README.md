# Hyrox Seoul — Singles Women's Open ticket alert

Watches the **HYROX Seoul Season 26/27** ticket shop and pushes a phone
notification the instant the **Singles / Women / Open (Sunday)** division becomes
buyable — e.g. when a cancellation reopens a spot.

- **Race:** 14–15 Nov 2026, KINTEX (Goyang, Korea)
- **Shop:** https://korea.hyrox.com/event/hyrox-seoul-season-26-27-vthaza
- **Ticket watched:** `HYROX WOMEN 여자 오픈 | Sunday` (internal key `SOLO_OPEN_W`)

It is **read-only**: it polls the same public JSON endpoints the official ticket
page uses and never adds to cart, reserves, or buys anything.

---

## How it decides "available"

Each poll hits two public vivenu endpoints (no login/API key needed):

| Endpoint | Used for |
|---|---|
| `…/api/events/public/listings/{eventId}` | `saleStatus`, `availabilityIndicator` |
| `…/api/public/events/{eventId}/availabilities` | `checkout.allowed` + the blocked/sold-out ticket list |

A division is reported **BUYABLE** when:
`saleStatus == onSale` **and** `checkout.allowed` **and** the ticket is **not** in
the blocked list **and** the ticket's `active` flag is true.

It remembers the last status in `state.json` and only pushes when the status
**changes** (so no per-minute spam). It also sends a daily "still alive"
heartbeat and a rate-limited error ping if the API can't be reached.

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
| `hyrox_monitor.py` | The watcher (stdlib only — no `pip install` needed). |
| `config.json` | Event id, watched ticket(s), alert settings. **No secrets.** |
| `config.local.json` | Git-ignored. Holds the real ntfy topic for local runs. |
| `config.local.example.json` | Template — copy to `config.local.json` and fill in your topic. |
| `state.json` | Last-seen status (auto-created; safe to delete to re-baseline). |
| `monitor.log` | Run history. |
| `.github/workflows/monitor.yml` | GitHub Actions cloud backup. |

### Watch more divisions
Add entries to `watch[]` in `config.json`. Ticket IDs for this event, for example:

| Division | match_key | ticket_id |
|---|---|---|
| Singles Women's Open (Sun) | `SOLO_OPEN_W` | `69fafdfb399575a4b35692a4` |

(Find others by opening the shop and inspecting `…/api/events/info/{eventId}`.)
