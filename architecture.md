# Event Radar architecture

## Goal and scope

Discover relevant upcoming public Luma events around Berkeley and the San
Francisco Bay Area and email newly discovered matches once a day. The service
should be inexpensive, inspectable, and usable without an LLM, frontend, paid
Luma API, or always-on personal computer.

The local proof of concept established that public web endpoints can supply
usable event data. The user has selected those endpoints instead of MCP.
All prototype code and artifacts have been removed. The new Python package and
read-only collector, enrichment, filtering/ranking, preview CLI, and offline CI
are implemented, along with local state and explicitly enabled delivery. Sections
below describe the target design unless noted.

## Data flow

The CLI defaults to read-only preview. It collects and normalizes, filters date/location/
availability, enriches only those eligible events to conserve detail requests,
ranks, and writes local HTML/text/JSON previews. `--config` and `--output` select
settings and artifacts. Exit codes are 0 for successful (possibly partial)
preview, 2 for invalid configuration, 3 for total collection failure, and 4 for
output failure. Partial warnings appear in console and artifacts. Failed runs
must not be treated as a fresh successful preview; old/partial artifacts may
remain in an output directory. State and explicit email modes are described below;
manual Actions and remote state are implemented; the authorized daily trigger
requires both delivery and rollout-verification environment gates.

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
synonyms, per-keyword title points, negative keywords, minimum score,
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

Ranking sums independent group contributions. Within a group, the maximum
configured title-keyword points wins; with no title match, any description matches
award exactly 1 point total. This deliberately retains a group maximum so related
synonyms and overlapping phrases cannot inflate that interest, while allowing
specific title terms to outweigh broader ones. Title plus description matches
never add within a group. Repeated occurrences never multiply points; overlapping
terms across different groups can contribute independently. Terms use Unicode word
boundaries and case folding; phrase whitespace is flexible. Overlapping terms
within a group do not multiply its score; independent groups can each contribute.
Negative matches in either field exclude the event. The minimum score is
inclusive. Ordering is score descending, start ascending, then stable ID.
Explanations retain terms from both fields, all tied maximum scoring terms, and
each group's points. Unfetched or
failed descriptions explicitly mark title-only scoring; fetched-empty is distinct.

`digest.py` renders HTML, plain text, and JSON without I/O. It revalidates links,
escapes every external HTML field, uses Pacific timezone labels plus UTC offsets,
and displays coverage warnings even when no matches remain. Selection uses the
ranking order and exposes omitted eligible IDs; JSON retains all eligible matches
with provenance, contributions, and inclusion status. Descriptions become short
single-line summaries. Rendering has no state or delivery side effects.

`interests` uses group mappings of keyword/phrase to numeric title points in
[1, 1000]. Legacy all-list groups still load with their global title weight
(default 3) and description weight 1; other description weights require explicit
migration. Mixed schemas and ignored global weights on mapping groups fail.
YAML duplicate keys and normalized duplicate weighted terms are rejected rather
than silently overwritten. The example preserves previous production scores;
the editing and migration workflow is documented in `docs/relevance.md`.

## State and delivery semantics

`--send` is the only send-capable CLI operation. It locks state across collection,
selection, provider contact, and acknowledgement. Cached future unemailed events
are reevaluated along with current discoveries. `--pending-preview` reads the
same cached state and suppresses acknowledged events without mutating history.
Rediscovery updates current metadata while retaining completed cached descriptions,
including fetched-empty results, until a new completed description replaces them.
The cache is reused before enrichment and ranking and survives temporary eligibility
exclusions and observation-only updates. Reused descriptions consume no detail
requests; automatic description refresh is not implemented.
`--send-empty` optionally allows one empty summary per UTC day after usable
collection; total collection failure always aborts before sending.

The version-1 state schema accepts an additive `batches` map (older files remain
readable). A prepared batch persists the exact subject/HTML/text, selected IDs,
creation time, payload hash, envelope HMAC, and deterministic idempotency key
before send. Accepted/rejected batches retain outcome metadata but discard bodies.
Only provider-accepted included events get `emailed_at`; omitted and rejected
events remain pending. Prepared/ambiguous retries freeze the original payload,
require the same envelope and still-relevant events, and stop at 24 hours.
Pending events that expire or lose relevance require reconciliation rather than
silently changing a potentially accepted request. Post-acceptance save failure
exits 8 with duplicate risk; provider/config/recovery errors exit 7, state access
errors exit 6. All paths retain pending intent for recovery.

Explicit offline reconciliation records a provider-verified accepted message ID,
or a provider-confirmed non-acceptance. It never contacts the provider or sends.
Accepted reconciliation acknowledges only the batch's included IDs; confirmed
non-acceptance releases them for a new explicit run. Unknown outcomes stay
unresolved. Recovery tests inject interruption before send, after acceptance,
and after state commit, plus fsync/replacement failures, expiry, changed relevance,
payload corruption, lock contention, and provider-key retention expiry.

