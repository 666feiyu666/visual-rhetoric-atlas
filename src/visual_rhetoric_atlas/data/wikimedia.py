"""Data-stage collection of raw files and provenance from Wikimedia Commons."""

from __future__ import annotations

import html
import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image

from ..common.files import atomic_text, read_json, read_jsonl, replace_with_retry, write_csv, write_json, write_jsonl
from ..common.hashing import digest_file
from ..common.provenance import utc_now


API_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
DEFAULT_CATEGORY = "Category:Alphonse Mucha, the complete graphic works (1980)"
DEFAULT_CONTACT = "https://github.com/666feiyu666/visual-rhetoric-atlas"
EXTMETADATA_FIELDS = (
    "Artist|ObjectName|ImageDescription|DateTimeOriginal|Credit|Source|"
    "LicenseShortName|LicenseUrl|UsageTerms|AttributionRequired|Copyrighted"
)


def write_manifest(path, records):
    write_jsonl(path, records, sort_key=lambda item: (item["commons_title"].casefold(), item["commons_page_id"]))


def read_manifest(path):
    return read_jsonl(path)


class CommonsClient:
    def __init__(self, *, contact=DEFAULT_CONTACT, retries=4, timeout=60, sleep=time.sleep):
        self.user_agent = f"VisualRhetoricAtlasMuchaBot/0.1 ({contact}) Python-urllib"
        self.retries = retries
        self.timeout = timeout
        self.sleep = sleep

    def _open(self, request):
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                return urlopen(request, timeout=self.timeout)
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    raise
                delay = int(exc.headers.get("Retry-After", 0) or 0) or min(2 ** attempt, 30)
            except URLError as exc:
                last_error = exc
                if attempt == self.retries:
                    raise
                delay = min(2 ** attempt, 30)
            self.sleep(delay)
        raise last_error  # pragma: no cover

    def api(self, params):
        values = {"format": "json", "formatversion": 2, "maxlag": 5, **params}
        request = Request(
            API_ENDPOINT + "?" + urlencode(values),
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        for attempt in range(self.retries + 1):
            with self._open(request) as response:
                value = json.load(response)
                retry_after = response.headers.get("Retry-After")
            error = value.get("error")
            if not error:
                return value
            if error.get("code") != "maxlag" or attempt == self.retries:
                raise RuntimeError(f"Wikimedia API error: {error.get('code')}: {error.get('info')}")
            self.sleep(int(retry_after or 5))
        raise RuntimeError("Wikimedia API retry limit reached")  # pragma: no cover

    def download(self, url, destination):
        request = Request(url, headers={"User-Agent": self.user_agent})
        with self._open(request) as response, Path(destination).open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)


def category_files(client, category=DEFAULT_CATEGORY, *, limit=None):
    results = []
    continuation = {}
    while True:
        response = client.api({
            "action": "query", "list": "categorymembers", "cmtitle": category,
            "cmtype": "file", "cmprop": "ids|title|timestamp", "cmlimit": "max",
            **continuation,
        })
        results.extend(response["query"]["categorymembers"])
        if limit is not None and len(results) >= limit:
            return results[:limit]
        if "continue" not in response:
            return results
        continuation = response["continue"]


def chunks(values, size):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def metadata_pages(client, members, *, batch_size=10):
    by_title = {member["title"]: member for member in members}
    pages = []
    for batch in chunks(list(by_title), batch_size):
        response = client.api({
            "action": "query", "prop": "info|imageinfo", "titles": "|".join(batch),
            "inprop": "url", "iilimit": 1,
            "iiprop": "timestamp|user|canonicaltitle|url|size|sha1|mime|mediatype|extmetadata",
            "iiextmetadatalanguage": "en", "iimetadataversion": "latest",
            "iiextmetadatafilter": EXTMETADATA_FIELDS,
        })
        for page in response["query"]["pages"]:
            page["category_member"] = by_title.get(page["title"], {})
            pages.append(page)
    return pages


def metadata_value(metadata, key):
    value = metadata.get(key, {})
    return value.get("value", "") if isinstance(value, dict) else value


