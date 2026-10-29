# Event Radar architecture

## Goal and scope

Discover relevant upcoming public Luma events around Berkeley and the San
Francisco Bay Area and email newly discovered matches once a day. The service
should be inexpensive, inspectable, and usable without an LLM, frontend, paid
Luma API, or always-on personal computer.

The local proof of concept established that public web endpoints can supply
usable event data. The user has selected those endpoints instead of MCP.
All prototype code and artifacts have been removed. The new Python package and
development tooling are in place; collection and delivery are being implemented
in the ordered backlog. Sections below describe the target design unless noted.

## Data flow

The current CLI is read-only. It collects and normalizes, filters date/location/
availability, enriches only those eligible events to conserve detail requests,
ranks, and writes local HTML/text/JSON previews. `--config` and `--output` select
settings and artifacts. Exit codes are 0 for successful (possibly partial)
preview, 2 for invalid configuration, 3 for total collection failure, and 4 for
output failure. Partial warnings appear in console and artifacts. Failed runs
must not be treated as a fresh successful preview; old/partial artifacts may
remain in an output directory. State and email below are future milestones.

```text
Local CLI or GitHub Actions
  -> load configuration and persistent state
  -> collect public Luma discovery feeds
  -> normalize and merge records by stable event ID
  -> filter by date and location, rank by configured interests
  -> select relevant events without a successful delivery record
  -> render a digest or local preview
  -> send email when enabled
  -> persist state and delivery outcome
```

## Collection and normalization

Keep all undocumented URLs, request parameters, page parsing, and source schema
handling inside a Luma web adapter. Initially collect the San Francisco city
feed and AI and tech category feeds. Resolve discovery identifiers from public
page data rather than relying solely on fixed IDs. Use explicit location
selection wherever supported; category results can depend on the runner's IP.

The proof of concept exercised `/discover/get-paginated-events` and `/event/get`
on `https://api.lu.ma`, with discovery metadata from `https://luma.com/sf`,
`https://luma.com/ai`, and `https://luma.com/tech`. Treat these as observed
interfaces that can change, not supported public API contracts.

`collectors/luma.py` uses the standard-library HTTP transport without login,
cookies, or redirects. Each attempt (including metadata fetches and retries)
consumes the shared configured request budget. Timeouts and selected transient
statuses retry with capped exponential or Retry-After backoff; permanent errors,
invalid JSON, and exhausted budgets fail explicitly. Transport, sleep, and the
clock are injectable for offline testing. Responses are capped at 8 MB.

Discovery collection is implemented. Public `__NEXT_DATA__` metadata resolves
the configured place/category IDs, with expected kind and slug validation.
City requests use `discover_place_api_id`; category requests use
`discover_category_api_id` and the verified metadata slug. All feed requests
include explicit configured latitude/longitude. The unrelated `category_api_id`
parameter was observed to be silently ignored and must not be used. Follow
`pagination_cursor` with `pagination_limit=20`, validate `entries`/`has_more`,
and report missing/repeated cursors or page-budget truncation. No date filtering
occurs during collection. Nonempty pages with no parseable records fail visibly;
valid empty pages count as successful collection. Results retain per-source
diagnostics, including when a later page fails after usable earlier pages.

Use request timeouts, bounded retries for transient errors, rate-limit backoff,
and configurable page and detail-request budgets. Follow opaque cursors, detect
repeated or missing cursors, and expose truncated feeds. Do not assume records
are ordered by date when deciding whether to stop pagination. Fetch event
descriptions within an explicit budget and distinguish missing descriptions
from descriptions that have been fetched and are empty.

Description enrichment uses public `/event/get` responses and extracts plain
text from top-level `description_mirror` rich-text trees. It prioritizes earliest
start then stable ID, skips completed fetches, and counts retries against both
the detail and global HTTP attempt budgets. Failed/malformed details retain the
discovery event with `failed` status; empty trees produce `empty`, and events
omitted by a budget remain `not_fetched`. Coverage counts and warnings are returned.

Normalize each event to a stable ID, canonical registration URL, title,
description, UTC start and optional end, source timezone, city, neighborhood,
venue, coordinates, registration availability, and source provenance. Merge
duplicates across feeds by event ID. Record collection timestamps and parsing
warnings. One failed feed may produce a visibly partial result; total collection
failure must fail the run and must not send a nothing-new message.

The normalized `Event` model validates required fields and offset-aware times,
canonicalizes registration links to HTTPS `luma.com`, and retains IANA source
timezone metadata. Duplicate merge precedence is the latest collection time,
with canonical JSON as a stable tie-break. Missing optional metadata falls back
to earlier observations; completed description fetches outrank failed or
unattempted ones. Provenance and warnings are unioned. Conflicting required
metadata is reported, and coordinates are always selected as a pair.

## Configuration and relevance

Use YAML for non-sensitive settings: discovery sources, date horizon, Berkeley
origin coordinates, radius, unknown-location policy, interest groups and
synonyms, title and description weights, negative keywords, minimum score,
request budgets, and digest length. Validate configuration at startup. Keep
email credentials and recipient settings in environment variables.

