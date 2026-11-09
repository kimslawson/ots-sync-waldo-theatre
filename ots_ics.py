#!/usr/bin/env python3
"""Build an .ics calendar from public On The Stage show pages.

Usage:
  python3 ots_ics.py https://www.onthestage.tickets/<org>          # whole season
  python3 ots_ics.py https://www.onthestage.tickets/show/<org>/<id> [more URLs]
                     [--season] [--about] [--durations FILE] [--state FILE]
                     [--out waldo.ics]

Needs Python 3.9+ and nothing else (standard library only).
Use canonical onthestage.tickets URLs, never the our.show short links.

Each performance on a show page is a <div class="event-date" data-eventid="...">.
The eventid becomes the iCalendar UID, so re-running updates events in
subscribers' calendars instead of duplicating them.

Seeding with the org page (onthestage.tickets/<org>) follows every show it
links to, so the feed doesn't depend on any one production's page existing.

Durations: --durations FILE (JSON {"<24-hex show id>": minutes}) wins, then a
run time found on the show's /about page (with --about, e.g. "runs 117
minutes"), then --duration (default 120).

History: show pages keep past performances listed for a while, but whole
productions eventually drop off the season list. With --state FILE, events
seen on earlier runs are kept once they are in the past, so the feed keeps
its history. Future events that vanish are treated as cancelled and dropped
(refusing to drop many at once unless --allow-drops). DTSTAMPs are kept
stable for unchanged events, so a run with no changes writes identical bytes.

Fails closed: if a page can't be fetched, a date can't be parsed, a page
doesn't look like a show page, or no performances are found, nothing is
written, so a scheduled run never overwrites a good feed with a broken one.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://www.onthestage.tickets"
UA = "Mozilla/5.0 (compatible; ots-ics/1.1)"
MIN_DELAY = 1.0  # seconds between requests; never go below this

SHOW_RE = re.compile(r"/show/([A-Za-z0-9_-]+)/([0-9a-f]{24})")
ORG_URL_RE = re.compile(
    r"^https?://(?:www\.)?onthestage\.tickets/([A-Za-z0-9_-]+)/?$")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# e.g. "Sat. Nov 7th 2026, 4:00 pm"
WHEN_RE = re.compile(
    r"([A-Za-z]{3,9})\.?\s+([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+"
    r"(\d{4}),?\s+(\d{1,2}):(\d{2})\s*([ap])\.?m",
    re.I,
)
# Run time stated in an /about blurb, e.g. "The film runs 117 minutes",
# "Running time: 1 hour and 45 minutes", "runs approximately two hours".
# Deliberately conservative: needs a run-time keyword in the same sentence.
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}
RUNTIME_RE = re.compile(
    r"\b(?:runs|running\s+time|run\s*-?\s*time|runtime|length)\b[^.\n]{0,40}?"
    r"(?:(?P<h>\d(?:\.\d+)?|one|two|three|four)\s*(?:hours?|hrs?)\b"
    r"(?:[^.\n\d]{0,12}(?P<hm>\d{1,2})\s*(?:minutes|mins?)\b)?"
    r"|(?P<m>\d{1,3})\s*(?:minutes|mins?)\b)",
    re.I,
)
STAMP_FMT = "%Y%m%dT%H%M%SZ"


def die(msg: str) -> None:
    sys.exit(msg)


def classify(url: str) -> tuple[str, str]:
    """Return ("show", canonical show URL) or ("org", canonical org URL)."""
    u = url.strip()
    if "our.show" in u:
        die(f"Short links aren't supported; use the onthestage.tickets URL: {u}")
    m = SHOW_RE.search(u)
    if m:
        return "show", f"{BASE}/show/{m.group(1)}/{m.group(2)}"
    m = ORG_URL_RE.match(u)
    if m and m.group(1) != "show":
        return "org", f"{BASE}/{m.group(1)}"
    die(f"Not an On The Stage show or org URL: {u}\n"
        f"  expected {BASE}/show/<org>/<24-hex id> or {BASE}/<org>")
    raise AssertionError  # unreachable


_last_request = 0.0


def fetch(url: str, delay: float = MIN_DELAY) -> str:
    """GET a page, at least `delay` seconds after the previous request."""
    global _last_request
    wait = _last_request + max(delay, MIN_DELAY) - time.monotonic()
    if _last_request and wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException) as e:  # URLError, timeouts, resets
        die(f"Failed to fetch {url}: {e}")
    finally:
        _last_request = time.monotonic()
    raise AssertionError  # unreachable


class ShowParser(HTMLParser):
    """Collects (eventid, text) for each div.event-date, plus the page title."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.perfs: list[tuple[str | None, str]] = []
        self.title = ""
        self.og_title = ""
        self.has_calendar = False
        self._cap: str | None = None
        self._eid: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "meta" and a.get("property") == "og:title":
            self.og_title = a.get("content") or ""
        if self._cap is not None:
            return
        if tag == "div" and "home-calendar" in classes:
            self.has_calendar = True
        if tag == "div" and "event-date" in classes:
            self._cap, self._eid, self._buf = "perf", a.get("data-eventid"), []
        elif tag == "h1" and "home-tickets-title" in classes and not self.title:
            self._cap, self._buf = "title", []

    def handle_data(self, data):
        if self._cap:
            self._buf.append(data)

    def handle_endtag(self, tag):
        text = " ".join("".join(self._buf).split())
        if self._cap == "perf" and tag == "div":
            self.perfs.append((self._eid, text))
            self._cap = None
        elif self._cap == "title" and tag == "h1":
            self.title = text
            self._cap = None