def plain_metadata(value):
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", str(value))
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def record_from_page(page, previous=None):
    if page.get("missing") or not page.get("imageinfo"):
        return {
            "commons_page_id": page.get("pageid", -1), "commons_title": page["title"],
            "download_status": "metadata_failed", "error": "No imageinfo returned",
        }
    info = page["imageinfo"][0]
    metadata = info.get("extmetadata", {})
    record = {
        "format_version": 1,
        "commons_page_id": page["pageid"],
        "commons_title": page["title"],
        "commons_description_url": info.get("descriptionurl") or page.get("canonicalurl", ""),
        "original_url": info.get("url", ""),
        "commons_sha1": info.get("sha1", ""),
        "width": info.get("width"),
        "height": info.get("height"),
        "byte_size": info.get("size"),
        "mime": info.get("mime", ""),
        "media_type": info.get("mediatype", ""),
        "upload_timestamp": info.get("timestamp", ""),
        "uploader": info.get("user", ""),
        "page_last_revision_id": page.get("lastrevid"),
        "category_added_at": page.get("category_member", {}).get("timestamp", ""),
        "artist_raw": metadata_value(metadata, "Artist"),
        "artist_text": plain_metadata(metadata_value(metadata, "Artist")),
        "object_name_raw": metadata_value(metadata, "ObjectName"),
        "object_name_text": plain_metadata(metadata_value(metadata, "ObjectName")),
        "description_raw": metadata_value(metadata, "ImageDescription"),
        "date_raw": metadata_value(metadata, "DateTimeOriginal"),
        "credit_raw": metadata_value(metadata, "Credit"),
        "source_raw": metadata_value(metadata, "Source"),
        "license_short_name": plain_metadata(metadata_value(metadata, "LicenseShortName")),
        "license_url": metadata_value(metadata, "LicenseUrl"),
        "usage_terms": plain_metadata(metadata_value(metadata, "UsageTerms")),
        "attribution_required": plain_metadata(metadata_value(metadata, "AttributionRequired")),
        "copyrighted": plain_metadata(metadata_value(metadata, "Copyrighted")),
        "extmetadata": metadata,
        "metadata_retrieved_at": utc_now(),
        "download_status": "pending",
    }
    if previous and previous.get("commons_sha1") == record["commons_sha1"]:
        for key in ("download_status", "local_path", "local_sha1", "local_sha256", "downloaded_at", "error"):
            if key in previous:
                record[key] = previous[key]
    return record


def digest(path, algorithm="sha256"):
    return digest_file(path, algorithm)


