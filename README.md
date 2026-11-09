# The Waldo Theatre: calendar feed

An iCalendar feed of performances at [The Waldo Theatre](https://www.thewaldotheatre.org/)
in Waldoboro, Maine. It's built from the theatre's public ticketing pages on
On The Stage and refreshed every 6 hours.

**Subscribe:** `https://kimslawson.github.io/ots-sync-waldo-theatre/waldo.ics`

- **Apple Calendar:** File > New Calendar Subscription, then paste the URL.
- **Google Calendar:** Other calendars > + > From URL. Google refreshes on its
  own schedule, often 12–24 hours.
- **Outlook:** Add calendar > Subscribe from web.

Unofficial; not affiliated with The Waldo Theatre or On The Stage. Always check
the ticket link in each event for current details.

## How it works

`ots_ics.py` (Python 3.9+, standard library only) starts from the season page
`https://www.onthestage.tickets/the-waldo-theatre`, visits each show page, and
turns every listed performance into an event. Each performance's On The Stage
id is the event UID, so changes update existing events instead of
duplicating them. It waits at least 1 second between requests.

A GitHub Actions workflow (`.github/workflows/update-feed.yml`) runs the
tests, then the script, and commits `docs/waldo.ics` and `state.json` only
when they changed. GitHub Pages serves `docs/`.

The script fails closed. If a page can't be fetched, a date can't be parsed,
or the page layout looks different, it writes nothing and the last good feed
stays up. A failed scheduled run emails the repository owner.

- **History:** `state.json` remembers past performances after the site stops
  listing them. A future performance that disappears is treated as cancelled
  and dropped. If more than half the upcoming events vanish at once, the run
  stops instead (rerun with `--allow-drops` if that was real).
- **Event length:** the pages show no end times. The script uses a run time
  stated on the show's About page ("runs 117 minutes"), else 120 minutes.
  To override per production, add the show id (from `/show/the-waldo-theatre/<id>`)
  to `durations.json`, e.g. `{"6abd41e844487bdaf0511966": 130}`.

## Running locally

```sh
python3 ots_ics.py https://www.onthestage.tickets/the-waldo-theatre \
    --about --durations durations.json --state state.json --out docs/waldo.ics
python3 -m unittest discover -s tests     # offline; uses tests/fixtures
```
