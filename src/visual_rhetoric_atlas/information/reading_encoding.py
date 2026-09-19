"""Validation and encoding of visible evidence as Information records."""


def validate_observations(observations):
    ids = [observation["id"] for observation in observations]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate observation IDs")
    for observation in observations:
        x0, y0, x1, y1 = observation["bbox"]
        if x0 >= x1 or y0 >= y1:
            raise ValueError("Observation bounds must have positive area")
    return set(ids)


def observation_records(*, reading_id, object_id, observations):
    """Turn one reading's evidence into independently addressable Information."""
    validate_observations(observations)
    return [{
        "format_version": 1,
        "information_id": f"{reading_id}:{item['id']}",
        "reading_id": reading_id,
        "object_id": object_id,
        "observation_id": item["id"],
        "description": item["description"],
        "location": item["location"],
        "bbox": item["bbox"],
        "visible_text": item["visible_text"],
        "uncertainty": item["uncertainty"],
        "epistemic_status": "visible_observation",
    } for item in observations]

