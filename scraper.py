#!/usr/bin/env python3
"""
Eye of Riyadh -> ICS generator (no-code friendly, safe version)
- Scrapes events and builds build/eyeofriyadh.ics
- Never crashes: always writes an ICS (adds a 1hr "TEST" event for tomorrow if no events found)
- Keep dependencies limited to: requests, beautifulsoup4, python-dateutil, pytz
"""

import os
import re
import time
import hashlib
import logging
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
import pytz

# ---------- Config ----------
BASE_URL = os.environ.get("EOR_BASE_URL", "https://www.eyeofriyadh.com/events/")
MAX_PAGES = int(os.environ.get("EOR_MAX_PAGES", "10"))
REQUEST_TIMEOUT = 25
SLEEP_BETWEEN_REQUESTS = 0.2  # seconds
TZ = pytz.timezone("Asia/Riyadh")
OUT_DIR = os.environ.get("OUT_DIR", "build")
OUT_FILE = os.path.join(OUT_DIR, "eyeofriyadh.ics")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
# ----------------------------

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("eor")

# Broad selectors; site layout can vary. We keep this forgiving.
SELECTORS = {
    "card": [
        "div.events-list div.event-item",
        "div.event-card",
        "div.card",
        "ul.events li",
        "li.event",
        "article",
        "div[class*='event']",
    ],
    "title": ["h3", "h2", "a[title]", "a"],
    "link": ["a[href]"],
    "date": ["time", "div.date", "span.date", "p.date", "li.date", "section time"],
    "location": ["address", "div.location", "span.location", "p.location", "li.location"],
    "detail_desc": ["div.event-description", "div#description", "div.description", "article", "main"],
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; EyeOfRiyadhICS/1.1)"
}

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())

def first_text(el):
    return normalize(el.get_text(" ", strip=True)) if el is not None else None

def pick_first(soup, selectors):
    for sel in selectors:
        try:
            found = soup.select(sel)
            if found:
                return found[0]
        except Exception:
            pass
    return None

def backoff_fetch(url: str, tries: int = 3) -> str:
    delay = 1.0
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            log.warning("Fetch failed [%s] (%s) attempt %d/%d", url, e, attempt, tries)
            time.sleep(delay)
            delay *= 1.8
    return None

def list_pages():
    urls = [BASE_URL]
    patterns = ["?p={i}", "page/{i}/", "?page={i}", "&p={i}", "&page={i}"]
    for i in range(2, MAX_PAGES + 1):
        for pat in patterns:
            urls.append(urljoin(BASE_URL, pat.format(i=i)))
    # de-dup while keeping order
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def parse_dt(text):
    if not text:
        return None, None
    text = normalize(text)
    parts = re.split(r"\s?(?:–|-|to)\s?", text, flags=re.I)
    start = end = None
    try:
        default_start = TZ.localize(datetime.now().replace(hour=9, minute=0, second=0, microsecond=0))
        start = dateparser.parse(parts[0], dayfirst=True, default=default_start)
        start = start.astimezone(TZ) if start.tzinfo else TZ.localize(start)
    except Exception:
        start = None
    if len(parts) > 1 and start:
        try:
            default_end = start.replace(hour=18, minute=0)
            end = dateparser.parse(parts[1], dayfirst=True, default=default_end)
            end = end.astimezone(TZ) if end.tzinfo else TZ.localize(end)
        except Exception:
            end = None
    if start and (start.hour, start.minute) == (0, 0):
        start = start.replace(hour=9, minute=0)
    if end and (end.hour, end.minute) == (0, 0):
        end = end.replace(hour=18, minute=0)
    return start, end

def scrape():
    events = []
    for url in list_pages():
        html = backoff_fetch(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        cards = None
        for sel in SELECTORS["card"]:
            found = soup.select(sel)
            if found:
                cards = found
                break
        if not cards:
            continue
        for c in cards:
            title_el = pick_first(c, SELECTORS["title"])
            link_el  = pick_first(c, SELECTORS["link"])
            date_el  = pick_first(c, SELECTORS["date"])
            loc_el   = pick_first(c, SELECTORS["location"])
            title = first_text(title_el)
            href  = link_el.get("href") if link_el else None
            link  = urljoin(url, href) if href else None
            date_text = first_text(date_el)
            location  = first_text(loc_el)
            if title and link:
                events.append({
                    "title": title,
                    "link": link,
                    "date_text": date_text,
                    "location": location,
                })
        time.sleep(SLEEP_BETWEEN_REQUESTS)
    # dedupe
    seen, out = set(), []
    for e in events:
        key = (e.get("link") or "") + "|" + (e.get("title") or "")
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out

def build_ics(events):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//EyeOfRiyadh ICS//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    now_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for ev in events:
        title = normalize(ev.get("title") or "Untitled Event")
        link  = ev.get("link") or BASE_URL
        start, end = parse_dt(ev.get("date_text"))
        if not start:
            start = TZ.localize(datetime.now().replace(hour=9, minute=0, second=0, microsecond=0))
        if not end:
            end = start + timedelta(hours=8)
        uid = hashlib.md5(f"{title}|{link}|{start}".encode("utf-8")).hexdigest() + "@eyeofriyadh"
        desc = normalize(f"More info: {link}")
        location = normalize(ev.get("location") or "Riyadh, Saudi Arabia")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_utc}",
            f"SUMMARY:{title}",
            f"DTSTART;TZID=Asia/Riyadh:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Riyadh:{end.strftime('%Y%m%dT%H%M%S')}",
            f"LOCATION:{location}",
            f"DESCRIPTION:{desc}",
            f"URL:{link}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)

def write_ics(text):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    log.info("Wrote %s", OUT_FILE)

def write_test_ics():
    tomorrow = TZ.localize(datetime.now()).date() + timedelta(days=1)
    start = TZ.localize(datetime.combine(tomorrow, datetime.min.time()).replace(hour=10, minute=0))
    end   = start + timedelta(hours=1)
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//EyeOfRiyadh ICS//EN\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "METHOD:PUBLISH\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:test-{int(datetime.utcnow().timestamp())}@eyeofriyadh\r\n"
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}\r\n"
        "SUMMARY:TEST: Eye of Riyadh feed wiring\r\n"
        f"DTSTART;TZID=Asia/Riyadh:{start.strftime('%Y%m%dT%H%M%S')}\r\n"
        f"DTEND;TZID=Asia/Riyadh:{end.strftime('%Y%m%dT%H%M%S')}\r\n"
        "LOCATION:Riyadh\r\n"
        f"URL:{BASE_URL}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    write_ics(ics)

def main():
    try:
        events = scrape()
    except Exception as e:
        log.error("Scrape crashed: %s", e)
        events = []

    if not events:
        log.warning("No events found; writing TEST ICS so subscription shows something")
        write_test_ics()
        return

    ics = build_ics(events)
    write_ics(ics)

if __name__ == "__main__":
    main()
