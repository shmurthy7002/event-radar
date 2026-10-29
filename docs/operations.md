# Event Radar operations

## Safe first-run checklist

1. Install and run offline checks. Review non-sensitive YAML; keep credentials
   and recipient addresses out of configuration and command arguments.
2. Inspect a local preview and explicit smoke check. Check Pacific dates,
   relevance explanations, hidden-location exclusions, and coverage warnings.
3. Determine whether delivery history already exists. Migrate it rather than
   bootstrapping empty over lost, inaccessible, or ambiguous history.
4. Bootstrap durable state explicitly. Fetch from a fresh cache and run the
   manual persisted-state preview before enabling email.
5. Configure the delivery environment below. Select and authorize the intended
   recipient before the first real-send test.
6. Run one manual send. Check provider acceptance, then have the recipient verify
   useful HTML and a plain-text MIME alternative. Acceptance alone is insufficient.
7. Repeat from a fresh runner: acknowledged events must be suppressed, omissions
   remain pending, and history survive. Review recovery and failure notifications.
8. Record issue #20 evidence, then activate daily execution only with rollout
   authorization. Leave the milestone open until the user closes it.

## GitHub settings

Create [environment `event-radar-delivery`](https://github.com/shmurthy7002/event-radar/settings/environments),
restricted to main with appropriate required reviewers. Configure environment
secrets directly in GitHub:

| Secret | Purpose |
| --- | --- |
| `EVENT_RADAR_EMAIL_API_KEY` | Resend key authorized for sending. |
| `EVENT_RADAR_EMAIL_FROM` | Verified sender/domain address, optional display name. |
| `EVENT_RADAR_EMAIL_TO` | Intended recipients, comma-separated bare addresses. |

Do not paste values into chat, YAML, issues, artifacts, or logs. Initially set
environment variables `EVENT_RADAR_DELIVERY_ENABLED=false` and
`EVENT_RADAR_ROLLOUT_VERIFIED=false`. The implicit `GITHUB_TOKEN` handles Git
state access; no Luma OAuth, browser login, cookies, or personal token is needed.
Preview/smoke need only `contents: read`; initialize/send need `contents: write`.
Organization/repository policies must permit those writes. Protect state history
against deletion/force-push without blocking ordinary workflow descendant updates.
Do not require a state-branch PR that leaves accepted email unpersisted. Use a
hosted store if repository policy cannot support the chosen state model.

## Bootstrap and migration

For a genuine first run with no history, dispatch **Event Radar operations** with
mode `initialize` and confirmation `INITIALIZE_EMPTY_STATE`. Existing history is
never overwritten. For existing local history, migrate it explicitly instead:

```powershell
python -m event_radar.git_state initialize --remote origin --from-state state/state.json
python -m event_radar.git_state fetch --remote origin --state state/durable.json
```

Use ordinary authorized Git authentication locally, without credentials embedded
in the remote URL. The separate bare cache defaults to ignored `state/git-cache/`.
Remote state contains normalized public event metadata and keyed envelope
fingerprints, not credentials/sender/recipient settings. Pending messages retain
their exact payload; accepted/rejected bodies are discarded.

Normal retrieval never initializes missing state. Publication checks the expected
head and pushes without force or history merging. Corruption, access failure,
changed/deleted heads, and races fail visibly. Staleness means a superseded
revision, not an arbitrary age limit. Observations/prepared intent are published
before provider contact, then outcomes afterward. Actions caches are not durable
delivery history.

## Manual modes and schedule controls

Use `smoke` for bounded Berkeley/SF samples without state mutation, or `preview`
for pending matches from persisted state. Read run summaries and sanitized JSON.
Every mode shares `event-radar-delivery-state` concurrency with
`cancel-in-progress: false`. GitHub may replace a pending queued run; active sends
are not canceled. Dispatch order is not a delivery guarantee. See
[concurrency documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

For an authorized first email test, enable `EVENT_RADAR_DELIVERY_ENABLED`, leave
rollout verification false, and dispatch `send` with `SEND_CONFIGURED_RECIPIENT`.
Unavailable gates/settings/state fail before provider contact. Keep cron disabled
while checking receipt and rerun suppression.

After issue #20 validation and send/deployment authorization, set
`EVENT_RADAR_ROLLOUT_VERIFIED=true` and uncomment the schedule in
`.github/workflows/event-radar.yml` through a reviewed PR. The proposed fixed time
is **15:17 UTC**, 08:17 PDT / 07:17 PST. Scheduled jobs run on main, may be
delayed/dropped, and public-repository schedules may stop after 60 inactive days.
See [schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

To disable delivery, set the environment's `EVENT_RADAR_DELIVERY_ENABLED=false`;
remove/comment cron to stop recurring jobs. Let active sends finish rather than
canceling during acknowledgement. Before re-enabling, inspect current state,
unresolved batches, and provider outcomes.

## Monitoring and recovery

Enable Actions failure notifications in the operator's account settings. The
workflow emits a failing job, explicit error annotation, and sanitized reports.
Actual email/web notification receipt depends on account settings. See
[workflow notifications](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs).
Artifacts retain seven days of metrics, not state backups. Missing metrics after
failure mean unavailable rather than zero successful events.

| Problem | Action |
| --- | --- |
| Endpoint/schema changed | Stop trusting empty output; inspect bounded public data and refresh synthetic fixtures. |
| Partial discovery/budget reached | Inspect per-source/detail counters and warnings; adjust within runtime limits. Don't claim exhaustive coverage. |
| DNS/TLS/HTTP failure without OAuth | Check source availability/runner connectivity. Don't implicitly add login, cookies, or paid API. |
| Provider auth/config/rejection | Fix sender/key/settings; events stay pending. No blind transport retry occurs. |
| Missing/inaccessible/corrupt state | Restore trustworthy history/access before sending; never auto-initialize. |
| Stale local lock | Verify no writer exists, then remove only the `.lock`; preserve JSON history. |
| Push conflict or branch deletion | Fetch/compare revisions and provider outcomes. Never force-push, silently merge, or bootstrap over uncertainty. |
| Acceptance followed by persistence failure (exit 8) | Preserve local result and remote prepared intent; retry exactly within the window or reconcile verified acceptance. |
| Unknown outcome after 24 hours, changed envelope, expired/irrelevant event | Stop automatic retry. Verify provider logs and reconcile explicitly. |

Resend [idempotency retention](https://resend.com/docs/dashboard/emails/idempotency-keys)
is 24 hours. An explicit retry within it reuses the original payload/key. After
it, uncertain requests may be processed again. Local acknowledgements suppress
known delivered events beyond provider retention; crashes and failed persistence
still prevent an exactly-once guarantee. Acceptance does not prove inbox delivery.

Provider rejection reports retain HTTP status and an allowlisted reason code.
`test_recipient_restricted` requires a verified sending domain/from address for
the intended recipient; `sender_domain_unverified` requires sender verification;
`api_key_invalid` requires an active valid key. Response bodies and embedded
addresses never appear in diagnostics. Check the provider dashboard when the
reason is unclassified; do not change to an unauthorized recipient as a workaround.

After verifying the provider outcome, recover a current durable copy and use one
offline reconciliation command:

```powershell
python -m event_radar --state state/durable.json --batch-id BATCH_KEY --reconcile-accepted PROVIDER_MESSAGE_ID
python -m event_radar --state state/durable.json --batch-id BATCH_KEY --reconcile-not-accepted
```

The first acknowledges confirmed acceptance; the second is only for confirmed
non-acceptance and leaves events pending. Neither sends or contacts the provider.
For remote history, keep delivery disabled and perform retrieval, reconciliation,
and publication against the **same** captured revision in one process. Example
for already verified acceptance (replace placeholders only after checking logs):

```python
from datetime import UTC, datetime
from pathlib import Path

from event_radar.git_state import GitStateStore
from event_radar.runner import reconcile_batch
from event_radar.state import load_state, save_state

store = GitStateStore("state/recovery-cache", "https://github.com/shmurthy7002/event-radar.git")
current = store.fetch()
path = Path("state/recovery.json")
save_state(path, current, initialize=not path.exists())
reconcile_batch(path, "BATCH_KEY", now=datetime.now(UTC), accepted_message_id="PROVIDER_MESSAGE_ID")
store.publish(load_state(path))
```

A competing update must fail publication; do not fetch a newer revision merely
to push an older edited snapshot over it. Compare/reconcile both histories first.
If acceptance remains unknown, retain the unresolved batch.
