# Beacon Hill Compliance Tracker v2: Project Proposal

Status: approved; Phase 1 (M1 to M5, M5a) done 2026-11-20. Date: 2026-11-20.
Companion documents: [DESIGN.md](DESIGN.md) (the specification) and
[mockup/](mockup/index.html) (the visual reference).

## 1. Summary

Rebuild the public site as a server-rendered Flask + HTMX application on Postgres, fed
by the new collector/evaluator client through a versioned push contract. The rebuild
fixes the three problems with the current site: a React codebase that is hard to
maintain alone, slow queries, and a database that keeps growing.

The site presents itself as a neutral data provider. Every figure is a detection drawn
from the public record and links to its audit trail, and the copy never grades or
ranks (DESIGN.md 13.1).

The work is split into three phases. Phase 1 alone is enough to replace the current
site.

## 2. What is already settled

| Area | Decision | Where |
|---|---|---|
| Stack | Flask, Jinja, HTMX, Chart.js, psycopg 3, hand-written SQL, no Node build | DESIGN 2 |
| Data contract | Pydantic `beacon_contract` package shared by client and site; per-committee gzip uploads, atomic publish | DESIGN 5 |
| Database | Postgres read models rebuilt at publish; detail retention configurable, default 2 publications | DESIGN 8 |
| Units | Bills vs. bill segments, one row per segment | DESIGN 4.1 |
| Rates | Headline: (compliant + provisional) / total. Committee range: compliant / (compliant + non-compliant) | DESIGN 9.2, 9.4 |
| Auth | Email magic links, roles `user` and `webmaster`, webmaster granted only by CLI | DESIGN 6 |
| Accounts | No import from the old site; new sign-up records email preferences | DESIGN 6.1 |
| Embeds | Public, no key | DESIGN 11 |
| Voice | "Beacon Hill Compliance Tracker", "the Tracker" / "I", detections not judgments | DESIGN 13.1 |
| Changelog | Client's `CHANGELOG.md` (Keep a Changelog, SemVer) pushed through ingest, shown at `/changes`; site changes not listed | DESIGN 5.6 |
| Look | ProPublica-style, Merriweather / Inter / Roboto Mono, status colors validated for color blindness | mockup/ |

## 3. Scope by phase

| Phase | Contents | Outcome |
|---|---|---|
| **1. Public site (MVP)** | Contract and client push, ingest and publish, dashboard, bill pages, summary figures, charts, insights, static pages, contact form, deployment | Replaces the current site. Read-only for visitors |
| **2. Accounts** | Sign-up with email preferences, sign-in, watchlist, alerts, weekly summary, announcements, data downloads | Readers can follow bills and committees and download the data |
| **3. Reuse** | Public JSON API with read keys, embeddable widget | Others can build on the data |

Not in any phase: the old site's saved views (shareable URLs replace them), the old
Updates page (replaced by `/changes`, M5a), and the "privileged" and "admin" roles.

## 4. Milestones

Sizes are relative (S, M, L) and assume one person working part-time. Each milestone
ends with something that can be run and checked.

### Phase 1

| # | Milestone | Size | Done when |
|---|---|---|---|
| M1 | **Done.** **Contract and push.** `beacon_contract` package with validators and the shared roll-up. Client `site_push` reporter and `beacon push` command | M | J19 and J45 bundles built from the client's store pass validation; tests cover every rejection rule in DESIGN 5.3 |
| M2 | **Done.** **Site skeleton and ingest.** App factory, config, connection pool, SQL migrations, ingest endpoints, publish with read-model rebuild, retention, `flask users` / `flask runs` CLI, ingest keys | L | The client pushes J19 and J45 into local Postgres and publishes. Pushing again changes nothing. Rollback restores the previous publication |
| M3 | **Done.** **Dashboard list and bill pages.** Templates ported from the mockup, filters with URL state, keyset pagination, lazy-loaded audit trail, bill permalink | L | Pages match the mockup with real data. Query tests confirm index scans; p95 under 50 ms on a seeded set of 15k segments |
| M4 | **Done.** **Figures, charts, insights.** Snapshots, deltas, pie, funnel, committee range, insight templates | M | Every figure on the page can be reproduced by one SQL query listed in tests. Insight copy passes the 13.1 checklist |
| M5 | **Done.** **Static pages and readiness.** Methodology, About, Press, Privacy, Terms, contact form, request logging, security headers, performance pass | M | Every page in the header and footer is served from real data; deployment and cutover are handled by the owner (section 5) |
| M5a | **Done.** **Changelog.** `v1.Changelog` contract, `PUT /api/ingest/v1/changelog`, `changelog_releases` with every text kept, `/changes` page, footer and Methodology links, 14-day dashboard line for major and minor releases. Client parser and push step | S | `beacon push` sends the changelog before the run; corrections keep the earlier text; a removed or future-dated release is refused |

