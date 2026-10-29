# Milestone 3 rollout evidence

Status: read-only rollout checks passed; authorized live requests were rejected
before acceptance by Resend's test-recipient restriction. Receipt and daily
activation remain pending. Issue
#20 and milestone 3 must stay open until the remaining
criteria are satisfied. Test sending to a user-selected account was authorized
in the task; its address is stored only as an encrypted environment secret.

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
permits its own account recipient. The intended recipient remains unchanged;
no sender/key/address values are recorded here. The user must verify a sending
domain/from address or supply a Resend account matching the authorized test
recipient. The delivery gate was disabled immediately after the diagnostic run.

The complete offline suite now contains **215 passing tests**, and all four
Python/OS CI combinations passed before the rollout-refinement PR merged.

## Remaining release gates

- Resolve `test_recipient_restricted`. All three secret names are configured in
  `event-radar-delivery`, but successful provider acceptance is still unverified.
  No secret/address values belong in Git or artifacts.
- Perform an authorized real send. Record acceptance separately from recipient
  confirmation of useful HTML and plain-text content.
- Repeat real delivery on a fresh runner: verify suppression and later delivery
  of eligible length-limit omissions.
- Confirm configured Actions failure notifications reach the operator. The
  failure job/annotation/report path is verified; notification receipt is not.
- Confirm daily deployment scope, mark rollout verified only after checks pass,
  and activate cron through a reviewed PR.

The delivery variable was enabled only for authorized tests, then disabled
after rejection. No cron trigger has been activated. Do not close
issue #20 or the milestone on these read-only checks alone.
