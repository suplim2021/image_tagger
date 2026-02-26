import importlib
import os
from pathlib import Path

import pytest

# Ensure a temporary API key file exists so the module can be imported
ROOT = Path(__file__).resolve().parents[1]
API_KEY_FILE = ROOT / "api_key.txt"

@pytest.fixture(autouse=True)
def _create_api_key():
    API_KEY_FILE.write_text("dummy")
    yield
    API_KEY_FILE.unlink()


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