Initial interests are robotics and embodied AI, RL and ML research, AI safety,
hackathons, and technical startups. Score title matches above description
matches, count synonyms once per interest group, use word boundaries for short
terms such as AI and RL, and let negative keywords exclude an event. Include
the matched terms and score contributions in each result.

Filter upcoming event starts within a configurable horizon. Measure geographic
distance from Berkeley; initially exclude unknown coordinates and report that
exclusion count. This policy can miss events with hidden locations. Keep
registration availability visible; make any sold-out or waitlist exclusion an
explicit setting. Render dates in America/Los_Angeles, not the runner timezone.

Filtering is implemented in `relevance.py`: starts must lie in the half-open
UTC interval `[now, now + horizon_days)`, with an injected aware clock. Distance
uses the haversine formula and mean Earth radius 6371.0088 km; the configured
radius boundary is included. Exclusion counts use the first applicable reason
in date, location, then availability order, so counts sum to excluded events.
Unknown-location inclusion and sold-out/waitlist exclusion are explicit settings.

Ranking contributes at most once per interest group: the title weight if any
title synonym matches, otherwise the description weight. Terms use Unicode word
boundaries and case folding; phrase whitespace is flexible. Overlapping terms
within a group do not multiply its score; independent groups can each contribute.
Negative matches in either field exclude the event. The minimum score is
inclusive. Ordering is score descending, start ascending, then stable ID.
Explanations retain terms from both fields and each group's points. Unfetched or
failed descriptions explicitly mark title-only scoring; fetched-empty is distinct.

`digest.py` renders HTML, plain text, and JSON without I/O. It revalidates links,
escapes every external HTML field, uses Pacific timezone labels plus UTC offsets,
and displays coverage warnings even when no matches remain. Selection uses the
ranking order and exposes omitted eligible IDs; JSON retains all eligible matches
with provenance, contributions, and inclusion status. Descriptions become short
single-line summaries. Rendering has no state or delivery side effects.

## State and delivery semantics

Start with a versioned JSON state file keyed by event ID, recording
`first_seen_at`, `last_seen_at`, and nullable `emailed_at`. Newly seen means
first discovered by this service, not newly published on Luma. Update event
metadata when it changes, and reevaluate relevance on later runs. A relevant
event remains eligible until delivery succeeds or its event date passes, even
if an earlier run saw it without emailing it.

Local preview mode must not change delivery state. A production run may persist
observations before sending, but must mark `emailed_at` only after the provider
accepts the message. Save state atomically. Keep an explicit batch identity and
provider message ID when available. Use provider idempotency support where
available and document its retention window.

An email accepted immediately before a crash or failed state save can be sent
again on retry. Provider acceptance also does not prove inbox delivery. Document
these limits and test recovery rather than promising exactly-once delivery.

Render a short HTML email with a plain-text alternative: title, local date and
time, neighborhood or venue, one-line description, match reason, and direct
registration link. Initially implement one provider adapter, choosing Resend
or SMTP at the delivery milestone. Send only when there are eligible matches;
make a nothing-new summary optional. Events omitted by a digest length limit
must remain eligible for a subsequent run.

## Scheduling and durable storage

Add a daily GitHub Actions workflow plus a manual trigger after local collection
and delivery are verified. Store credentials in Actions secrets. Scheduled
execution may be delayed; fixed UTC cron times also shift local time with
daylight saving changes. Choose and document the schedule at deployment.

Ephemeral runners must retrieve persistent state before every run and save it
afterward. The initial approach is a dedicated state branch in the repository,
with minimum required write permissions, serialized workflow runs, and explicit
handling of push conflicts. Read current state before sending; fail visibly if
state is unavailable or invalid. Treat post-send persistence failure as an
operational error with a duplicate-delivery risk. Validate this approach for
the target repository and keep private data out of committed state. Move to a
hosted store if repository permissions or reliability require it; do not depend
on an Actions cache for durable delivery history.

## Proposed project layout

```text
event_radar/          CLI and application modules
  collectors/        Luma public web adapter
  models.py          Normalized event records
  config.py          YAML parsing and validation
  relevance.py       Date, distance, and keyword rules
  state.py           Versioned state and atomic writes
  digest.py          HTML and text rendering
  delivery.py        Email provider adapter
tests/               Offline behavioral tests and sanitized fixtures
config.example.yaml  Non-sensitive starting configuration
.github/workflows/   Checks and, later, scheduled execution
```

## Implementation milestones

1. Implement the read-only local collector, event schema, YAML configuration,
   deterministic ranking, and JSON and HTML preview outputs. Add meaningful
   offline tests and an explicit live smoke-check command.
2. Add seen and delivered state, one email provider, and rerun and failure
   recovery tests. Keep preview as the safe local default.
3. Add daily Actions execution and durable state updates. Validate behavior on
   the runner, including source location bias, before enabling delivery.

Report per-source pages and event counts, malformed and excluded records,
deduplication, description coverage, matches, pending and delivered counts,
truncation, and errors. Use those signals to tune relevance and detect source
breakage. Public discovery is incomplete; source changes and request budgets
can hide events even when a run succeeds.
