"""Consolidate raw digital assets into non-duplicated catalogue objects."""

from collections import Counter, defaultdict
from pathlib import Path

from ..common.files import read_jsonl, write_json, write_jsonl
from ..common.provenance import derived_record, utc_now
from ..data.wikimedia import verify_records
from .bridges import object_id, parse_bridges_title
from .codebook import load_codebook, load_feature_schema


def asset_record(source):
    reference = parse_bridges_title(source["commons_title"])
    return {
        "format_version": 1,
        "asset_id": f"commons_{source['commons_page_id']}",
        "source_record_id": str(source["commons_page_id"]),
        "source": "Wikimedia Commons",
        "source_title": source["commons_title"],
        "source_url": source["commons_description_url"],
        "local_path": source.get("local_path", ""),
        "sha1": source.get("local_sha1") or source.get("commons_sha1", ""),
        "sha256": source.get("local_sha256", ""),
        "width": source.get("width"),
        "height": source.get("height"),
        "byte_size": source.get("byte_size"),
        "mime": source.get("mime", ""),
        "license": source.get("license_short_name", ""),
        "download_status": source.get("download_status", ""),
        "catalogue_reference": reference.as_record() if reference else None,
        "object_id": object_id(reference) if reference else None,
        "epistemic_status": "source_assertion",
    }


def aspect_ratio(asset):
    if not asset.get("width") or not asset.get("height"):
        return None
    return asset["width"] / asset["height"]


def canonical_score(asset):
    area = (asset.get("width") or 0) * (asset.get("height") or 0)
    color = asset.get("catalogue_reference", {}).get("source_variant") == "color_plate"
    verified = asset.get("download_status") in {"downloaded", "existing_verified"}
    return verified, area, color, asset.get("byte_size") or 0


def relation_to_canonical(asset, canonical):
    if asset["asset_id"] == canonical["asset_id"]:
        return "canonical"
    first, second = aspect_ratio(asset), aspect_ratio(canonical)
    ratio_close = first and second and abs(first - second) / max(first, second) <= 0.03
    smaller = ((asset.get("width") or 0) <= (canonical.get("width") or 0)
               and (asset.get("height") or 0) <= (canonical.get("height") or 0))
    return "lower_resolution_variant" if ratio_close and smaller else "alternate_reproduction"


def consolidate_records(source_records):
    assets = [asset_record(item) for item in source_records]
    groups = defaultdict(list)
    unparsed = []
    for asset in assets:
        if asset["object_id"]:
            groups[asset["object_id"]].append(asset)
        else:
            unparsed.append(asset)
    objects, review = [], []
    for grouped_id, members in sorted(groups.items()):
        canonical = max(members, key=canonical_score)
        variants = [{"asset_id": item["asset_id"], "relation": relation_to_canonical(item, canonical)}
                    for item in sorted(members, key=lambda value: value["asset_id"])]
        reference = canonical["catalogue_reference"]
        object_record = {
            **derived_record(stage="information", method="bridges_1980_filename_and_resolution",
                             inputs=[item["asset_id"] for item in members]),
            "object_id": grouped_id,
            "catalogue": reference["catalogue"],
            "catalogue_code": reference["catalogue_code"],
            "series_code": reference["series_code"],
            "member_number": reference["member_number"],
            "canonical_asset_id": canonical["asset_id"],
            "canonical_reason": "largest verified pixel area; color plate breaks ties",
            "asset_variants": variants,
            "verification_status": "machine_proposed" if len(members) > 1 else "single_source",
            "epistemic_status": "computed_mapping",
        }
        objects.append(object_record)
        alternates = [item for item in variants if item["relation"] == "alternate_reproduction"]
        if alternates:
            review.append({
                "review_id": f"review_{grouped_id}", "object_id": grouped_id,
                "reason": "Composition or crop may differ; automatic canonical selection needs review.",
                "candidate_asset_ids": [item["asset_id"] for item in members],
                "status": "pending",
            })
    for asset in unparsed:
        review.append({
            "review_id": f"review_unparsed_{asset['asset_id']}", "object_id": None,
            "reason": "Filename does not contain a recognized Bridges 1980 reference.",
            "candidate_asset_ids": [asset["asset_id"]], "status": "pending",
        })
    return assets, objects, review


def build_information(manifest_path, output_dir):
    manifest_path, output_dir = Path(manifest_path), Path(output_dir)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest does not exist: {manifest_path}")
    source = read_jsonl(manifest_path)
    if not source:
        raise ValueError("Refusing to replace Information outputs from an empty manifest.")
    invalid = [item["commons_title"] for item in source
               if item.get("download_status") not in {"downloaded", "existing_verified"}]
    if invalid:
        raise ValueError(f"Manifest contains {len(invalid)} unavailable assets; verify Data first.")
    integrity = verify_records(manifest_path.parent, source)
    if not integrity["complete"]:
        raise ValueError(
            f"Data integrity failed for {len(integrity['issues'])} assets; run data download or verify."
        )
    assets, objects, review = consolidate_records(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "assets.jsonl", assets, sort_key=lambda item: item["asset_id"])
    write_jsonl(output_dir / "objects.jsonl", objects, sort_key=lambda item: item["catalogue_code"])
    write_jsonl(output_dir / "review_queue.jsonl", review, sort_key=lambda item: item["review_id"])
    write_json(output_dir / "visual_features.codebook.json", load_codebook())
    write_json(output_dir / "visual_feature_record.schema.json", load_feature_schema())
    counts = Counter(
        variant["relation"] for item in objects for variant in item["asset_variants"]
    )
    summary = {
        "format_version": 1,
        "stage": "information",
        "method": "bridges_1980_filename_and_resolution",
        "created_at": utc_now(),
        "input_manifest": str(manifest_path.resolve()),
        "source_asset_count": len(source),
        "asset_count": len(assets),
        "object_count": len(objects),
        "review_queue_count": len(review),
        "available_information_codebooks": [{"codebook_id": "visual_features", "version": 1}],
        "variant_relations": dict(sorted(counts.items())),
    }
    write_json(output_dir / "summary.json", summary)
    return summary
