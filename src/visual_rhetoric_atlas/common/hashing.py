"""Stable content hashes used across every pipeline stage."""

import hashlib
from pathlib import Path


def digest_bytes(data, algorithm="sha256"):
    return hashlib.new(algorithm, data).hexdigest()


def digest_file(path, algorithm="sha256"):
    value = hashlib.new(algorithm)
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()

