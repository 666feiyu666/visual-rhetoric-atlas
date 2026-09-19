"""Convert interpretation texts into comparable units and mine explicit terms."""

from collections import Counter
from itertools import combinations


def interpretation_units(*, reading_id, object_id, result, source_type="model_reading"):
    units = []
    tags = sorted(set(result.get("tags", [])))
    for index, item in enumerate(result.get("sign_relations", []), 1):
        units.append({
            "format_version": 1,
            "interpretation_unit_id": f"{reading_id}:sign_relation:{index}",
            "reading_id": reading_id,
            "object_id": object_id,
            "source_type": source_type,
            "statement_type": "sign_relation",
            "information_ids": [f"{reading_id}:{value}" for value in item["observation_ids"]],
            "sign_vehicle": item["sign_vehicle"],
            "proposed_object": item["proposed_object"],
            "proposed_interpretant": item["proposed_interpretant"],
            "relation_types": item["relation_types"],
            "grounds": item["grounds"],
            "alternative_readings": item["alternative_readings"],
            "limits": item["limits"],
            "concept_terms": sorted(set(item["relation_types"] + tags)),
            "epistemic_status": "interpretive_hypothesis",
        })
    for index, item in enumerate(result.get("candidate_insights", []), 1):
        units.append({
            "format_version": 1,
            "interpretation_unit_id": f"{reading_id}:candidate_insight:{index}",
            "reading_id": reading_id,
            "object_id": object_id,
            "source_type": source_type,
            "statement_type": "candidate_insight",
            "information_ids": [f"{reading_id}:{value}" for value in item["observation_ids"]],
            "claim": item["claim"],
            "applicability": item["applicability"],
            "limits": item["limits"],
            "concept_terms": tags,
            "epistemic_status": "candidate_knowledge",
        })
    for index, item in enumerate(result.get("contextual_updates", []), 1):
        units.append({
            "format_version": 1,
            "interpretation_unit_id": f"{reading_id}:contextual_update:{index}",
            "reading_id": reading_id,
            "object_id": object_id,
            "source_type": source_type,
            "statement_type": "contextual_update",
            **item,
            "concept_terms": tags,
            "epistemic_status": "context_dependent_interpretation",
        })
    return units


def grouped_concepts(units, unit_of_analysis="object"):
    """Return one concept set per object, reading, or interpretation unit."""
    if unit_of_analysis == "interpretation_unit":
        return [set(unit.get("concept_terms", [])) for unit in units]
    if unit_of_analysis not in {"object", "reading"}:
        raise ValueError("unit_of_analysis must be object, reading, or interpretation_unit")
    key = "object_id" if unit_of_analysis == "object" else "reading_id"
    groups = {}
    for unit in units:
        groups.setdefault(unit[key], set()).update(unit.get("concept_terms", []))
    return list(groups.values())


def concept_frequencies(units, *, unit_of_analysis="object"):
    return Counter(term for terms in grouped_concepts(units, unit_of_analysis) for term in terms)


def concept_cooccurrences(units, *, unit_of_analysis="object"):
    counts = Counter()
    for concepts in grouped_concepts(units, unit_of_analysis):
        terms = sorted(concepts)
        counts.update(combinations(terms, 2))
    return counts
