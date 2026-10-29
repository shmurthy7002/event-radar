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
`models.py` will own normalized records; `config.py` validated YAML;
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
