# The Waldo Theatre: calendar feed

An iCalendar feed of performances at [The Waldo Theatre](https://www.thewaldotheatre.org/)
in Waldoboro, Maine. It's built from the theatre's public ticketing pages on
On The Stage and refreshed every 6 hours.

**Subscribe:** `https://kimslawson.github.io/ots-sync-waldo-theatre/waldo.ics`

- **Apple Calendar:** File > New Calendar Subscription, then paste the URL.
- **Google Calendar:** Other calendars > + > From URL. Google refreshes on its
  own schedule, often 12–24 hours.
- **Outlook:** Add calendar > Subscribe from web.

Unofficial; not affiliated with On The Stage. Always check the ticket link in
each event for current details.

## How it works

`ots_ics.py` (Python 3.9+, standard library only) starts from the season page
`https://www.onthestage.tickets/the-waldo-theatre`, visits each show page and
its About page, and turns every listed performance into an event. Each
performance's On The Stage id is the event UID, so changes update existing
events instead of duplicating them.

A GitHub Actions workflow (`.github/workflows/update-feed.yml`) runs the
tests, then the script, and commits `docs/waldo.ics`, `state.json` and
`structure.json` only when they changed. GitHub Pages serves `docs/`.

- **History:** `state.json` remembers past performances after the site stops
  listing them. A future performance that disappears is treated as cancelled
  and dropped.
- **Event length:** the pages show no end times. The script uses a run time
  stated on the show's About page ("runs 117 minutes"), else 120 minutes.
  To override per production, add the show id (from `/show/the-waldo-theatre/<id>`)
  to `durations.json`, e.g. `{"6abd41e844487bdaf0511966": 130}`.
- **Being polite:** one request at a time, at least 1 second apart (a full
  run is 33 requests, about 40 seconds). On The Stage's robots.txt allows
  these pages.

## Fails closed

If anything looks wrong, the script writes nothing and the last good feed
stays up. That includes:

- a page that can't be fetched, redirects (On The Stage redirects removed
  shows), isn't complete HTML, or is over 5 MB. Timeouts, dropped connections
  and server errors get one retry; the whole run gives up after 10 minutes;
- a date that doesn't parse, has the wrong weekday or an implausible year,
  or a performance without an id;
- **page markup it hasn't seen before**, even if parsing still works.
  `structure.json` lists the known layouts: where dates, titles, links and
  run-time text sit, and the shape of the date text (so a new "SOLD OUT" or
  "Cancelled" next to a date is caught). Shows use more than one layout, so a
  known layout not appearing is fine;
- more than half of the upcoming events vanishing at once.

## When an alert arrives

A failed run opens (or updates) the issue **"Calendar feed update failing"**,
which emails the person named in `ALERT_MENTION` in the workflow. The same
error isn't re-sent every 6 hours, and the issue closes itself after the next
good run. The error text in the issue tells you which case it is:

| Error says | What to do |
|---|---|
| `Failed to fetch`, `HTTP 5xx`, `timed out`, `gave up after 2 attempts` | On The Stage was down or slow. Usually clears on the next run; nothing to do. |
| `redirected ... show removed or URL changed?` | A show linked from the season page is gone. Usually clears on the next run, once the season page catches up. |
| `page structure changed` + `new: ...` lines | Open a couple of show pages and compare them with the feed (the run log lists each show's performance count). If the feed is still right, go to **Actions > Update calendar feed > Run workflow**, tick **Accept the page-structure change**, and run. If the feed would be wrong, the script needs updating. |
| `vanished at once; refusing` | Check the season page. If those shows really were cancelled or removed, run the workflow with **Allow many upcoming events to disappear** ticked. |
| `weekday mismatch`, `unrecognised date format`, `implausible year`, `without data-eventid`, `listed as both` | A date on a show page is malformed or the format changed. Check that show's page. |
| A test failure | Code in this repo is broken; check the latest commit. |

To check that alerts reach you, run the workflow with **Fail on purpose to
test the alert email** ticked. It doesn't contact On The Stage. Then run it
normally to close the issue.

## Running locally

```sh
python3 ots_ics.py https://www.onthestage.tickets/the-waldo-theatre \
    --about --durations durations.json --state state.json \
    --structure structure.json --out docs/waldo.ics
python3 -m unittest discover -s tests     # offline: fixtures + a local test server
```

`python3 ots_ics.py --help` lists every option (timeouts, retries, delay,
history length and so on).
