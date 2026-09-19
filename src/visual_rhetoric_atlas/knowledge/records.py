"""Persistence for interpretations, reviews, and their source images."""
import io
import re
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps

from ..common.files import read_json, write_json
from ..common.hashing import digest_bytes
from ..common.provenance import utc_now


def now():
    return utc_now()


def identifier(prefix):
    return f"{prefix}_{uuid4().hex}"


def digest(data):
    return digest_bytes(data)


class Repository:
    def __init__(self, root):
        self.root = Path(root).resolve()
        for name in ("corpus", "readings", "reviews", "exports"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def path(self, collection, item_id):
        if collection not in {"corpus", "readings", "reviews", "exports"}:
            raise ValueError("Unknown collection")
        if not re.fullmatch(r"[a-z]+_[a-f0-9]{32}", item_id):
            raise ValueError("Invalid record ID")
        target = (self.root / collection / item_id).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Record path escapes the data directory")
        return target

    def import_artwork(self, raw, *, title, source="", context="", kind="unknown"):
        if not title.strip():
            raise ValueError("Give the artwork a title.")
        if not raw or len(raw) > 20 * 1024 * 1024:
            raise ValueError("Choose an image of at most 20 MB.")
        if kind not in {"unknown", "existing_work", "generated", "synthetic_demo"}:
            raise ValueError("Invalid artwork kind")
        with Image.open(io.BytesIO(raw)) as original:
            if original.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Choose PNG, JPEG or WebP.")
            if original.width * original.height > 30_000_000:
                raise ValueError("Image exceeds 30 megapixels.")
            extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[original.format]
            oriented = ImageOps.exif_transpose(original).convert("RGBA")
            background = Image.new("RGBA", oriented.size, "white")
            normalized = Image.alpha_composite(background, oriented).convert("RGB")
            buffer = io.BytesIO()
            normalized.save(buffer, format="PNG")
        asset_id = identifier("artwork")
        folder = self.path("corpus", asset_id)
        folder.mkdir()
        (folder / f"original.{extension}").write_bytes(raw)
        (folder / "input.png").write_bytes(buffer.getvalue())
        artwork = {
            "format_version": 1, "id": asset_id, "title": title.strip(),
            "source": source, "context": context, "kind": kind, "created_at": now(),
            "original_file": f"original.{extension}", "original_sha256": digest(raw),
            "input_file": "input.png", "input_sha256": digest(buffer.getvalue()),
            "width": normalized.width, "height": normalized.height,
            "normalization": "EXIF orientation applied; transparency composited on white; RGB PNG; no resizing",
        }
        write_json(folder / "artwork.json", artwork)
        return artwork

    def artwork(self, asset_id):
        return read_json(self.path("corpus", asset_id) / "artwork.json")

    def image(self, asset_id):
        return self.path("corpus", asset_id) / "input.png"

    def artworks(self):
        return sorted((read_json(p) for p in (self.root / "corpus").glob("artwork_*/artwork.json")),
                      key=lambda item: item["created_at"], reverse=True)

    def readings(self, asset_id):
        records = [read_json(p) for p in (self.root / "readings").glob("reading_*/manifest.json")]
        return sorted((r for r in records if r["artwork_id"] == asset_id),
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
        value = {"id": review_id, "reading_id": reading_id, "artwork_id": record["artwork_id"],
                 "created_at": now(), "verdict": verdict, "notes": notes,
                 "author_role": "human_reviewer", "format_version": 1}
        write_json(self.path("reviews", review_id) / "review.json", value)
        return value

    def reviews(self, reading_id):
        records = [read_json(p) for p in (self.root / "reviews").glob("review_*/review.json")]
        return sorted((r for r in records if r["reading_id"] == reading_id), key=lambda r: r["created_at"])

    def export(self, reading_id):
        """Export hypotheses with provenance, not automatically verified knowledge."""
        import shutil
        record = self.reading(reading_id)
        if record["status"] != "completed":
            raise ValueError("Export a completed reading.")
        artwork = self.artwork(record["artwork_id"])
        package_id = identifier("export")
        folder = self.path("exports", package_id)
        folder.mkdir()
        source_folder = self.path("readings", reading_id)
        shutil.copy2(source_folder / "input.png", folder / "image.png")
        package = {"format_version": 1, "id": package_id, "created_at": now(),
                   "epistemic_status": "synthetic_fixture" if record["mode"] == "demo" else "model_hypotheses_with_separate_human_reviews",
                   "image": "image.png", "artwork": artwork, "reading": record,
                   "result": self.result(reading_id), "human_reviews": self.reviews(reading_id),
                   "request": read_json(source_folder / "request.json")}
        if record.get("parent_reading_id"):
            parent_id = record["parent_reading_id"]
            package["blind_parent"] = {"reading": self.reading(parent_id),
                                      "result": self.result(parent_id),
                                      "human_reviews": self.reviews(parent_id)}
        write_json(folder / "reference.json", package)
        return folder
