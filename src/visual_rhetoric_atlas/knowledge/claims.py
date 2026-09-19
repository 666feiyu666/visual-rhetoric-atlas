"""Evidence-linked candidate knowledge produced by visual and text mining."""

from ..common.provenance import derived_record


def candidate_claim(*, claim_id, text, information_ids, interpretation_unit_ids,
                    scope, method, counterexample_ids=()):
    if not information_ids and not interpretation_unit_ids:
        raise ValueError("A knowledge claim must cite information or interpretation evidence.")
    return {
        **derived_record(stage="knowledge", method=method,
                         inputs=[*information_ids, *interpretation_unit_ids]),
        "claim_id": claim_id,
        "claim": text,
        "information_ids": list(information_ids),
        "interpretation_unit_ids": list(interpretation_unit_ids),
        "counterexample_ids": list(counterexample_ids),
        "scope": scope,
        "epistemic_status": "candidate_knowledge",
        "review_status": "unreviewed",
    }

