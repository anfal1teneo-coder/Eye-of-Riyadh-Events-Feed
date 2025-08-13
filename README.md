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
