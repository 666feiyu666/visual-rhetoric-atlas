"""Information stage: consolidate and encode comparable source facts."""

from .pipeline import build_information, feature_record, load_codebook, validate_feature_record

__all__ = ["build_information", "feature_record", "load_codebook", "validate_feature_record"]
