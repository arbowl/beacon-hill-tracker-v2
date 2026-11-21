# Beacon Hill Compliance Tracker v2: Site Design

Status: draft for review. Date: 2026-11-20.

This document specifies the public website for the Beacon Hill Compliance Tracker. It
replaces the React/Flask site in the `beacon-hill-tracker` repository. The data comes from the new
collector/evaluator client (`beacon-hill-compliance-tracker-v2`, package `beacon`).

## 1. Goals and non-goals

Goals:

- Answer one question fast: what does the public record show about each committee
  following the joint rules, bill by bill.
- Act as a neutral data provider. The site puts the data in front of readers so they
  can draw their own comparisons. It does not grade, rank, or advocate (13.1).
- Make every finding auditable on the page: derivation steps, evidence, and the
  verbatim rule text it was checked against.
- Server-rendered pages with HTMX for partial updates. No JavaScript build step, no SPA.
- Postgres-first SQL. Every page is served from small, indexed, precomputed read tables.
- An explicit, versioned ingest contract between client and site.
- Simple auth: email magic links, two roles.

Non-goals:

- The site is not the system of record. The client's store (captures, observations,
  blobs) is. The site keeps what it publishes, plus compact history (aggregates and
  label changes). It does not mirror raw captures or observations.
- No user-generated content beyond watchlists and the contact form.
- No migration of old compliance data. The client re-pushes from its own store.
- No migration of old user accounts. Everyone signs up again through the new flow
  (6.1), which records the emails they want to receive.

Features from the old site that are dropped: password auth, password reset, the
"privileged" and "admin" roles, saved views (shareable URLs replace them), the global
stats cache table, HMAC signing keys (replaced by scoped API keys, see 6.3), and the
FAQ/Updates pages (FAQ content moves into Methodology). The old changelog ingest is
replaced by the client's changelog (5.6).

## 2. Stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12, managed with uv | Matches the client |
| Web | Flask 3, Jinja2 templates | Already familiar; no frontend framework |
| Interactivity | HTMX 2 (vendored file in `static/`) | Partial page swaps, URL push, lazy loading |
| Charts | Chart.js 4 (vendored) for the pie and funnel; server-rendered inline SVG for the committee range | Charts only where they're needed |
| DB driver | psycopg 3 + psycopg_pool, hand-written SQL | Explicit queries, `COPY` for bulk ingest, no ORM |
| Migrations | Numbered `.sql` files applied by `flask db migrate` | Plain SQL, no Alembic |
| Validation | Pydantic 2 for every boundary (ingest, config, forms, public API) | Same as the client |
| Config | pydantic-settings, env vars | |
| Email | smtplib through a transactional provider (Postmark or SES SMTP) | |
| Server | Gunicorn on Render, 2 to 4 workers, `gthread` | |
| Jobs | Render cron jobs that run Flask CLI commands | No Celery, no Redis |
| CSS | One hand-written stylesheet using CSS custom properties | No Tailwind, no Node |

Fonts are self-hosted as woff2 files. This keeps the CSP strict and avoids sending
visitor data to Google.

## 3. Repository layout

```
beacon-hill-tracker-v2/
  DESIGN.md
  pyproject.toml
  contract/                     beacon-contract: ingest models, pydantic only
    beacon_contract/
      __init__.py
      v1.py
  tracker/
    __init__.py                 create_app()
    config.py
    db.py                       pool, transaction helper
    enums.py                    StrEnums mirroring Postgres enum types
    migrations/                 0001_init.sql, 0002_..., applied in order
    ingest/                     routes.py, service.py (validate, COPY, publish)
    publish/                    read-model rebuild, stats, insights, snapshots, dumps
    dashboard/                  routes.py, queries.py, filters.py
    bills/                      bill permalink + audit fragment
    changes/                    /changes page and the dashboard notice query
    auth/                       magic link, sessions, roles, CLI
    watch/                      watchlist, subscriptions
    alerts/                     event generation, digest sending, CLI
    api/                        public JSON API v1, embeds, API keys
    pages/                      methodology, about, contact, press, privacy, terms
    email/                      sender, templates/
    templates/                  base.html, partials/, pages/
    static/                     css/site.css, js/htmx.min.js, js/chart.umd.min.js,
                                js/site.js, fonts/
  tests/
```

`contract/` is a separate installable package. The site and the client both depend on it,
the client through a uv git or path dependency. That way there is exactly one definition
of the wire format.

## 4. Domain vocabulary

These terms come from the client and are used the same way on the site.

- **Session**: a General Court, e.g. 194 (2025-2026).
- **Committee**: e.g. `J19`, Joint Committee on the Judiciary. Chamber: Joint, House or
  Senate.
- **Bill**: e.g. `H10`, normalized with no dot.
- **Bill segment** (in code and the contract: **tenure**): one referral of one bill to
  one committee, i.e. the bill as one committee saw it. Id: `194/H10:J19:2025-02-27`.
  A bill referred to two committees has two segments, and gets two dashboard rows.
  Segments are the unit that gets evaluated. The code keeps the client's name,
  `tenure`, so the contract and the database match the client. "Bill segment" is the
  term used in every piece of user-facing text.
- **Requirement**: one of the four core metrics under ruleset `s194`:

| Requirement id | Dashboard label | Rules |
|---|---|---|
| `hearing_notice` | Hearing notice | JR 1D, SR 12, HR 17A |
| `summary_posted` | Summary | JR 1B |
| `votes_posted` | Vote | JR 1D, HR 17B |
| `report_out_deadline` | Reported out | JR 10, HR 27 |

- **Applicability**: a tenure carries a result only for requirements whose obligation
  arose (a bill never heard has no hearing-notice result), so it has one to four
  results. A bill listed under a committee with no observed referral gets a placeholder
  tenure, `194/H4319:J45:unobserved`, whose only result is `tenure_observed`
  (Provisional, `missing_data`). On the dashboard an absent requirement shows as "Not
  applicable", never as Met.
- **Requirement status**: `Met`, `Unmet`, or `Provisional`. A Provisional status always
  comes with a reason: `not_yet_due`, `ambiguous_timing`, `missing_data`, or
  `awaiting_review`.
