"""Persistence for annotations attached to the canonical corpus objects."""

import re
import shutil
from pathlib import Path
from uuid import uuid4

from ..common.files import read_json, read_jsonl, write_json
from ..common.hashing import digest_bytes
from ..common.provenance import utc_now


def now():
    return utc_now()


def identifier(prefix):
    return f"{prefix}_{uuid4().hex}"


def digest(data):
    return digest_bytes(data)


class Repository:
    """One corpus root shared by Data, Information, and Knowledge."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.information_root = self.root / "information"
        self.knowledge_root = self.root / "knowledge"
        for name in ("readings", "reviews", "exports"):
            (self.knowledge_root / name).mkdir(parents=True, exist_ok=True)

    def _index(self, filename, key):
        return {record[key]: record for record in read_jsonl(self.information_root / filename)}

    def object(self, object_id):
        try:
            return self._index("objects.jsonl", "object_id")[object_id]
        except KeyError as exc:
            raise ValueError(f"Unknown corpus object: {object_id}") from exc

    def asset(self, asset_id):
        try:
            return self._index("assets.jsonl", "asset_id")[asset_id]
        except KeyError as exc:
            raise ValueError(f"Unknown corpus asset: {asset_id}") from exc

    def canonical_asset(self, object_id):
        return self.asset(self.object(object_id)["canonical_asset_id"])

    def image(self, object_id):
        asset = self.canonical_asset(object_id)
        path = (self.root / "wikimedia" / asset["local_path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Canonical asset path escapes the corpus root")
        if not path.is_file():
            raise FileNotFoundError(f"Canonical asset is missing: {path}")
        return path

    def source_context(self, object_id):
        obj = self.object(object_id)
        asset = self.canonical_asset(object_id)
        return {
            "object": {
                "object_id": obj["object_id"],
                "catalogue": obj["catalogue"],
                "catalogue_code": obj["catalogue_code"],
                "series_code": obj["series_code"],
                "member_number": obj["member_number"],
            },
            "canonical_source": {
                "asset_id": asset["asset_id"],
                "source": asset["source"],
                "source_title": asset["source_title"],
                "source_url": asset["source_url"],
                "license": asset["license"],
            },
        }

    def path(self, collection, item_id):
        if collection not in {"readings", "reviews", "exports"}:
            raise ValueError("Unknown Knowledge collection")
        if not re.fullmatch(r"[a-z]+_[a-f0-9]{32}", item_id):
            raise ValueError("Invalid record ID")
        target = (self.knowledge_root / collection / item_id).resolve()
        if not target.is_relative_to(self.knowledge_root):
            raise ValueError("Record path escapes the Knowledge directory")
        return target

    def readings(self, object_id):
        records = [read_json(path) for path in
                   (self.knowledge_root / "readings").glob("reading_*/manifest.json")]
        return sorted((record for record in records if record["object_id"] == object_id),
                      key=lambda item: item["created_at"], reverse=True)

    def reading(self, reading_id):
        return read_json(self.path("readings", reading_id) / "manifest.json")

    def result(self, reading_id):
        return read_json(self.path("readings", reading_id) / "result.json")

    def save_review(self, reading_id, *, verdict, notes):
        record = self.reading(reading_id)
        if record["status"] != "completed":
            raise ValueError("Review a completed reading.")
        if verdict not in {"needs_revision", "useful_with_limits", "rejected"}:
            raise ValueError("Invalid review verdict")
        if not notes.strip():
            raise ValueError("Add review notes or corrections.")
        review_id = identifier("review")
        value = {
            "id": review_id,
            "reading_id": reading_id,
            "object_id": record["object_id"],
            "created_at": now(),
            "verdict": verdict,
            "notes": notes,
            "author_role": "human_reviewer",
            "format_version": 1,
        }
        write_json(self.path("reviews", review_id) / "review.json", value)
        return value

    def reviews(self, reading_id):
        records = [read_json(path) for path in
                   (self.knowledge_root / "reviews").glob("review_*/review.json")]
        return sorted((record for record in records if record["reading_id"] == reading_id),
                      key=lambda item: item["created_at"])

    def export(self, reading_id):
        """Export hypotheses with corpus identity and provenance."""
        record = self.reading(reading_id)
        if record["status"] != "completed":
            raise ValueError("Export a completed reading.")
        object_record = self.object(record["object_id"])
        asset = self.canonical_asset(record["object_id"])
        package_id = identifier("export")
        folder = self.path("exports", package_id)
        folder.mkdir()
        source_folder = self.path("readings", reading_id)
        snapshot = source_folder / record["image_file"]
        shutil.copy2(snapshot, folder / snapshot.name)
        package = {
            "format_version": 1,
            "id": package_id,
            "created_at": now(),
            "epistemic_status": "model_hypotheses_with_separate_human_reviews",
            "image": snapshot.name,
            "object": object_record,
            "canonical_asset": asset,
            "reading": record,
            "result": self.result(reading_id),
            "human_reviews": self.reviews(reading_id),
            "request": read_json(source_folder / "request.json"),
        }
        if record.get("parent_reading_id"):
            parent_id = record["parent_reading_id"]
            package["blind_parent"] = {
                "reading": self.reading(parent_id),
                "result": self.result(parent_id),
                "human_reviews": self.reviews(parent_id),
            }
        write_json(folder / "reference.json", package)
        return folder
