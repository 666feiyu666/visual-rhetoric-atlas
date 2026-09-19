import json
from copy import deepcopy
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from visual_rhetoric_atlas.common.files import read_jsonl, write_jsonl
from visual_rhetoric_atlas.knowledge.mining import build_knowledge
from visual_rhetoric_atlas.knowledge.records import Repository, read_json, write_json
from visual_rhetoric_atlas.knowledge.reading import OpenAIProvider, execute, prepare, token, validate_result


def image_bytes():
    image = Image.new("RGB", (600, 800), "#faf8f3")
    draw = ImageDraw.Draw(image)
    draw.text((60, 180), "A GAP IN THE SEQUENCE", fill="#252a2b", font_size=27)
    for index in range(6):
        if index != 3:
            draw.rectangle((60 + index * 80, 350, 112 + index * 80, 445), fill="#252a2b")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def result(stage):
    value = {
        "summary": "Test fixture: a synthetic arrangement of text and interrupted repetition.",
        "observations": [
            {"id": "o1", "description": "A short line of dark text above the shapes.",
             "location": "Upper-left area", "bbox": [0.08, 0.20, 0.95, 0.28],
             "visible_text": "A GAP IN THE SEQUENCE", "uncertainty": ""},
            {"id": "o2", "description": "Five rectangles occupy six equally spaced positions.",
             "location": "Middle horizontal band", "bbox": [0.10, 0.43, 0.86, 0.56],
             "visible_text": "", "uncertainty": "The grouping is only a visual description."},
        ],
        "sign_relations": [{
            "observation_ids": ["o1", "o2"], "sign_vehicle": "Repetition with one omitted position",
            "proposed_object": "An interruption in a sequence",
            "proposed_interpretant": "A viewer might notice an absence or a pause.",
            "relation_types": ["iconic", "symbolic"],
            "grounds": "A schematic sequence establishes expectation.",
            "alternative_readings": ["A missing or unfinished item"],
            "limits": "No audience reception or author intent has been established.",
        }],
        "candidate_insights": [{
            "observation_ids": ["o2"],
            "claim": "An omission can become perceptible against a regular sequence.",
            "applicability": "Layouts with a clear repeated unit.",
            "limits": "This does not establish what the omission means.",
        }],
        "contextual_updates": [], "uncertainties": ["Test fixture."],
        "tags": ["repetition", "negative-space"],
    }
    if stage == "contextual":
        value["contextual_updates"] = [{
            "earlier_claim": "The gap may be read as an interruption.",
            "assessment": "supported", "context_evidence": "Synthetic test context.",
            "explanation": "The context identifies the fixture but not audience response.",
        }]
    return value


class FixtureProvider:
    def __call__(self, request, image):
        return {"status": "completed", "output_text": json.dumps(result(request["stage"])),
                "response_id": None, "usage": None, "fixture": True}


def add_object(root, object_id, asset_id, raw):
    image_dir = root / "wikimedia" / "files"
    image_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / f"{asset_id}.png"
    image_path.write_bytes(raw)
    asset = {
        "format_version": 1, "asset_id": asset_id, "source_record_id": asset_id,
        "source": "Test fixture", "source_title": "SECRET TITLE",
        "source_url": "https://example.test/source", "local_path": f"files/{asset_id}.png",
        "sha1": hashlib.sha1(raw).hexdigest(), "sha256": hashlib.sha256(raw).hexdigest(),
        "width": 600, "height": 800, "byte_size": len(raw), "mime": "image/png",
        "license": "Public domain", "download_status": "downloaded",
        "catalogue_reference": None, "object_id": object_id,
        "epistemic_status": "source_assertion",
    }
    obj = {
        "format_version": 1, "stage": "information", "method": "test_fixture",
        "input_record_ids": [asset_id], "status": "completed",
        "created_at": "2026-01-01T00:00:00+00:00", "object_id": object_id,
        "catalogue": "test", "catalogue_code": object_id, "series_code": "test",
        "member_number": None, "canonical_asset_id": asset_id,
        "canonical_reason": "test fixture", "asset_variants": [{"asset_id": asset_id, "relation": "canonical"}],
        "verification_status": "single_source", "epistemic_status": "computed_mapping",
    }
    information = root / "information"
    information.mkdir(parents=True, exist_ok=True)
    assets = read_jsonl(information / "assets.jsonl")
    objects = read_jsonl(information / "objects.jsonl")
    write_jsonl(information / "assets.jsonl", [*assets, asset], sort_key=lambda item: item["asset_id"])
    write_jsonl(information / "objects.jsonl", [*objects, obj], sort_key=lambda item: item["object_id"])
    return obj


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "mucha"
    obj = add_object(root, "test_object_1", "test_asset_1", image_bytes())
    return Repository(root), obj


