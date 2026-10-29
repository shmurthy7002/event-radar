import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from event_radar.__main__ import main
from event_radar.config import load_config
from event_radar.delivery import DeliveryError, FakeProvider
from event_radar.diagnostics import RunReport
from event_radar.state import State, StateError, save_state
from tests.test_runner import NOW, client, event


def test_allowlist_never_exports_event_secret_or_provider_content(tmp_path):
    report = RunReport(
        json_path=str(tmp_path / "summary.json"), markdown_path=str(tmp_path / "summary.md")
    )
    report.update(
        {
            "matches": 0,
            "partial": False,
            "event": "Private person",
            "secret": "key-secret",
            "warnings": ["private@example.test"],
            "sources": [
                {
                    "source": "private@example.test",
                    "pages": 1,
                    "records": 0,
                    "errors": ["key-secret"],
                }
            ],
            "descriptions": {"failed": 0, "payload": "Private person"},
        }
    )
    assert report.finish(0) == 0
    data = json.loads((tmp_path / "summary.json").read_text())
    assert data["outcome"] == "healthy_empty"
    for path in tmp_path.iterdir():
        text = path.read_text()
        assert (
            "Private person" not in text
            and "key-secret" not in text
            and "private@example.test" not in text
        )


@pytest.mark.parametrize(
    "failure,outcome,code",
    [
        ("config", "configuration_error", 2),
        ("collection", "collection_failed", 3),
        ("rejected", "provider_rejected", 7),
        ("ambiguous", "provider_ambiguous", 7),
        ("post_save", "post_send_persistence_failed", 8),
    ],
)
def test_failure_reports_redaction_and_acceptance_counts(
    tmp_path, monkeypatch, capsys, failure, outcome, code
):
    from event_radar import runner

    config = load_config("config.example.yaml")
    data = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    data["sources"] = [{"slug": "sf", "kind": "city"}]
    data["requests"]["detail_requests"] = 0
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(data))
    state = tmp_path / "state.json"
    save_state(state, State(), initialize=True)
    provider = FakeProvider()
    if failure in ("rejected", "ambiguous"):
        provider.failure = DeliveryError(
            failure, "sensitive-key recipient@example.test Private Person"
        )
    if failure == "post_save":
        real = runner.save_state

        def save(path, state, **kwargs):
            if any(b.outcome == "accepted" for b in state.batches.values()):
                raise StateError("sensitive-key")
            real(path, state, **kwargs)

        monkeypatch.setattr(runner, "save_state", save)
    monkeypatch.setenv("EVENT_RADAR_EMAIL_API_KEY", "sensitive-key")
    monkeypatch.setenv("EVENT_RADAR_EMAIL_FROM", "sender@example.test")
    monkeypatch.setenv("EVENT_RADAR_EMAIL_TO", "recipient@example.test")
    summary = tmp_path / "summary.json"
    args = [
        "--config",
        str(config_path if failure != "config" else tmp_path / "missing"),
        "--summary-json",
        str(summary),
        "--diagnostics-only",
        "--send",
        "--state",
        str(state),
    ]
    assert (
        main(
            args,
            client_factory=lambda _: client(
                replace(config, requests=replace(config.requests, detail_requests=0)),
                [event()],
                fail=failure == "collection",
            ),
            provider_factory=lambda _: provider,
            now=NOW,
        )
        == code
    )
    report = json.loads(summary.read_text())
    assert report["outcome"] == outcome
    assert report["metrics"].get("provider_accepted", 0) == (1 if failure == "post_save" else 0)
    output = capsys.readouterr()
    text = output.out + output.err + summary.read_text()
    assert (
        "sensitive-key" not in text and "example.test" not in text and "Private Person" not in text
    )
    assert not (tmp_path / "digest.html").exists()