- **Label** (a tenure's roll-up over the results it has): `Compliant` if every result is Met,
  `Non-Compliant` if any is Unmet, `Provisional` otherwise.
- **Derivation**: the ordered steps that produced a due date or outcome. Each step has
  an optional date, an optional clause, and a role (trigger, base_due, extension, cap,
  ...).
- **Evidence**: dated lines of source text, each linked to the malegislature.gov page it
  was read from.
- **Clause citation**: a rule reference plus the sha256 of its verbatim text at
  evaluation time.

### 4.1 Bills versus bill segments in copy

Every count shown on the site says which unit it counts, and the two units are never
mixed in one figure.

| Claim | Unit | Computed as |
|---|---|---|
| "The Tracker follows 11,000+ bills" | bills | `count(DISTINCT bill_id)` |
| "X bills had a detected violation" | bills | Distinct bills with at least one Non-Compliant segment |
| "Y bill segments are non-compliant" | segments | Count of segments with that label |
| Compliance rates, pie, funnel, committee range | segments | Segment labels |
| Deadlines, requirement chips, audit trails | segments | Each belongs to one segment |

The rule: use **bills** when the claim is about legislation existing or being
affected, and **segments** when it is about a committee's handling of it. A committee
filter always counts segments, because within one committee each bill has exactly one
segment. Labels and tooltips say "bill segments" in full the first time they appear on
a page. Methodology defines the term with an example of a bill referred to two
committees.

## 5. Ingest contract (client to site)

### 5.1 Transport

The client pushes as a sequence of HTTP calls. Each committee is a separate upload, so
a failure partway through can be retried without starting over. Nothing becomes
visible until the push is published. On the site, one push is one row in `runs`.

```
POST   /api/ingest/v1/runs                            open a push (RunManifest)
PUT    /api/ingest/v1/runs/{push_id}/committees/{id}  upload one CommitteeBundle
POST   /api/ingest/v1/runs/{push_id}/publish          publish every committee atomically
DELETE /api/ingest/v1/runs/{push_id}                  abandon an unpublished push
GET    /api/ingest/v1/runs/{push_id}                  status
PUT    /api/ingest/v1/changelog                       the client's released versions (5.6)
```

- Auth: `Authorization: Bearer <key>`. The key must have scope `ingest`. Only a
  webmaster can be issued one (6.3), and the key stops working if its owner loses the
  role or is disabled.
- Uploads are JSON sent with `Content-Encoding: gzip`. Limits: 25 MB compressed and
  200 MB uncompressed. The inflater is bounded, so a gzip bomb gets a 413. The real J19
  bundle (791 bills) is 269 KB compressed.
- Every call is idempotent. `push_id` is a uuid5 of the committees' evaluation run ids,
  so pushing the same evaluations twice is the same push. Opening an existing push with
  the same session and committees returns it; with a different scope it is a 409.
  Re-uploading a committee replaces that committee's rows. Publishing a published push
  returns the original result. Every success returns a `RunState`.
- Errors return an `ErrorBody`: `{"error": {"code", "message", "details": [{loc, msg}]}}`.
  The statuses are:
  - 401: missing or invalid key.
  - 404: unknown push.
  - 409: scope conflict, push not open, or publish with committees missing.
  - 413, 415, 400: size, encoding, and gzip problems.
  - 422: validation failure (with field paths), or a bundle uploaded to the wrong
    committee path.

### 5.2 Models (`contract/beacon_contract/v1.py`)

The module is the specification. Every model is a frozen Pydantic model with
`extra="forbid"`, and every enum is a StrEnum whose values match the client's.

| Model | Carries |
|---|---|
| `RunManifest` | `push_id`, session, client `code_version`, `created_at`, the committees the push will upload |
| `CommitteeBundle` | One committee's latest evaluation run: session, `evaluation_run_id`, ruleset id and version, `as_of`, the committee, its bills, tenures, cited clauses, and committee output |
| `Tenure` | `tenure_id`, bill, committee, `referred_on` (None when unobserved), label, input fingerprint, one to four `RequirementResult`s |
| `RequirementResult` | Status, reason, detail, due, resolves-on and completed dates, derivation steps, evidence, and `ClauseCitation`s (rule reference plus text hash) |
| `Evidence` | `observation_id` and `row_index` into the client store, plus the resolved `EvidenceLine` (source, date, text, url), or None if the client could not resolve it |
| `Clause` | A rule reference with its verbatim text and sha256 |
| `Changelog` | `code_version` and every released version of the client, newest first (5.6) |
| `Release`, `ChangeSection` | A version, its release date, and its Keep a Changelog sections (`ChangeKind`) of plain-text items |
| `RunState`, `ChangelogState`, `ErrorBody` | The site's replies |

Provenance travels with every bundle:
- **Per committee.** Evaluation run, ruleset version, and as-of are recorded for each
  committee, because the client evaluates each committee in its own run.
- **Per evidence item.** Each keeps its `observation_id` and `row_index`, so any line on
  the site can be traced back to a capture in the client store. The site never needs
  the client's blobs.
- **Unresolved evidence.** A reference the client could not resolve is sent with
  `line: null` rather than dropped.

### 5.3 Validation at the boundary

The site does not repair data: a bundle failing any check is rejected with 422.
`tests/contract/test_v1.py` has one case per rule.

- Ids match the client's normalized forms: bills `H10`, committees `J19`, tenures
  `194/H10:J19:2025-02-27` or `194/H10:J19:unobserved`. The tenure id must agree with
  the tenure's bill, committee, and `referred_on`, and with the bundle's session and
  committee.
- Each `obligation_id` is `{tenure_id}:{requirement_id}`.
- A tenure has at least one result, and at most one per requirement. Requirements must
  belong to the bundle's ruleset (`RULESET_REQUIREMENTS`). `tenure_observed` appears
  on unobserved tenures only, and alone.
- `label == roll_up(statuses)`, using the roll-up in `beacon_contract`. The client has a
  parity test against it.
- `reason` is present if and only if the status is Provisional.
- Every derivation step's clause is among the result's citations. Every citation's hash
  and reference are in the bundle's `clauses`, and each clause's `text_hash` is the
  sha256 of its text.
- `bills` is exactly the set of bills the tenures reference. Tenure, bill, and clause
  ids are unique.
- URLs are http or https. Datetimes carry a timezone.
- `check_upload`: the path's committee id equals the bundle's and is in the push's
  scope, and the bundle's session equals the push's.

### 5.4 Client side (done in M1)

- `beacon/reporters/site_bundle.py`: `SiteBundleReporter` runs with the other
  reporters. It writes `data/reports/<as_of date>/<committee>.bundle.json.gz`, and
  refuses if a cited rule's text has changed since evaluation, because the site needs
  the text that was actually applied.
- `beacon/adapters/site_push.py` and `beacon push`: pushes the newest bundle of each
  committee named with `--committee`, or of every committee that has one. Config is
  `[site] url`, and the key comes from the `BEACON_SITE_KEY` env var.
- `beacon run mvp` pushes the bundles it just wrote when `[site] url` is set, unless
  `--no-push` is given.

### 5.5 Publish semantics

A run covers a scope of committees. When a run is published:

1. In one transaction: mark the run `published`, point `committee_current` at this run
   for each committee in its scope, and add a `committee_publications` row for each.
   Committees outside the scope keep their previous run.
2. Rebuild the rows of `current_tenures` for those committees (8.3).
3. Diff each tenure's new label and requirement statuses against the live rows they
   replace, and write `label_changes`. A new tenure has `from_label` NULL; a tenure
   that disappeared has `to_label` NULL. Alert events come in M7.
4. Recompute `committee_stats` for the session, and append `snapshots` for the
   published committees and the session row. Insights come in M4.
5. Commit. Readers see either the old state or the new one, never a mix (Postgres MVCC).
6. After commit: build data dumps, and prune run detail that is no longer needed (8.5).

Rollback: `flask runs rollback <committee_id> --session 194` marks the committee's
latest publication rolled back, points it at the previous publication, and re-runs
steps 2 to 4, so the reversal is itself recorded in `label_changes`. Rollback can only
reach a publication whose detail is still retained (8.5). Publish, rollback, and prune
share one advisory lock, so they never interleave.

### 5.6 Changelog

The client keeps `CHANGELOG.md` in Keep a Changelog form and versions itself with
SemVer. Readers see it at `/changes`, so every entry is written for the public in the
Tracker's voice; developer detail stays in commit messages. Only client releases are
listed: site changes never change a finding.

Versioning rule (also in the client README):
- **Major**: a breaking contract change.
- **Minor**: any change that can change a finding (a ruleset version bump, or a change
  to how history rows are read or documents are found).
- **Patch**: a fix that changes no finding.

Upload:
- `beacon push`, and so `beacon run mvp`, sends the changelog before opening the run.
  If the changelog fails, nothing is pushed. A separate endpoint is needed because
  `push_id` derives from evaluation run ids, so a changelog edit with no new evaluations
  would otherwise never reach the site.
- The client parses the file into `v1.Changelog`; the site never parses Markdown.
  `[Unreleased]`, the preamble, and link references are not sent. Items are plain text
  and the site escapes them.
- Contract rules: versions are `MAJOR.MINOR.PATCH` with no pre-release or build part,
  unique, strictly newest first, and never dated before an older release. Each release
  has at least one section and no repeated section kind. `code_version` must equal the
  newest release, so a version bump without a changelog entry cannot be pushed.
- Site rules, in one transaction under an advisory lock: a release dated after today
  (ET) is a 422 `future_release`. A changelog missing a version the site already holds
  is a 409 `release_removed`; published history is never deleted.
- Storage: `changelog_releases` holds every distinct text of each version, keyed by
  version and the sha256 of the release's JSON, with the sending `code_version`, key,
  and `received_at`. The row with the latest `current_since` is shown. Sending
  unchanged text is a no-op. Changed text adds a row, and the page shows "Note
  corrected" with that date. Re-sending an earlier text makes it current again.

## 6. Auth, roles, keys

### 6.1 Magic link login

Signing up and signing in are separate forms that share one link mechanism.

**Sign up** (`/signup`):

1. The form has an email field and one checkbox for each email type in `email_kind`
   (8.4). The user ticks the emails they want to receive:

   | `email_kind` | Label | Default |
   |---|---|---|
   | `watch_status_changed` | "A bill segment I watch changes status" | checked |
   | `watch_deadline_approaching` | "A deadline on something I watch is 7 days away" | checked |
   | `watch_deadline_missed` | "A deadline on something I watch is missed" | checked |
   | `weekly_summary` | "Weekly compliance summary" | unchecked |
   | `announcements` | "Occasional announcements about the Tracker" | unchecked |

   The three watch kinds set which alerts a new watch starts with (10). The other two
   are site-wide mailings.
2. The site sends a link to `/signin/verify?t=<token>` and stores the selections on the
   token row. No account exists until the link is used, so unverified addresses never
   become accounts.
3. Using the link creates the `user` with those preferences.

**Sign in** (`/signin`): the user enters an email address and the site sends a link.
If no account exists for the address, the email instead says so and links to
`/signup`. Either way the page replies "Check your email", so the form does not reveal
which addresses have accounts.

**The link, for both flows:**

1. The token is 32 random bytes, base64url. Only its sha256 is stored. It is
   single-use and expires after 15 minutes.
2. Opening the link (GET) shows a page with a "Sign in" button. Clicking it (POST)
   consumes the token. The extra step stops email link scanners, which issue GETs,
   from using up the token.
3. Signing in creates a server-side session: a row in `web_sessions`, and a random id
   in a cookie that is `HttpOnly`, `Secure`, `SameSite=Lax`, lasting 30 days. Because
   the session lives in the database, logout and "sign out everywhere" take effect
   immediately.

Users change their email preferences on the watchlist page at any time.

Throttling is enforced in the database, so it holds across all Gunicorn workers: at
most 3 link requests per email per 15 minutes, and 20 per IP per hour. Requests are
counted in `login_requests`, not `login_tokens`, because a sign-in request for an
address with no account sends an email but no link. Links are built from `SITE_URL`,
never from the request's Host header.

### 6.2 Roles

Postgres enum `user_role`: `user`, `webmaster`.

- `user`: watchlist, alerts, read API keys, data dumps.
- `webmaster`: everything a user can do, plus ingest keys, run status, and rollback. The
  role is granted only from a shell on the host:

```
flask users set-role someone@example.org webmaster
flask users list --role webmaster
flask users disable someone@example.org
```

No web route can change a role.

### 6.3 API keys

There is one `api_keys` table with a `scope` enum: `read`, `ingest`. Embeds need no
key (11).

- Key format: `bht_<scope>_<random>`. The site stores the sha256 of the key plus an
  8-character prefix for display. The full key is shown once, at creation.
- `read` keys are for the public JSON API, rate-limited per key. Users create and
  revoke them on the watchlist page, at most `API_KEYS_PER_USER` (5) live at a time.
- `ingest` keys can be created by webmasters only.
- Every key records `last_used_at`, and any key can be revoked.

### 6.4 CSRF and headers

- Every POST form and every HTMX request carries a CSRF token. The token is set once
  on `<body hx-headers='{"X-CSRF-Token": "..."}'>`.
- CSP: `default-src 'self'`, with no inline scripts. Chart data is passed in
  `<script type="application/json">` blocks.
- Every route sends `frame-ancestors 'none'` except the embed routes, which send
  `frame-ancestors *`.

## 7. Pages and routes

Header navigation: Dashboard, Methodology, About, Contact, and Watchlist (signed in)
or Sign In (signed out).
Footer: About, Methodology, Press, Changes, Privacy, Terms. The footer also shows the last
updated time and the ruleset version, which links to `/changes`.

| Route | Page | Notes |
|---|---|---|
| `/` | Dashboard | Section 9 |
| `/bills/<session>/<bill_id>` | Bill permalink | All tenures, audit trails expanded. Target for alert emails |
| `/methodology` | Methodology | The four requirements, how each finding is derived, Provisional reasons, the rule texts (from `clauses`), and FAQ |
| `/about`, `/press`, `/privacy`, `/terms` | Static | Jinja templates |
| `/changes` | Changes | Every client release, newest first (5.6). Linked from the footer and Methodology |
| `/contact` | Contact form | Honeypot field, rate limit, emails the webmaster. Not stored |
| `/signup`, `/signin`, `/signin/verify`, `/signout` | Auth | 6.1. `/signout` is POST only; `everywhere=1` ends every session |
| `/watchlist` | Watchlist | Watched bills and committees, alert settings, email preferences, API keys, embed builder, downloads |
| `/watchlist/downloads/<format>` | Data dump | Login required. `csv.gz` or `json.gz` |
| `/embed/committee/<id>`, `/embed/bill/<session>/<id>` | Widget | Small public iframe page |
| `/embed` | Embed builder | Public. Linked from the watchlist and every bill page |
| `/email/unsubscribe` | Unsubscribe | Signed link from any email; one kind or all |
| `/api/v1/...` | Public JSON API | Section 12 |
| `/api/ingest/v1/...` | Ingest | Section 5 |
| `/healthz` | Health | Checks DB connectivity |

Every list and filter route returns a full page on a normal request. When the
`HX-Request` header is present, it returns only the fragment being swapped. Each route
has one handler and one query; the request type only changes which template is rendered.

## 8. Database (Postgres 16)

### 8.1 Principles

- **Separate read models from source data.** Ingested rows (`tenures`, `requirements`,
  `requirement_audit`) are written once per run. Pages read precomputed tables
  (`current_tenures`, `committee_stats`, `insights`, `snapshots`). The publish step
  rebuilds those tables, so page loads don't aggregate anything.
- **Keep hot rows narrow.** The bill list reads one table and does no joins. Audit
  detail (JSONB) sits in a separate table and is fetched only when an accordion opens.
- **Bounded growth.** Full detail is kept only for the current run and the previous run
  of each committee. History is kept as aggregates (`snapshots`) and changes
  (`label_changes`). Both are small and kept forever. This replaces the old database's
  per-scan bloat.
- **Native enum types** for every fixed set of values, with matching Python StrEnums in
  `tracker/enums.py`. `timestamptz` for instants and `date` for legislative dates.
  Dates are never stored as text.
- **Bulk writes use `COPY FROM STDIN`** (psycopg `cursor.copy`). Ingesting about 44k
  requirement rows takes seconds.

At this data size (about 11k bills, 15k tenures, 60k requirement results), Postgres
should answer every page query in single-digit milliseconds with the indexes below. The
old site's lag most likely came from aggregating on every request, loading whole
datasets into Python or the browser, missing indexes, and text-typed dates. This
design rules each of those out by construction.

### 8.2 Schema

The schema is defined by `tracker/migrations/0001_init.sql`; this section summarizes
it. Enum types: `chamber`, `requirement` (the four ids plus `tenure_observed`),
`req_status`, `prov_reason`, `label`, `run_status`, `user_role`, `key_scope`.

| Table | One row per | Notes |
|---|---|---|
| `runs` | push | `run_id` is the client's `push_id`; scope, status, and the key that opened it |
| `run_committees` | committee in a push | Upload ledger. Holds the committee's evaluation run id, ruleset version, as-of, name/chamber/url, bundle sha256, and `pruned_at`. It is kept after its detail is pruned, as provenance |
| `run_bills`, `committee_outputs` | bill or document in an upload | Detail; pruned |
| `tenures`, `requirements`, `requirement_audit` | tenure, result, result | Detail; pruned. `referred_on` is nullable. Audit JSONB holds derivation, evidence, and citations as sent |
| `clauses` | distinct rule text | Content-addressed by `text_hash` and never pruned, so any past citation can be shown |
| `committees` | committee | Dimension, upserted at publish (not upload) from the run that went live |
| `committee_current` | committee | Which run is live |
| `committee_publications` | publish of a committee | Drives diffing, rollback, and retention |
| `changelog_releases` | distinct text of a client release | Never pruned. Shown row per version is the latest `current_since` (5.6) |
| `users`, `api_keys` | | 8.4 |

### 8.3 Read models (rebuilt at publish)

| Table | Contents |
|---|---|
| `current_tenures` | One row per live tenure. The four requirement columns are nullable: NULL means no obligation arose. Also `referral_observed`, `report_due`, `next_due` (the earliest due date of a Provisional result), `severity`, and a generated `search` tsvector. Indexes: `ct_list`, `ct_committee`, `ct_bill`, `ct_search`, `ct_bill_trgm` |
| `committee_stats` | One row per committee plus a session row (`committee_id = '*'`). Rows are built with `GROUPING SETS` over `current_tenures`. Columns: label counts, `bills_total`, `bills_with_violation`, the per-requirement counts (`hearing_notice_total`, `hearing_notice_unmet`, `summary_unmet`, `votes_unmet`, `report_out_unmet`), `due_next_14_days`, `funnel`, `compliance_rate`, and `decided_rate` |
| `snapshots` | Label counts per committee per publish. Kept forever |
| `label_changes` | One row per tenure whose label or any requirement status changed in a publish or rollback, with `changed_requirements` as `[{requirement, from, to}]`. Kept forever |
| `insights` | M4 |

The per-requirement counts are named for what they count. A hearing-notice result
can be Unmet for reasons other than short notice, so the insight copy in 9.3 has to
be built from the derivation, not from `hearing_notice_unmet` alone.

**Funnel.** `funnel` is `[total, s1, s2, s3, s4]`. Stage k counts tenures with an
observed referral whose first k requirements are each Met or absent, so `s4` equals
Compliant. An absent requirement counts as passed. In committees where many bills are
never heard, this makes the "hearing notice" stage look nearly complete (J45: 66 of
67), so how the funnel labels absent stages has to be settled in M4.

**Rebuild.** For each published committee, in the publish transaction:
1. Diff the rows built from the new runs against the live `current_tenures` and write
   `label_changes`.
2. Delete and re-insert those committees' rows. The rebuild is one `INSERT ... SELECT`
   that pivots `requirements` with `max(status) FILTER (WHERE requirement = ...)`.
3. Recompute `committee_stats` for the whole session.
4. Append `snapshots`.

Publishing J19 and J45 (858 tenures) takes well under a second locally.

### 8.4 Accounts, alerts, keys

`users` and `api_keys` are in `0001_init.sql`. The rest of this section is the
specification for M6 to M8.

```sql
CREATE TYPE user_role AS ENUM ('user', 'webmaster');
CREATE TYPE key_scope AS ENUM ('read', 'ingest');
CREATE TYPE watch_kind AS ENUM ('bill', 'committee');
CREATE TYPE alert_kind AS ENUM ('status_changed', 'deadline_approaching', 'deadline_missed');
CREATE TYPE email_kind AS ENUM ('watch_status_changed', 'watch_deadline_approaching',
  'watch_deadline_missed', 'weekly_summary', 'announcements');

CREATE TABLE users (id bigserial PRIMARY KEY, email citext UNIQUE NOT NULL,
  role user_role NOT NULL DEFAULT 'user', created_at timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz, disabled_at timestamptz);
CREATE TABLE user_email_prefs (user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  kind email_kind NOT NULL, enabled boolean NOT NULL, updated_at timestamptz NOT NULL,
  PRIMARY KEY (user_id, kind));               -- one row per kind, written at sign-up
CREATE TABLE login_tokens (token_hash bytea PRIMARY KEY, email citext NOT NULL,
  created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, used_at timestamptz,
  request_ip inet NOT NULL,
  signup_prefs email_kind[],                  -- set only for sign-up links: the kinds ticked
  next_path text);                            -- same-site path to return to after sign-in
CREATE TABLE login_requests (id bigserial PRIMARY KEY, email citext NOT NULL, ip inet NOT NULL,
  requested_at timestamptz NOT NULL DEFAULT now());  -- throttle ledger, kept one day
CREATE INDEX ON login_requests (email, requested_at);
CREATE INDEX ON login_requests (ip, requested_at);
CREATE TABLE web_sessions (id_hash bytea PRIMARY KEY, user_id bigint NOT NULL REFERENCES users,
  created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, last_seen_at timestamptz);
CREATE TABLE api_keys (id bigserial PRIMARY KEY, user_id bigint NOT NULL REFERENCES users,
  scope key_scope NOT NULL, name text NOT NULL, prefix text NOT NULL, key_hash bytea UNIQUE NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz, revoked_at timestamptz);
CREATE TABLE watches (id bigserial PRIMARY KEY, user_id bigint NOT NULL REFERENCES users,
  kind watch_kind NOT NULL, session int NOT NULL, target_id text NOT NULL,
  alerts alert_kind[] NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, kind, session, target_id));
CREATE INDEX ON watches (kind, session, target_id);
CREATE TABLE alert_events (id bigserial PRIMARY KEY, kind alert_kind NOT NULL,
  session int NOT NULL, tenure_id text NOT NULL, bill_id text NOT NULL,
  committee_id text NOT NULL, run_id uuid NOT NULL,
  label_change_id bigint REFERENCES label_changes,     -- status_changed: the change reported
  requirement requirement, obligation_id text, due date, -- deadline kinds: what is or was due
  created_at timestamptz NOT NULL, withdrawn_at timestamptz);  -- set by rollback; not sent
-- One live event per label change, and per (kind, obligation, due date): an extended
-- deadline alerts again for its new date.
CREATE UNIQUE INDEX ON alert_events (label_change_id)
  WHERE kind = 'status_changed' AND withdrawn_at IS NULL;
CREATE UNIQUE INDEX ON alert_events (kind, obligation_id, due)
  WHERE kind <> 'status_changed' AND withdrawn_at IS NULL;
CREATE TABLE alert_deliveries (user_id bigint, alert_event_id bigint, sent_at timestamptz NOT NULL,
  PRIMARY KEY (user_id, alert_event_id));
CREATE TABLE email_sends (user_id bigint, kind email_kind, key text, sent_at timestamptz NOT NULL,
  PRIMARY KEY (user_id, kind, key));          -- weekly (ISO week) and announcements (file name)
CREATE TYPE dump_format AS ENUM ('csv.gz', 'json.gz');
CREATE TABLE data_dumps (id bigserial PRIMARY KEY, session int NOT NULL,
  format dump_format NOT NULL, data_version text NOT NULL,  -- digest of the live runs
  as_of timestamptz NOT NULL, built_at timestamptz NOT NULL,
  current_since timestamptz NOT NULL,          -- moves when this data is live again
  sha256 text NOT NULL, size bigint NOT NULL, bytes bytea NOT NULL,
  UNIQUE (session, format, data_version));
```

### 8.5 Retention

Retention is set in config (`tracker/config.py`, env vars), not in code:

| Setting | Default | Meaning |
|---|---|---|
| `RETAIN_DETAIL_PUBLICATIONS` | 2 | Publications per committee whose detail rows are kept. Must be at least 1. Also the rollback depth |
| `RETAIN_DUMPS_RECENT` | 30 | Most recent data dumps kept |
| `RETAIN_DUMPS_MONTHLY` | true | Also keep the first dump of each month, indefinitely |
| `ABANDON_OPEN_RUNS_AFTER_HOURS` | 48 | Unpublished runs older than this are abandoned and deleted |

`flask runs prune` runs daily and after each publish. It works per committee upload:
an upload's detail rows (`tenures`, `requirements`, `requirement_audit`, `run_bills`,
`committee_outputs`) are deleted once it is no longer among that committee's latest
`RETAIN_DETAIL_PUBLICATIONS` non-rolled-back publications. Its `run_committees` row is
kept and marked `pruned_at`. A run whose every upload is pruned gets status `pruned`, and
the run row stays as a record that it existed. Lowering a
setting takes effect at the next prune. Raising it only affects runs that haven't been
pruned yet. With the defaults, detail storage stays at about two copies of the dataset
(tens of MB). `snapshots` and `label_changes` are never pruned.

