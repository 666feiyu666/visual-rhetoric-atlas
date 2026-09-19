"""Pre-annotation corpus triage and codebook routing.

The initial pass uses source metadata only. It deliberately leaves visual content
types and work types uncertain until a human or image-based classifier reviews
the file itself.
"""

from collections import Counter
import json
from pathlib import Path

from ..common.files import read_jsonl, write_json, write_jsonl
from ..common.provenance import utc_now


RESOURCES = Path(__file__).parent / "resources"


def load_classification_codebook():
    return json.loads((RESOURCES / "corpus_classification.v1.json").read_text(encoding="utf-8"))


def _source_variant(asset):
    return (asset.get("catalogue_reference") or {}).get("source_variant", "unknown")


def classify_asset(asset, *, created_at=None):
    """Create an honest metadata-only triage record for one digital asset."""
    variant = _source_variant(asset)
    color_status = (
        "catalogue_designated_color"
        if variant == "color_plate"
        else "not_catalogue_designated_color"
        if variant == "catalogue_scan"
        else "uncertain"
    )
    description = (
        f"Bridges filename convention identifies source_variant={variant}."
        if variant in {"color_plate", "catalogue_scan"}
        else "No recognized Bridges source-variant marker is available."
    )
    return {
        "format_version": 1,
        "codebook_id": "corpus_classification",
        "codebook_version": 1,
        "record_type": "asset_classification",
        "classification_id": f"classification_{asset['asset_id']}",
        "asset_id": asset["asset_id"],
        "object_id": asset.get("object_id"),
        "source_variant": variant,
        "asset_content_type": "uncertain",
        "completeness": "uncertain",
        "color_status": color_status,
        "analysis_eligibility": "review",
        "classification_status": "machine_proposed",
        "method": "source_metadata_triage",
        "evidence": [{
            "field": "color_status", "basis": "source_metadata", "description": description,
        }],
        "created_at": created_at or utc_now(),
    }


def route_codebooks(work_type, *, color_eligible=False):
    """Return codebook IDs after classification has been reviewed."""
    codebook = load_classification_codebook()
    routes = []
    if work_type not in {"uncertain", "photograph_or_document"}:
        routes.append(codebook["routing"]["shared"])
        extension = codebook["routing"]["by_work_type"].get(work_type)
        if extension:
            routes.append(extension)
    if color_eligible and work_type != "photograph_or_document":
        routes.append(codebook["routing"]["color"])
    return routes


def classify_object(obj, asset_classifications, *, created_at=None):
    """Create an object-level routing record without inferring a work type."""
    members = [asset_classifications[item["asset_id"]] for item in obj["asset_variants"]]
    color_assets = [item["asset_id"] for item in members
                    if item["color_status"] == "catalogue_designated_color"]
    return {
        "format_version": 1,
        "codebook_id": "corpus_classification",
        "codebook_version": 1,
        "record_type": "object_classification",
        "classification_id": f"classification_{obj['object_id']}",
        "object_id": obj["object_id"],
        "work_type": "uncertain",
        "work_subtype": None,
        "classification_status": "needs_review",
        "method": "source_metadata_triage",
        "candidate_color_asset_ids": color_assets,
        "active_codebooks": [],
        "candidate_codebooks": ["color_features"] if color_assets else [],
        "created_at": created_at or utc_now(),
    }


def classify_records(assets, objects, *, created_at=None):
    timestamp = created_at or utc_now()
    asset_records = [classify_asset(item, created_at=timestamp) for item in assets]
    by_asset = {item["asset_id"]: item for item in asset_records}
    object_records = [classify_object(item, by_asset, created_at=timestamp) for item in objects]
    by_object = {item["object_id"]: item for item in object_records}
    review = []
    for obj in objects:
        classification = by_object[obj["object_id"]]
        has_color = bool(classification["candidate_color_asset_ids"])
        review.append({
            "review_id": f"classification_review_{obj['object_id']}",
            "object_id": obj["object_id"],
            "asset_ids": [item["asset_id"] for item in obj["asset_variants"]],
            "priority": "high" if has_color else "normal",
            "reason": (
                "Confirm asset content, completeness, work type, and color-analysis eligibility; "
                "this object has a catalogue-designated color plate."
                if has_color else
                "Confirm asset content, completeness, work type, and analysis eligibility."
            ),
            "fields_to_review": [
                "asset_content_type", "completeness", "analysis_eligibility", "work_type"
            ],
            "status": "pending",
        })
    return asset_records, object_records, review


