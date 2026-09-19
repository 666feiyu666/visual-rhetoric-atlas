"""Parse identifiers from Ann Bridges's 1980 Mucha catalogue filenames.

This is a source-specific adapter, not a general visual-encoding system.
"""

from dataclasses import asdict, dataclass
from pathlib import PurePath
import re


PREFIX = "Mucha - Bridges, "


@dataclass(frozen=True)
class BridgesReference:
    catalogue: str
    catalogue_code: str
    series_code: str
    member_number: int | None
    source_variant: str

    def as_record(self):
        return asdict(self)


def parse_bridges_title(title):
    filename = title.split(":", 1)[-1]
    stem = PurePath(filename).stem
    if not stem.startswith(PREFIX):
        return None
    value = stem[len(PREFIX):]
    source_variant = "color_plate" if value.endswith(" -c") else "catalogue_scan"
    if source_variant == "color_plate":
        value = value[:-3]
    value = value.strip()
    member = re.fullmatch(r"(.+)-(\d+)", value)
    series_code = member.group(1) if member else value
    member_number = int(member.group(2)) if member else None
    return BridgesReference(
        catalogue="bridges_1980",
        catalogue_code=value,
        series_code=series_code,
        member_number=member_number,
        source_variant=source_variant,
    )


def object_id(reference):
    safe = re.sub(r"[^a-z0-9]+", "_", reference.catalogue_code.casefold()).strip("_")
    return f"bridges_1980_{safe}"