### 8.6 Query rules

- The bill list uses keyset pagination on `(severity, next_due, bill_num, tenure_id)`,
  50 rows per page, never `OFFSET`. The total count comes from `committee_stats` when
  only the committee filter or status filter is set. A `count(*)` runs only when search
  text is present, and at about 15k rows that is still cheap.
- Bill id search: the input is normalized first, so `H.10` becomes `H10`. An exact match
  on `ct_bill` is tried first, then a prefix match through trigram. Keyword search uses
  `websearch_to_tsquery('english', q)` against `search`.
- The dashboard header, charts, committee range, and insights are single-row or few-row primary
  key lookups.
- Responses carry `ETag: <max(committee_current.as_of)>-<filters hash>` and
  `Cache-Control: public, max-age=60`. Conditional requests return 304 without
  touching the read tables.
- `pg_stat_statements` is on in production. Any statement with a mean above 20 ms is a
  bug.

## 9. Dashboard

### 9.1 Layout

```
+------------------------------------------------------------------------------+
| Header: logo | Dashboard  Methodology  About  Contact  Watchlist/Sign In      |
+------------------------------------------------------------------------------+
| HERO: "Is Beacon Hill Following Its Own Rules?"                              |
| The Massachusetts Legislature is bound by joint rules to record votes, post  |
| agendas, and ensure transparency. The Tracker follows 11,000+ bills to see   |
| if they are.                                                                 |
+------------------------------------------------------------------------------+
| FIGURES: [Compliance % (delta)] [Bills tracked] [Detected viol.] [Provisional] |
|          (bill segments)        (bills/segments)  (segments)      (segments)    |
+------------------------------------------------------------------------------+
| INSIGHTS: 3 to 5 generated sentences, each linking to a filtered view        |
+------------------------------------------------------------------------------+
| [ Breakdown pie ]       | [ Attrition funnel ]       | [ Committee range ]   |
+------------------------------------------------------------------------------+
| FILTER PANE         | BILL LIST                                              |
|  Query filters      |  H10  An Act relative to ...   J19   [N][S][V][R] Non-C|
|   (reset all)       |   > Audit trail (accordion)                            |
|  Bill ID / keyword  |  ...                                                   |
|  Status (radios)    |  [ first page | next 50 ]                              |
|  Committee (select) |                                                        |
|  Share URL [Copy]   |                                                        |
|  Last updated ...   |                                                        |
+------------------------------------------------------------------------------+
| Footer: About  Methodology  Press  Privacy  Terms   | updated, ruleset s194 v5 |
+------------------------------------------------------------------------------+
```

