# Roadmap

## Next: move the repo into a GitHub organization

**Why:** the feed is effectively the theatre's. In an organization, other
theatre people can maintain it, and GitHub can route this repo's
notifications (including alerts) to a work email address. GitHub only routes
notifications per organization, not per repo.

**The catch:** the public feed URL changes from
`https://kimslawson.github.io/ots-sync-waldo-theatre/waldo.ics` to
`https://<org>.github.io/ots-sync-waldo-theatre/waldo.ics`, and GitHub does
**not** redirect the old Pages URL. Anyone subscribed to the old URL
silently stops getting updates, so do this early, while there are few
subscribers. A custom domain (step 1) makes this the last time the URL
changes.

### Steps

1. **Optional but recommended: pick a permanent URL.** For example
   `calendar.thewaldotheatre.org`. In the theatre's DNS (the website is on
   Wix, which supports this), add a CNAME record `calendar` → `<org>.github.io`.
   After the transfer, set it in the repo under **Settings > Pages > Custom
   domain** and tick **Enforce HTTPS**. Future moves then never change the
   URL subscribers use.
2. **Create the organization** (free plan) on GitHub.
3. **Add and verify the work email** on your personal GitHub account
   (**Settings > Emails**). Routing can only use a verified address.
4. **Transfer the repo:** **Settings > General > Danger Zone > Transfer
   ownership** → the organization.
5. **Check the moved repo:**
   - **Settings > Pages**: source is still `main` / `/docs` (plus the custom
     domain, if any).
   - **Settings > Actions > General**: Actions allowed, and organization
     policy doesn't block the workflow's `contents: write` / `issues: write`.
   - **Actions > Update calendar feed > Run workflow**: a normal run succeeds.
6. **Route notifications:** personal **Settings > Notifications > Custom
   routing** → the organization → the work email.
7. **Test the alert path:** run the workflow with **Fail on purpose to test
   the alert email** ticked; confirm the email arrives at the work address;
   then run normally to close the issue.
8. **Retire the Gmail forwarding filter.** Its search
   `list:ots-sync-waldo-theatre.kimslawson.github.com` stops matching after
   the move. Delete it once step 7 works; if you'd rather keep forwarding,
   change it to `list:ots-sync-waldo-theatre.<org>.github.com`.
9. **Update this repo:**
   - README: the subscribe URL.
   - Workflow `ALERT_MENTION`: keep a person's username. An organization
     can't be @-mentioned; a team can (`<org>/<team>`).
10. **Update local clones:**
    `git remote set-url <remote> https://github.com/<org>/ots-sync-waldo-theatre.git`
    (GitHub redirects the old git URL, but don't rely on it).
11. **Update anything that links to the old feed URL** (theatre website,
    newsletters, posts).

Unchanged by the move: GitHub's own "scheduled workflow failed" emails go to
whoever last edited the `cron:` line in the workflow, which stays the same
person until someone else edits it.

## Later / ideas

- When the first cancelled or sold-out performance shows up, see how
  On The Stage marks it. The structure check will alert if it adds text or
  markup to the date line. Then decide whether the feed should show it
  (e.g. "SOLD OUT" in the title, or STATUS:CANCELLED).
- Add SEQUENCE / LAST-MODIFIED to events, for people who *import* the file
  once instead of subscribing (some importers ignore changes without them).
