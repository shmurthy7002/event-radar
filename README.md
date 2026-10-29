# Event Radar

Discover relevant upcoming public Luma events near Berkeley and the San
Francisco Bay Area. The local CLI produces inspectable HTML, text, and JSON
previews. Discovery is incomplete and uses undocumented public web interfaces.
No Luma login, credentials, or always-on computer is needed for preview.

## Development setup

Use Python 3.11 or newer. From a fresh checkout:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
```

On Linux/macOS replace `.venv\Scripts\python` with `.venv/bin/python`.
Windows installs `tzdata` for IANA timezones. PyYAML is the only other runtime
dependency; HTTP uses the standard library. Ordinary tests run offline and never
send email. In a restrictive sandbox, add `--basetemp output/pytest` to pytest.

## Local preview

State can be explicitly initialized with `python -m event_radar --init-state
--state state/state.json` (one command). Add `--observe --state state/state.json`
to the preview command to persist observations without marking anything sent.
Plain preview does not touch state. Missing/corrupt state or persistence failures
exit 6; initialization never overwrites existing delivery history. Working state
and lock files belong in the ignored `state/` directory. Atomic writes protect
the prior file on failed replacement. A stale `.lock` requires checking that no
writer is running before manual removal.

Copy `config.example.yaml` to ignored `config.yaml` and edit it, then run:

```powershell
.venv\Scripts\python -m event_radar --config config.yaml --output output/preview
```

To try the checked-in non-sensitive defaults directly, use
`--config config.example.yaml`. The command makes bounded public HTTP requests
and writes `digest.html`, `digest.txt`, and `results.json` in the output directory.
Open the HTML file locally. JSON retains all eligible matches with provenance,
scores, coverage, and IDs omitted by the digest length limit.

The command collects all fetched records, filters date/location/availability,
enriches eligible events within the request budget, then ranks and renders.
There is no email or delivery-state mutation. Exit codes: 0 successful preview
(including visibly partial discovery), 2 invalid configuration/arguments,
3 total collection failure, 4 output-write failure. Check the exit code before
using artifacts; an unsuccessful run can leave old or partially written files.

## Configuration and matching

YAML controls SF/AI/tech sources, date horizon, origin coordinates, radius,
availability policy, request budgets, interests, weights, and digest length.
Unknown keys and invalid types/ranges fail at startup. Unknown coordinates are
excluded by default; hidden locations can therefore hide relevant matches.
Sold-out and waitlisted events are retained unless explicitly excluded.

Starts must be in `[now, now + horizon_days)` and distance must be at most the
configured radius. Each interest group scores once, using the title weight when
its title matches or otherwise the description weight. Unicode word boundaries
prevent AI/RL/ML matching inside other words. Negative keywords in either field
exclude an event. Ties use start then ID. Preview times use America/Los_Angeles,
including daylight saving offsets. Missing and failed descriptions visibly use
title-only scoring; a fetched-empty description is tracked separately.

Metadata and retry attempts count against the total HTTP budget; description
attempts also count against the detail budget. A zero detail budget disables
enrichment. More pages than the page budget, missing/repeated cursors, malformed
records, and source failures produce explicit diagnostics. Total source failure
raises instead of presenting a healthy empty run.

Credentials, sender, and recipients belong only in `EVENT_RADAR_EMAIL_API_KEY`,
`EVENT_RADAR_EMAIL_FROM`, and comma-separated `EVENT_RADAR_EMAIL_TO`. Preview does
not read or use them. Local state and explicit sending are available; scheduling
and remote durable state belong to later issues.
Keep local artifacts in ignored `output/` and state working copies in `state/`.

## Explicit local sending

Initialize state intentionally, configure the three email environment variables,
and inspect pending matches with `python -m event_radar --pending-preview
--state state/state.json --config config.yaml` (one command). When ready to send,
replace `--pending-preview` with `--send`. Sending never occurs in ordinary preview
or smoke mode. `--send-empty` is an additional opt-in for one empty summary per
UTC day; by default empty runs send nothing. No scheduled delivery is enabled.

The sender saves a stable batch and exact payload before contacting Resend.
Only included events are acknowledged after provider acceptance. Length-limited
omissions, rejected requests, and earlier observations remain eligible for later
runs. State retains provider message ID and batch outcome without sender or
recipient values. A keyed fingerprint binds recovery to the same API key/envelope.

State-aware runs reuse completed cached descriptions, including fetched-empty
results, before enrichment and ranking. Rediscovery updates other event metadata;
missing or failed details do not erase a cached description. Cached descriptions
also survive observation-only updates and temporary eligibility exclusions.
Automatic description refresh is not implemented.

October 29, 2026 verification: all 188 offline tests pass, including 60 state,
provider, orchestration, and recovery cases. Regressions cover cached content and
fetched-empty descriptions, negative-keyword exclusions after detail-budget
exhaustion, changed metadata, and temporary location exclusions. Delivery checks
use fake transports/providers; no real email is sent. Completion is tracked in
[milestone 2](https://github.com/shmurthy7002/event-radar/milestone/2).

Exit 6 means state access/write failure; exit 7 means provider configuration,
rejection, ambiguous outcome, or recovery guard; exit 8 means acceptance occurred
but acknowledgement could not be saved. For uncertain outcomes, retain state
and rerun explicitly within 24 hours to reuse the same payload/key. Changed
recipients/API key, expired/no-longer-relevant events, or a batch older than
24 hours stop automatic retry for operator reconciliation. Never delete or
reinitialize state to bypass these guards: the provider may already have accepted
the message. Provider acceptance is not proof of inbox delivery.

The email adapter uses [Resend](https://resend.com/docs/api-reference/emails/send-email).
Before explicitly enabling sending, configure a verified sender/domain in the
provider and set `EVENT_RADAR_EMAIL_API_KEY`, `EVENT_RADAR_EMAIL_FROM`, and
comma-separated `EVENT_RADAR_EMAIL_TO` in the environment. Keep them out of YAML
and logs. The adapter requires explicit send enablement, sends both HTML and
plain text, and reports acceptance IDs separately from inbox delivery.
It never automatically retries. Resend retains idempotency keys for 24 hours;
ambiguous requests must reuse the exact persisted payload/key within that window
or be reconciled manually afterward. Preview and automated tests cannot send.

## Delivery failure recovery

Keep the state file and check the provider's message/request logs before resolving
an uncertain outcome. The batch key is the key under `batches` in local state.
After provider verification, use **one** of these offline commands:

```powershell
python -m event_radar --state state/state.json --batch-id BATCH_KEY --reconcile-accepted PROVIDER_MESSAGE_ID
python -m event_radar --state state/state.json --batch-id BATCH_KEY --reconcile-not-accepted
```

The first records confirmed acceptance and suppresses the included events. The
second releases a batch only when the provider confirms it was not accepted;
events remain pending for a later explicit send. Neither command sends email or
contacts the provider. If the outcome remains unknown, leave the batch unresolved.
Reinitializing/deleting history or falsely asserting non-acceptance can duplicate
an already accepted message, especially beyond the 24-hour idempotency window.

| Failure point | Persisted outcome and recovery |
| --- | --- |
| Observation/prepared-state write fails | Provider is not contacted; fix storage, then rerun. |
| Crash with prepared intent, or connection outcome unknown | Retain exact batch; explicit retry within 24 hours reuses payload/key. |
| Known provider rejection | Events stay pending; fix configuration/provider problem and rerun. |
| Provider accepts, acknowledgement save fails (exit 8) | Pending intent survives; retry within the window or reconcile verified acceptance. |
| Acceptance and state commit succeed, then process crashes | A later run suppresses acknowledged events, even after provider keys expire. |
| Pending batch is too old, changed envelope, expired, or no longer relevant | Automatic retry stops; verify the provider outcome and reconcile explicitly. |

A hard process termination can leave `state.json.lock`. Verify that no writer is
running before removing that local lock; never delete the state file as cleanup.
The local lock protects one filesystem, not multiple runners. Cross-runner
serialization and durable remote state remain milestone 3 work. Provider
acceptance still does not prove inbox delivery, and exactly-once email delivery
is not guaranteed.

## Implementation boundaries

`collectors/luma.py` owns undocumented Luma transport, metadata, schema parsing,
and enrichment. `models.py` owns normalized events; `config.py` validates YAML;
`relevance.py` filters and ranks; `digest.py` renders; and `pipeline.py` connects
read-only preview. `state.py` persists observations/batches, `delivery.py` owns
the provider, and `runner.py` coordinates explicit delivery.
See `architecture.md` and `AGENTS.md` for decisions and remaining milestones.

## Live verification evidence

October 28, 2026: nine HTTP attempts fetched two pages and 40 records per source,
yielding 109 unique events, 11 duplicates, no malformed records, and expected
page-budget truncation on every feed. This verifies the observed interfaces from
this machine, not exhaustive discovery or coverage on an Actions runner.
Fixtures use synthetic event data and contain no attendee/host data.

## Offline CI and bounded live smoke check

Pull requests and pushes to main run installation, offline tests, Ruff lint and
format checks, and CLI help on Python 3.11/3.14 on Ubuntu and Windows. The workflow
uses read-only repository permissions, pinned official actions, and no secrets.
Tests block real HTTP/socket connections, so Luma availability cannot affect CI.
The official [checkout](https://github.com/actions/checkout) and
[setup-python](https://github.com/actions/setup-python) actions provide runner setup.

Run the live check explicitly; it is never part of default tests or CI:

```powershell
.venv\Scripts\python -m event_radar --smoke-check --config config.example.yaml --output output/smoke
```

This command caps configured budgets at two pages per source, 15 total HTTP
attempts, three detail attempts, one retry, ten-second timeouts, and three-second
backoff. Smaller configured limits remain in effect. It requires the SF city
source and supports the SF/AI/tech sources. It writes only sanitized counts,
schema diagnostics, and warnings in `smoke.json`, with no event/person payloads.
It compares city-page event IDs with all fetched city-feed IDs **before relevance
filtering**. Page-only/feed-only counts describe a bounded sample; neither source
is exhaustive. Exit 5 means a source/schema/detail health failure; expected budget
truncation alone is not failure. Exit codes 2/3/4 retain their usual meaning.

October 28, 2026 smoke check: 12/15 HTTP attempts, 40 records per source,
109 unique events, 11 duplicates, three content descriptions, zero failed details
and zero malformed records. All 20 SF page IDs overlapped the 40 city-feed IDs
(page-only 0, feed-only 20). All feeds and detail coverage reported expected
budget truncation. This check ran locally, with no email or persistent state.

Milestone 1 verification on October 28, 2026: all 126 offline tests, Ruff lint and
format checks, CLI help, and all four main-branch CI jobs passed at `f73da4b`.
A fresh bounded smoke check reproduced the counts above. A separate bounded
preview produced 10 matches and all three artifact formats, with zero malformed
records or failed details and explicit budget warnings. Milestone 1 is complete;
state, email, and scheduled execution remain future work.

To refresh fixtures after a source change, inspect only the public adapter paths
with a small explicit request budget. Keep raw captures in ignored `output/`.
Compare metadata keys, page cursors, nested event/location fields, and rich-text
shape. Recreate the shape with synthetic IDs, titles, addresses, and timestamps
under `tests/fixtures/`; omit host/guest data, tracking values, and personal data.
Add an offline regression for the changed shape, run the complete suite, then
repeat the bounded smoke check and record counts/warnings. Never silently treat
an unfamiliar response schema as an empty successful result.
