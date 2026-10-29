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
not read or use them. Sending, persistent state, and scheduling are later issues.
Keep local artifacts in ignored `output/` and state working copies in `state/`.

## Implementation boundaries

`collectors/luma.py` owns undocumented Luma transport, metadata, schema parsing,
and enrichment. `models.py` owns normalized events; `config.py` validates YAML;
`relevance.py` filters and ranks; `digest.py` renders; and `pipeline.py` connects
read-only preview. Planned `state.py` and `delivery.py` belong to later issues.
See `architecture.md` and `AGENTS.md` for decisions and remaining milestones.

## Live verification evidence

October 28, 2026: nine HTTP attempts fetched two pages and 40 records per source,
yielding 109 unique events, 11 duplicates, no malformed records, and expected
page-budget truncation on every feed. This verifies the observed interfaces from
this machine, not exhaustive discovery or coverage on an Actions runner.
Fixtures use synthetic event data and contain no attendee/host data.
