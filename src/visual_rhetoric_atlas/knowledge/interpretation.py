"""Validation of interpretation claims against Information-layer evidence."""


def validate_interpretations(value, observation_ids, stage):
    for item in value["sign_relations"] + value["candidate_insights"]:
        cited = item["observation_ids"]
        if not cited or not set(cited) <= observation_ids:
            raise ValueError("Interpretations must cite existing observations")
    if stage == "blind" and value["contextual_updates"]:
        raise ValueError("Blind readings cannot contain context updates")