def apply_overrides(records, overrides, *, id_field):
    """Apply explicit human review decisions without altering generated source records."""
    by_id = {item[id_field]: item for item in records}
    for override in overrides:
        target_id = override[id_field]
        if target_id not in by_id:
            raise ValueError(f"Classification override references unknown {id_field}: {target_id}")
        record = by_id[target_id]
        record.update(override["changes"])
        record["classification_status"] = "human_reviewed"
        record["method"] = "human_visual_review"
        record["review"] = {
            "annotator": override["annotator"],
            "evidence": override.get("evidence", []),
            "reviewed_at": override["reviewed_at"],
        }
    return records


def build_classification(information_dir):
    """Build classification and routing outputs from existing Information records."""
    information_dir = Path(information_dir)
    assets = read_jsonl(information_dir / "assets.jsonl")
    objects = read_jsonl(information_dir / "objects.jsonl")
    if not assets or not objects:
        raise ValueError("Classification requires non-empty assets.jsonl and objects.jsonl.")
    asset_records, object_records, _ = classify_records(assets, objects)
    overrides_path = information_dir / "classification_overrides.jsonl"
    overrides = read_jsonl(overrides_path)
    asset_overrides = [item for item in overrides if item["record_type"] == "asset_override"]
    object_overrides = [item for item in overrides if item["record_type"] == "object_override"]
    apply_overrides(asset_records, asset_overrides, id_field="asset_id")
    by_asset = {item["asset_id"]: item for item in asset_records}
    object_records = [classify_object(item, by_asset) for item in objects]
    apply_overrides(object_records, object_overrides, id_field="object_id")
    for record in object_records:
        eligible_color = any(
            by_asset[asset_id]["analysis_eligibility"] == "eligible"
            for asset_id in record["candidate_color_asset_ids"]
        )
        if record["classification_status"] == "human_reviewed":
            record["active_codebooks"] = route_codebooks(
                record["work_type"], color_eligible=eligible_color
            )
    review = []
    reviewed_objects = {item["object_id"] for item in object_overrides}
    for obj in objects:
        if obj["object_id"] in reviewed_objects:
            continue
        classification = next(item for item in object_records
                              if item["object_id"] == obj["object_id"])
        has_color = bool(classification["candidate_color_asset_ids"])
        review.append({
            "review_id": f"classification_review_{obj['object_id']}",
            "object_id": obj["object_id"],
            "asset_ids": [item["asset_id"] for item in obj["asset_variants"]],
            "priority": "high" if has_color else "normal",
            "reason": (
                "Confirm asset content, completeness, work type, and color-analysis eligibility; "
                "this object has a catalogue-designated color plate."
                if has_color else
                "Confirm asset content, completeness, work type, and analysis eligibility."
            ),
            "fields_to_review": [
                "asset_content_type", "completeness", "analysis_eligibility", "work_type"
            ],
            "status": "pending",
        })
    write_jsonl(information_dir / "asset_classifications.jsonl", asset_records,
                sort_key=lambda item: item["asset_id"])
    write_jsonl(information_dir / "object_classifications.jsonl", object_records,
                sort_key=lambda item: item["object_id"])
    write_jsonl(information_dir / "classification_review_queue.jsonl", review,
                sort_key=lambda item: (item["priority"] != "high", item["object_id"]))
    write_json(information_dir / "corpus_classification.codebook.json",
               load_classification_codebook())
    summary = {
        "format_version": 1,
        "stage": "information_classification",
        "method": "source_metadata_triage",
        "created_at": utc_now(),
        "asset_count": len(asset_records),
        "object_count": len(object_records),
        "asset_content_types": dict(sorted(Counter(
            item["asset_content_type"] for item in asset_records).items())),
        "color_statuses": dict(sorted(Counter(
            item["color_status"] for item in asset_records).items())),
        "work_types": dict(sorted(Counter(
            item["work_type"] for item in object_records).items())),
        "high_priority_review_count": sum(item["priority"] == "high" for item in review),
        "review_queue_count": len(review),
        "human_reviewed_asset_count": len(asset_overrides),
        "human_reviewed_object_count": len(object_overrides),
        "note": "Visual content and work types remain uncertain until image-level review.",
    }
    write_json(information_dir / "classification_summary.json", summary)
    return summary
