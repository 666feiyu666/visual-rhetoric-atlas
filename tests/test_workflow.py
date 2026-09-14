import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from visual_rhetoric_atlas.demo import DemoProvider, image_bytes, result
from visual_rhetoric_atlas.records import Repository, read_json, write_json
from visual_rhetoric_atlas.workflow import OpenAIProvider, execute, prepare, token, validate_result


@pytest.fixture
def case(tmp_path):
    repo = Repository(tmp_path / "data")
    artwork = repo.import_artwork(image_bytes(), title="SECRET TITLE", source="SECRET SOURCE",
                                  context="SECRET CONTEXT", kind="synthetic_demo")
    return repo, artwork


def request_for(case, **kwargs):
    repo, artwork = case
    return prepare(repo, artwork["id"], model="offline-fixture", mode="demo", **kwargs)


def run(case, request=None, provider=None):
    request = request or request_for(case)
    return execute(case[0], request, approved_token=token(request), provider=provider or DemoProvider())


def test_blind_input_excludes_metadata(case):
    request = request_for(case)
    sent = request["instructions"] + request["input_text"]
    assert "SECRET" not in sent
    assert request["parent_reading_id"] is None


def test_context_preserves_blind_and_export_provenance(case):
    repo, artwork = case
    blind = run(case)
    first_bytes = (repo.path("readings", blind["id"]) / "result.json").read_bytes()
    contextual = request_for(case, stage="contextual", parent_reading_id=blind["id"])
    assert "SECRET CONTEXT" in contextual["input_text"]
    second = run(case, contextual)
    assert second["parent_reading_id"] == blind["id"]
    assert (repo.path("readings", blind["id"]) / "result.json").read_bytes() == first_bytes
    repo.save_review(second["id"], verdict="needs_revision", notes="The interpretation is provisional.")
    exported = read_json(repo.export(second["id"]) / "reference.json")
    assert exported["blind_parent"]["reading"]["id"] == blind["id"]
    assert exported["human_reviews"][0]["notes"] == "The interpretation is provisional."
    assert exported["epistemic_status"] != "verified"


def test_changed_request_rejected_before_provider(case):
    request = request_for(case)
    approved = token(request)
    request["model"] = "changed"
    with pytest.raises(ValueError, match="changed"):
        execute(case[0], request, approved_token=approved, provider=lambda *args: pytest.fail("API called"))
    assert case[0].readings(case[1]["id"]) == []


def test_changed_image_rejected(case):
    request = request_for(case)
    case[0].image(case[1]["id"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        execute(case[0], request, approved_token=token(request), provider=lambda *a: pytest.fail("API called"))


def test_parent_from_other_artwork_rejected(case):
    repo, artwork = case
    parent = run(case)
    other = repo.import_artwork(image_bytes(), title="Other", context="Context", kind="synthetic_demo")
    with pytest.raises(ValueError, match="Parent"):
        prepare(repo, other["id"], model="offline-fixture", stage="contextual",
                parent_reading_id=parent["id"], mode="demo")


@pytest.mark.parametrize("response", [
    {"status": "incomplete", "output_text": ""},
    {"status": "completed", "output_text": "not json"},
    {"status": "completed", "output_text": json.dumps({"summary": "missing fields"})},
])
def test_invalid_response_retained_but_not_completed(case, response):
    calls = []
    def provider(*args):
        calls.append(1)
        return response
    with pytest.raises(Exception):
        run(case, provider=provider)
    records = case[0].readings(case[1]["id"])
    assert len(calls) == 1
    assert records[0]["status"] == "failed"
    assert (case[0].path("readings", records[0]["id"]) / "response.json").exists()
    assert not (case[0].path("readings", records[0]["id"]) / "result.json").exists()


def test_interrupt_retained(case):
    def interrupt(*args):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run(case, provider=interrupt)
    assert case[0].readings(case[1]["id"])[0]["status"] == "interrupted"


@pytest.mark.parametrize("mutation", ["reference", "bounds", "duplicate", "context"])
def test_semantic_validation(case, mutation):
    value = result("blind")
    if mutation == "reference":
        value["sign_relations"][0]["observation_ids"] = ["missing"]
    elif mutation == "bounds":
        value["observations"][0]["bbox"] = [0.8, 0.1, 0.2, 0.3]
    elif mutation == "duplicate":
        value["observations"][1]["id"] = "o1"
    else:
        value["contextual_updates"] = result("contextual")["contextual_updates"]
    with pytest.raises(ValueError):
        validate_result(value, request_for(case)["response_schema"], "blind")


def test_path_escape_rejected(case):
    with pytest.raises(ValueError):
        case[0].path("corpus", "../../secret")


def test_arbitrary_upload_cannot_use_fixture(case):
    repo, _ = case
    other = repo.import_artwork(image_bytes(), title="Real artwork", kind="existing_work")
    with pytest.raises(ValueError, match="fixture"):
        prepare(repo, other["id"], model="offline-fixture", mode="demo")


def test_repeated_readings_and_reviews_do_not_overwrite(case):
    repo, _ = case
    first, second = run(case), run(case)
    assert first["id"] != second["id"]
    before = repo.result(first["id"])
    a = repo.save_review(first["id"], verdict="rejected", notes="First assessment.")
    b = repo.save_review(first["id"], verdict="needs_revision", notes="Correction.")
    assert a["id"] != b["id"] and len(repo.reviews(first["id"])) == 2
    assert repo.result(first["id"]) == before
    reopened = Repository(repo.root)
    assert len(reopened.readings(case[1]["id"])) == 2


def test_api_adapter_sends_exact_preview_without_metadata(case, monkeypatch):
    import openai
    seen = {}
    class Client:
        def __init__(self, **kwargs):
            seen["settings"] = kwargs
            self.responses = self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def create(self, **kwargs):
            seen["call"] = kwargs
            return SimpleNamespace(status="completed", output_text=json.dumps(result("blind")),
                                   id="mock-response", usage=None)
    monkeypatch.setattr(openai, "OpenAI", Client)
    request = prepare(case[0], case[1]["id"], model="test-model", mode="live")
    reading = execute(case[0], request, approved_token=token(request), provider=OpenAIProvider())
    assert reading["status"] == "completed"
    assert seen["settings"]["max_retries"] == 0
    assert seen["call"]["store"] is False
    assert seen["call"]["instructions"] == request["instructions"]
    assert "SECRET" not in json.dumps(seen["call"])
    assert seen["call"]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")

def test_transparent_input_is_composited_on_white(tmp_path):
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(buffer, format="PNG")
    raw = buffer.getvalue()
    repo = Repository(tmp_path)
    asset = repo.import_artwork(raw, title="Transparent")
    with Image.open(repo.image(asset["id"])) as normalized:
        assert normalized.getpixel((0, 0)) == (255, 255, 255)
    assert (repo.path("corpus", asset["id"]) / "original.png").read_bytes() == raw
