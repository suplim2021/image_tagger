import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    """Let image_tagger_gui import without a real key (see test_parse_json_content)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    yield


@pytest.fixture
def mod():
    return importlib.import_module("image_tagger_gui")


def test_estimate_cost_uses_per_million_rates(mod):
    # Sonnet 5.5: $2 in / $10 out per million tokens.
    assert mod.estimate_cost("claude-sonnet-5-5", 1_000_000, 0) == pytest.approx(2.0)
    assert mod.estimate_cost("claude-sonnet-5-5", 3_000, 1_500) == pytest.approx(0.021)


def test_estimate_cost_unknown_model_is_zero(mod):
    assert mod.estimate_cost("some-other-model", 10_000, 10_000) == 0.0


def test_format_cost_precision(mod):
    assert mod.format_cost(0.0213) == "$0.0213"
    assert mod.format_cost(12.345) == "$12.35"


def _fake_response(text, input_tokens, output_tokens):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def test_process_batch_reports_usage(mod, monkeypatch):
    response = _fake_response('[{"index": 1, "title": "t", "tags": ["a"]}]', 3000, 1500)
    monkeypatch.setattr(mod.client.messages, "create", lambda **kw: response)
    monkeypatch.setattr(mod, "write_metadata", lambda *a, **kw: None)

    seen = []
    mod.process_images_batch(
        ["img.jpg"], "claude-sonnet-5-5", "me",
        encoded_cache={"img.jpg": "data"},
        on_usage=lambda i, o: seen.append((i, o)),
    )
    assert seen == [(3000, 1500)]


def test_unparseable_response_still_reports_usage(mod, monkeypatch):
    # A response that fails to parse is still billed, so it must be counted.
    response = _fake_response("not json at all", 2000, 800)
    monkeypatch.setattr(mod.client.messages, "create", lambda **kw: response)

    seen = []
    results = mod.process_images_batch(
        ["img.jpg"], "claude-sonnet-5-5", "me",
        encoded_cache={"img.jpg": "data"},
        on_usage=lambda i, o: seen.append((i, o)),
    )
    assert seen == [(2000, 800)]
    assert results["img.jpg"]["title"] == "Unprocessed Image"
