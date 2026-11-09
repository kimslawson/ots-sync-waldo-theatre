"""Offline tests for ots_ics.py.  Run:  python3 -m unittest discover -s tests

Pages are served from tests/fixtures (live pages fetched 2026-11-09 with
prose, scripts and images stripped) instead of the network, and the clock is
pinned to that date. The network code is tested against a local HTTP server.
"""
import contextlib
import io
import json
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import ots_ics  # noqa: E402

FIX = HERE / "fixtures"
BASELINE = HERE.parent / "structure.json"
ORG = "https://www.onthestage.tickets/the-waldo-theatre"
SHOW = "https://www.onthestage.tickets/show/the-waldo-theatre/"
NOW = datetime(2026, 11, 9, 21, 0, tzinfo=timezone.utc)

ADDITION = "6abc208819e3f437fa92556d"   # 6 performances Nov 7-22, layout "visible-xs"
PUSS = "6a9ab9b7f708187865f44851"       # Nov 6 (past at NOW)
MUPPET = "6ad7c0c2cc6d474b796a4f3d"     # Dec 13
WALTZ = "6abd41e844487bdaf0511966"      # about: "runs 117 minutes"
WHALE = "6abd429c51ee696532237eab"      # about: "runs 81 minutes"
OKEEFFE = "6abd40b044487bdaf050e951"    # Sun. Nov 15th 2026, 2:00 pm
OKEEFFE_EID = re.compile(r'data-eventid="([0-9a-f]{24})" class="event-date">Sun\. Nov 15th')


def unlink(html, *id_patterns):
    """Remove links to the given show ids (regex) from a page."""
    for p in id_patterns:
        html = re.sub(r'href="/show/the-waldo-theatre/' + p + r'[^"]*"', 'href="#"', html)
    return html


def parse_ics(path):
    # bytes, not read_text(): universal newlines would hide the CRLF folding
    text = Path(path).read_bytes().decode("utf-8").replace("\r\n ", "")
    unesc = lambda v: re.sub(r"\\([\\;,nN])",
                             lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)
    out = {}
    for block in text.split("BEGIN:VEVENT")[1:]:
        props = {k: unesc(v) for k, v in re.findall(r"^([A-Z-]+):(.*?)\r$", block, re.M)}
        out[props["UID"].split("@")[0]] = props
    return out