The page has a maximum width of 1440 px, centered. Below 960 px, the chart row stacks
into one column and the filter pane becomes a collapsible block above the list.

The bill count in the explainer comes from the data (`bills_total`, distinct bills),
rounded down to the nearest thousand followed by "+", so the sentence stays true as the
count changes.

For 14 days after a major or minor client release (5.6), one muted line sits under the
explainer: "The Tracker's methods changed on 2026-12-01 (version 0.2.0). What changed",
linking to that release on `/changes`. Patch releases change no finding and get no
line. The notice is part of the dashboard's ETag, so it appears and expires without a
new publish.

### 9.2 Summary figures

There are four cards. The compliance % card shows its delta in the top-right corner:
`+1.2 pts today` and `-0.4 pts 7d`. The delta is green when the rate went up and red
when it went down, with an up or down glyph so the direction doesn't depend on color.

The cards:

- **Compliance rate** = `(compliant + provisional) / total`, over bill segments. A
  segment counts toward the rate unless it has a detected violation. The card's
  caption says so: "Bill segments with no detected violation".
- **Bills tracked**: distinct bills, with the segment count beneath, e.g. "11,204 bills
  in 12,870 bill segments". This card is where the two units are introduced side by
  side.
- **Detected violations**: segments labeled Non-Compliant, with "in N bills"
  (`bills_with_violation`) beneath.
