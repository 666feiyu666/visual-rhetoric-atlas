import json

import pytest

from visual_rhetoric_atlas.information.bridges import object_id, parse_bridges_title
from visual_rhetoric_atlas.information.consolidate import consolidate_records
from visual_rhetoric_atlas.knowledge.claims import candidate_claim
from visual_rhetoric_atlas.knowledge.text_mining import (
    concept_cooccurrences,
    concept_frequencies,
    interpretation_units,
)


def source(pageid, title, width, height):
    return {
        "commons_page_id": pageid,
        "commons_title": title,
        "commons_description_url": f"https://commons.wikimedia.org/wiki/{title}",
        "commons_sha1": str(pageid),
        "local_sha256": str(pageid) * 2,
        "local_path": f"files/{pageid}.jpg",
        "width": width,
        "height": height,
        "byte_size": width * height,
        "mime": "image/jpeg",
        "license_short_name": "Public domain",
        "download_status": "downloaded",
    }


def test_bridges_parser_is_explicitly_source_specific():
    reference = parse_bridges_title("File:Mucha - Bridges, G01g-3 -c.jpg")
    assert reference.catalogue_code == "G01g-3"
    assert reference.series_code == "G01g"
    assert reference.member_number == 3
    assert reference.source_variant == "color_plate"
    assert object_id(reference) == "bridges_1980_g01g_3"
    assert parse_bridges_title("File:Girl of Ivančice.jpeg") is None


def test_consolidation_keeps_assets_but_selects_one_canonical_object():
    raw = [
        source(1, "File:Mucha - Bridges, A01.jpg", 140, 403),
        source(2, "File:Mucha - Bridges, A01 -c.jpg", 593, 1715),
    ]
    assets, objects, review = consolidate_records(raw)
    assert len(assets) == 2
    assert len(objects) == 1
    assert objects[0]["canonical_asset_id"] == "commons_2"
    assert {item["relation"] for item in objects[0]["asset_variants"]} == {
        "canonical", "lower_resolution_variant"
    }
    assert review == []


def test_interpretations_become_mineable_text_units():
    result = {
        "tags": ["circular-frame", "female-figure"],
        "sign_relations": [{
            "observation_ids": ["o1"], "sign_vehicle": "A circular frame",
            "proposed_object": "An emblem", "proposed_interpretant": "The figure appears emblematic",
            "relation_types": ["iconic", "symbolic"], "grounds": "The figure is enclosed",
            "alternative_readings": ["Decoration"], "limits": "Reception is unknown",
        }],
        "candidate_insights": [{
            "observation_ids": ["o1"], "claim": "Circular enclosure can support emblematic readings.",
            "applicability": "Single-figure designs", "limits": "Not sufficient by itself",
        }],
        "contextual_updates": [],
    }
    units = interpretation_units(reading_id="reading_1", object_id="bridges_A01", result=result)
    assert len(units) == 2
    assert units[0]["information_ids"] == ["reading_1:o1"]
    assert concept_frequencies(units)["circular-frame"] == 2
    assert concept_cooccurrences(units)[("circular-frame", "female-figure")] == 2


def test_candidate_claims_require_traceable_corpus_evidence():
    with pytest.raises(ValueError):
        candidate_claim(claim_id="c0", text="Unsupported", information_ids=[],
                        interpretation_unit_ids=[], scope="none", method="none")
    claim = candidate_claim(
        claim_id="c1", text="A candidate pattern", information_ids=["i1"],
        interpretation_unit_ids=["u1"], scope="Mucha corpus", method="cooccurrence",
    )
    assert claim["information_ids"] == ["i1"]
    assert claim["interpretation_unit_ids"] == ["u1"]
    assert claim["epistemic_status"] == "candidate_knowledge"
