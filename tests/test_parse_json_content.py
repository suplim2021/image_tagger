import importlib

import pytest


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    """Let image_tagger_gui import without a real key configured.

    Sets an OS env var rather than writing/deleting a .env file on disk --
    a file-based fixture would risk clobbering (and then permanently
    deleting) a developer's real .env if one exists in the project root.
    load_env() only falls back to this env var when .env doesn't already
    define ANTHROPIC_API_KEY, so a real .env present locally still wins
    here, which is harmless since this module-level import never makes an
    actual API call.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    yield


@pytest.fixture
def get_parser():
    module = importlib.import_module("image_tagger_gui")
    return module.parse_json_content


def test_parse_valid_json(get_parser):
    parse = get_parser
    content = '{"title": "img", "tags": ["a", "b"]}'
    assert parse(content) == {"title": "img", "tags": ["a", "b"]}


def test_parse_json_code_fence(get_parser):
    parse = get_parser
    content = """```json\n{\"title\": \"img\", \"tags\": [\"a\"]}\n```"""
    assert parse(content) == {"title": "img", "tags": ["a"]}


def test_parse_trailing_comma(get_parser):
    parse = get_parser
    content = '{"title": "img", "tags": ["a", "b", ]}'
    assert parse(content) == {"title": "img", "tags": ["a", "b"]}


def test_parse_unrecoverable(get_parser):
    parse = get_parser
    content = '{"title": "img", "tags": ["a", "b"}'  # missing closing bracket
    assert parse(content) is None