class Base(unittest.TestCase):
    """Runs main() with self.get as the fetcher (fixtures + overrides)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.override = {}
        self.fetched = []

    def get(self, url):
        self.fetched.append(url)
        if url in self.override:
            return self.override[url]
        if url == ORG:
            return (FIX / "org.html").read_text(encoding="utf-8")
        m = re.fullmatch(re.escape(SHOW) + r"([0-9a-f]{24})(/about)?", url)
        if not m:
            raise AssertionError(f"unexpected fetch {url}")
        name = ("about_" if m.group(2) else "show_") + m.group(1) + ".html"
        return (FIX / name).read_text(encoding="utf-8")

    def page(self, kind, show_id):
        return (FIX / f"{kind}_{show_id}.html").read_text(encoding="utf-8")

    def run_main(self, *argv, now=NOW):
        """-> (exit code, stdout, stderr); exit code 0 on success."""
        self.fetched.clear()
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                ots_ics.main(list(argv), now=now, fetcher=self)
            except SystemExit as e:
                code = e.code if e.code is not None else 0
        return code, out.getvalue(), err.getvalue()

    def assertOk(self, result):
        code, out, err = result
        self.assertEqual(code, 0, f"stdout:\n{out}\nstderr:\n{err}")
        return result

    def assertFailsClosed(self, result, path, needle=None):
        code, out, err = result
        self.assertNotEqual(code, 0, out)
        self.assertFalse(Path(path).exists(), "file written despite failure")
        if needle:
            self.assertIn(needle, f"{code}\n{err}")


# ---- pure functions ---------------------------------------------------------

class TestParsing(unittest.TestCase):
    def test_runtime_phrases(self):
        cases = {
            "The film runs 117 minutes and is rated PG.": 117,
            "In the Whale runs 81 minutes.": 81,
            "Running time: 1 hour and 45 minutes.": 105,
            "The show runs approximately two hours with one intermission.": 120,
            "Run time 2 hrs 10 mins": 130,
            "Runtime: 95 min.": 95,
            "runs 2.5 hours": 150,
            "Doors and reception at 5:30pm.": None,
            "Join us for 90 minutes of fun.": None,          # no run-time keyword
            "Act one runs 50 minutes. Act two runs 40 minutes.": None,  # ambiguous
            "The festival runs 600 minutes.": None,          # implausible
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(ots_ics.find_runtime(text)[0], want)

    def test_about_parser_is_scoped(self):
        p = ots_ics.AboutParser()
        p.feed('<div id="production-about"><p>Fun.</p><div><p>Runs 90 minutes.</p></div></div>'
               '<div id="department-about"><p>Our gala runs 300 minutes.</p></div>')
        self.assertTrue(p.found)
        self.assertEqual(ots_ics.find_runtime(p.text)[0], 90)

    def test_parse_when_and_weekday_check(self):
        tz = ots_ics.ZoneInfo("America/New_York")
        dt = ots_ics.parse_when("Sat. Nov 7th 2026, 4:00 pm", tz)
        self.assertEqual(ots_ics.to_stamp(dt), "20261107T210000Z")   # EST
        dt = ots_ics.parse_when("Sat. Jun 6th 2026, 12:30 am", tz)
        self.assertEqual(ots_ics.to_stamp(dt), "20260606T043000Z")   # EDT, 12 am
        with self.assertRaises(ValueError):
            ots_ics.parse_when("Mon. Nov 7th 2026, 4:00 pm", tz)

    def test_date_shape(self):
        self.assertEqual(ots_ics.date_shape("Sun. Nov 22nd 2026,  4:00 pm"),
                         "A. A 9A 9, 9:9 A")

    def test_fold_is_75_octets_and_utf8_safe(self):
        line = "SUMMARY:" + "Valentease! A Very Valentine’s Burlesque Show " * 4
        folded = ots_ics.fold(line)
        for part in folded.split("\r\n"):
            self.assertLessEqual(len(part.encode("utf-8")), 75)
        self.assertEqual(folded.replace("\r\n ", ""), line)

    def test_url_handling(self):
        self.assertEqual(ots_ics.classify(ORG + "/"), ("org", ORG))
        self.assertEqual(ots_ics.classify(SHOW + ADDITION + "/tickets"),
                         ("show", SHOW + ADDITION))
        for bad in ["https://our.show/aaoo", "https://www.onthestage.tickets/show",
                    "https://example.com/x"]:
            with self.subTest(url=bad), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                ots_ics.classify(bad)


# ---- network ------------------------------------------------------------------

PAGE = b"<!DOCTYPE html><html><body>ok</body></html>"


class LocalServer:
    """Scripted HTTP server: routes[path] = list of responders, used in order
    (the last one repeats). hits[path] counts requests."""

    def __init__(self):
        self.routes, self.hits = {}, {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits[self.path] = outer.hits.get(self.path, 0) + 1
                seq = outer.routes.get(self.path) or [respond(404, b"")]
                seq[min(outer.hits[self.path], len(seq)) - 1](self)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.httpd.handle_error = lambda *a: None   # clients that hung up early
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def respond(status=200, body=PAGE, ctype="text/html; charset=utf-8", headers=None,
            delay=0.0, length=None):
    def go(h):
        if delay:
            time.sleep(delay)
        h.send_response(status)
        if ctype:
            h.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.send_header("Content-Length", str(len(body) if length is None else length))
        h.end_headers()
        h.wfile.write(body)
    return go


class FakeClock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.t += s


class TestFetcher(unittest.TestCase):
    def setUp(self):
        self.srv = LocalServer()
        self.addCleanup(self.srv.close)
        self.clock = FakeClock()

    def fetcher(self, **kw):
        kw.setdefault("delay", 1.0)
        kw.setdefault("retry_wait", 5.0)
        kw.setdefault("timeout", 5.0)
        return ots_ics.Fetcher(clock=self.clock.now, sleep=self.clock.sleep, **kw)

    def route(self, path, *responders):
        self.srv.routes[path] = list(responders)
        return self.srv.base + path

    def test_ok_and_spacing_between_requests(self):
        url = self.route("/a", respond())
        f = self.fetcher()
        for _ in range(3):
            self.assertIn("ok", f.get(url))
        self.assertEqual(self.clock.sleeps, [1.0, 1.0])   # never closer than --delay
        self.assertEqual(self.srv.hits["/a"], 3)

    def test_charset_from_header(self):
        url = self.route("/latin", respond(body="Café</html>".encode("latin-1"),
                                           ctype="text/html; charset=iso-8859-1"))
        self.assertIn("Café", self.fetcher().get(url))

    def test_redirect_is_refused_not_followed(self):
        self.route("/show/x", respond(302, b"", ctype="text/plain",
                                      headers={"Location": "/elsewhere"}))
        self.route("/elsewhere", respond())
        with self.assertRaisesRegex(ots_ics.FetchError, "redirected.*/elsewhere"):
            self.fetcher().get(self.srv.base + "/show/x")
        self.assertNotIn("/elsewhere", self.srv.hits)
        self.assertEqual(self.srv.hits["/show/x"], 1)

    def test_404_is_not_retried(self):
        url = self.route("/gone", respond(404, b"nope"))
        with self.assertRaisesRegex(ots_ics.FetchError, "HTTP 404"):
            self.fetcher().get(url)
        self.assertEqual(self.srv.hits["/gone"], 1)

    def test_500_retried_once_then_succeeds(self):
        url = self.route("/flaky", respond(500, b"err"), respond())
        self.assertIn("ok", self.fetcher().get(url))
        self.assertEqual(self.srv.hits["/flaky"], 2)
        self.assertEqual(self.clock.sleeps, [5.0])

    def test_500_twice_gives_up(self):
        url = self.route("/down", respond(503, b"err"))
        with self.assertRaisesRegex(ots_ics.FetchError, "gave up after 2 attempts"):
            self.fetcher().get(url)
        self.assertEqual(self.srv.hits["/down"], 2)

    def test_429_honours_short_retry_after(self):
        url = self.route("/slowdown", respond(429, b"", headers={"Retry-After": "3"}),
                         respond())
        self.fetcher().get(url)
        self.assertEqual(self.clock.sleeps, [3.0])

    def test_429_with_long_retry_after_fails_without_retry(self):
        url = self.route("/later", respond(429, b"", headers={"Retry-After": "3600"}))
        with self.assertRaisesRegex(ots_ics.FetchError, "wait 3600"):
            self.fetcher().get(url)
        self.assertEqual(self.srv.hits["/later"], 1)

    def test_retry_wait_never_below_delay(self):
        url = self.route("/flaky", respond(500, b""), respond())
        self.fetcher(retry_wait=0.0, delay=1.0).get(url)
        self.assertEqual(self.clock.sleeps, [1.0])

    def test_timeout_is_retried_then_fails(self):
        url = self.route("/hang", respond(delay=1.0))
        f = ots_ics.Fetcher(delay=0, retry_wait=0, timeout=0.2, retries=1)
        t0 = time.monotonic()
        with self.assertRaisesRegex(ots_ics.FetchError, "gave up after 2 attempts"):
            f.get(url)
        self.assertLess(time.monotonic() - t0, 0.9)   # didn't wait for the server
        self.assertEqual(f.requests, 2)

    def test_connection_refused(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(ots_ics.FetchError):
            self.fetcher().get(f"http://127.0.0.1:{port}/")

    def test_deadline(self):
        url = self.route("/down", respond(500, b""))
        with self.assertRaisesRegex(ots_ics.FetchError, "time budget"):
            self.fetcher(retry_wait=40.0, deadline=30.0).get(url)
        self.assertEqual(self.srv.hits["/down"], 1)

    def test_rejects_non_html(self):
        url = self.route("/json", respond(body=b"{}", ctype="application/json"))
        with self.assertRaisesRegex(ots_ics.FetchError, "expected text/html"):
            self.fetcher().get(url)

    def test_rejects_oversized(self):
        url = self.route("/big", respond(body=b"x" * 500 + b"</html>"))
        with self.assertRaisesRegex(ots_ics.FetchError, "over 100 bytes"):
            self.fetcher(max_bytes=100).get(url)

    def test_rejects_page_without_closing_html(self):
        url = self.route("/cut", respond(body=b"<html><body>half a page"))
        with self.assertRaisesRegex(ots_ics.FetchError, "truncated"):
            self.fetcher().get(url)

    def test_short_body_is_retried(self):
        # server promises more bytes than it sends, then hangs up
        url = self.route("/short", respond(body=PAGE[:20], length=len(PAGE)), respond())
        self.assertIn("ok", self.fetcher().get(url))
        self.assertEqual(self.srv.hits["/short"], 2)

    def test_cli_limits(self):
        for bad in (["--timeout", "0"], ["--retries", "9"], ["--deadline", "5"],
                    ["--delay", "0.5"], ["--accept-structure"]):
            with self.subTest(args=bad), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                ots_ics.main([ORG, *bad])


# ---- crawling and output ------------------------------------------------------

class TestCrawl(Base):
    def test_single_show_saved_html(self):
        out = self.tmp / "a.ics"
        self.assertOk(self.run_main("--html", str(FIX / f"show_{ADDITION}.html"),
                                    SHOW + ADDITION, "--out", str(out)))
        ev = parse_ics(out)
        self.assertEqual(len(ev), 6)
        self.assertEqual(self.fetched, [])
        self.assertEqual(ev["6abd8ec5580206fcf2b9f2d2"]["DTSTART"], "20261107T210000Z")

    def test_season_from_show_nav(self):
        out = self.tmp / "s.ics"
        self.assertOk(self.run_main(SHOW + ADDITION, "--season", "--out", str(out)))
        self.assertEqual(len(parse_ics(out)), 22)
        self.assertEqual(len(self.fetched), 16)

    def test_org_seed_with_about(self):
        out = self.tmp / "o.ics"
        self.assertOk(self.run_main(ORG, "--about", "--out", str(out)))
        ev = parse_ics(out)
        self.assertEqual(len(ev), 22)
        self.assertEqual(len(self.fetched), 1 + 16 + 16)
        by_title = {e["SUMMARY"]: e for e in ev.values()}
        waltz = by_title["Martin Scorsese's The Last Waltz"]
        self.assertEqual((waltz["DTSTART"], waltz["DTEND"]),
                         ("20261127T233000Z", "20261128T012700Z"))     # 117 min
        whale = by_title["In the Whale: The Greatest Fish Story Ever Told"]
        self.assertEqual(whale["DTEND"], "20261203T005100Z")            # 81 min
        anniv = by_title["The Waldo's 90th Anniversary Show"]
        self.assertEqual(anniv["DTEND"], "20261210T013000Z")            # default 120
        text = out.read_bytes().decode("utf-8")
        self.assertIn("X-PUBLISHED-TTL:PT6H", text)
        self.assertIn("X-WR-TIMEZONE:America/New_York", text)

    def test_ticket_link_appears_once_per_event(self):
        # Apple Calendar shows URL and DESCRIPTION both; we only emit DESCRIPTION.
        out = self.tmp / "o.ics"
        self.assertOk(self.run_main(ORG, "--out", str(out)))
        for e in parse_ics(out).values():
            self.assertNotIn("URL", e)
            self.assertEqual(e["DESCRIPTION"].count("https://"), 1)
            self.assertTrue(e["DESCRIPTION"].endswith("/tickets"))

    def test_durations_file_wins(self):
        d = self.tmp / "d.json"
        d.write_text(json.dumps({"_comment": "x", WALTZ: 150, ADDITION: 105}))
        out = self.tmp / "d.ics"
        self.assertOk(self.run_main(ORG, "--about", "--durations", str(d), "--out", str(out)))
        ev = parse_ics(out)
        self.assertEqual(ev["6abd8ec5580206fcf2b9f2d2"]["DTEND"], "20261107T224500Z")
        self.assertNotIn(SHOW + WALTZ + "/about", self.fetched)

    def test_bad_durations_file(self):
        d = self.tmp / "d.json"
        for bad in ['{"abc": 90}', '{"%s": "90"}' % WALTZ, '{"%s": 0}' % WALTZ, "[1]", "nope"]:
            with self.subTest(content=bad):
                d.write_text(bad)
                out = self.tmp / "x.ics"
                self.assertFailsClosed(self.run_main(ORG, "--durations", str(d),
                                                     "--out", str(out)), out)


class TestFailClosed(Base):
    def okeeffe(self, old, new):
        page = self.page("show", OKEEFFE)
        self.assertIn(old, page)
        self.override[SHOW + OKEEFFE] = page.replace(old, new)

    def test_empty_calendar_is_a_warning(self):
        self.override[SHOW + OKEEFFE] = re.sub(
            r'<div data-eventid="[0-9a-f]+" class="event-date">[^<]*</div>', "",
            self.page("show", OKEEFFE))
        out = self.tmp / "e.ics"
        code, _, err = self.assertOk(self.run_main(ORG, "--out", str(out)))
        self.assertIn("calendar is empty", err)
        self.assertEqual(len(parse_ics(out)), 21)

    def test_layout_change_fails_closed(self):
        self.override[SHOW + OKEEFFE] = (self.page("show", OKEEFFE)
                                         .replace("home-calendar", "x").replace("event-date", "y"))
        out = self.tmp / "b.ics"
        self.assertFailsClosed(self.run_main(ORG, "--out", str(out)), out, "layout changed")

    def test_bad_date_fails_closed(self):
        self.okeeffe("Sun. Nov 15th 2026", "Mon. Nov 15th 2026")
        out = self.tmp / "b.ics"
        self.assertFailsClosed(self.run_main(ORG, "--out", str(out)), out, "weekday mismatch")

    def test_implausible_year_fails_closed(self):
        y = next(y for y in range(2029, 2100) if date(y, 11, 15).weekday() == 6)
        self.okeeffe("Sun. Nov 15th 2026", f"Sun. Nov 15th {y}")
        out = self.tmp / "b.ics"
        self.assertFailsClosed(self.run_main(ORG, "--out", str(out)), out, "implausible year")

    def test_event_without_id_fails_closed(self):
        eid = OKEEFFE_EID.search(self.page("show", OKEEFFE)).group(1)
        self.okeeffe(f'data-eventid="{eid}" ', "")
        out = self.tmp / "b.ics"
        self.assertFailsClosed(self.run_main(ORG, "--out", str(out)), out,
                               "without data-eventid")

    def test_same_event_id_with_different_dates_fails_closed(self):
        eid = OKEEFFE_EID.search(self.page("show", OKEEFFE)).group(1)
        self.okeeffe(eid, "6abd8ec5580206fcf2b9f2d2")   # An Addition of One's Nov 7
        out = self.tmp / "b.ics"
        self.assertFailsClosed(self.run_main(ORG, "--out", str(out)), out, "listed as both")

    def test_missing_title_fails_closed(self):
        page = self.page("show", OKEEFFE)
        page = re.sub(r'<h1 class="home-tickets-title[^>]*>[^<]*</h1>', "", page)
        page = re.sub(r'<meta property="og:title"[^>]*>', "", page)
        self.override[SHOW + OKEEFFE] = page
        out = self.tmp / "b.ics"
        self.assertFailsClosed(self.run_main(ORG, "--out", str(out)), out, "no title")

    def test_org_page_without_links_fails_closed(self):
        self.override[ORG] = "<html><body>maintenance</body></html>"
        out = self.tmp / "b.ics"
        self.assertFailsClosed(self.run_main(ORG, "--out", str(out)), out)

    def test_missing_about_section_fails_closed(self):
        self.override[SHOW + WHALE + "/about"] = "<html><body>oops</body></html>"
        out = self.tmp / "b.ics"
        self.assertFailsClosed(self.run_main(ORG, "--about", "--out", str(out)), out,
                               "production-about")

    def test_fetch_error_fails_closed(self):
        def boom(url):
            raise ots_ics.FetchError(f"{url} redirected (HTTP 302) to '/x'")
        self.get = boom
        out = self.tmp / "keep.ics"
        out.write_bytes(b"GOOD")
        code, _, _ = self.run_main(ORG, "--out", str(out))
        self.assertIn("redirected", str(code))
        self.assertEqual(out.read_bytes(), b"GOOD")

    def test_existing_feed_untouched_on_failure(self):
        out = self.tmp / "keep.ics"
        out.write_bytes(b"GOOD")
        self.override[ORG] = "<html></html>"
        code, _, _ = self.run_main(ORG, "--out", str(out))
        self.assertNotEqual(code, 0)
        self.assertEqual(out.read_bytes(), b"GOOD")


class TestStructure(Base):
    def setUp(self):
        super().setUp()
        self.base = self.tmp / "structure.json"
        shutil.copy(BASELINE, self.base)
        self.out = self.tmp / "s.ics"

    def run_s(self, *extra):
        return self.run_main(ORG, "--about", "--structure", str(self.base),
                             "--out", str(self.out), *extra)

    def test_fixtures_match_live_baseline(self):
        """The committed baseline (from a live run) describes the fixtures exactly,
        so fixtures and live pages have the same structure."""
        lines = ots_ics.structure_lines((FIX / "org.html").read_text(), "org",
                                        "the-waldo-theatre")
        for f in FIX.glob("show_*.html"):
            lines |= ots_ics.structure_lines(f.read_text(), "show", "the-waldo-theatre")
        for f in FIX.glob("about_*.html"):
            lines |= ots_ics.structure_lines(f.read_text(), "about", "the-waldo-theatre")
        self.assertEqual(sorted(lines), json.loads(BASELINE.read_text())["lines"])

    def test_unchanged_pages_pass(self):
        self.assertOk(self.run_s())

    def test_known_layout_absent_is_fine(self):
        # one page only: most known variants are not seen, which is not an error
        _, out, _ = self.assertOk(self.run_main(
            "--html", str(FIX / f"show_{ADDITION}.html"), SHOW + ADDITION,
            "--structure", str(self.base), "--out", str(self.out)))
        self.assertIn("not seen this run", out)

    def test_extra_text_in_dates_is_flagged(self):
        # "SOLD OUT" still parses as a date, but the date text has a new shape
        self.override[SHOW + OKEEFFE] = self.page("show", OKEEFFE).replace(
            "2:00 pm</div>", "2:00 pm SOLD OUT</div>")
        self.assertFailsClosed(self.run_s(), self.out, "page structure changed")

    def test_markup_inside_dates_is_flagged(self):
        self.override[SHOW + OKEEFFE] = self.page("show", OKEEFFE).replace(
            "2:00 pm</div>", "2:00 pm <span>!</span></div>")
        code, _, err = self.run_s()
        self.assertIn("A. A 9A 9, 9:9 A <span>!", err)

    def test_moved_container_is_flagged(self):
        self.override[SHOW + OKEEFFE] = self.page("show", OKEEFFE).replace(
            'class="home-calendar"', 'class="home-calendar v2"')
        code, _, err = self.run_s()
        self.assertFailsClosed((code, "", err), self.out, "page structure changed")
        self.assertIn("new: show: event element", err)

    def test_new_event_attribute_is_flagged(self):
        eid = OKEEFFE_EID.search(self.page("show", OKEEFFE)).group(1)
        self.override[SHOW + OKEEFFE] = self.page("show", OKEEFFE).replace(
            f'data-eventid="{eid}"', f'data-eventid="{eid}" data-status="cancelled"')
        code, _, err = self.run_s()
        self.assertIn("data-eventid,data-status", err)
        self.assertNotEqual(code, 0)

    def test_accept_adds_to_baseline(self):
        self.override[SHOW + OKEEFFE] = self.page("show", OKEEFFE).replace(
            'class="home-calendar"', 'class="home-calendar v2"')
        before = set(json.loads(self.base.read_text())["lines"])
        _, out, _ = self.assertOk(self.run_s("--accept-structure"))
        after = set(json.loads(self.base.read_text())["lines"])
        self.assertTrue(before < after)          # merged, nothing forgotten
        self.assertOk(self.run_s())              # new layout now known
        self.override.clear()
        self.assertOk(self.run_s())              # old layout still known

    def test_missing_baseline_fails_closed(self):
        self.base.unlink()
        self.assertFailsClosed(self.run_s(), self.out, "no structure baseline")


class TestHistory(Base):
    def setUp(self):
        super().setUp()
        self.state = self.tmp / "state.json"
        self.out = self.tmp / "feed.ics"
        self.assertOk(self.run_main(ORG, "--state", str(self.state), "--out", str(self.out)))
        self.first = self.out.read_bytes()

    def again(self, *extra, now=NOW + timedelta(hours=6)):
        return self.run_main(ORG, "--state", str(self.state), "--out", str(self.out),
                             *extra, now=now)

    def test_unchanged_run_is_byte_identical(self):
        _, out, _ = self.assertOk(self.again())
        self.assertIn("(unchanged)", out)
        self.assertEqual(self.out.read_bytes(), self.first)

    def test_changed_event_gets_new_dtstamp_only(self):
        self.override[SHOW + ADDITION] = self.page("show", ADDITION).replace(
            "Fri. Nov 13th 2026, 7:00 pm", "Fri. Nov 13th 2026, 8:00 pm")
        self.assertOk(self.again())
        new = parse_ics(self.out)
        first = dict(re.findall(r"UID:(\w+)@\S+\r\nDTSTAMP:(\S+)", self.first.decode()))
        self.assertEqual(new["6abd8ec6580206fcf2b9f2e0"]["DTSTART"], "20261114T010000Z")
        self.assertNotEqual(new["6abd8ec6580206fcf2b9f2e0"]["DTSTAMP"],
                            first["6abd8ec6580206fcf2b9f2e0"])
        self.assertEqual(new["6abd8ec6580206fcf2b9f2e7"]["DTSTAMP"],
                         first["6abd8ec6580206fcf2b9f2e7"])

    def test_past_kept_future_dropped(self):
        org = (FIX / "org.html").read_text(encoding="utf-8")
        self.override[ORG] = unlink(org, PUSS, MUPPET)
        self.override[SHOW + ADDITION] = re.sub(
            r'<div data-eventid="6abd8ec5580206fcf2b9f2d2" class="event-date">[^<]*</div>',
            "", self.page("show", ADDITION))
        _, out, _ = self.assertOk(self.again())
        ev = parse_ics(self.out)
        titles = {e["SUMMARY"] for e in ev.values()}
        self.assertIn("Puss N Boots featuring Norah Jones, Sasha Dobson and Catherine Popper",
                      titles)                                   # past, delisted: kept
        self.assertIn("6abd8ec5580206fcf2b9f2d2", ev)            # past perf removed: kept
        self.assertNotIn("The Muppet Christmas Carol", titles)   # future, delisted: dropped
        self.assertIn("dropped (no longer listed)", out)
        self.assertEqual(len(ev), 21)

    def test_history_expires_after_keep_days(self):
        self.override[ORG] = unlink((FIX / "org.html").read_text(encoding="utf-8"), PUSS)
        self.assertOk(self.again("--keep-days", "1", now=NOW + timedelta(days=5)))
        titles = {e["SUMMARY"] for e in parse_ics(self.out).values()}
        self.assertFalse(any(t.startswith("Puss N Boots") for t in titles))

    def test_mass_drop_refused_then_allowed(self):
        self.override[ORG] = unlink((FIX / "org.html").read_text(encoding="utf-8"),
                                    r"(?!%s)[0-9a-f]{24}" % ADDITION)
        before = (self.state.read_bytes(), self.out.read_bytes())
        code, _, _ = self.again()
        self.assertNotEqual(code, 0)
        self.assertIn("refusing", str(code))
        self.assertEqual((self.state.read_bytes(), self.out.read_bytes()), before)
        self.assertOk(self.again("--allow-drops"))

    def test_corrupt_state_fails_closed(self):
        self.state.write_text("{not json")
        before = self.out.read_bytes()
        code, _, _ = self.again()
        self.assertNotEqual(code, 0)
        self.assertEqual(self.out.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
