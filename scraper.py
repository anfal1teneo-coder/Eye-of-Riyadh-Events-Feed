#!/usr/bin/env python3
"""
Eye of Riyadh -> ICS generator
- Scrapes event listings and builds an .ics calendar
- Designed to be run on a schedule (e.g., GitHub Actions)
- Output: build/eyeofriyadh.ics
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

BASE_URL = os.environ.get("EOR_BASE_URL", "https://www.eyeofriyadh.com/events/")
MAX_PAGES = int(os.environ.get("EOR_MAX_PAGES", "8"))
TZ = pytz.timezone("Asia/Riyadh")
OUT_DIR = os.environ.get("OUT_DIR", "build")
OUT_FILE = os.path.join(OUT_DIR, "eyeofriyadh.ics")

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("eor")

SELECTORS = {
    "card": ["div.event-card", "div.events-list div.event-item", "div.list div.item"],
    "title": ["h3", "a.title", "h2"],
    "link": ["a", "a.title", "h3 a"],
    "date": ["div.date", "span.date", "p.date"],
    "location": ["div.location", "span.location", "p.location"],
    "detail_date": ["div.event-date", "p:contains('Date')", "li:contains('Date')"],
    "detail_location": ["div.event-location", "p:contains('Location')", "li:contains('Location')"],
    "detail_desc": ["div.event-description", "div#description", "div.description", "article"]
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; EyeOfRiyadhICS/1.0)"
}

def first_text(el):
    if not el:
        return None
    t = el.get_text(" ", strip=True)
    return t or None

def pick(soup, selectors):
    for sel in selectors:
        try:
            found = soup.select(sel)
            if found:
                return found[0]
        except Exception:
            continue
    return None

def normalize(s):
    return re.sub(r"\s+", " ", s or "").strip()

def parse_dt(text):
    if not text:
        return None, None
    text = normalize(text)
    parts = re.split(r"\s?(?:–|-|to)\s?", text, flags=re.I)
    start = None
    end = None
    try:
        default_start = TZ.localize(datetime.now().replace(hour=9, minute=0, second=0, microsecond=0))
        start = dateparser.parse(parts[0], dayfirst=True, default=default_start)
        if start.tzinfo is None:
            start = TZ.localize(start)
        else:
            start = start.astimezone(TZ)
    except Exception:
        start = None
    if len(parts) > 1 and start:
        try:
            default_end = start.replace(hour=18, minute=0)
            end = dateparser.parse(parts[1], dayfirst=True, default=default_end)
            if end.tzinfo is None:
                end = TZ.localize(end)
            else:
                end = end.astimezone(TZ)
        except Exception:
            end = None
    if start and start.hour == 0 and start.minute == 0:
        start = start.replace(hour=9, minute=0)
    if end and end.hour == 0 and end.minute == 0:
        end = end.replace(hour=18, minute=0)
    return start, end

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r

def list_page_urls():
    urls = [BASE_URL]
    patterns = ["?p={i}", "page/{i}/", "?page={i}", "&p={i}", "&page={i}"]
    for i in range(2, MAX_PAGES + 1):
        for pat in patterns:
            urls.append(urljoin(BASE_URL, pat.format(i=i)))
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def scrape_listings():
    events = []
    for url in list_page_urls():
        try:
            res = fetch(url)
        except Exception as e:
            log.debug("skip page %s (%s)", url, e)
            continue
        soup = BeautifulSoup(res.text, "html.parser")
        cards = []
        for sel in SELECTORS["card"]:
            found = soup.select(sel)
            if found:
                cards = found
                break
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
            events.append({"title": title, "link": link, "date_text": date_text, "location": location})
    # dedupe
    dedup = {}
    for e in events:
        key = e.get("link") or e.get("title")
        if key and key not in dedup:
            dedup[key] = e
    return list(dedup.values())

def enrich_from_detail