The first provider is Resend's HTTPS `/emails` API, using the standard library
and environment-only key/sender/recipients. Explicit send enablement and valid
settings are mandatory. Requests include HTML and plain text, with one attempt
per call and no redirects. 4xx rejection (except uncertain timeout/conflict
responses) is distinct from ambiguous network/5xx/malformed-success outcomes.
Errors never echo provider bodies, credentials, or addresses. Provider acceptance
returns a message ID; it does not prove inbox delivery. Tests use a fake provider.
Resend [idempotency keys](https://resend.com/docs/dashboard/emails/idempotency-keys)
last 24 hours and require identical request payloads. Retry orchestration must
persist the original message and key, bind its envelope with a keyed fingerprint,
and stop for operator reconciliation after the retention window. Rotated keys
or changed recipients invalidate that fingerprint; addresses never enter state.

Local version-1 JSON state is implemented in `state.py`. Explicit `--init-state`
creates an empty file without network access and refuses existing history.
`--observe` updates observations after a successful preview; ordinary previews
never read or mutate state. Unknown/missing/corrupt state fails with exit 6.
Writes validate normalized records, fsync a same-directory temporary file, and
atomically replace the destination under an exclusive local writer lock.
Windows sharing/access conflicts during replacement get at most three local
attempts (150 ms total backoff); this never repeats a provider request.
A leftover `.lock` is an operator recovery condition, never removed automatically
as stale. Verify there is no active writer before removing it. This local lock
does not serialize machines; Actions serialization belongs to milestone 3.
First-seen time persists, current metadata replaces prior observations, and
unemailed future events remain available for relevance reevaluation on later runs.

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
registration link. The implemented provider adapter uses Resend as described
above. Send only when there are eligible matches;
make a nothing-new summary optional. Events omitted by a digest length limit
must remain eligible for a subsequent run.

## Scheduling and durable storage

The operations workflow now supports manual smoke, persisted-state preview,
explicit bootstrap, and gated send. A shared concurrency group never cancels an
active run. Write jobs require the delivery environment and `contents: write`;
read jobs need only repository read access. Prepared/outcome remote checkpoints
run under the same local transaction as delivery. Artifacts are sanitized reports
only, with bounded date/score audit samples and seven-day retention.
The authorized 15:17 UTC daily cron trigger is active. Its Pacific
DST shift are documented; manual sends require explicit confirmation and a
delivery variable, and scheduled sends additionally require rollout verification.

`git_state.py` now stores `state.json` alone on the dedicated `event-radar-state`
branch, using an isolated bare Git cache rather than the application checkout.
Bootstrap is explicit and refuses existing history; an existing local state file
can be explicitly migrated. Every normal run fetches/validates current state.
Publication compares the expected remote head and pushes only a descendant
commit without force or automatic merging. Changed/deleted/unavailable heads
fail visibly. "Stale" means a superseded revision, not a wall-clock age limit.
Runner checkpoints can publish observations and prepared intent before provider
contact, then accepted/rejected outcomes afterward. A post-acceptance push error
retains remote prepared intent for exact-payload idempotent recovery. Independent
machines still need shared Actions concurrency and protected state-branch history.

Offline CI now checks Python 3.11/3.14 on Ubuntu/Windows for PRs and main pushes.
It uses no Luma requests or secrets. An explicitly invoked `--smoke-check` caps
live requests, reports description coverage, and compares SF page/feed IDs before
filtering; normal CI never invokes it. Daily execution uses the separate operations workflow.

The daily trigger follows verified live rollout and user authorization recorded
in `docs/rollout.md`. Store credentials in Actions secrets. Scheduled
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

## Project layout

```text
event_radar/          CLI and application modules
  collectors/        Luma public web adapter
  models.py          Normalized event records
  config.py          YAML parsing and validation
  relevance.py       Date, distance, and keyword rules
  state.py           Versioned state and atomic writes
  digest.py          HTML and text rendering
  delivery.py        Email provider adapter
  runner.py          Transactional delivery and reconciliation
  diagnostics.py     Sanitized operational reports
  git_state.py       Conflict-safe durable Git state
  actions.py         Bounded hosted-runner operations
tests/               Offline behavioral tests and sanitized fixtures
docs/operations.md   Setup, activation, monitoring, and recovery
config.example.yaml  Non-sensitive starting configuration
.github/workflows/   Offline checks and gated scheduled execution
```

## Current implementation status (October 29, 2026)

Milestones 1 and 2 provide local discovery, state, Resend, and offline recovery.
Milestone 3 adds diagnostics, durable Git state, bounded manual Actions, and an
operations guide. Issue #20 records live rollout evidence: two disjoint accepted
batches on fresh runners, durable acknowledgement, recipient-confirmed HTML/text
receipt, and confirmed failure alerts. Daily delivery at 15:17 UTC is authorized.
Keep milestone 3 open as requested; provider acceptance never substitutes for
receipt evidence.

## Implementation milestones

Milestone 1 was verified October 28, 2026; subsequent implementation and rollout
status are described above and tracked in GitHub.

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

`diagnostics.py` provides allowlisted JSON and Markdown run summaries. The CLI
can emit diagnostics only for CI, omitting event artifacts. Outcomes distinguish
healthy empty/partial discovery, collection failure, rejection, ambiguous
acceptance, and post-send persistence failure. Numeric counts never include raw
event/person/provider payloads; unavailable counts stay unavailable after failure.