class AboutParser(HTMLParser):
    """Text of <div id="production-about"> on a show's /about page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found = False
        self._depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self._depth:
            if tag == "div":
                self._depth += 1
            elif tag in ("p", "br", "li"):
                self._parts.append("\n")
        elif tag == "div" and dict(attrs).get("id") == "production-about":
            self.found = True
            self._depth = 1

    def handle_endtag(self, tag):
        if self._depth and tag == "div":
            self._depth -= 1

    def handle_data(self, data):
        if self._depth:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return "".join(self._parts)


def find_runtime(text: str) -> tuple[int | None, str]:
    """(minutes, matched phrase) or (None, reason) from free text."""
    found: dict[int, str] = {}
    for m in RUNTIME_RE.finditer(text):
        if m.group("m"):
            mins = int(m.group("m"))
        else:
            h = m.group("h").lower()
            hours = NUM_WORDS[h] if h in NUM_WORDS else float(h)
            mins = round(hours * 60) + int(m.group("hm") or 0)
        found.setdefault(mins, " ".join(m.group(0).split()))
    if not found:
        return None, "no run time stated"
    if len(found) > 1:
        return None, f"ambiguous run times {sorted(found)}"
    mins, phrase = next(iter(found.items()))
    if not 15 <= mins <= 300:
        return None, f"implausible run time {mins} min in {phrase!r}"
    return mins, phrase


def find_show_links(html: str, org: str) -> list[str]:
    """Every distinct /show/<org>/<id> linked from the page (nav, etc.)."""
    found: list[str] = []
    for m in re.finditer(r'href="([^"]*)"', html):
        sm = SHOW_RE.search(m.group(1))
        if sm and sm.group(1) == org:
            u = f"{BASE}/show/{org}/{sm.group(2)}"
            if u not in found:
                found.append(u)
    return found


def parse_when(text: str, tz: ZoneInfo) -> datetime:
    m = WHEN_RE.search(text)
    if not m:
        raise ValueError(f"unrecognised date format: {text!r}")
    wd, mon, day, year, hh, mm, ap = m.groups()
    month = MONTHS.get(mon[:3].lower())
    if month is None:
        raise ValueError(f"unrecognised month in: {text!r}")
    hour = int(hh) % 12 + (12 if ap.lower() == "p" else 0)
    dt = datetime(int(year), month, int(day), hour, int(mm), tzinfo=tz)
    if WEEKDAYS[dt.weekday()] != wd[:3].lower():
        raise ValueError(
            f"weekday mismatch in {text!r} (that date is a {WEEKDAYS[dt.weekday()]})")
    return dt


def load_durations(path: str) -> dict[str, int]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        die(f"Can't read --durations {path}: {e}")
    if not isinstance(raw, dict):
        die(f"--durations {path}: expected a JSON object of show id -> minutes")
    out: dict[str, int] = {}
    for k, v in raw.items():
        if k.startswith("_"):
            continue  # "_comment" keys etc.
        if not re.fullmatch(r"[0-9a-f]{24}", k) or not isinstance(v, int) \
                or isinstance(v, bool) or not 1 <= v <= 600:
            die(f"--durations {path}: bad entry {k!r}: {v!r} "
                "(want 24-hex show id -> minutes 1..600)")
        out[k] = v
    return out


# ---- history state ----------------------------------------------------------

def to_stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(STAMP_FMT)


def from_stamp(s: str) -> datetime:
    return datetime.strptime(s, STAMP_FMT).replace(tzinfo=timezone.utc)


def load_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            raise ValueError(f"unsupported version {data.get('version')!r}")
        events = {}
        for eid, e in data["events"].items():
            events[eid] = dict(e, id=eid, start=from_stamp(e["start"]),
                               end=from_stamp(e["end"]))
        return events
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        die(f"Can't read --state {path}: {e}")
    raise AssertionError  # unreachable


def dump_state(events: list[dict]) -> bytes:
    out = {}
    for e in events:
        out[e["id"]] = {k: e[k] for k in ("show", "title", "location", "url", "stamp")}
        out[e["id"]].update(start=to_stamp(e["start"]), end=to_stamp(e["end"]))
    return (json.dumps({"version": 1, "events": out}, indent=1, sort_keys=True,
                       ensure_ascii=False) + "\n").encode("utf-8")


def signature(e: dict) -> tuple:
    return (e["title"], to_stamp(e["start"]), to_stamp(e["end"]),
            e["location"], e["url"])


def merge_history(events: dict[str, dict], old: dict[str, dict], now: datetime,
                  keep_days: int, allow_drops: bool) -> tuple[int, list[dict]]:
    """Fold earlier runs' events into `events` in place.

    Returns (number of past events kept from history, future events dropped).
    """
    now_stamp = to_stamp(now)
    for e in events.values():
        o = old.get(e["id"])
        e["stamp"] = o["stamp"] if o and signature(o) == signature(e) else now_stamp
    kept, dropped = 0, []
    horizon = now - timedelta(days=keep_days)
    for eid, o in old.items():
        if eid in events:
            continue
        if o["start"] < now:
            if o["start"] >= horizon:
                events[eid] = o
                kept += 1
        else:
            dropped.append(o)
    old_future = sum(1 for o in old.values() if o["start"] >= now)
    if len(dropped) > 3 and len(dropped) * 2 > old_future and not allow_drops:
        for o in dropped:
            print(f"error: would drop {o['title']} {o['start']:%Y-%m-%d %H:%M}Z",
                  file=sys.stderr)
        die(f"{len(dropped)} of {old_future} upcoming events vanished at once; "
            "refusing (page change?). Re-run with --allow-drops if real. "
            "Nothing written.")
    return kept, dropped


# ---- iCalendar output (RFC 5545) -------------------------------------------

def esc(s: str) -> str:
    return (s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
             .replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Fold to 75 octets per line without splitting multi-byte characters."""
    out: list[str] = []
    cur, cur_bytes = "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if cur_bytes + n > 75:
            out.append(cur)
            cur, cur_bytes = " ", 1
        cur += ch
        cur_bytes += n
    out.append(cur)
    return "\r\n".join(out)


