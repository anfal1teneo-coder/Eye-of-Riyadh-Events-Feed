#!/usr/bin/env python3
"""
Eye of Riyadh → ICS generator
- Scrapes event listings and builds an .ics calendar
- Designed to be run on a schedule (e.g., GitHub Actions)
- Output: build/eyeofriyadh.ics

Customize CSS selectors in SELECTORS below if the site structure changes.
"""
import re
import os
import sys
import time
import json
import hashlib
import logging
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    from dateutil import parser as dateparser
    import pytz
except Exception as e:
    print("Please install requirements: pip install -r requirements.txt", file=sys.stderr)
    raise

BASE_URL = os.environ.get("EOR_BASE_URL", "https://www.eyeofriyadh.com/events/")
# How many listing pages to scan
MAX_PAGES = int(os.environ.get("EOR_MAX_PAGES", "8"))

# Timezone for Riyadh
TZ = pytz.timezone("Asia/Riyadh")

# Output file
OUT_DIR = os.environ.get("OUT_DIR", "build")
OUT_FILE = os.path.join(OUT_DIR, "eyeofriyadh.ics")

# Logging
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("eor")

# --- Site-specific selectors (adjust if needed) ---
SELECTORS = {
    # container for a single event item on listing page
    "card": ["div.event-card", "div.events-list div.event-item", "div.list div.item"],
    # within a card:
    "title": ["h3", "a.title", "h2"],
    "link": ["a", "a.title", "h3 a"],
    "date": ["div.date", "span.date", "p.date"],
    "location": ["div.location", "span.location", "p.location"],
    # on the detail page (fallbacks)
    "detail_date": ["div.event-date", "li:contains('Date')", "p:contains('Date')"],
    "detail_location": ["div.event-location", "li:contains('Location')", "p:contains('Location')"],
    "detail_desc": ["div.event-description", "div#description", "div.description", "article"]
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; EyeOfRiyadhICS/1.0; +https://github.com/YOU)"
}

def first_text(el):
    if not el:
        return None
    txt = el.get_text(" ", strip=True)
    return txt if txt else None

def pick(soup, selectors):
    for sel in selectors:
        try:
            found = soup.select(sel)
            if found:
                return found[0]
        except Exception:
            continue
    return None

def normalize_whitespace(s):
    return re.sub(r"\s+", " ", s or "").strip()

def parse_dt(text):
    # Attempt to parse various date formats, assume Asia/Riyadh
    if not text:
        return None, None
    text = normalize_whitespace(text)
    # Some sites show ranges like "Jan 12–15, 2026" or "12-15 January 2026"
    # dateutil can handle many; we also try to split on range dashes
    m = re.split(r"\s?[–\-to]+\s?", text, flags=re.I)
    try:
        start = dateparser.parse(m[0], dayfirst=True, default=TZ.localize(datetime.now()).replace(hour=9, minute=0, second=0, microsecond=0))
        start = TZ.localize(start.replace(tzinfo=None)) if start.tzinfo is None else start.astimezone(TZ)
    except Exception:
        start = None
    end = None
    if len(m) > 1:
        try:
            # If second part lacks month/year, dateutil infers from first
            end = dateparser.parse(m[1], dayfirst=True, default=start or TZ.localize(datetime.now()))
            end = TZ.localize(end.replace(tzinfo=None)) if end and end.tzinfo is None else (end.astimezone(TZ) if end else None)
        except Exception:
            end = None
    # Make all-day semantics: set times to 09:00-18:00 if none
    if start:
        if start.hour == 0 and start.minute == 0:
            start = start.replace(hour=9, minute=0)
    if end:
        if end.hour == 0 and end.minute == 0:
            end = end.replace(hour=18, minute=0)
    return start, end

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r

def list_page_urls():
    urls = [BASE_URL]
    # Guess pagination patterns commonly used
    patterns = [
        "?p={i}", "page/{i}/", "?page={i}", "&p={i}", "&page={i}"
    ]
    for i in range(2, MAX_PAGES + 1):
        for pat in patterns:
            urls.append(urljoin(BASE_URL, pat.format(i=i)))
    # Deduplicate while preserving order
    seen = set()
    result = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result

def scrape_listings():
    events = []
    for url in list_page_urls():
        try:
            res = fetch(url)
        except Exception as e:
            log.debug("Skip page %s (%s)", url, e)
            continue
        soup = BeautifulSoup(res.text, "html.parser")
        cards = []
        for sel in SELECTORS["card"]:
            found = soup.select(sel)
            if found:
                cards = found
                break
        if not cards:
            log.debug("No cards on %s", url)
            continue
        for c in cards:
            title_el = pick(c, SELECTORS["title"])
            link_el = pick(c, SELECTORS["link"])
            date_el = pick(c, SELECTORS["date"])
            loc_el = pick(c, SELECTORS["location"])
            title = first_text(title_el)
            href = link_el.get("href") if link_el else None
            link = urljoin(url, href) if href else None
            date_text = first_text(date_el)
            location = first_text(loc_el)
            events.append({
                "title": title,
                "link": link,
                "date_text": date_text,
                "location": location
            })
    # Deduplicate by link/title
    dedup = {}
    for e in events:
        key = e.get("link") or e.get("title")
        if key and key not in dedup:
            dedup[key] = e
    return list(dedup.values())

