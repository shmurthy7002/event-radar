# Event Radar

Discover relevant upcoming public Luma events near Berkeley and the San Francisco
Bay Area. Preview locally or run bounded GitHub Actions jobs with durable state
and explicitly enabled Resend delivery. No Luma login, paid API, or LLM is used.
Discovery is incomplete; delivery is not guaranteed exactly once.

## Install and check

Use Python 3.11 or newer. On Windows:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
```

On Linux/macOS use `.venv/bin/python`. Runtime dependencies are PyYAML and Windows
IANA timezone data (`tzdata`); HTTP and Git integration use the standard library
and Git. Tests are offline and use fake providers; they cannot send real email.
In a restrictive sandbox, add `--basetemp output/pytest`. Git for Windows tests
may need permission to start local helper processes.

## Preview and configure

Copy `config.example.yaml` to ignored `config.yaml`, edit it, then run:

```powershell
python -m event_radar --config config.yaml --output output/preview
```

Use `--config config.example.yaml` for non-sensitive defaults. The command makes
bounded public requests and writes `digest.html`, `digest.txt`, and `results.json`.
Open the HTML locally. JSON retains matches, provenance, scores, warnings, and
digest-limit omissions. Plain preview does not read or mutate delivery state.

YAML controls discovery sources, date horizon, Berkeley origin, radius,
unknown-location/availability policies, request budgets, interests/synonyms,
per-keyword title points, negative keywords, minimum score, and digest length. Invalid types,
ranges, and keys fail early. Starts must lie in `[now, now + horizon_days)`;
distance must be at most the radius. Hidden coordinates are excluded by default;
sold-out/waitlisted events are retained unless configured otherwise. Display
times use America/Los_Angeles, including daylight saving offsets.

Each interest group awards its strongest matching title keyword's points, or
1 point for description-only matches; group contributions add together.
See [customizing relevance](docs/relevance.md) for adding/removing keywords and
groups, a worked scoring example, safe previews, and legacy YAML migration.
Word boundaries prevent AI/RL/ML matching inside other words.
Negative keywords exclude events; ties use start then ID. Unavailable descriptions
visibly use title-only scoring. State-aware runs retain completed cached
descriptions, including fetched-empty results; automatic refresh is not implemented.

## Local state and explicit send

Initialize only an intentional first run:

```powershell
python -m event_radar --init-state --state state/state.json
python -m event_radar --observe --state state/state.json --config config.yaml
python -m event_radar --pending-preview --state state/state.json --config config.yaml
```

`--observe` persists observations without marking delivery. `--pending-preview`
reads cached future events and suppresses acknowledged ones without mutation.
First seen means discovered by this service, not newly published on Luma.
Unemailed observations and length-limit omissions remain eligible until accepted
delivery or their event start passes.

Configure a verified sender/domain in [Resend](https://resend.com/docs/api-reference/emails/send-email).
Keep `EVENT_RADAR_EMAIL_API_KEY`, `EVENT_RADAR_EMAIL_FROM`, and comma-separated
`EVENT_RADAR_EMAIL_TO` only in environment variables, never YAML, Git, or logs.
After selecting and authorizing the intended recipient, send explicitly:

```powershell
python -m event_radar --send --state state/state.json --config config.yaml
```

`--send-empty` additionally opts into at most one nothing-new summary per UTC day.
Empty runs otherwise send nothing; total collection failure never sends an empty
message. Both HTML and plain text are sent. Provider acceptance/message ID is not
proof of inbox delivery. Preview, smoke, and default tests never send.

## Hosted operation

The **Event Radar operations** workflow offers manual `smoke`, `preview`,
`initialize`, and gated `send`. Read jobs use repository read permissions;
bootstrap/send use `contents: write` in `event-radar-delivery`. All modes share a
non-cancelling concurrency group. Validated `state.json` lives on the dedicated
`event-radar-state` branch, separate from application code.

Hosted sends publish a mobile-friendly digest of up to **30** pending matches
on public GitHub Pages, then email a short notification linking to its immutable
snapshot. `digest_length` controls the cap (1–100). Only provider acceptance of
that notification acknowledges the page's included events; omissions stay pending.
The page must be publicly fetched and match the saved HTML before email is sent.
Previous emailed snapshots remain on the dedicated `event-radar-pages` branch
and in every subsequent deployment. The root page links to the latest digest;
emails always use a batch-specific URL. See [Pages setup and recovery](docs/pages.md).
Local previews never publish or send; the existing local `--send` inline mode
and prepared inline batches remain supported for compatibility.

See the [operations guide](docs/operations.md) for secret setup, bootstrap and
migration, permissions, first-run verification, notifications, schedule controls,
and operational recovery.

The authorized daily time is **12:00 noon Pacific** (`America/Los_Angeles`),
automatically following daylight saving time (19:00 UTC in PDT / 20:00 UTC in PST).
GitHub may delay the scheduled start. The cron trigger
is enabled following issue #20's verified rollout. Manual send requires an
enabled delivery variable and typed confirmation; scheduled send also requires
rollout verification. See the rollout evidence for live tests and recipient confirmation.

Jobs have 20-minute limits. Normal hosted runs cap total HTTP attempts at 40,
pages/source at 3, details at 20, request timeouts at 10 seconds, retries at 1,
and backoff at 3 seconds. Smaller configured limits remain effective. Checkpoints
publish prepared intent before send and outcomes afterward, without force or
automatic history merging. Git commands have 30-second limits.

## Diagnostics and explicit smoke checks

```powershell
python -m event_radar --smoke-check --config config.example.yaml --output output/smoke
python -m event_radar --config config.yaml --summary-json output/run.json --summary-markdown output/run.md
```

Each smoke sample caps HTTP attempts at 15, pages/source at 2, details at 3,
timeouts at 10 seconds, retries at 1, and backoff at 3 seconds. Hosted smoke runs
separate Berkeley/SF samples and compares SF page/feed IDs before filtering.
Truncation and coverage differences are not exhaustive discovery claims; explicit
coordinates do not eliminate possible IP/location bias.

`--diagnostics-only` omits event artifacts/raw output, preserving the selected
operation. Hosted reports contain allowlisted counts and date/score audit samples
only, retained for seven days: no titles, IDs, raw keyword text, provider bodies,
credentials, or recipients.

| Exit | Meaning |
| --- | --- |
| 0 | Completed operation; partial discovery remains visibly flagged. |
| 2 | Invalid configuration, arguments, or bootstrap confirmation. |
| 3 | Total collection failure; no healthy empty run is implied. |
| 4 | Output/diagnostic write or hosted setup failure. |
| 5 | Smoke health failure; budget truncation alone is not failure. |
| 6 | Missing/corrupt/locked state, Git access failure, or push conflict. |
| 7 | Delivery gated, provider config/rejection, ambiguity, or recovery guard. |
| 8 | Provider accepted but acknowledgement/checkpoint persistence failed. |
| 9 | Pages publication or public-content verification failed; no email sent. |

Check exit codes before using files: failed runs can leave stale/partial local
artifacts. Never reset state to make failure appear empty.

## Development and fixture maintenance

PRs and main pushes check Python 3.11/3.14 on Ubuntu and Windows, installing the
package and running offline tests, lint/format, and CLI help. Default CI contacts
neither Luma nor Resend. Source interfaces stay in `collectors/luma.py`; models,
relevance, state, provider, orchestration, and diagnostics remain separate.
See [architecture.md](architecture.md) and [AGENTS.md](AGENTS.md).

After source changes, inspect a small explicitly budgeted public sample. Keep
raw captures only in ignored `output/`. Recreate metadata, cursor, event/location,
and rich-text shapes with synthetic data in `tests/fixtures/`; omit host/guest,
tracking, and private data. Add an offline regression, run checks, then repeat
the explicit smoke check. Unknown schemas must fail visibly.

Milestone 1 was verified October 28, 2026 at `f73da4b` with 126 tests and live
discovery checks. Milestone 2 verification on October 29 included 188 tests and
cached-description regressions at `e3d4643`. Live rollout evidence is recorded
in [docs/rollout.md](docs/rollout.md). The current milestone remains open until the user
closes it.

## License

[MIT](LICENSE). Original implementation by Event Radar contributors; no external
source code was copied into this repository. Dependencies/actions retain their
own licenses. Public Luma data is not licensed by this project's code license.
