"""Versioned schema for comparable, non-interpretive visual features."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from ..common.provenance import utc_now


RESOURCES = Path(__file__).parent / "resources"


def load_codebook():
    return json.loads((RESOURCES / "visual_features.v1.json").read_text(encoding="utf-8"))


def load_feature_schema():
    return json.loads((RESOURCES / "visual_feature_record.schema.json").read_text(encoding="utf-8"))


def validate_feature_record(record, *, object_ids=None):
    Draft202012Validator(load_feature_schema(), format_checker=FormatChecker()).validate(record)
    if object_ids is not None and record["object_id"] not in set(object_ids):
        raise ValueError("Feature record must reference an existing corpus object")
    present = record["features"]["figure_presence"]
    count = record["features"]["figure_count"]
    if present == "absent" and count != 0:
        raise ValueError("figure_count must be 0 when figure_presence is absent")
    if present == "present" and (count is None or count < 1):
        raise ValueError("figure_count must be positive when figure_presence is present")
    known_features = set(load_codebook()["features"])
    for evidence in record["evidence"]:
        unknown = set(evidence["feature_ids"]) - known_features
        if unknown:
            raise ValueError(f"Evidence cites unknown feature IDs: {sorted(unknown)}")
        if evidence["bbox"] is not None:
            x0, y0, x1, y1 = evidence["bbox"]
            if x0 >= x1 or y0 >= y1:
                raise ValueError("Evidence bounds must have positive area")
    return record


def feature_record(*, information_record_id, object_id, annotator, features,
                   evidence=(), review_status="unreviewed", created_at=None):
    record = {
        "format_version": 1,
        "codebook_id": "visual_features",
        "codebook_version": 1,
        "information_record_id": information_record_id,
        "object_id": object_id,
        "annotator": dict(annotator),
        "features": dict(features),
        "evidence": list(evidence),
        "review_status": review_status,
        "created_at": created_at or utc_now(),
    }
    return validate_feature_record(record)
