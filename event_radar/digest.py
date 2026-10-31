"""Safe, side-effect-free HTML, plain-text, and inspectable JSON rendering."""

from dataclasses import asdict, dataclass
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from event_radar.models import DescriptionStatus, registration_url, utc_timestamp
from event_radar.relevance import RankedEvent

PACIFIC = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class Digest:
    html: str
    text: str
    data: dict
    selected_ids: tuple[str, ...]
    omitted_ids: tuple[str, ...]


def local_time(value: datetime) -> str:
    return utc_timestamp(value).astimezone(PACIFIC).strftime("%a, %b %d, %Y · %I:%M %p %Z (UTC%z)")


def _description(ranked: RankedEvent) -> str:
    event = ranked.event
    if event.description_status == DescriptionStatus.CONTENT:
        line = " ".join(event.description.split())
        return line if len(line) <= 220 else line[:217].rstrip() + "…"
    return {
        DescriptionStatus.EMPTY: "No description provided.",
        DescriptionStatus.NOT_FETCHED: "Description not fetched.",
        DescriptionStatus.FAILED: "Description unavailable (fetch failed).",
    }[event.description_status]


def _reason(ranked: RankedEvent) -> str:
    groups = [
        f"{c.group}: title [{', '.join(c.title_terms) or 'none'}]; "
        f"description [{', '.join(c.description_terms) or 'none'}]; "
        f"{'title maximum' if c.title_terms else 'description only'} "
        f"[{', '.join(c.scoring_terms)}] (+{c.points:g})"
        for c in ranked.contributions
    ]
    text = "; ".join(groups) or "No interest keywords matched"
    text += f" · score {ranked.score:g}"
    if ranked.title_only:
        text += " · title-only scoring"
    return text


def render_digest(
    matches: list[RankedEvent],
    *,
    limit: int,
    now: datetime,
    warnings: list[str] | None = None,
    coverage: dict | None = None,
) -> Digest:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("digest limit must be a positive integer")
    ordered = sorted(matches, key=lambda r: (-r.score, r.event.start, r.event.id))
    chosen, omitted = ordered[:limit], ordered[limit:]
    notes = list(warnings or [])
    notes.append("Public discovery is incomplete; source limits can hide events.")
    text = [
        "Event Radar",
        "Your upcoming event matches",
        f"Generated {local_time(now)}",
        "",
        "Coverage notes:",
    ]
    text.extend(f"- {note}" for note in notes)
    html = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Event Radar</title>',
        "<style>body{margin:0;background:#f4f5f1;color:#18342d;font:16px/1.55 system-ui,sans-serif}main{max-width:680px;margin:32px auto;padding:0 20px}h1{font-size:34px;margin-bottom:0}h2{font-size:21px;margin:0 0 8px}a{color:#12654c}article{background:white;padding:24px;margin:20px 0;border:1px solid #d8e1dc;border-radius:12px}.meta,.reason,footer{font-size:14px;color:#42594f}.notes{padding:16px 20px;background:#e9eee7;border-radius:10px}li{margin:6px 0}p{overflow-wrap:anywhere}h2{overflow-wrap:anywhere}</style></head><body><main>",
        f'<header><h1>Event Radar</h1><p>Your upcoming event matches<br><span class="meta">Generated {escape(local_time(now))}</span></p></header>',
        '<section class="notes" aria-label="Coverage notes"><strong>Coverage notes</strong><ul>',
        *(f"<li>{escape(note)}</li>" for note in notes),
        "</ul></section>",
    ]
    if not chosen:
        message = "No matching events in this collected sample."
        html.append(f"<p>{message}</p>")
        text.extend(["", message])
    for ranked in chosen:
        event = ranked.event
        url = registration_url(event.url)  # Revalidate at the output boundary.
        location = (
            " · ".join(dict.fromkeys(v for v in (event.venue, event.neighborhood, event.city) if v))
            or "Location unavailable"
        )
        when, description, reason = local_time(event.start), _description(ranked), _reason(ranked)
        html.append(
            f'<article><h2><a href="{escape(url, quote=True)}">{escape(event.title)}</a></h2><p class="meta">{escape(when)}<br>{escape(location)} · {escape(event.availability.value)}</p><p>{escape(description)}</p><p class="reason">{escape(reason)}</p><a href="{escape(url, quote=True)}">View registration →</a></article>'
        )
        text.extend(
            [
                "",
                event.title,
                when,
                f"{location} · {event.availability.value}",
                description,
                reason,
                url,
            ]
        )
    footer = f"Showing {len(chosen)} of {len(ordered)} eligible matches. {len(omitted)} omitted by the digest limit."
    html.extend(
        [
            f"<footer><p>{footer}</p><p>All times America/Los_Angeles.</p></footer>",
            "</main></body></html>",
        ]
    )
    text.extend(["", footer, "All times America/Los_Angeles."])
    data = {
        "generated_at": utc_timestamp(now).isoformat(),
        "display_timezone": "America/Los_Angeles",
        "warnings": notes,
        "coverage": coverage or {},
        "selected_ids": [r.event.id for r in chosen],
        "omitted_ids": [r.event.id for r in omitted],
        "matches": [
            {
                "event": r.event.to_dict(),
                "score": r.score,
                "contributions": [asdict(c) for c in r.contributions],
                "title_only": r.title_only,
                "included": i < limit,
            }
            for i, r in enumerate(ordered)
        ],
    }
    return Digest(
        "\n".join(html),
        "\n".join(text) + "\n",
        data,
        tuple(data["selected_ids"]),
        tuple(data["omitted_ids"]),
    )
