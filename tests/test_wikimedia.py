import hashlib
import io
import json
from pathlib import Path

from PIL import Image

from visual_rhetoric_atlas.wikimedia import (
    category_files,
    discover,
    download_records,
    read_manifest,
    replace_with_retry,
    safe_filename,
)


def jpeg_bytes(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 12), color).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeClient:
    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    def api(self, params):
        self.calls.append(params)
        if params.get("list") == "categorymembers":
            if "cmcontinue" not in params:
                return {"query": {"categorymembers": [
                    {"pageid": 1, "ns": 6, "title": "File:One.jpg", "timestamp": "2026-01-01T00:00:00Z"}
                ]}, "continue": {"cmcontinue": "next", "continue": "-||"}}
            return {"query": {"categorymembers": [
                {"pageid": 2, "ns": 6, "title": "File:Two.jpg", "timestamp": "2026-01-02T00:00:00Z"}
            ]}}
        titles = params["titles"].split("|")
        pages = []
        for title in titles:
            pageid = 1 if title == "File:One.jpg" else 2
            pages.append({
                "pageid": pageid, "ns": 6, "title": title, "lastrevid": 100 + pageid,
                "canonicalurl": f"https://commons.wikimedia.org/wiki/{title}",
                "imageinfo": [{
                    "timestamp": "2026-01-01T00:00:00Z", "user": "Uploader",
                    "url": f"https://upload.wikimedia.org/{title}",
                    "descriptionurl": f"https://commons.wikimedia.org/wiki/{title}",
                    "size": len(self.raw), "width": 8, "height": 12,
                    "sha1": hashlib.sha1(self.raw).hexdigest(), "mime": "image/jpeg",
                    "mediatype": "BITMAP", "extmetadata": {
                        "Artist": {"value": "<b>Alfons Mucha</b>"},
                        "LicenseShortName": {"value": "Public domain"},
                    },
                }],
            })
        return {"query": {"pages": pages}}

    def download(self, url, destination):
        Path(destination).write_bytes(self.raw)


def test_category_pagination_and_limit():
    client = FakeClient(jpeg_bytes())
    assert [item["pageid"] for item in category_files(client)] == [1, 2]
    limited = FakeClient(jpeg_bytes())
    assert [item["pageid"] for item in category_files(limited, limit=1)] == [1]
    assert len([call for call in limited.calls if call.get("list")]) == 1


def test_discover_preserves_metadata_and_matches_existing(tmp_path):
    raw = jpeg_bytes()
    existing = tmp_path / "legacy"
    existing.mkdir()
    (existing / "old.jpg").write_bytes(raw)
    output = tmp_path / "wikimedia"
    records = discover(FakeClient(raw), output, existing_dir=existing)
    assert len(records) == 2
    assert records[0]["artist_text"] == "Alfons Mucha"
    assert records[0]["license_short_name"] == "Public domain"
    assert all(item["download_status"] == "existing_verified" for item in records)
    assert json.loads((output / "collection.json").read_text(encoding="utf-8"))["file_count_at_snapshot"] == 2
    assert (output / "reports" / "metadata-completeness.csv").exists()


def test_download_is_verified_and_resumable(tmp_path):
    raw = jpeg_bytes("blue")
    output = tmp_path / "wikimedia"
    client = FakeClient(raw)
    discover(client, output)
    first = download_records(client, output)
    assert all(item["download_status"] == "downloaded" for item in first)
    paths = [(output / item["local_path"]).resolve() for item in first]
    assert all(path.exists() for path in paths)
    before = len([call for call in client.calls if call.get("list")])
    second = download_records(client, output)
    assert all(item["download_status"] == "downloaded" for item in second)
    assert len([call for call in client.calls if call.get("list")]) == before
    assert not list(output.rglob("*.part"))


def test_corrupt_download_is_not_promoted(tmp_path):
    raw = jpeg_bytes()
    output = tmp_path / "wikimedia"
    client = FakeClient(raw)
    discover(client, output)
    client.raw = b"broken"
    records = download_records(client, output)
    assert all(item["download_status"] == "failed" for item in records)
    assert not list((output / "files").glob("*.jpg"))
    assert not list(output.rglob("*.part"))


def test_safe_filename_removes_windows_metacharacters():
    assert safe_filename('File:A<B>:C/"D"?.jpg') == "A_B__C__D__.jpg"


def test_atomic_replace_retries_brief_windows_lock(tmp_path, monkeypatch):
    source = tmp_path / "value.tmp"
    destination = tmp_path / "value.json"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    original = Path.replace
    attempts = []

    def flaky(path, target):
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError("brief sharing lock")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", flaky)
    replace_with_retry(source, destination, delay=0)
    assert destination.read_text(encoding="utf-8") == "new"
    assert len(attempts) == 2