- **Provisional**: segments with that label.

The deltas:

- Daily delta = the current compliance rate minus the rate in the most recent
  `snapshots` row at least 24 hours old. The 7-day delta uses a row at least 7 days
  old. If no such snapshot exists, the card shows no delta rather than 0.

When a committee is selected, every card switches to that committee's
`committee_stats` row. Within one committee, bills and segments are the same count, so
the bills sub-lines are hidden.

### 9.3 Insights

Insights replace "Recent Activity". They are generated at publish time from
`committee_stats`, `snapshots`, and `label_changes`, using fixed templates and
deterministic selection. No LLM is involved. Each insight is stored with an `href` to
the filtered view that backs it, so every claim can be checked in one click.

Insights state counts and changes. They never characterize a committee as best,
worst, leading, or lagging, and never attribute a cause (13.1). The selection rules
decide which fact is surfaced; the sentence only states the fact.

| Kind | Template | Selection |
|---|---|---|
| `deadline_count` | "{n} bill segments in the {name} have a detected missed reporting deadline this session. {rate}% of its segments have no detected violation." | Largest `missed_report_deadlines` among committees with at least 20 segments |
| `weekly_change` | "The share of the {name}'s bill segments with no detected violation changed by {+d} points over the past week." | Largest absolute change against the newest snapshot at least 7 days old; skipped if zero or no such snapshot |
| `due_soon` | "{n} bill segments have a pending requirement due in the next 14 days, {m} of them in the {name}." Links to the list sorted by next deadline | Session `due_next_14_days`, and the committee with the most |
| `bills_affected` | "{n} of {total} tracked bills have at least one bill segment with a detected violation." | Session row `bills_with_violation`, `bills_total` |