def enrich_from_detail(event):
    if not event.get("link"):
        return event
    try:
        res = fetch(event["link"])
    except Exception as e:
        log.debug("detail fetch failed: %s", e)
        return event
    soup = BeautifulSoup(res.text, "html.parser")
    # try to improve date/location/description
    if not event.get("date_text"):
        d = pick(soup, SELECTORS["detail_date"])
        event["date_text"] = first_text(d)
    if not event.get("location"):
        l = pick(soup, SELECTORS["detail_location"])
        event["location"] = first_text(l)
    desc_el = pick(soup, SELECTORS["detail_desc"])
    event["description"] = first_text(desc_el)
    return event

def to_uid(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest() + "@eyeofriyadh"

def build_ics(events):
    # Minimal ICS builder (no external deps)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//EyeOfRiyadh ICS//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    now_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for ev in events:
        title = normalize_whitespace(ev.get("title") or "Untitled Event")
        link = ev.get("link") or BASE_URL
        start, end = parse_dt(ev.get("date_text"))
        # fallback: treat as all-day today if no parse
        if not start:
            start = TZ.localize(datetime.now()).replace(hour=9, minute=0)
        if not end:
            # if the date string has a single date, make a 1-day event
            end = start + timedelta(hours=8)
        uid = to_uid(f"{title}|{link}|{start.isoformat()}")
        desc_parts = []
        if ev.get("description"):
            desc_parts.append(ev["description"])
        desc_parts.append(f"More info: {link}")
        desc = normalize_whitespace(" ".join(desc_parts))
        location = normalize_whitespace(ev.get("location") or "Riyadh, Saudi Arabia")

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_utc}",
            f"SUMMARY:{title}",
            f"DTSTART;TZID=Asia/Riyadh:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Riyadh:{end.strftime('%Y%m%dT%H%M%S')}",
            f"LOCATION:{location}",
            "DESCRIPTION:" + desc.replace('\\n', ' ').replace('\n', ' '),
            f"URL:{link}",
            "END:VEVENT"
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    log.info("Scraping listings...")
    events = scrape_listings()
    log.info("Found ~%d events. Enriching...", len(events))
    enriched = []
    for e in events:
        enriched.append(enrich_from_detail(e))
        time.sleep(0.2)  # be polite
    # filter for future events only
    future = []
    today = TZ.localize(datetime.now()).date()
    for e in enriched:
        start, _ = parse_dt(e.get("date_text"))
        if not start or start.date() >= today - timedelta(days=7):  # keep recent/past week
            future.append(e)
    ics = build_ics(future)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics)
    log.info("Wrote %s (%d bytes)", OUT_FILE, len(ics))

if __name__ == "__main__":
    main()

# Eye of Riyadh → ICS (Auto-updating)

This repo scrapes events from **Eye of Riyadh** and publishes an auto-updating **ICS calendar** you can subscribe to from **Teamup**, Google Calendar, or Outlook.

## Quick start (local)

```bash
pip install -r requirements.txt
python scraper.py
# output: build/eyeofriyadh.ics
```

Subscribe in **Teamup**: *Settings → Calendars → Add calendar via URL* and paste the URL where you host the generated `eyeofriyadh.ics` (see GitHub Pages below).

## Deploy (GitHub Actions + Pages)

1. Create a new public repo with these files.
2. Enable **Actions** and **Pages** (Pages → Deploy from branch → `gh-pages`).
3. Add a **fine-scoped PAT** (classic is OK) as a repo secret named `GH_TOKEN` with `contents: write`.
4. Commit and push. Actions will run daily and publish `eyeofriyadh.ics` at:

```
https://<your-username>.github.io/<repo-name>/eyeofriyadh.ics
```

Use that URL in Teamup.

### Workflow details

- Scraper runs **daily at 05:00 UTC**.
- Output is pushed to the `gh-pages` branch at the repo root for easy Pages hosting.

If Eye of Riyadh changes HTML structure, tweak the CSS selectors in `scraper.py` at `SELECTORS` or increase `EOR_MAX_PAGES` via env var.

## Configuration

Environment variables (optional):

- `EOR_BASE_URL` — base listing URL (default: `https://www.eyeofriyadh.com/events/`)
- `EOR_MAX_PAGES` — number of pages to attempt (default: `8`)
- `OUT_DIR` — output directory (default: `build`)
- `LOG_LEVEL` — e.g., `DEBUG` for verbose logs

## Notes

- Timezone is set to **Asia/Riyadh**.
- Dates are parsed heuristically; the script attempts to detect ranges like “12–15 Jan 2026”.
- The ICS builder is self-contained (no external icalendar dependency).

requests
beautifulsoup4
python-dateutil
pytz