def build_ics(events: list[dict], cal_name: str, tzname: str, ttl_hours: int) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ots-ics//On The Stage show pages//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(cal_name)}",
        f"X-WR-TIMEZONE:{tzname}",
        f"X-PUBLISHED-TTL:PT{ttl_hours}H",
        f"REFRESH-INTERVAL;VALUE=DURATION:PT{ttl_hours}H",
    ]
    for e in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['id']}@onthestage.tickets",
            f"DTSTAMP:{e['stamp']}",
            f"DTSTART:{to_stamp(e['start'])}",
            f"DTEND:{to_stamp(e['end'])}",
            f"SUMMARY:{esc(e['title'])}",
            f"LOCATION:{esc(e['location'])}",
            f"URL:{e['url']}",
            f"DESCRIPTION:{esc('Tickets: ' + e['url'])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def write_atomic(path: Path, data: bytes) -> None:
    """Never leave a half-written file where a web server might serve it."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ---- main -------------------------------------------------------------------

def main(argv: list[str] | None = None, now: datetime | None = None) -> None:
    """`now` (UTC) is injectable for tests; defaults to the current time."""
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("urls", nargs="+",
                   help="org URL (onthestage.tickets/<org>) or show page URL(s)")
    p.add_argument("--season", action="store_true",
                   help="also follow every show linked in the page nav (same org); "
                        "implied for an org URL")
    p.add_argument("--out", default="waldo.ics")
    p.add_argument("--name", default="The Waldo Theatre", help="calendar name")
    p.add_argument("--location",
                   default="The Waldo Theatre, 916 Main Street, Waldoboro, ME 04572")
    p.add_argument("--tz", default="America/New_York")
    p.add_argument("--duration", type=int, default=120,
                   help="default event length in minutes (pages show no end time)")
    p.add_argument("--durations", metavar="FILE",
                   help='JSON {"<show id>": minutes} per-production overrides')
    p.add_argument("--about", action="store_true",
                   help="fetch each show's /about page and use a stated run time "
                        "(one extra request per show)")
    p.add_argument("--state", metavar="FILE",
                   help="JSON history file: keep past events and stable DTSTAMPs")
    p.add_argument("--keep-days", type=int, default=400,
                   help="with --state, keep past events this many days (default 400)")
    p.add_argument("--allow-drops", action="store_true",
                   help="with --state, allow many upcoming events to vanish at once")
    p.add_argument("--ttl-hours", type=int, default=6,
                   help="refresh interval hint for subscribing clients")
    p.add_argument("--delay", type=float, default=MIN_DELAY,
                   help=f"seconds between requests (minimum {MIN_DELAY})")
    p.add_argument("--html",
                   help="parse this saved show page instead of fetching (testing)")
    args = p.parse_args(argv)
    if args.html and (args.season or args.about or len(args.urls) != 1):
        p.error("--html takes exactly one show URL and can't be combined with "
                "--season or --about")
    if args.delay < MIN_DELAY:
        p.error(f"--delay must be at least {MIN_DELAY}")

    tz = ZoneInfo(args.tz)
    durations = load_durations(args.durations) if args.durations else {}
    seeds = [classify(u) for u in args.urls]
    if args.html and seeds[0][0] != "show":
        p.error("--html needs a show URL")

    queue: list[str] = []
    seen: set[str] = set()
    events: dict[str, dict] = {}
    problems: list[str] = []
    warnings: list[str] = []
    pages = 0

    def enqueue(links: list[str]) -> None:
        for link in links:
            if link not in seen and link not in queue:
                queue.append(link)

    for kind, url in seeds:
        if kind == "org":
            html = fetch(url, args.delay)
            pages += 1
            org = ORG_URL_RE.match(url).group(1)
            links = find_show_links(html, org)
            print(f"  {len(links):2d} shows linked from {url}")
            if not links:
                problems.append(f"no show links found on org page {url}")
            enqueue(links)
        else:
            enqueue([url])

    while queue:
        url = queue.pop(0)
        seen.add(url)
        org, show_id = SHOW_RE.search(url).groups()
        if args.html:
            html = Path(args.html).read_text(encoding="utf-8", errors="replace")
        else:
            html = fetch(url, args.delay)
        pages += 1

        parser = ShowParser()
        parser.feed(html)
        title = parser.title
        if not title and parser.og_title:
            title = re.sub(r"^.*?\bPresents:\s*", "", parser.og_title, count=1,
                           flags=re.I).strip()
        title = title or "Performance"

        minutes, source = args.duration, "default"
        if show_id in durations:
            minutes, source = durations[show_id], "--durations"
        elif args.about:
            ap = AboutParser()
            ap.feed(fetch(f"{url}/about", args.delay))
            pages += 1
            if not ap.found:
                problems.append(f"{title}: no #production-about on {url}/about")
            else:
                found, why = find_runtime(ap.text)
                if found:
                    minutes, source = found, f"about: {why!r}"
                elif why != "no run time stated":
                    warnings.append(f"{title}: {why}; using {minutes} min")

        added = 0
        for eid, text in parser.perfs:
            if not eid or eid in events:
                continue  # page may repeat its date list (mobile/desktop copies)
            try:
                start = parse_when(text, tz)
            except ValueError as e:
                problems.append(f"{title}: {e}")
                continue
            events[eid] = {
                "id": eid,
                "show": show_id,
                "title": title,
                "start": start,
                "end": start + timedelta(minutes=minutes),
                "location": args.location,
                "url": f"{url}/tickets",
            }
            added += 1
        print(f"  {added:2d} performances  {minutes:3d} min ({source})  {title}")
        if not parser.perfs:
            if parser.has_calendar and parser.title:
                warnings.append(f"{title}: calendar is empty at {url}; skipped")
            else:
                problems.append(f"{title}: no div.home-calendar / div.event-date at "
                                f"{url} (page layout changed?)")

        if args.season:
            enqueue(find_show_links(html, org))

    for msg in warnings:
        print(f"warning: {msg}", file=sys.stderr)
    if problems or not events:
        for msg in problems:
            print(f"error: {msg}", file=sys.stderr)
        die("Nothing written (see errors above)." if problems
            else "No performances found; nothing written.")

    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    state_path = Path(args.state) if args.state else None
    if state_path:
        kept, dropped = merge_history(events, load_state(state_path), now,
                                      args.keep_days, args.allow_drops)
        for o in dropped:
            print(f"  dropped (no longer listed): {o['title']} "
                  f"{o['start'].astimezone(tz):%a %b %d %Y %I:%M %p}")
        if kept:
            print(f"  kept {kept} past event(s) from history")
    else:
        for e in events.values():
            e["stamp"] = to_stamp(now)

    ordered = sorted(events.values(), key=lambda e: (e["start"], e["id"]))
    ics = build_ics(ordered, args.name, args.tz, args.ttl_hours).encode("utf-8")
    out = Path(args.out)
    changed = not out.exists() or out.read_bytes() != ics
    write_atomic(out, ics)
    if state_path:
        write_atomic(state_path, dump_state(ordered))
    print(f"Wrote {len(ordered)} events from {pages} page(s) to {args.out}"
          + ("" if changed else " (unchanged)"))


if __name__ == "__main__":
    main()