### Phase 2

| # | Milestone | Size | Done when |
|---|---|---|---|
| M6 | **Done.** **Accounts.** Sign-up with preferences, magic-link sign-in, sessions, watchlist page | M | Sign-up through first sign-in works end to end against a real mail provider; link-scanner GETs do not consume tokens |
| M7 | **Done.** **Alerts and email.** Alert events at publish, daily deadline scan, hourly digests, weekly summary, announcements, one-click unsubscribe | M | A status change in a test publish reaches a watcher's inbox once, and unsubscribe takes effect immediately |
| M8 | **Done.** **Data downloads.** CSV and JSON dumps built after publish, served behind sign-in | S | Downloaded files match the published data and their published sha256 |

### Phase 3

| # | Milestone | Size | Done when |
|---|---|---|---|
| M9 | **Built; owner check pending.** **Public API.** Read keys, `/api/v1` endpoints, rate limits, generated OpenAPI document | M | Endpoints return the same figures as the dashboard for the same filters |
| M10 | **Built; owner check pending.** **Embeds.** Committee and bill widgets, embed builder | S | Widget renders in a third-party page and stays under 20 KB |

## 5. Cutover from the current site (owner-managed; for reference)

1. Build in its own repository, `beacon-hill-tracker-v2`, so the old site keeps running untouched.
2. Deploy v2 to a staging subdomain on Render with its own Postgres.
3. Run a full-coverage client push to staging and compare a sample of committees
   against the client's own HTML audit reports.
4. Point beaconhilltracker.org at v2. Keep the old services stopped but not deleted for
   two weeks.
5. Archive the old repository. Archive the old databases with a dump before deleting the old services.

If cutover happens at the end of Phase 1, "Sign In" stays out of the header until M6
ships. Old accounts are not carried over either way.

## 6. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| **Data coverage.** The client has reports for J19 and J45 so far; a full sweep of every committee hasn't been run end to end | Launch is blocked on coverage, not on the site | Start a full `--committee all` run during M2 so any scraper issues surface early |
| **Misreading detections as accusations** | Reputational and legal exposure | DESIGN 13.1 copy rules, an audit trail on every finding, and Methodology links next to counts. Review all copy against the checklist before launch |
| **Upstream site changes** on malegislature.gov break the scraper | Data goes stale quietly | Show each committee's own as-of time; display a notice when it is more than 7 days old |
| **Client runs on a personal machine** | Data updates stop if that machine is off | Decide where the client runs (section 7, item 4) |
| **Email deliverability** (Phase 2) | Sign-in links land in spam, so people can't sign in | SPF, DKIM, and DMARC on the domain before M6; use a transactional provider, not a personal mailbox |
| **Solo maintenance** | Each extra service adds upkeep | No Node, Redis, Celery, or ORM. Cron jobs are Flask CLI commands |
| **Performance claims are untested** | The site could still be slow | M3's acceptance test runs on a seeded 15k-segment set, before any more features are added |

## 7. Decisions needed from you

1. **Status label wording.** Decided: readers see "No violation detected",
   "Provisional", and "Violation detected". Code and contract names stay as they are.
   (DESIGN 16.7)
2. **Committee range.** Decided: (b), an unnamed dot distribution with names on hover
   and in the linked table. (DESIGN 9.4, 16.8)
3. **Cutover timing.** Decided: the owner runs v2 locally and handles deployment and
   cutover separately. Section 5 is a reference, not part of the milestones.
4. **Where the client runs and how often.** Decided: on the owner's machine, once a day,
   usually between 1 and 3 AM ET. The 24-hour delta compares against the previous
   night's publish.
5. **Mail provider** (needed by M6): Postmark (simpler) or Amazon SES (cheaper).

6. **Changelog.** Decided: the client's changelog only, at `/changes`, with a footer
   link and a short-lived dashboard line. Versioning rule in DESIGN 5.6.

Items 1 to 4 and 6 are settled. Item 5 is needed before M6.
