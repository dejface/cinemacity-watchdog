#!/usr/bin/env python3
"""
Cinema City watchdog — stráži nové termíny filmu v IMAX sále a pošle e-mail.

Použitie:
  1. Vyplň konfiguráciu nižšie (GMAIL_USER, GMAIL_APP_PASSWORD, TO_EMAIL).
  2. Prvé spustenie:  python3 kino_watchdog.py --seed   (len si zapamätá aktuálny stav)
  3. Cron (každých 10 min):  */10 * * * * /usr/bin/python3 /cesta/kino_watchdog.py

Žiadne závislosti mimo štandardnej knižnice.
"""

import json
import os
import smtplib
import ssl
import sys
import urllib.request
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

# ========================== KONFIGURÁCIA ==========================
CINEMA_ID = "1052"            # Praha Flora (1056=Chodov, 1033=Slovanský dům, ...)
FILM_PATTERN = "odyss"        # podreťazec názvu filmu, case-insensitive ("Odyssea")
AUDITORIUM_PATTERN = "imax"   # podreťazec názvu sály; "" = všetky sály
ONLY_70MM = True              # True = sledovať výhradne 70mm projekcie
DAYS_AHEAD = 45               # koľko dní dopredu kontrolovať
NOTIFY_FREED_SEATS = True     # upozorniť aj keď sa vypredané predstavenie uvoľní

GMAIL_USER = os.environ.get("GMAIL_USER", "tvoj.email@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "xxxx xxxx xxxx xxxx")
TO_EMAIL = os.environ.get("TO_EMAIL", GMAIL_USER)

STATE_FILE = Path(__file__).with_name("kino_state.json")
# ==================================================================

API = "https://www.cinemacity.cz/cz/data-api-service/v1/quickbook/10101/film-events/in-cinema/{cinema}/at-date/{date}?attr=&lang=cs_CZ"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) kino-watchdog/1.0"}


def fetch_day(day: str) -> dict:
    req = urllib.request.Request(API.format(cinema=CINEMA_ID, date=day), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["body"]


def collect_events() -> dict:
    """Vráti {event_id: info} pre všetky matchujúce predstavenia."""
    found = {}
    today = date.today()
    for i in range(DAYS_AHEAD):
        day = (today + timedelta(days=i)).isoformat()
        try:
            body = fetch_day(day)
        except Exception as e:
            print(f"WARN: {day}: {e}", file=sys.stderr)
            continue
        films = {f["id"]: f["name"] for f in body.get("films", [])}
        for ev in body.get("events", []):
            name = films.get(ev["filmId"], "")
            if FILM_PATTERN.lower() not in name.lower():
                continue
            if AUDITORIUM_PATTERN and AUDITORIUM_PATTERN.lower() not in ev.get("auditorium", "").lower():
                continue
            attrs = ev.get("attributeIds", [])
            if ONLY_70MM and "70-mm" not in attrs:
                continue
            found[ev["id"]] = {
                "film": name,
                "when": ev["eventDateTime"],
                "auditorium": ev.get("auditorium", "?"),
                "format": "70mm" if "70-mm" in attrs else ("dabing" if "dubbed" in attrs else "titulky"),
                "soldOut": ev.get("soldOut", False),
                "availability": ev.get("availabilityRatio"),
                "link": ev.get("bookingLink", "https://www.cinemacity.cz"),
            }
    return found


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def fmt(ev: dict) -> str:
    when = ev["when"].replace("T", " ")
    avail = ""
    if ev["availability"] is not None:
        avail = f", voľné ~{ev['availability'] * 100:.0f}%"
    return f"• {ev['film']} — {when} ({ev['format']}, {ev['auditorium']}{avail})\n  Kúpiť: {ev['link']}"


def send_email(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = TO_EMAIL
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        s.send_message(msg)


def main() -> None:
    seed = "--seed" in sys.argv
    current = collect_events()
    previous = load_state()

    new_events = {k: v for k, v in current.items() if k not in previous}
    freed = {}
    if NOTIFY_FREED_SEATS:
        freed = {
            k: v for k, v in current.items()
            if k in previous and previous[k].get("soldOut") and not v["soldOut"]
        }

    if seed:
        save_state(current)
        print(f"Seed: zapamätaných {len(current)} predstavení, nič neposielam.")
        return

    lines = []
    if new_events:
        lines.append("NOVÉ TERMÍNY:")
        lines += [fmt(v) for v in sorted(new_events.values(), key=lambda x: x["when"])]
    if freed:
        lines.append("\nUVOĽNENÉ MIESTA (bolo vypredané):")
        lines += [fmt(v) for v in sorted(freed.values(), key=lambda x: x["when"])]

    if lines:
        n = len(new_events) + len(freed)
        subject = f"🎬 Kino alert: {n} predstavení ({FILM_PATTERN.title()}, IMAX Flora)"
        body = "\n".join(lines)
        print(body)
        try:
            send_email(subject, body)
            print("E-mail odoslaný.")
        except Exception as e:
            print(f"CHYBA pri odosielaní e-mailu: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Nič nové.")

    save_state(current)


if __name__ == "__main__":
    main()
