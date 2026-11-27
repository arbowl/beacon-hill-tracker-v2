# Beacon Hill Compliance Tracker

The public site for the Beacon Hill Compliance Tracker: a Flask + HTMX + Postgres app that
publishes what the public record shows about Massachusetts legislative committees following
the joint rules. The data comes from the `beacon` client
(`beacon-hill-compliance-tracker-v2`), which pushes one bundle per committee.

```
contract/     beacon-contract: the push wire format, shared with the client
tracker/      the site (routes, queries, templates, static files, SQL migrations)
tests/        contract tests, and site tests that run against a real Postgres
compose.yaml  local Postgres 16
```

## Setup

Requirements: [uv](https://docs.astral.sh/uv/) and Docker.

```
docker compose up -d          # Postgres on 127.0.0.1:54329, data in the v2_pgdata volume
copy .env.example .env        # cp on macOS/Linux; then set SECRET_KEY (32+ random characters)
uv sync
uv run flask --app tracker db migrate
uv run flask --app tracker users create you@example.org --role webmaster
uv run flask --app tracker keys create you@example.org --scope ingest --name client
```

The last command prints an ingest key once; store it for the client. Run every command from
the repository root, where `.env` is read. Environment variables override `.env`.

| Setting | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | required | Postgres connection URL |
| `SECRET_KEY` | required | Signs session cookies; at least 32 characters |
| `SECURE_COOKIES` | `true` | Set `false` only when serving plain http locally |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` | unset | Outgoing email; without them the contact form and sign-in say they are unavailable |
| `WEBMASTER_EMAIL` | unset | Where contact form messages go |
| `SITE_URL` | unset | Public base URL used in emailed sign-in links; sign-in is unavailable without it |
| `LOGIN_LINK_MINUTES`, `SESSION_DAYS` | `15`, `30` | Sign-in link lifetime; signed-in session lifetime |
| `API_REQUESTS_PER_MINUTE`, `API_KEYS_PER_USER` | `60`, `5` | Public API limit per read key; live read keys per account |
| `LOGIN_LIMIT_PER_EMAIL`, `LOGIN_LIMIT_PER_IP_PER_HOUR` | `3`, `20` | Sign-in email requests allowed per address per 15 minutes, and per network per hour |
| `RETAIN_DETAIL_PUBLICATIONS` | `2` | Publications per committee whose full detail is kept; also the rollback depth |
| `ABANDON_OPEN_RUNS_AFTER_HOURS` | `48` | Unpublished pushes older than this are discarded |
| `RETAIN_DUMPS_RECENT`, `RETAIN_DUMPS_MONTHLY` | `30`, `true` | Downloads kept per session and format: the newest builds, plus the first build of each month |

## Running

```
uv run flask --app tracker run                 # development, http://127.0.0.1:5000
gunicorn "tracker:create_app()"                # production (Linux/macOS)
uv run pytest                                  # creates and drops its own test database
uv run ruff check . && uv run ruff format --check .
```

### Publishing data

In the client's `beacon.toml`:

```toml
[site]
url = "http://127.0.0.1:5000"
```

Set `BEACON_SITE_KEY` to the ingest key, then run either `beacon run mvp` (which pushes
after reporting, unless given `--no-push`) or `beacon --committee J19 push`. A push uploads
each committee's newest `data/reports/<date>/<committee>.bundle.json.gz` and publishes them
in one transaction. Pushing the same evaluations twice changes nothing.

Before the run, the push sends the client's `CHANGELOG.md` to `PUT /api/ingest/v1/changelog`.
The site shows it at `/changes` and keeps every text a release has had. A changelog that
drops a release the site already shows, or dates one in the future, is refused, and then
nothing is pushed.

### Public API and embeds

Signed-in users create read keys on their watchlist. The API is described by
`/api/v1/openapi.json`:

```
curl -H "Authorization: Bearer bht_read_..." http://127.0.0.1:5000/api/v1/stats?committee=J19
```

Widgets need no key. Build a snippet at `/embed`, or frame `/embed/committee/J19?theme=dark`,
`/embed/chamber/H`, `/embed/all`, or `/embed/bill/194/H10` directly.

### Operations

```
uv run flask --app tracker runs list                         # recent pushes
uv run flask --app tracker runs rollback J19 --session 194   # withdraw a committee's latest publish
uv run flask --app tracker runs prune                        # also runs after every publish
uv run flask --app tracker users list --role webmaster
uv run flask --app tracker users set-role someone@example.org webmaster
uv run flask --app tracker users disable someone@example.org # their keys stop working
uv run flask --app tracker keys revoke bht_ingest_ab12cd34   # by the prefix shown at creation
```

### Scheduled jobs

A deployment needs these five Render cron jobs. Each is safe to rerun: one that finds
nothing new to do changes nothing and sends nothing.

| Command | Schedule | What it does |
|---|---|---|
| `flask --app tracker alerts scan` | daily | Records reminders for deadlines 7 days out |
| `flask --app tracker alerts send` | hourly | Emails each watcher one digest of what they have not been sent |
| `flask --app tracker emails weekly` | weekly | Sends the weekly summary, at most once per account per ISO week |
| `flask --app tracker runs prune` | daily | Deletes detail no longer retained and abandons stale pushes |
| `flask --app tracker dumps rebuild` | every 10 minutes | Rebuilds the downloads when the live data has changed |

Every job needs `DATABASE_URL` and `SECRET_KEY`, so give the cron jobs the same environment
as the web service. `alerts send`, `emails weekly` and `emails announce` also need
`SMTP_HOST`, `MAIL_FROM` and `SITE_URL`, and fail without them. Publishing does not build
the downloads, so without `dumps rebuild` they stay at the last data they were built from;
before the first publish it exits with "No data has been published".

Announcements are sent by hand:

```
uv run flask --app tracker emails announce notes/launch.txt  # subject on line 1; sent once per file name
```

Roles are granted only from these commands; no web route can change one. Only a webmaster
can hold an ingest key.

## The client contract

`contract/beacon_contract/v1.py` defines everything that crosses from the client to the
site: the run manifest, the committee bundle, the site's replies, the shared enums, the
label roll-up, and every validation rule. The site rejects a bundle that fails validation
with a 422 and never repairs data. The client depends on this package through a path
dependency in its `pyproject.toml`:

```toml
beacon-contract = { path = "../beacon-hill-tracker-v2/contract", editable = true }
```

So both repositories must sit side by side. Because the dependency is editable, a change
here is live in the client immediately.

### Rules

- Enum values must equal the client's (`RequirementStatus`, `ComplianceLabel`, `StepRole`,
  and so on). The client has a parity test for the roll-up
  (`tests/reporters/test_site_bundle.py::test_roll_up_matches_contract`).
- Postgres enum types in `tracker/migrations/` mirror the contract enums. Adding an enum
  value takes a new migration (`ALTER TYPE ... ADD VALUE`), never an edit to an applied
  one; `flask db migrate` refuses to run if an applied file changed.
- Reader-facing wording for every enum value lives in `tracker/display.py`. Add an entry
  there whenever a value is added.

### Making a change

Additive changes (a new optional field, a new enum value, a new ruleset) stay in `v1`:

1. Edit `v1.py`, and add a case to `tests/contract/test_v1.py` for any new rule.
2. If the site must store the new data, add a migration and update
   `tracker/ingest/service.py` (the COPY into the detail tables). If it appears on pages,
   update `tracker/publish/rebuild.py` too.
3. Update the client's `beacon/reporters/site_bundle.py` to fill it.
4. Refresh the fixtures from the client (below) and run both test suites.

A new ruleset needs an entry in `RULESET_REQUIREMENTS`. A new requirement id also needs a
value in the `requirement` Postgres enum, status and detail columns in `current_tenures`
if it gets a chip, and entries in `tracker/display.py`.

Breaking changes (renaming or removing a field, changing a meaning) get a new module,
`v2.py`, with `SCHEMA_VERSION = 2` and new endpoints under `/api/ingest/v2/`. Keep `v1`
accepted until the client has moved over, then remove it.

### Refreshing fixtures

`tests/fixtures/bundles/` holds real J19 and J45 bundles produced by the client. After a
contract change, regenerate and copy them:

```
cd ../beacon-hill-compliance-tracker-v2
uv run beacon --committee J19 --committee J45 report
copy data\reports\<date>\J19.bundle.json.gz ..\beacon-hill-tracker-v2\tests\fixtures\bundles\
copy data\reports\<date>\J45.bundle.json.gz ..\beacon-hill-tracker-v2\tests\fixtures\bundles\
```

Some tests assert counts from these files (for example, 791 J19 segments). Update them if
the refreshed data differs.