The `notice` kind is not built: an Unmet hearing notice is not always a short one, and there is no list filter that would back the sentence. The dashboard shows up to four insights, at most one per kind. Templates whose
precondition fails (for example, no 7-day snapshot yet) are skipped.

### 9.4 Chart row (three columns)

1. **Compliance breakdown**: a Chart.js pie of bill segments with three slices, using
   the status colors. The legend shows counts and percentages, and clicking a slice
   applies the matching status filter.
2. **Attrition funnel**: horizontal floating bars centered on an axis, from Chart.js
   bar data `[-v/2, v/2]`. Stages are cumulative, in the order of a bill's life:
   - Bill segments tracked
   - Hearing notice met
   - and summary posted
   - and vote posted
   - and reported out on time (equal to Compliant)

   Each bar is labeled with its count and its drop from the previous stage. Provisional
   requirements count as "not yet passed" at their stage. The counts are
   `committee_stats.funnel`.
3. **Committee range** (requested as "Top movers"): an unnamed distribution.
   - **What it shows.** One dot per committee on a 0 to 100% axis of `decided_rate` =
     `compliant / (compliant + non_compliant)`. Provisional segments are left out, so
     it reflects determined outcomes only. That is a different measure from the
     headline compliance rate, and the captions say so.
   - **Which committees.** Only committees with at least 20 determined segments are
     drawn. A dashed tick marks the median; with an even count, it is the lower of the
     two middle committees.
   - **Names.** None are printed. Each dot has an SVG `<title>` with the name and rate,
     and is focusable, so the name shows on hover and to screen readers. The caption
     links to the full alphabetical committee table.
   - **Committee filter.** When a committee is selected, its dot is drawn in the link
     color, so readers see where it sits without any ranking text.
   - **Rendering.** The strip is a server-rendered inline SVG with no JavaScript.
     Coincident dots stack upward, and the SVG's `aria-label` summarizes the count,
     range, and median.
   - **History.** The sparklines from the earlier design are dropped, because they
     named committees. Per-committee history belongs on the committee view.

Chart data is embedded as JSON in the page. `site.js` (about 100 lines) initializes the
charts on load and after each HTMX swap of the chart row.

### 9.5 Filter pane and URL state

- **Query Filters** heading, with a "Reset all" link to `/`.
- **Search**: a text input for bill id or keyword. It sends
  `hx-get="/" hx-trigger="input changed delay:300ms, search" hx-target="#results"
  hx-push-url="true"`.
- **Compliance status**: radios for All, No violation detected, Provisional, and
  Violation detected. Changing
  one triggers the same HTMX request.
- **Committee**: a select listing every committee as "Name (J19)", sorted by name, from
  `committees`. Choosing one also swaps the summary figures and charts out-of-band
  (`hx-swap-oob`) so they reflect the committee.
- **Share this view**: a read-only Roboto Mono box showing the canonical URL, with a
  "Copy" link that uses `navigator.clipboard`. The server re-renders the box with every
  swap, so it always matches the address bar.
- **Last updated**: shown at the bottom, as `max(committee_current.as_of)` in
  America/New_York, formatted like "Nov 20, 2026, 3:31 AM ET". When a committee filter
  is active, that committee's own as_of appears beneath it.

Query parameters are `q`, `status` (`compliant|provisional|non-compliant`),
`committee`, `after` (keyset cursor), and `sort` (`severity` default, `bill`, `due`).
All of them are parsed into one frozen Pydantic `DashboardFilters` model. Invalid values
return 400. They are never silently dropped.

