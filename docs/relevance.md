# Customize your interests and title points

Copy `config.example.yaml` to ignored `config.yaml` for personal experiments.
Under `interests`, each group contains keyword/phrase names followed by their
title points. Use spaces for indentation, not tabs. For example:

```yaml
interests:
  robotics:
    embodied AI: 8
    robots: 5
    AI: 2
  research:
    reinforcement learning: 6
    RL: 6
negative_keywords: [speed dating]
minimum_score: 1
```

Change a number to change that keyword's title importance. Points can be numbers
from 1 to 1000, including fractions such as `2.5`. Add a keyword on a new indented
line, or delete its line to remove it. Add a group by copying a group block and
changing its name and keywords. Delete the whole block to remove a group. Remove
empty groups; keep at least one group and one keyword in each. Quote keywords
that YAML could interpret as numbers/booleans or syntax, for example `"yes": 2`
or `"AI: research": 5`. Duplicate keys and keywords differing only in case or
whitespace are errors. Do not include global `title_weight` or
`description_weight` with these mappings.

## How matches combine

The score is the **sum of group contributions**. Within each group:

- A title match awards the **highest configured points** among matching title
  keywords. Other title matches in that group do not add points.
- With no title match, any number of description matches awards **1 point total**.
- With no match in either field, the group awards nothing.

Groups represent separate interests; keywords within a group represent related
terms or synonyms. This maximum rule lets a specific preference outweigh a broad
term without rewarding long synonym lists. If you intentionally want two
interests to add together, put them in separate groups. The same term or
overlapping phrase in different groups can then contribute in both groups.

Repeated occurrences never multiply points. Overlapping phrases such as `AI`
and `embodied AI` both appear in the explanation, but only the stronger title
match determines that group's contribution. Equal strongest matches are all
listed as scoring terms, with the points awarded once. A title plus description
match does not add the description point, even when the description contains a
higher-valued keyword. **Title points apply only in titles**; description-only
groups always contribute 1.

Matching ignores case and accepts flexible phrase whitespace. Unicode word
boundaries keep `AI`, `RL`, and `ML` from matching inside words such as `mail`
and `XML`. A negative keyword in either field excludes the entire event,
regardless of its positive score. `minimum_score` is inclusive. Ties sort by
event start time, then stable event ID, independently of YAML/group order.
Missing/failed descriptions remain visibly marked as title-only scoring.

## Worked example

Using the configuration above, assuming equal date/location eligibility:

| Title | Description | Score | Reason |
| --- | --- | --- | --- |
| Embodied AI workshop | Robots and reinforcement learning | 9 | robotics title maximum 8, research description-only 1 |
| AI meetup | Embodied AI and robots | 2 | robotics title maximum 2; description adds nothing |
| AI AI and robots | AI | 5 | robotics title maximum 5, counted once |
| Community workshop | Robots, AI, embodied AI | 1 | robotics description-only 1 |
| RL and embodied AI | — | 14 | research title 6 plus robotics title 8 |

Thus the strongly preferred title phrase outranks the weaker title keyword even
when the weaker event mentions the strong phrase in its description. Move terms
between groups or adjust `minimum_score` deliberately; both can change selection.
Dates, radius, availability, negative keywords, and request budgets still apply.

## Preview before delivery

Validate edits without network access or email:

```powershell
python -c "from event_radar.config import load_config; load_config('config.yaml'); print('Configuration valid')"
```

Then collect a bounded public sample and render a preview:

```powershell
python -m event_radar --config config.yaml --output output/tuning
```

Inspect `output/tuning/digest.html`, `digest.txt`, and `results.json`. Each group
explains title/description matches, the scoring terms, and awarded points. JSON
includes all eligible matches, including digest-limit omissions. Change points,
rerun, and compare; live source contents and coverage may also change between
runs. Preview does not send or mutate state. If you already have local state,
add `--pending-preview --state state/state.json` to inspect cached future,
unemailed events with your new rules. Do not bootstrap over existing history.

The hosted daily workflow reads **tracked `config.example.yaml`**, not your
ignored local `config.yaml`. To deploy your preferences, copy only non-sensitive
settings into that tracked file, run offline tests, and merge a reviewed PR.
Keep addresses and credentials in environment secrets. Existing acknowledgements
remain suppressed; scoring edits do not resend accepted events. Resolve any
prepared/ambiguous batch before changing delivery settings; recovery preserves
its original payload and rechecks relevance. See [operations](operations.md).

## Existing YAML migration

Legacy list groups remain readable:

```yaml
interests:
  robotics: [robotics, robots, embodied AI]
title_weight: 3
description_weight: 1
```

This assigns 3 title points to each term and retains the same per-group scores.
If omitted, legacy weights default to 3 and 1. To customize individual points,
convert **every** group list into a keyword-to-points mapping and remove both
global weights. Do not mix lists and mappings. The shipped example shows the
equivalent mappings with all previous title defaults preserved.

Legacy `description_weight` values other than 1 are rejected with a migration
message: change it to 1 and review your threshold and preview. Description points
are fixed, so they cannot retain a custom old weight. Legacy title weights must
still exceed 1; mapping entries may use 1 for weak title matches. Malformed types,
empty groups, duplicate keys, and out-of-range points fail before collection or
email with an edit location. No credentials belong in this file.
