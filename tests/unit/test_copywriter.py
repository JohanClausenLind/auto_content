"""Model-backed copywriter: schema-validated drafts, card-count contract, local-only routing."""

from __future__ import annotations

import json

from content_factory.hardware.probe import mock_inventory
from content_factory.models import copywriter
from content_factory.schemas import content
from content_factory.schemas.content import ContentCampaign
from content_factory.schemas.fixtures import WS, sample_brief


def _campaign() -> ContentCampaign:
    return ContentCampaign(
        campaign_id="cmp_copytest0001",
        workspace_id=WS,
        brief=sample_brief(),
        deliverables=(
            content.SingleImagePostSpec(deliverable_id="dlv_copytest0001", title="card"),
        ),
    )


def _wire_fake(monkeypatch, reply: dict) -> list[dict]:
    calls: list[dict] = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return {
            "choices": [{"message": {"content": json.dumps(reply)}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 80},
        }

    copywriter._gateway.cache_clear()
    gateway = copywriter._gateway()
    gateway.completion_fn = fake_completion
    gateway.inventory = mock_inventory("rtx3090")
    return calls


def test_draft_caption_routes_local_and_carries_writer(monkeypatch) -> None:
    calls = _wire_fake(
        monkeypatch,
        {
            "caption": "Wind now covers about a fifth of Sweden's power.",
            "alt_text": "Data card showing wind's 21% share.",
        },
    )
    out = copywriter.draft_caption(_campaign())
    assert out["writer"] == "local_structured"
    assert out["caption"].startswith("Wind now covers")
    assert str(calls[0]["model"]).startswith("ollama_chat/")

    sent = calls[0]
    # Two messages: the shared system instruction the gateway attaches to every role, and the
    # brief. The schema is a decoding constraint, not a JSON Schema pasted in front of the prompt.
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert "Never invent a figure" in sent["messages"][0]["content"]
    assert "Topic:" in sent["messages"][1]["content"]
    assert "JSON Schema" not in " ".join(m["content"] for m in sent["messages"])
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"]["properties"].keys() >= {
        "caption",
        "alt_text",
    }
    # And the four settings that were never passed at all.
    assert sent["think"] is False  # reasoning tokens do not come out of the answer's budget
    assert sent["num_ctx"] == 16384  # Ollama's own default is 4096, whatever the model declares
    assert sent["keep_alive"] == 0  # 12 GB released before the next stage loads the image model
    assert out["model"]["schema_enforced"] is True and out["model"]["num_ctx"] == 16384


def test_draft_carousel_always_honours_card_count(monkeypatch) -> None:
    _wire_fake(monkeypatch, {"cards": ["Hook line", "Middle fact"]})  # model under-delivers
    out = copywriter.draft_carousel(_campaign(), card_count=4)
    assert len(out["cards"]) == 4
    assert [c["card_id"] for c in out["cards"]] == [f"card_{i:012d}" for i in range(1, 5)]
    _wire_fake(monkeypatch, {"cards": [f"c{i}" for i in range(9)]})  # and over-delivers
    out = copywriter.draft_carousel(_campaign(), card_count=3)
    assert [c["text"] for c in out["cards"]] == ["c0", "c1", "c2"]
