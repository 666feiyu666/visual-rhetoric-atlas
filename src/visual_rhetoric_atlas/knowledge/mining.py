"""Persist corpus-level mining outputs from completed interpretation records."""

from pathlib import Path

from ..common.files import read_json, write_json, write_jsonl
from ..common.provenance import utc_now
from .text_mining import concept_cooccurrences, concept_frequencies, interpretation_units


def build_knowledge(corpus_root):
    corpus_root = Path(corpus_root).resolve()
    knowledge_root = corpus_root / "knowledge"
    output = knowledge_root / "mining"
    units = []
    completed = 0
    for manifest_path in sorted((knowledge_root / "readings").glob("reading_*/manifest.json")):
        reading = read_json(manifest_path)
        result_path = manifest_path.parent / "result.json"
        if reading.get("status") != "completed" or not result_path.is_file():
            continue
        completed += 1
        units.extend(interpretation_units(
            reading_id=reading["id"], object_id=reading["object_id"],
            result=read_json(result_path), source_type="model_reading",
        ))
    frequencies = concept_frequencies(units, unit_of_analysis="object")
    cooccurrences = concept_cooccurrences(units, unit_of_analysis="object")
    write_jsonl(output / "interpretation_units.jsonl", units,
                sort_key=lambda item: item["interpretation_unit_id"])
    write_json(output / "concept_frequencies.json", {
        "format_version": 1, "unit_of_analysis": "catalogue_object",
        "counts": dict(sorted(frequencies.items())),
    })
    write_jsonl(output / "concept_cooccurrences.jsonl", [
        {"concept_a": pair[0], "concept_b": pair[1], "object_count": count}
        for pair, count in sorted(cooccurrences.items())
    ], sort_key=lambda item: (item["concept_a"], item["concept_b"]))
    summary = {
        "format_version": 1, "stage": "knowledge", "created_at": utc_now(),
        "unit_of_analysis": "catalogue_object", "completed_reading_count": completed,
        "interpretation_unit_count": len(units),
        "object_count": len({unit["object_id"] for unit in units}),
        "concept_count": len(frequencies),
    }
    write_json(output / "summary.json", summary)
    return summary