def safe_filename(title):
    name = title.split(":", 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ")
    return name or "unnamed-file"


def validate_file(path, record):
    path = Path(path)
    if record.get("byte_size") is not None and path.stat().st_size != record["byte_size"]:
        raise ValueError(f"Size mismatch: expected {record['byte_size']}, got {path.stat().st_size}")
    local_sha1 = digest(path, "sha1")
    if record.get("commons_sha1") and local_sha1.casefold() != record["commons_sha1"].casefold():
        raise ValueError("SHA-1 mismatch")
    if record.get("mime") in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/tiff"}:
        with Image.open(path) as image:
            image.verify()
    return local_sha1, digest(path, "sha256")


def match_existing(records, existing_dir, output_dir):
    existing_dir = Path(existing_dir)
    output_dir = Path(output_dir)
    if not existing_dir.exists():
        return records
    by_sha1 = {}
    for path in existing_dir.iterdir():
        if path.is_file():
            by_sha1.setdefault(digest(path, "sha1"), path)
    for record in records:
        match = by_sha1.get(record.get("commons_sha1"))
        if match and record.get("download_status") == "pending":
            record.update(
                download_status="existing_verified",
                local_path=os.path.relpath(match.resolve(), output_dir.resolve()),
                local_sha1=record["commons_sha1"],
                local_sha256=digest(match, "sha256"),
                downloaded_at=utc_now(),
            )
    return records


def discover(client, output_dir, *, category=DEFAULT_CATEGORY, limit=None, existing_dir=None,
             refresh=False):
    output_dir = Path(output_dir)
    manifest_path = output_dir / "manifest.jsonl"
    previous = {item["commons_title"]: item for item in read_manifest(manifest_path)}
    if previous and not refresh:
        raise ValueError("Manifest already exists. Use refresh to update it intentionally.")
    collection_path = output_dir / "collection.json"
    if limit is not None and previous:
        collection = read_json(collection_path) if collection_path.exists() else {}
        if not collection.get("limited_discovery"):
            raise ValueError(
                "Refusing to replace a full manifest with a limited discovery. "
                "Use a separate output directory for test runs."
            )
    members = category_files(client, category, limit=limit)
    pages = metadata_pages(client, members)
    records = [record_from_page(page, previous.get(page["title"])) for page in pages]
    if existing_dir:
        match_existing(records, existing_dir, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(manifest_path, records)
    write_json(output_dir / "collection.json", {
        "format_version": 1, "source": "Wikimedia Commons", "category": category,
        "api_endpoint": API_ENDPOINT, "retrieved_at": utc_now(),
        "collector": "VisualRhetoricAtlasMuchaBot/0.1", "file_count_at_snapshot": len(records),
        "limited_discovery": limit is not None, "limit": limit,
    })
    write_reports(output_dir, records)
    return records


def download_records(client, output_dir):
    output_dir = Path(output_dir)
    manifest_path = output_dir / "manifest.jsonl"
    records = read_manifest(manifest_path)
    if not records:
        raise ValueError("Run discover before download.")
    files_dir = output_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for record in records:
        if record.get("download_status") in {"downloaded", "existing_verified"}:
            local = (output_dir / record["local_path"]).resolve()
            try:
                local_sha1, local_sha256 = validate_file(local, record)
                record.update(local_sha1=local_sha1, local_sha256=local_sha256)
                continue
            except (OSError, ValueError):
                record["download_status"] = "pending"
        target = files_dir / f"{record['commons_page_id']}__{safe_filename(record['commons_title'])}"
        partial = target.with_name(target.name + ".part")
        try:
            if partial.exists():
                partial.unlink()
            client.download(record["original_url"], partial)
            local_sha1, local_sha256 = validate_file(partial, record)
            partial.replace(target)
            record.update(
                download_status="downloaded",
                local_path=os.path.relpath(target.resolve(), output_dir.resolve()),
                local_sha1=local_sha1,
                local_sha256=local_sha256,
                downloaded_at=utc_now(),
            )
            record.pop("error", None)
        except Exception as exc:
            if partial.exists():
                partial.unlink()
            record.update(download_status="failed", error=f"{type(exc).__name__}: {exc}")
        write_manifest(manifest_path, records)
        write_reports(output_dir, records)
    return records


def verify_records(output_dir, records=None):
    """Verify every manifest path and hash without changing download status."""
    output_dir = Path(output_dir)
    records = read_manifest(output_dir / "manifest.jsonl") if records is None else records
    if not records:
        raise ValueError("No manifest found. Run discover first.")
    issues = []
    counts = Counter()
    for record in records:
        local_path = record.get("local_path")
        if not local_path:
            state, detail = "unavailable", "Manifest record has no local_path"
        else:
            path = (output_dir / local_path).resolve()
            if not path.is_file():
                state, detail = "missing", f"Local file does not exist: {local_path}"
            else:
                try:
                    local_sha1, local_sha256 = validate_file(path, record)
                    if record.get("local_sha256") and local_sha256 != record["local_sha256"]:
                        raise ValueError("SHA-256 mismatch")
                    state, detail = "valid", ""
                except (OSError, ValueError) as exc:
                    state, detail = "corrupt", f"{type(exc).__name__}: {exc}"
        counts[state] += 1
        if state != "valid":
            issues.append({
                "commons_page_id": record.get("commons_page_id"),
                "commons_title": record.get("commons_title", ""),
                "local_path": local_path or "",
                "state": state,
                "detail": detail,
            })
    report = {
        "format_version": 1,
        "generated_at": utc_now(),
        "total": len(records),
        "counts": dict(sorted(counts.items())),
        "complete": not issues,
        "issues": issues,
    }
    write_json(output_dir / "reports" / "integrity.json", report)
    return report


def write_reports(output_dir, records):
    output_dir = Path(output_dir)
    reports = output_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    status = Counter(item.get("download_status", "unknown") for item in records)
    licenses = Counter(item.get("license_short_name") or "missing" for item in records)
    write_json(reports / "download-summary.json", {
        "generated_at": utc_now(), "total": len(records),
        "statuses": dict(sorted(status.items())), "licenses": dict(sorted(licenses.items())),
        "total_remote_bytes": sum(item.get("byte_size") or 0 for item in records),
    })
    metadata_fields = ("artist_text", "object_name_text", "date_raw", "source_raw", "license_short_name")
    rows = [[item["commons_page_id"], item["commons_title"], *["yes" if item.get(key) else "no" for key in metadata_fields]] for item in records]
    write_csv(reports / "metadata-completeness.csv", ["page_id", "title", *metadata_fields], rows)
    duplicates = defaultdict(list)
    for item in records:
        if item.get("commons_sha1"):
            duplicates[item["commons_sha1"]].append(item["commons_title"])
    duplicate_rows = [[key, len(titles), " | ".join(titles)] for key, titles in duplicates.items() if len(titles) > 1]
    write_csv(reports / "duplicates.csv", ["commons_sha1", "count", "titles"], duplicate_rows)
    failure_rows = [[item["commons_page_id"], item["commons_title"], item.get("error", "")] for item in records if item.get("download_status") in {"failed", "metadata_failed"}]
    write_csv(reports / "failures.csv", ["page_id", "title", "error"], failure_rows)
