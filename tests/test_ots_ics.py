"""Offline tests for ots_ics.py.  Run:  python3 -m unittest discover -s tests

Pages are served from tests/fixtures (trimmed copies of the live site taken
2026-11-09) instead of the network, and the clock is pinned to that date.
"""
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import ots_ics  # noqa: E402

FIX = HERE / "fixtures"
ORG = "https://www.onthestage.tickets/the-waldo-theatre"
SHOW = "https://www.onthestage.tickets/show/the-waldo-theatre/"
NOW = datetime(2026, 11, 9, 21, 0, tzinfo=timezone.utc)

ADDITION = "6abc208819e3f437fa92556d"   # 6 performances Nov 7-22
PUSS = "6a9ab9b7f708187865f44851"       # Nov 6 (past at NOW)
MUPPET = "6ad7c0c2cc6d474b796a4f3d"     # Dec 13
WALTZ = "6abd41e844487bdaf0511966"      # about: "runs 117 minutes"
WHALE = "6abd429c51ee696532237eab"      # about: "runs 81 minutes"
OKEEFFE = "6abd40b044487bdaf050e951"


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
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.override = {}
        self.fetched = []
        self._real_fetch = ots_ics.fetch
        ots_ics.fetch = self.fake_fetch

    def tearDown(self):
        ots_ics.fetch = self._real_fetch

    def fake_fetch(self, url, delay=1.0):
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
                ots_ics.main(list(argv), now=now)
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
        text = out.read_text(encoding="utf-8")
        self.assertIn("X-PUBLISHED-TTL:PT6H", text)
        self.assertIn("X-WR-TIMEZONE:America/New_York", text)

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

    def test_delay_floor(self):
        out = self.tmp / "x.ics"
        self.assertFailsClosed(self.run_main(ORG, "--delay", "0.5", "--out", str(out)), out)


class TestFailClosed(Base):
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
        self.override[SHOW + OKEEFFE] = self.page("show", OKEEFFE).replace(
            "Sun. Nov 15th 2026", "Mon. Nov 15th 2026")
        out = self.tmp / "b.ics"
        self.assertFailsClosed(self.run_main(ORG, "--out", str(out)), out, "weekday mismatch")

    def test_org_page_without_links_fails_closed(self):
        self.override[ORG] = "<html><body>maintenance</body></html>"
        out = self.tmp / "b.ics"
        self.assertFailsClosed(self.run_main(ORG, "--out", str(out)), out)

    def test_missing_about_section_fails_closed(self):
        self.override[SHOW + WHALE + "/about"] = "<html><body>oops</body></html>"
        out = self.tmp / "b.ics"
        self.assertFailsClosed(self.run_main(ORG, "--about", "--out", str(out)), out,
                               "production-about")

    def test_existing_feed_untouched_on_failure(self):
        out = self.tmp / "keep.ics"
        out.write_bytes(b"GOOD")
        self.override[ORG] = "<html></html>"
        code, _, _ = self.run_main(ORG, "--out", str(out))
        self.assertNotEqual(code, 0)
        self.assertEqual(out.read_bytes(), b"GOOD")


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
        first = {k: v for k, v in re.findall(r"UID:(\w+)@\S+\r\nDTSTAMP:(\S+)",
                                              self.first.decode())}
        self.assertEqual(new["6abd8ec6580206fcf2b9f2e0"]["DTSTART"], "20261114T010000Z")
        self.assertNotEqual(new["6abd8ec6580206fcf2b9f2e0"]["DTSTAMP"],
                            first["6abd8ec6580206fcf2b9f2e0"])
        self.assertEqual(new["6abd8ec6580206fcf2b9f2e7"]["DTSTAMP"],
                         first["6abd8ec6580206fcf2b9f2e7"])

    def test_past_kept_future_dropped(self):
        org = (FIX / "org.html").read_text(encoding="utf-8")
        self.override[ORG] = unlink(org, PUSS, MUPPET)
        self.override[SHOW + ADDITION] = self.page("show", ADDITION).replace(
            '<div data-eventid="6abd8ec5580206fcf2b9f2d2" class="event-date">'
            'Sat. Nov 7th 2026, 4:00 pm</div>', "")
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
