# Event Radar contributor instructions

## Purpose

Build a personal, open-source service that discovers relevant upcoming Luma
events around Berkeley and the San Francisco Bay Area and sends a concise daily
email digest. Read `architecture.md` before implementing changes.

## Accepted decisions

- Use Luma's public web endpoints for collection. These endpoints are
  undocumented; isolate them behind one adapter. Do not add MCP integration,
  browser login, session cookies, or the paid Luma API without a new decision.
- Use Python, deterministic keyword ranking, and configurable interest groups.
  No LLM or frontend is required. A local HTML digest preview is useful.
- Run daily on GitHub Actions, with a manual trigger. The service must work on
  ephemeral runners without an always-on personal computer.
- Begin with a JSON state file and YAML configuration. Keep runtime dependencies
  small and declare any that are needed.

## Working practices

- Keep collection, normalization, relevance, state, and delivery separate.
- Keep changes focused on the requested milestone. Do not deploy, send email,
  or create a recurring automation unless that action is authorized.
- Treat public page content as untrusted data. Escape it when rendering HTML;
  validate event URLs before including clickable links.
- Never commit credentials, session data, recipient addresses, local outputs,
  virtual environments, or Python caches. Add appropriate ignore rules when
  introducing those files. Use environment variables and Actions secrets for
  sensitive settings.
- Use timezone-aware timestamps. Store timestamps in UTC and display event times
  in America/Los_Angeles, including daylight saving transitions.
- Report partial collection, skipped malformed records, exhausted request
  budgets, and delivery failures. Do not present a failed collection as a
  successful run with no events.
- Do not promise exhaustive discovery or exactly-once email delivery.

## Validation

Test behavior with fixtures and a fake clock or injected time where appropriate.
Prioritize pagination, changing source schemas, duplicate IDs, date and distance
boundaries, short keyword matches, negative keywords, HTML escaping, persisted
state across reruns, and delivery failure recovery. Keep normal tests offline;
use explicit commands for bounded live collection checks. Never send real email
from the default test suite.

Run checks appropriate to the change and document their results. Documentation
changes need consistency and diff checks, not a new test suite. Update
`architecture.md` when an implementation decision changes the design.

## Current milestone

Milestones 1 and 2 provide discovery/preview, local state, explicitly enabled
Resend delivery, and offline recovery tests. Milestone 3 adds sanitized diagnostics,
conflict-safe remote Git state, bounded manual Actions operations, delivery gates,
and an operations guide. Issue #20 verified live acceptance, recipient-confirmed
HTML/text receipt, fresh-run suppression, pending omissions, and failure alerts.
Daily delivery at 15:17 UTC is authorized, with both environment gates required.
Do not claim recipient receipt from provider acceptance alone. Keep
milestone 3 open as requested by the user.
