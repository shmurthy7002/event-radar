# GitHub Pages digests

Daily and manually confirmed hosted sends select up to `digest_length` events
(default 30, configurable from 1 to 100) in score-descending, start-ascending,
stable-ID order. They publish a static digest and send a short HTML/text email
with its count, Pacific date, and immutable snapshot URL. With 45 matches, the
first 30 appear on the page and are acknowledged only after provider acceptance;
the other 15 remain pending while upcoming and relevant. Publishing alone never
acknowledges events. No clicks, opens, accounts, or analytics are tracked.

## One-time setup

1. Preserve existing delivery state on `event-radar-state`. Do not reinitialize it.
   Stop sending while setting up hosting (`EVENT_RADAR_DELIVERY_ENABLED=false`),
   and let active runs finish before changing settings.
2. Explicitly create the public archive branch with ordinary authorized Git
   authentication. This writes only `.nojekyll` and a fixed initial homepage:

   ```powershell
   python -m event_radar.pages initialize --remote https://github.com/OWNER/REPO.git
   ```

   It refuses an existing archive. Normal delivery fails if the archive is
   missing; it never silently bootstraps lost history.
3. In repository Settings → Pages, select **Deploy from a branch**, branch
   `event-radar-pages`, root `/`. Use the supplied HTTPS `OWNER.github.io/REPO/`
   address (or `OWNER.github.io/` for a user site), without a custom domain.
   Set environment variable `EVENT_RADAR_PAGES_BASE_URL` in `event-radar-delivery`
   to that URL. Only HTTPS GitHub Pages URLs with an optional project-site path
   are accepted; credentials, ports, query strings, fragments, encoded paths,
   and redirects are rejected.
4. The write job needs `contents: write` for the two Git branches and
   `pages: write` for requesting/checking Pages builds. Read-only jobs retain
   `contents: read`. Setup requires repository Pages administration permission;
   daily operation does not. No PAT, domain purchase, or extra runtime package
   is required. Both existing delivery gates and the noon Pacific schedule remain.
5. Re-enable delivery only with authorization. Dispatch `send` with
   `SEND_CONFIGURED_RECIPIENT` for the bounded live acceptance check, then verify
   receipt separately from provider acceptance. The optional `send_empty` input
   permits one explicit empty-summary notification per UTC day; normally zero
   eligible events means no page or notification. A failed collection still fails.

[GitHub documents branch publishing](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
and the [Pages build API](https://docs.github.com/en/rest/pages/pages#request-a-github-pages-build).
Workflow-token pushes do not automatically trigger a Pages build, so the adapter
explicitly requests one. It then makes up to 30 public GET checks, with 10-second
request timeouts and 10-second spacing, while checking build status. Email is
blocked until an unauthenticated 200 response exactly matches the saved HTML.
An artifact upload or successful build status alone is insufficient. This uses
the existing 20-minute job limit; API/Git calls have their own bounded timeouts.

## Archive and disclosure boundaries

The durable `event-radar-pages` branch holds **only** `.nojekyll`, `index.html`,
and `digests/YYYY-MM-DD/HASH.html`. The date is Pacific; the identity includes
creation time, selected IDs, and page content hash. Same-day runs have distinct
identities. Pages deployments rebuild from the entire branch, retaining every
snapshot indefinitely until an explicit operator retention change. Artifacts
and caches are not the archive. Back up/protect branch history against deletion
and force pushes; monitor repository/Pages size limits and build-rate limits.
There is no automatic pruning. Changing retention or deleting the repository/site
can break old links; choose a migration before doing so.

The archive adapter only inserts safe-rendered public digest HTML and a fixed
latest link. It rejects unexpected paths, executable/symlink entries, immutable
snapshot collisions, and stale/racing pushes. It never uploads an output folder,
state JSON, profiles/configuration files, provider bodies, environment settings,
or internal JSON diagnostics. Public event titles, descriptions, locations,
relevance explanations, and collection warnings are intentionally public. Event
text is escaped, registration links revalidated, and dates shown in Pacific time.

All hosted modes share `event-radar-delivery-state` concurrency, without canceling
an active run. The state lock also covers publication and provider contact.
Every archive update preserves prior files and pushes without force; an external
writer race fails rather than merging or replacing history. Do not introduce an
independent deployment workflow or manually push during active delivery.
The latest entry point may change; email links always target immutable snapshots.
Retrying an archived snapshot verifies it without moving latest backwards.

## Recovery and migration

Version-1 state remains readable. New batches add optional `snapshot` metadata:
immutable path/URL, exact HTML, and SHA-256. The batch key binds the snapshot's
identity and hash as well as the original envelope and exact notification payload.
This full intent is checkpointed to durable state **before** archive push/build
or provider contact. Old inline batches retain their original key/payload and
retry inline even after Pages is enabled. Do not rewrite or reinitialize them.

| Failure | Action |
| --- | --- |
| Missing archive, invalid source/base URL, permissions, push conflict | Fail visibly with no email. Restore trustworthy archive/settings and retry the prepared batch. |
| Build failure or propagation/content mismatch (exit 9) | No email or acknowledgement. Inspect Pages builds and public availability; retry the frozen snapshot within its window. |
| Page published, email definitively rejected | Page may remain public but events stay pending. Fix settings; a new batch may select them again with a new snapshot. |
| Ambiguous acceptance or interruption | Preserve original page, URL, notification, envelope, and idempotency key. Retry only within the existing 24-hour window and relevance guards. |
| Acceptance then acknowledgement/checkpoint failure (exit 8) | Preserve remote prepared intent; verify provider outcome and reconcile or retry idempotently. Never delete the original page. |
| Batch exceeds retry window, changed envelope/site, expired/irrelevant events | Stop; reconcile confirmed acceptance or confirmed non-acceptance using the operations guide. Never rerank a possibly accepted request into the same identity. |

Prepared retries always reuse the exact saved page and notification; collection
and relevance checks may still run as existing safety guards. Accepted/rejected
notification bodies are discarded, but snapshot metadata/content remain for
integrity/recovery. A page's existence does not prove an email was accepted or
received. Provider acceptance acknowledges exactly that batch's IDs, with no
exactly-once or inbox-delivery guarantee.

For a rollback, disable delivery and let active runs finish. Retain both Git
branches and all emailed snapshots. Restore a trusted archive revision through
a new descendant commit and request a rebuild; do not force-push or discard
newer snapshots. Rolling application code back before snapshot support cannot
read new batches; keep this compatible state reader/reconciler until all new
batches are resolved. Use [operations reconciliation](operations.md) for durable
state updates from the same captured revision.

Local preview never deploys. The legacy local `--send` mode still sends inline;
hosted manual `send` creates snapshots with the same publication/state guarantees
as scheduled sends. Hosted jobs always read the tracked daily configuration.
Temporary local profiles remain local unless deliberately deployed by a reviewed
configuration change; they are never copied to the public archive.
