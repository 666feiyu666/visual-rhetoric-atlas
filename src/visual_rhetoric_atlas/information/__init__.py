"""Information stage: consolidate and encode comparable source facts."""

from .pipeline import (
    build_classification,
    build_information,
    classify_asset,
    classify_object,
    feature_record,
    load_classification_codebook,
    load_codebook,
    route_codebooks,
    validate_feature_record,
)

__all__ = [
    "build_classification", "build_information", "classify_asset", "classify_object",
    "feature_record", "load_classification_codebook", "load_codebook",
    "route_codebooks", "validate_feature_record",
]
