"""Minimal provenance shared by Data, Information, Knowledge, and RAG."""

from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def derived_record(*, stage, method, inputs, status="completed", format_version=1):
    return {
        "format_version": format_version,
        "stage": stage,
        "method": method,
        "input_record_ids": list(inputs),
        "status": status,
        "created_at": utc_now(),
    }