def request_for(case, **kwargs):
    repo, obj = case
    return prepare(repo, obj["object_id"], model="test-fixture", **kwargs)


def run(case, request=None, provider=None):
    request = request or request_for(case)
    return execute(case[0], request, approved_token=token(request), provider=provider or FixtureProvider())


def test_blind_input_excludes_metadata(case):
    request = request_for(case)
    sent = request["instructions"] + request["input_text"]
    assert "SECRET" not in sent
    assert request["parent_reading_id"] is None


def test_context_preserves_blind_and_export_provenance(case):
    repo, obj = case
    blind = run(case)
    first_bytes = (repo.path("readings", blind["id"]) / "result.json").read_bytes()
    contextual = request_for(case, stage="contextual", parent_reading_id=blind["id"])
    assert "SECRET TITLE" in contextual["input_text"]
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
    assert case[0].readings(case[1]["object_id"]) == []


def test_changed_image_rejected(case):
    request = request_for(case)
    case[0].image(case[1]["object_id"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        execute(case[0], request, approved_token=token(request), provider=lambda *a: pytest.fail("API called"))


def test_parent_from_other_object_rejected(case):
    repo, obj = case
    parent = run(case)
    other = add_object(repo.root, "test_object_2", "test_asset_2", image_bytes())
    with pytest.raises(ValueError, match="Parent"):
        prepare(repo, other["object_id"], model="test-fixture", stage="contextual",
                parent_reading_id=parent["id"])


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
    records = case[0].readings(case[1]["object_id"])
    assert len(calls) == 1
    assert records[0]["status"] == "failed"
    assert (case[0].path("readings", records[0]["id"]) / "response.json").exists()
    assert not (case[0].path("readings", records[0]["id"]) / "result.json").exists()


def test_interrupt_retained(case):
    def interrupt(*args):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run(case, provider=interrupt)
    assert case[0].readings(case[1]["object_id"])[0]["status"] == "interrupted"


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
        case[0].path("readings", "../../secret")


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
    assert len(reopened.readings(case[1]["object_id"])) == 2


def test_completed_readings_feed_object_level_knowledge_mining(case):
    run(case)
    run(case)
    summary = build_knowledge(case[0].root)
    assert summary["completed_reading_count"] == 2
    assert summary["object_count"] == 1
    frequencies = read_json(case[0].root / "knowledge" / "mining" / "concept_frequencies.json")
    assert frequencies["unit_of_analysis"] == "catalogue_object"
    assert frequencies["counts"]["repetition"] == 1


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
    request = prepare(case[0], case[1]["object_id"], model="test-model")
    reading = execute(case[0], request, approved_token=token(request), provider=OpenAIProvider())
    assert reading["status"] == "completed"
    assert seen["settings"]["max_retries"] == 0
    assert seen["call"]["store"] is False
    assert seen["call"]["instructions"] == request["instructions"]
    assert "SECRET" not in json.dumps(seen["call"])
    assert seen["call"]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")

def test_repository_resolves_canonical_corpus_image(case):
    repo, obj = case
    assert repo.image(obj["object_id"]).read_bytes() == image_bytes()
    assert repo.canonical_asset(obj["object_id"])["asset_id"] == "test_asset_1"