### 9.6 Bill rows and audit accordion

The row layout stays close to the current site:

```
H10   An Act relative to nondisclosure agreements          Judiciary (J19)
      [Notice: Not shown] [Summary: Met] [Vote: Pending] [Reported: Not shown]   VIOLATION DETECTED
      > Show audit trail
```

- Each of the four chips has a colored dot and a text status, so meaning never depends
  on color alone. A requirement with no result for the segment shows a hollow gray
  chip reading "Not applicable". Hovering a chip shows the requirement's `detail` in a Merriweather
  tooltip, for example "Hearing notice posted 2025-10-29 for the 2025-11-04 hearing: 6
  days' notice, 10 days required".
- The bill id links to the permalink, and an icon links to malegislature.gov.
- The accordion is a `<details>` element. Its body loads on first open with
  `hx-get="/bills/194/H10/audit?tenure=..." hx-trigger="toggle once"`. Nothing is
  fetched for rows that are never opened.

Accordion content, one block per requirement:

- Status, reason (for Provisional, in plain words, e.g. "Not yet due; resolves
  2026-03-04"), due date, and completed date.
- **How this was decided**: the derivation steps as a vertical timeline. Each step
  shows its date, description, and role badge (Trigger, Deadline, Extension, Cap).
  Clause references are links that show the verbatim clause text from `clauses`.
- **Evidence**: source type, date, the quoted text, and a link to the original page.
  A search that found nothing says either "every source checked" or "search
  incomplete, not proof of absence", exactly as the client reported it.
- **Status history** for the bill segment, from `label_changes`, e.g. "Provisional ->
  Non-Compliant on 2026-01-04".
- If the bill has other segments, a line links to them: "This bill was also referred
  to: Ways and Means (H33), Non-Compliant".
- A provenance footer with the ruleset id and version, run id, as-of time, input
  fingerprint, and evidence observation ids (in a collapsed monospace block).
- If a cited clause's `text_hash` differs from the latest stored text for the same
  reference, the block shows a notice: "Judged against an earlier version of this
  rule".

## 10. Watchlist and alerts

- A "Watch" button on bill rows, the bill permalink, and the committee filter header
  adds a `watches` row. Signed-out users are sent to sign in and then returned to the
  page they were on.
- Watching a bill covers all of its segments. Watching a committee covers every
  segment in that committee.
- Each watch has its own set of alerts. A new watch starts with the watch kinds the
  user has enabled in `user_email_prefs` (6.1), and the user can change them per
  watch. Turning off a kind in email preferences stops it for every watch:
  - `status_changed`: a segment's label or a requirement status changed in a publish
    (from `label_changes`).
  - `deadline_approaching`: a Provisional `not_yet_due` requirement is due within 7
    days.
  - `deadline_missed`: a requirement turned Unmet because its due date passed.
- Events are written at publish time (`status_changed`, `deadline_missed`) and by the
  daily `flask alerts scan` job (`deadline_approaching`). The unique indexes mean a given
  obligation and due date produces each kind of alert only once.
- A watch receives only events created after it, so a new watch does not replay history.
  Events older than 14 days are not sent.
- A rollback marks the withdrawn run's events `withdrawn_at` so pending ones are not
  sent. The rollback's own changes are not emailed.
- `flask alerts send` runs as a Render cron job every hour. It groups undelivered
  events per user into one digest email, records them in `alert_deliveries` in the same
  transaction as the send (a failed send is retried next hour), and links each item to
  its bill permalink. An advisory lock keeps overlapping runs from sending twice.
- `flask emails weekly` runs as a weekly cron job, at most once per account per ISO week
  (`email_sends`). It sends the `weekly_summary` email
  (summary figures, 7-day deltas, current insights) to users who enabled it.
- `flask emails announce <file>` is a host-only CLI command. It sends a plain-text file
  (subject on the first line) to users who enabled `announcements`, once per account
  per file name.
- Every email has a one-click unsubscribe (an `itsdangerous`-signed link to
  `/email/unsubscribe`, plus `List-Unsubscribe` and `List-Unsubscribe-Post` headers)
  that turns off that email's kinds, with an option to turn off all email. Opening the
  link changes nothing; the button, or the mail client's one-click POST, does. The
  signed link is the credential, so these POSTs carry no CSRF token. Every email
  also links to the preferences on the watchlist page.

## 11. Embeds and data dumps

- **Embeds are public.** They need no key or account, and any site may frame them.
- **Embed builder** (`/embed`, public): pick a committee or a bill, then a size (small,
  medium, large) and theme (light, dark). The builder outputs an
  `<iframe src="https://.../embed/committee/J19?theme=light">` snippet and a live preview.
  It is linked from the watchlist and from every bill page. It is a plain GET form, so
  it needs no JavaScript.
- **The widget** shows the name, compliance rate, a breakdown bar of bill segments, the
  four requirement counts, the last-updated time, and a "Beacon Hill Compliance
  Tracker" link. It is under 20 KB, uses no JavaScript, and returns
  `Cache-Control: public, max-age=300`. Because embeds are cached and read only the
  precomputed tables, heavy embed traffic is cheap. Widgets send
  `frame-ancestors *`; every other page keeps `frame-ancestors 'none'`. They set no
  cookies, use system fonts and their own small stylesheet, draw the breakdown bar as
  SVG (the CSP forbids inline styles), and link to Methodology because they show counts
  without audit trails. Per-IP rate limiting is not built: counting views in the
  database would cost more than serving the cached widget, so it belongs in front of the
  app (a CDN or proxy) if traffic ever calls for it.
- If embeds later need keys, an `embed` value can be added to `key_scope` without
  changing the URLs. The key would be an optional query parameter.
- **Data dumps** (login required): `current.csv.gz` has one row per tenure, with the
  four statuses, due and completed dates, and the label. `current.json.gz` has the full
  `CommitteeBundle` structure for every committee, including audit trails and clauses.
  Both are built by `flask dumps rebuild` (the CSV with `COPY (...) TO STDOUT`) and
  stored in `data_dumps`. It runs every 10 minutes and after a rollback, and does nothing
  when the newest files already hold the live data. Building takes too long to run
  inside the publish request at full scale. Each JSON bundle is rebuilt from the detail
  tables and validated against the contract before it is written. Gzip runs with a fixed
  timestamp, so the same data always gives the same bytes and sha256, and a rollback
  reuses the earlier file. The site serves the files with their sha256 and as-of time in
  the response headers (`X-Tracker-SHA256`, `X-Tracker-As-Of`) and on the page.

## 12. Public JSON API (v1)

The API is read-only, needs a `read` key (`Authorization: Bearer ...`), and is limited
to `API_REQUESTS_PER_MINUTE` (60) requests per key per clock minute, counted in
`api_rate_windows` so the limit holds across workers; over it returns 429 with
`Retry-After`. The response models are Pydantic (`tracker/api/models.py`), and the
OpenAPI 3.1 document at `/api/v1/openapi.json` (public) is generated from them.

Each endpoint calls the dashboard's own query, so its figures match the dashboard for
the same filters. Labels and statuses use the data's values, which Methodology maps to
display labels. The bill endpoint returns each segment's contract `Tenure`, rebuilt from
the detail tables as the downloads are. Errors use the ingest API's `ErrorBody` shape,
and any error under `/api/` is JSON.

```
GET /api/v1/stats?committee=J19
GET /api/v1/committees
GET /api/v1/committees/{id}
GET /api/v1/segments?status=&committee=&q=&after=  keyset paginated, same filters as the dashboard
GET /api/v1/bills/{session}/{bill_id}              the bill and all its segments, with full audit
```

## 13. Visual design

ProPublica-style: editorial, with lots of whitespace, thin rules instead of boxes, and
no shadows or gradients.

```css
:root {
  --c-compliant: #166534;
  --c-provisional: #d97706;
  --c-noncompliant: #991b1b;
  --c-bg: #fafaf9;
  --c-surface: #ffffff;
  --c-text: #1c1917;
  --c-muted: #57534e;
  --c-rule: #e7e5e4;
  --c-link: #1d4ed8;
  --font-serif: "Merriweather", Georgia, serif;
  --font-ui: "Inter", system-ui, sans-serif;
  --font-mono: "Roboto Mono", ui-monospace, monospace;
  --max-width: 1440px;
}
```

- Merriweather is used for headlines, the hero, insights, narrative pages, and
  tooltips. Inter, with `font-variant-numeric: tabular-nums`, is used for UI controls,
  numbers, tables, and chips. Roboto Mono is used for bill ids, the share URL box, and
  provenance hashes.
- Status chips use a pale tint of the status color as the background and the full color
  for the text. Text contrast is checked against WCAG AA.
- Focus rings are visible, every control is reachable by keyboard, the accordion uses
  native `<details>`, and the charts each have a text summary for screen readers.

### 13.1 Voice and framing

The site is a humble data provider, not an advocate. It surfaces the record so readers
can make comparisons; it does not make them on the reader's behalf. These rules apply
to every page, email, embed, insight template, and API description.

- **Name**: "Beacon Hill Compliance Tracker" in full, "the Tracker" after first use.
- **Voice**: "the Tracker" for what the software does ("the Tracker checks"), and "I"
  for the person who runs it (About, Contact, Press). Never "we", "our", or "us".
- **Findings are detections, not judgments.** Use "detected violation", "possible
  violation", "the public record does not show". Avoid "violated", "failed", "broke the
  rules", or anything implying intent or cause.
- **No scorecarding or ranking language.** No "best", "worst", "leads", "lags",
  "top", "bottom", "grade", or "score". Orderings exist only to show spread or to sort
  a list, and say so.
- **State the limits.** Wherever a count of detected violations appears without its
  audit trail, a link to Methodology explains that a detection reflects what was
  posted online, which may differ from what a committee did.
- **Display labels.** Readers see the three labels as "No violation detected",
  "Provisional", and "Violation detected" (decision 7). Requirement chips read "Met",
  "Pending" (status Provisional), "Not shown" (status Unmet), or "Not applicable" (no
  result).
- **Neutral labels in code are fine.** Identifiers like `non_compliant`, the enum
  values `Compliant` and `Non-Compliant`, and `decided_rate` are internal and follow the
  client's vocabulary. So are URL parameters (`status=non-compliant`) and data-file
  columns, which Methodology maps to the display labels. The rules above govern only
  text a reader sees.

## 14. Operations

- **Render**: one web service (Gunicorn), one managed Postgres, and cron jobs for
  `flask alerts scan` (daily), `flask alerts send` (hourly), `flask emails weekly`
  (weekly), `flask runs prune` (daily), and `flask dumps rebuild` (every 10 minutes).
- **Environment**: `DATABASE_URL`, `SECRET_KEY`, `SMTP_*`, `MAIL_FROM`,
  `WEBMASTER_EMAIL`, `SITE_URL`, and the retention settings in 8.5.
- **CLI**: `flask db migrate`, `flask users ...`, `flask runs list|rollback|prune`,
  `flask alerts scan|send`, `flask emails weekly|announce`, `flask dumps rebuild`.
- **Logging**: structured JSON logs. Each ingest call logs the run id, committee,
  number of rows, and duration.
- **Tests**: pytest against a real Postgres database (a throwaway one in CI). Contract
  tests feed client-produced fixture bundles through validation and publish. A query
  test asserts that each dashboard query uses an index scan, using `EXPLAIN (FORMAT
  JSON)` on a seeded dataset of 15k tenures.
- **Performance budget**: server time under 50 ms p95 for the dashboard and fragments.
  First page load under 250 KB including fonts. HTMX fragment responses under 30 KB.

## 15. Build order

1. Write the contract package. Add the client's `site_push` reporter, and build fixture
   bundles from `data/reports`.
2. Write the schema and migrations. Implement ingest: open, upload, publish, with the
   read-model rebuild. Implement the webmaster CLI and ingest keys.
3. Build the dashboard: filters, bill list, keyset pagination, audit accordion, bill
   permalink.
4. Add the summary figures, snapshots and deltas, the chart row, committee range, and
   insights.
5. Add sign-up with email preferences, magic link sign-in, sessions, and the
   watchlist.
6. Add alerts and email: events, scan, digest send, weekly summary, announcements,
   unsubscribe.
7. Add public embeds, API keys, the public API, and data dumps.
8. Write the static pages and methodology, then do an accessibility pass, a performance
   pass, and Render deployment.

## 16. Decisions

Settled 2026-11-20 (7 to 9 after the proposal review):

1. **Compliance rate** = (compliant + provisional) / total bill segments (9.2). The
   committee range uses the separate `decided_rate`, compliant / (compliant +
   non-compliant) (9.4).
2. **Committee range** (requested as "Top movers"): superseded by decision 8.
3. **Row unit** is the bill segment, so one row per committee referral. Copy keeps
   bills and bill segments distinct (4.1).
4. **Retention** keeps two publications of detail per committee by default, adjustable
   through config (8.5).
5. **Embeds** are fully public for now (11).
6. **Accounts** are not migrated. Users sign up again through a flow that records
   their email preferences (6.1).
7. **Display labels** are "No violation detected", "Provisional", and "Violation
   detected"; requirement chips are "Met", "Pending", "Not shown", and "Not
   applicable" (13.1). Code, contract, and database keep the client's names.
8. **Committee range** is an unnamed dot distribution, with names only on hover,
   focus, and in the linked table (9.4).
9. **Changelog** is the client's `CHANGELOG.md`, pushed through the ingest API and shown
   at `/changes`, with a footer link and a 14-day dashboard line for major and minor
   releases. Site changes are not listed (5.6).
