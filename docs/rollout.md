# Milestone 3 rollout evidence

Status: rollout checks passed. Two authorized live digests were accepted on
fresh hosted runners, with durable acknowledgement and no repeated events.
The recipient confirmed useful HTML and plain text, confirmed failure alerts,
and authorized daily delivery at 15:17 UTC. Milestone 3 remains open at the
user's request. Recipient settings remain encrypted environment secrets.

## Verified on October 29, 2026

These runs used main revision `6991a119368ea11e2a98084c188c437cb685c43a`:

| Check | Evidence |
| --- | --- |
| Ubuntu hosted smoke, Berkeley and SF | [Successful run](https://github.com/shmurthy7002/event-radar/actions/runs/40946603181) |
| Missing state fails before collection/provider | [Deliberate failure](https://github.com/shmurthy7002/event-radar/actions/runs/40946675734), sanitized `state_failed` report and failure annotation |
| Explicit empty bootstrap on a verified missing branch | [Successful bootstrap](https://github.com/shmurthy7002/event-radar/actions/runs/40946914739) |
| Persisted preview from a fresh runner | [Successful preview](https://github.com/shmurthy7002/event-radar/actions/runs/40947032261) |
| Second runner retrieves the same durable state | [Successful repeated preview](https://github.com/shmurthy7002/event-radar/actions/runs/40947200683) |
| First authorized send, rejected before acceptance | [Failure and persisted pending state](https://github.com/shmurthy7002/event-radar/actions/runs/40947806673): zero accepted, 17 matches remain pending |

Local and hosted smoke results matched for these bounded samples:

| Origin | Unique events | Duplicates | HTTP attempts | Content descriptions |
| --- | --- | --- | --- | --- |
| Berkeley | 110 | 10 | 12 of 15 | 3 |
| SF | 111 | 9 | 12 of 15 | 3 |

Each source fetched two pages/40 records, with zero malformed records or failed
details. All 20 SF page IDs were present in the 40 city-feed IDs before filtering;
20 additional feed IDs were outside the page sample. Page/detail truncation was
expected and reported. Matching counts do not prove exhaustive discovery or
freedom from location/IP bias; samples were close in time and geography.

The first persisted preview collected 166 unique events, found 17 matches, and
used 24 requests with 12 content descriptions. It excluded 16 starts beyond the
horizon and seven already started events. Sanitized date/score samples matched
Pacific timezone conversion and summed group contributions. The state branch
stayed at `88a1ae2b7cc0a0aa68efd72085b53c60bacdcb03` across previews, as expected
for read-only operation. Artifacts contained allowlisted metrics/audit samples,
not state or message payloads.

Offline CI on Ubuntu/Windows and Python 3.11/3.14 covers acknowledged suppression,
pending omissions, fresh-cache retrieval, racing pushes, and failed remote
checkpoints after acceptance. Fake-provider tests are distinct from real inbox
receipt and must not be presented as live send evidence.

## Validation-driven refinements

Smoke budget fields now survive diagnostic sanitization. Preview reports show
selected/omitted counts without revealing IDs. The hosted detail cap rises from
12 to 20 while retaining the 40-attempt total cap and 20-minute job limit: the
initial preview fetched descriptions for only 12 of 143 eligible events despite
unused request capacity. Budget warnings/title-only scoring remain explicit;
exhaustive enrichment is not promised.

The live rejection also exposed insufficiently specific failure diagnostics.
Rejections now retain HTTP status and a controlled reason code (such as invalid
key, unverified sender domain, or restricted test recipient), without exporting
provider bodies, addresses, or credentials.

The refined preview passed on a hosted runner at `23489fc`:
[run 40948620434](https://github.com/shmurthy7002/event-radar/actions/runs/40948620434)
reported 167 collected events, 26 matches, 10 shown, 16 omitted, 32/40 HTTP
attempts, 32 cached/fetched content descriptions, and zero failed details. The
20 new detail requests were combined with 12 descriptions cached in durable
state after the first rejected send. Selection counts and date/score samples
were checked without exporting event identifiers or keyword text.

After PR #42 and all four CI jobs passed, a second explicitly authorized test on
main `87a7d55` produced the controlled diagnosis:
[run 40948915733](https://github.com/shmurthy7002/event-radar/actions/runs/40948915733),
HTTP **403**, `test_recipient_restricted`, zero provider-accepted events,
26 pending matches, 10 selected and 16 omitted. Resend's account test mode only
permits its own account recipient. The delivery gate was disabled immediately
after the diagnostic run. The user subsequently authorized their registered
account recipient and updated the environment secret directly. No
sender/key/address values are recorded here.

The complete offline suite now contains **215 passing tests**, and all four
Python/OS CI combinations passed before the rollout-refinement PR merged.

## Successful live delivery and daily activation

| Check | Evidence |
| --- | --- |
| First accepted digest | [Run 40949500188](https://github.com/shmurthy7002/event-radar/actions/runs/40949500188), main `87a7d55`, 10 accepted and durably acknowledged, 27 pending |
| Fresh-run delivery and suppression | [Run 40949707560](https://github.com/shmurthy7002/event-radar/actions/runs/40949707560), main `f0f98cd`, another 10 accepted, 27 pending after additional enrichment |

A fresh remote fetch after the second send confirmed 20 acknowledged events
across two disjoint accepted batches. All first-batch acknowledgement timestamps
were preserved. Every second-batch event was already observed and unemailed in
the first snapshot, verifying later delivery of pending omissions. No prepared
or ambiguous batches remained. Additional cached enrichment explains why the
pending count remained 27 while another ten events were delivered. These are
bounded, partial discovery results, not an exhaustive inventory.

On October 29 the user confirmed that HTML and plain text arrived and were useful,
confirmed Actions failure alerts were working/enabled, and explicitly authorized
daily delivery to the configured recipient at **15:17 UTC** (08:17 PDT / 07:17 PST).
The activation PR enables that cron after offline checks and CI; both
`EVENT_RADAR_DELIVERY_ENABLED` and `EVENT_RADAR_ROLLOUT_VERIFIED` must be `true`.
Manual delivery still requires typed confirmation. The first automatic scheduled
run has not yet occurred; GitHub scheduling can be delayed. Keep milestone 3 open.
