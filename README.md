# Event Radar

Personal discovery of relevant upcoming public Luma events near Berkeley and
the San Francisco Bay Area. Discovery is incomplete and uses undocumented public
web interfaces. No credentials or Luma login are needed.

## Development setup

Use Python 3.11 or newer. From a fresh checkout, create an isolated environment:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m event_radar --help
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
```

On Linux/macOS replace `.venv\Scripts\python` with `.venv/bin/python`.
Windows installs `tzdata` for IANA timezone support. PyYAML is the only other
runtime dependency; HTTP uses the Python standard library. Tests run offline
and must never send email. Ruff checks formatting and basic Python errors.

## Implementation boundaries

`event_radar/collectors/` owns public Luma HTTP, metadata, and schema handling.
`models.py` owns normalized records; `config.py` validates YAML. Planned modules:
`relevance.py` filtering/ranking; `digest.py` rendering; `state.py` durable
observations; and `delivery.py` provider communication. These modules are added
in their respective issues. The entry point currently displays help/version;
the complete read-only preview command is issue #10.

Keep local configuration in ignored `config.yaml`, preview artifacts in
`output/`, and working state in `state/`. Credentials, sender, and recipients
belong in environment variables, never in checked-in configuration. See
`architecture.md` and `AGENTS.md` for decisions and implementation milestones.

## Configuration

Copy `config.example.yaml` to `config.yaml` and edit non-sensitive settings.
`event_radar.config.load_config(path)` validates the file before use and rejects
unknown settings. Budgets cap pages per source, total HTTP attempts (including
retries), and later description requests. A zero detail budget disables
enrichment. Title weight must exceed description weight. Unknown coordinates
are excluded by default; sold-out and waitlisted events are retained by default.

Email settings are loaded separately from `EVENT_RADAR_EMAIL_API_KEY`,
`EVENT_RADAR_EMAIL_FROM`, and comma-separated `EVENT_RADAR_EMAIL_TO`. YAML cannot
set these values. An explicitly injected environment mapping replaces the
process environment for tests. No email adapter exists yet; send-time validation
belongs to issue #13. Configuration and settings loading have no delivery effects.

## Read-only discovery API

The package now collects SF, AI, and tech feeds, normalizes records, and merges
duplicates. Ranking, enrichment, preview rendering, state, and email are later
issues. All undocumented source parsing stays in `collectors/luma.py`.

For an explicitly requested bounded live check, run this Python snippet in the
installed environment (ordinary tests never call Luma):

```python
from dataclasses import replace
from event_radar.config import load_config
from event_radar.collectors.luma import collect, TotalCollectionFailure

config = load_config("config.example.yaml")
config = replace(
    config,
    requests=replace(
        config.requests,
        pages_per_source=2,
        total_requests=9,
        retries=0,
        detail_requests=0,
    ),
)
try:
    result = collect(config)
except TotalCollectionFailure as error:
    for source in error.result.sources:
        print(source)
    raise
for source in result.sources:
    print(source)
print(len(result.events), result.duplicate_count, result.requests, result.partial)
```

Metadata fetches and retries count toward the global budget. Two pages per feed
deliberately truncate discovery and produce visible warnings; this is a health
check, not exhaustive coverage. Partial source errors and skipped malformed
records remain visible in `result.sources`. Total failure raises an exception
with the same diagnostic result; a valid empty feed is a successful empty result.
Descriptions remain explicitly `not_fetched` until enrichment is implemented.

Local validation on October 28, 2026: nine HTTP attempts fetched two pages and
40 records per source, yielding 109 unique records, 11 duplicates, no malformed
records, and expected page-budget truncation on all three feeds. This verifies
the observed interfaces from this machine; it does not establish runner coverage
or eliminate location bias. Fixtures use synthetic event data matching the
observed shape and contain no attendee/host data.
