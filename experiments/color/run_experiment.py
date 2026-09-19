"""Run the reproducible Mucha corpus color-pattern experiment.

The script intentionally keeps the experiment outside the package pipeline.  It
reads the frozen corpus metadata, invokes the separately checked-out Pylette
library, and writes only beneath ``experiments/color``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.metrics import silhouette_score


EXPERIMENT_ID = "mucha_color_patterns_v1"
PALETTE_SIZES = (8, 16, 32)
PRESENCE_THRESHOLDS = (0.005, 0.01, 0.02)
COLOR_FAMILY_CANDIDATES = (12, 16, 20)
TOOL_VERSION = "6.0.0"
TOOL_COMMIT = "04701a7706c8375513448e92c7496321984b2039"

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
INPUTS = HERE / "inputs"
OUTPUTS = HERE / "outputs"
RAW = OUTPUTS / "raw"
NORMALIZED = OUTPUTS / "normalized"
MINING = OUTPUTS / "mining"
FIGURES = OUTPUTS / "figures"
CONTACT_SHEETS = FIGURES / "cluster_contact_sheets"
ASSETS_PATH = REPO_ROOT / "corpus" / "mucha" / "information" / "assets.jsonl"
OBJECTS_PATH = REPO_ROOT / "corpus" / "mucha" / "information" / "objects.jsonl"
WIKIMEDIA_ROOT = REPO_ROOT / "corpus" / "mucha" / "wikimedia"
PYLETTE_ROOT = Path(os.environ.get("PYLETTE_ROOT", REPO_ROOT.parent / "Pylette"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def relative_image_path(asset: dict[str, Any]) -> str:
    local_path = Path(asset["local_path"])
    return (Path("corpus") / "mucha" / "wikimedia" / local_path).as_posix()


def image_path(record: dict[str, Any]) -> Path:
    return REPO_ROOT / Path(record["path"])


def ensure_directories() -> None:
    for directory in (INPUTS, RAW, NORMALIZED, MINING, FIGURES, CONTACT_SHEETS):
        directory.mkdir(parents=True, exist_ok=True)


def prepare_inputs() -> list[dict[str, Any]]:
    ensure_directories()
    objects = {row["object_id"]: row for row in read_jsonl(OBJECTS_PATH)}
    selected: list[dict[str, Any]] = []
    for asset in read_jsonl(ASSETS_PATH):
        reference = asset.get("catalogue_reference") or {}
        if reference.get("source_variant") != "color_plate":
            continue
        obj = objects[asset["object_id"]]
        selected.append(
            {
                "asset_id": asset["asset_id"],
                "object_id": asset["object_id"],
                "catalogue_code": reference.get("catalogue_code"),
                "series_code": reference.get("series_code") or obj.get("series_code"),
                "member_number": reference.get("member_number") or obj.get("member_number"),
                "source_variant": reference["source_variant"],
                "path": relative_image_path(asset),
                "sha256": asset["sha256"],
            }
        )
    selected.sort(key=lambda row: row["asset_id"])
    write_jsonl(INPUTS / "color_assets.jsonl", selected)

    source_dir = PYLETTE_ROOT / "test-output"
    copies = {
        "a59-kmeans.json": RAW / "a59_kmeans_8.json",
        "a59-oklab.json": RAW / "a59_oklab_8.json",
        "a59-oklab-16.json": RAW / "a59_oklab_16.json",
        "a59-oklab-32.json": RAW / "a59_oklab_32.json",
        "a59-palette-comparison.png": FIGURES / "a59_palette_comparison.png",
    }
    for source_name, destination in copies.items():
        source = source_dir / source_name
        if source.exists():
            shutil.copy2(source, destination)
    return selected


def load_inputs() -> list[dict[str, Any]]:
    path = INPUTS / "color_assets.jsonl"
    return read_jsonl(path) if path.exists() else prepare_inputs()


def palette_to_record(palette: Any, sample: dict[str, Any], palette_size: int) -> dict[str, Any]:
    exported = palette.to_json(filename=None, colorspace="OKLab")
    assert isinstance(exported, dict)
    exported.update(
        {
            "asset_id": sample["asset_id"],
            "object_id": sample["object_id"],
            "catalogue_code": sample["catalogue_code"],
            "series_code": sample["series_code"],
            "input_sha256": sample["sha256"],
            "requested_palette_size": palette_size,
        }
    )
    return exported


def extract_palettes(max_workers: int = 4, force: bool = False) -> None:
    if str(PYLETTE_ROOT) not in sys.path:
        sys.path.insert(0, str(PYLETTE_ROOT))
    from pylette import batch_extract_colors

    samples = load_inputs()
    all_runs: list[dict[str, Any]] = []
    all_failures: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []

    if not force and all((RAW / f"pylette_oklab_{size}.json").exists() for size in PALETTE_SIZES):
        normalize_existing()
        return

    paths = [image_path(sample) for sample in samples]
    for palette_size in PALETTE_SIZES:
        print(f"Extracting OKLab/{palette_size} for {len(paths)} images ...", flush=True)
        results = batch_extract_colors(
            paths,
            palette_size=palette_size,
            resize=256,
            mode="OKLab",
            max_workers=max_workers,
        )
        raw_palettes: list[dict[str, Any]] = []
        for sample, result in zip(samples, results, strict=True):
            if result.success and result.palette is not None:
                record = palette_to_record(result.palette, sample, palette_size)
                raw_palettes.append(record)
                stats = record["metadata"]["processing_stats"]
                info = record["metadata"]["image_info"]
                all_runs.append(
                    {
                        "experiment_id": EXPERIMENT_ID,
                        "asset_id": sample["asset_id"],
                        "object_id": sample["object_id"],
                        "palette_size": palette_size,
                        "mode": "OKLab",
                        "input_sha256": sample["sha256"],
                        "processed_size": info["processed_size"],
                        "valid_pixels": stats["valid_pixels"],
                        "extraction_time_seconds": stats["extraction_time"],
                        "tool_version": TOOL_VERSION,
                        "tool_commit": TOOL_COMMIT,
                        "status": "completed",
                    }
                )
                for rank, color in enumerate(record["colors"], start=1):
                    normalized.append(
                        {
                            "experiment_id": EXPERIMENT_ID,
                            "asset_id": sample["asset_id"],
                            "object_id": sample["object_id"],
                            "catalogue_code": sample["catalogue_code"],
                            "series_code": sample["series_code"],
                            "palette_size": palette_size,
                            "palette_rank": rank,
                            "mode": "OKLab",
                            "rgb": color["rgb"],
                            "hex": color["hex"],
                            "oklab": color["oklab"],
                            "frequency": color["frequency"],
                        }
                    )
            else:
                failure = {
                    "experiment_id": EXPERIMENT_ID,
                    "asset_id": sample["asset_id"],
                    "object_id": sample["object_id"],
                    "palette_size": palette_size,
                    "mode": "OKLab",
                    "input_sha256": sample["sha256"],
                    "status": "failed",
                    "error_type": type(result.exception).__name__,
                    "error": str(result.exception),
                }
                all_failures.append(failure)
                all_runs.append(failure)
        write_json(
            RAW / f"pylette_oklab_{palette_size}.json",
            {
                "experiment_id": EXPERIMENT_ID,
                "tool": {"name": "Pylette", "version": TOOL_VERSION, "commit": TOOL_COMMIT},
                "mode": "OKLab",
                "requested_palette_size": palette_size,
                "palettes": raw_palettes,
            },
        )

    write_jsonl(NORMALIZED / "palettes.jsonl", normalized)
    write_jsonl(NORMALIZED / "extraction_runs.jsonl", all_runs)
    write_jsonl(NORMALIZED / "failures.jsonl", all_failures)


def normalize_existing() -> None:
    normalized: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for palette_size in PALETTE_SIZES:
        raw = json.loads((RAW / f"pylette_oklab_{palette_size}.json").read_text(encoding="utf-8"))
        for record in raw["palettes"]:
            meta = record["metadata"]
            runs.append(
                {
                    "experiment_id": EXPERIMENT_ID,
                    "asset_id": record["asset_id"],
                    "object_id": record["object_id"],
                    "palette_size": palette_size,
                    "mode": "OKLab",
                    "input_sha256": record["input_sha256"],
                    "processed_size": meta["image_info"]["processed_size"],
                    "valid_pixels": meta["processing_stats"]["valid_pixels"],
                    "extraction_time_seconds": meta["processing_stats"]["extraction_time"],
                    "tool_version": TOOL_VERSION,
                    "tool_commit": TOOL_COMMIT,
                    "status": "completed",
                }
            )
            for rank, color in enumerate(record["colors"], 1):
                normalized.append(
                    {
                        "experiment_id": EXPERIMENT_ID,
                        "asset_id": record["asset_id"],
                        "object_id": record["object_id"],
                        "catalogue_code": record.get("catalogue_code"),
                        "series_code": record.get("series_code"),
                        "palette_size": palette_size,
                        "palette_rank": rank,
                        "mode": "OKLab",
                        "rgb": color["rgb"],
                        "hex": color["hex"],
                        "oklab": color["oklab"],
                        "frequency": color["frequency"],
                    }
                )
    write_jsonl(NORMALIZED / "palettes.jsonl", normalized)
    write_jsonl(NORMALIZED / "extraction_runs.jsonl", runs)
    write_jsonl(NORMALIZED / "failures.jsonl", failures)


def rgb_hex(rgb: Iterable[float]) -> str:
    values = [max(0, min(255, int(round(value)))) for value in rgb]
    return "#" + "".join(f"{value:02X}" for value in values)


def oklab_to_rgb(lab: np.ndarray) -> np.ndarray:
    l, a, b = np.moveaxis(np.asarray(lab, dtype=float), -1, 0)
    l_ = l + 0.3963377774 * a + 0.2158037573 * b
    m_ = l - 0.1055613458 * a - 0.0638541728 * b
    s_ = l - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_**3, m_**3, s_**3
    linear = np.stack(
        [
            4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3,
            -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
            -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3,
        ],
        axis=-1,
    )
    srgb = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * np.maximum(linear, 0) ** (1 / 2.4) - 0.055)
    return np.clip(srgb * 255, 0, 255)


def choose_color_vocabulary(rows32: list[dict[str, Any]]) -> tuple[KMeans, list[dict[str, Any]], list[dict[str, Any]]]:
    x = np.asarray([row["oklab"] for row in rows32], dtype=float)
    weights = np.asarray([row["frequency"] for row in rows32], dtype=float)
    evaluations: list[dict[str, Any]] = []
    models: dict[int, KMeans] = {}
    rng = np.random.default_rng(2024)
    sample_index = rng.choice(len(x), size=min(2000, len(x)), replace=False, p=weights / weights.sum())
    for n_families in COLOR_FAMILY_CANDIDATES:
        model = KMeans(n_clusters=n_families, n_init=20, random_state=2024).fit(x, sample_weight=weights)
        models[n_families] = model
        labels = model.predict(x[sample_index])
        score = float(silhouette_score(x[sample_index], labels, metric="euclidean"))
        evaluations.append(
            {
                "n_families": n_families,
                "weighted_inertia": float(model.inertia_),
                "sampled_silhouette": score,
            }
        )
    best_n = max(evaluations, key=lambda row: (row["sampled_silhouette"], -abs(row["n_families"] - 16)))[
        "n_families"
    ]
    model = models[best_n]
    labels = model.predict(x)
    families: list[dict[str, Any]] = []
    total_weight = weights.sum()
    rgb_centers = oklab_to_rgb(model.cluster_centers_)
    for family_index, (center, rgb) in enumerate(zip(model.cluster_centers_, rgb_centers, strict=True), start=1):
        mask = labels == family_index - 1
        family_id = f"color_{family_index:02d}"
        families.append(
            {
                "color_family_id": family_id,
                "centroid_oklab": center.tolist(),
                "representative_rgb": [int(round(value)) for value in rgb],
                "representative_hex": rgb_hex(rgb),
                "corpus_weight": float(weights[mask].sum() / total_weight),
                "source_swatch_count": int(mask.sum()),
            }
        )
    return model, families, evaluations


def vectorize(
    rows: list[dict[str, Any]], model: KMeans, family_ids: list[str], samples: list[dict[str, Any]]
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_asset[row["asset_id"]].append(row)
    matrix = np.zeros((len(samples), len(family_ids)), dtype=float)
    csv_rows: list[dict[str, Any]] = []
    for index, sample in enumerate(samples):
        colors = by_asset[sample["asset_id"]]
        labs = np.asarray([color["oklab"] for color in colors])
        labels = model.predict(labs)
        for label, color in zip(labels, colors, strict=True):
            matrix[index, label] += color["frequency"]
        if matrix[index].sum():
            matrix[index] /= matrix[index].sum()
        csv_rows.append(
            {
                "asset_id": sample["asset_id"],
                "object_id": sample["object_id"],
                "catalogue_code": sample["catalogue_code"],
                "series_code": sample["series_code"],
                **{family_id: float(matrix[index, j]) for j, family_id in enumerate(family_ids)},
            }
        )
    return matrix, csv_rows


def cooccurrence_rows(
    matrix: np.ndarray, family_ids: list[str], palette_size: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    n_images = len(matrix)
    for threshold in PRESENCE_THRESHOLDS:
        presence = matrix >= threshold
        counts = presence.sum(axis=0)
        for left, right in combinations(range(len(family_ids)), 2):
            both = int(np.logical_and(presence[:, left], presence[:, right]).sum())
            support = both / n_images
            p_left = counts[left] / n_images
            p_right = counts[right] / n_images
            union = int(np.logical_or(presence[:, left], presence[:, right]).sum())
            expected = p_left * p_right
            rows.append(
                {
                    "palette_size": palette_size,
                    "presence_threshold": threshold,
                    "left_family_id": family_ids[left],
                    "right_family_id": family_ids[right],
                    "case_count": both,
                    "support": support,
                    "left_to_right_probability": both / counts[left] if counts[left] else 0.0,
                    "right_to_left_probability": both / counts[right] if counts[right] else 0.0,
                    "jaccard": both / union if union else 0.0,
                    "lift": support / expected if expected else 0.0,
                    "pmi": math.log2(support / expected) if support and expected else 0.0,
                }
            )
    return rows


def cluster_images(matrix: np.ndarray, palette_size: int) -> tuple[np.ndarray, list[dict[str, Any]], list[dict[str, Any]]]:
    evaluations: list[dict[str, Any]] = []
    models: dict[int, np.ndarray] = {}
    for n_clusters in range(3, min(10, len(matrix) - 1) + 1):
        try:
            estimator = AgglomerativeClustering(n_clusters=n_clusters, metric="cosine", linkage="average")
        except TypeError:
            estimator = AgglomerativeClustering(n_clusters=n_clusters, affinity="cosine", linkage="average")
        labels = estimator.fit_predict(matrix)
        score = float(silhouette_score(matrix, labels, metric="cosine"))
        evaluations.append({"palette_size": palette_size, "n_clusters": n_clusters, "silhouette": score})
        models[n_clusters] = labels
    best = max(evaluations, key=lambda row: row["silhouette"])
    labels = models[best["n_clusters"]]
    summaries: list[dict[str, Any]] = []
    for label in sorted(set(labels)):
        member_indexes = np.flatnonzero(labels == label)
        center = matrix[member_indexes].mean(axis=0)
        center_norm = np.linalg.norm(center)
        member_norms = np.linalg.norm(matrix[member_indexes], axis=1)
        similarities = (matrix[member_indexes] @ center) / np.maximum(member_norms * center_norm, 1e-12)
        distances = 1 - similarities
        summaries.append(
            {
                "palette_size": palette_size,
                "cluster_id": f"p{palette_size}_cluster_{label + 1:02d}",
                "size": int(len(member_indexes)),
                "member_indexes": member_indexes.tolist(),
                "centroid": center.tolist(),
                "representative_index": int(member_indexes[np.argmin(distances)]),
                "boundary_index": int(member_indexes[np.argmax(distances)]),
            }
        )
    return labels, evaluations, summaries


def color_for_hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def new_canvas(width: int, height: int, title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((24, 18), title, fill="#111111", font=ImageFont.load_default())
    return image, draw


def draw_global_palettes(families: list[dict[str, Any]]) -> None:
    image, draw = new_canvas(1200, 250, "Corpus-level color vocabulary (32-color source palettes)")
    ranked = sorted(families, key=lambda row: row["corpus_weight"], reverse=True)
    x = 24
    available = 1152
    for index, family in enumerate(ranked):
        width = max(12, int(available * family["corpus_weight"]))
        if index == len(ranked) - 1:
            width = 1176 - x
        draw.rectangle((x, 62, x + width, 172), fill=color_for_hex(family["representative_hex"]))
        if width >= 42:
            draw.text((x + 4, 180), family["color_family_id"], fill="#111111", font=ImageFont.load_default())
        x += width
    image.save(FIGURES / "global_palettes.png")


def draw_family_distribution(families: list[dict[str, Any]], matrices: dict[int, np.ndarray]) -> None:
    image, draw = new_canvas(1200, 720, "Color-family distribution across palette scales")
    max_value = max(float(matrix.mean(axis=0).max()) for matrix in matrices.values())
    for family_index, family in enumerate(families):
        y = 60 + family_index * 36
        draw.rectangle((24, y, 44, y + 20), fill=color_for_hex(family["representative_hex"]))
        draw.text((50, y + 4), family["color_family_id"], fill="#111111", font=ImageFont.load_default())
        for scale_index, size in enumerate(PALETTE_SIZES):
            value = float(matrices[size][:, family_index].mean())
            x0 = 160 + scale_index * 330
            bar = int(260 * value / max_value)
            draw.rectangle((x0, y, x0 + bar, y + 20), fill=color_for_hex(family["representative_hex"]))
            draw.text((x0 + 265, y + 4), f"{size}: {value:.3f}", fill="#222222", font=ImageFont.load_default())
    image.save(FIGURES / "color_family_distribution.png")


def draw_cooccurrence_heatmap(cooccurrences: list[dict[str, Any]], families: list[dict[str, Any]]) -> None:
    ids = [row["color_family_id"] for row in families]
    selected = [
        row for row in cooccurrences if row["palette_size"] == 32 and row["presence_threshold"] == 0.02
    ]
    lookup = {(row["left_family_id"], row["right_family_id"]): row["lift"] for row in selected}
    cell = max(24, min(44, 880 // len(ids)))
    image, draw = new_canvas(180 + cell * len(ids), 150 + cell * len(ids), "Pairwise lift: 32 colors, 2% presence")
    for i, left in enumerate(ids):
        draw.text((115, 78 + i * cell), left, fill="#111111", font=ImageFont.load_default())
        draw.text((165 + i * cell, 58), left.replace("color_", "c"), fill="#111111", font=ImageFont.load_default())
        for j, right in enumerate(ids):
            if i == j:
                value = 1.0
            else:
                key = (left, right) if i < j else (right, left)
                value = lookup.get(key, 0.0)
            if i == j:
                color = (232, 232, 232)
            elif value >= 1.0:
                strength = max(0, min(1, (value - 1.0) / 0.45))
                color = (255, int(255 - 150 * strength), int(255 - 205 * strength))
            else:
                strength = max(0, min(1, (1.0 - value) / 0.20))
                color = (int(255 - 190 * strength), int(255 - 105 * strength), 255)
            x, y = 165 + j * cell, 75 + i * cell
            draw.rectangle((x, y, x + cell - 2, y + cell - 2), fill=color)
            if i != j:
                draw.text((x + 7, y + 15), f"{value:.2f}", fill="#111111", font=ImageFont.load_default())
    image.save(FIGURES / "color_cooccurrence_heatmap.png")


def draw_cross_scale(patterns: list[dict[str, Any]], families: list[dict[str, Any]]) -> None:
    shown = patterns[:20]
    image, draw = new_canvas(1100, 90 + 34 * len(shown), "Candidate pair patterns across 8, 16, and 32 colors")
    family_lookup = {row["color_family_id"]: row for row in families}
    for index, pattern in enumerate(shown):
        y = 62 + index * 34
        left, right = pattern["color_family_ids"]
        draw.rectangle((24, y, 46, y + 22), fill=color_for_hex(family_lookup[left]["representative_hex"]))
        draw.rectangle((48, y, 70, y + 22), fill=color_for_hex(family_lookup[right]["representative_hex"]))
        draw.text((80, y + 5), f"{left} + {right}", fill="#111111", font=ImageFont.load_default())
        for scale_index, size in enumerate(PALETTE_SIZES):
            x = 330 + scale_index * 140
            active = pattern["detected_at"][str(size)]
            draw.rectangle((x, y, x + 100, y + 22), fill="#247A50" if active else "#D7D7D7")
            draw.text((x + 38, y + 5), str(size), fill="white" if active else "#444444", font=ImageFont.load_default())
        draw.text((780, y + 5), pattern["scale_role"], fill="#222222", font=ImageFont.load_default())
    image.save(FIGURES / "cross_scale_patterns.png")


def make_contact_sheet(
    output: Path, title: str, indexes: list[int], samples: list[dict[str, Any]], captions: list[str] | None = None
) -> None:
    captions = captions or [samples[index]["catalogue_code"] for index in indexes]
    tile_w, tile_h, columns = 180, 220, 5
    rows = max(1, math.ceil(len(indexes) / columns))
    image, draw = new_canvas(columns * tile_w, 50 + rows * tile_h, title)
    for position, (sample_index, caption) in enumerate(zip(indexes, captions, strict=True)):
        x = (position % columns) * tile_w
        y = 48 + (position // columns) * tile_h
        try:
            with Image.open(image_path(samples[sample_index])) as source:
                thumbnail = source.convert("RGB")
                thumbnail.thumbnail((160, 180))
                image.paste(thumbnail, (x + (tile_w - thumbnail.width) // 2, y))
        except Exception:
            draw.rectangle((x + 10, y, x + 170, y + 180), fill="#EEEEEE")
        draw.text((x + 10, y + 184), caption[:26], fill="#111111", font=ImageFont.load_default())
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def update_experiment_status(summary: dict[str, Any]) -> None:
    path = HERE / "experiment.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "completed"
    value["completed_at"] = utc_now()
    value["output_summary"] = summary
    write_json(path, value)


def mine_patterns() -> None:
    samples = load_inputs()
    rows = read_jsonl(NORMALIZED / "palettes.jsonl")
    rows_by_scale = {size: [row for row in rows if row["palette_size"] == size] for size in PALETTE_SIZES}
    model, families, vocabulary_evaluations = choose_color_vocabulary(rows_by_scale[32])
    family_ids = [row["color_family_id"] for row in families]
    write_json(
        MINING / "global_color_families.json",
        {
            "experiment_id": EXPERIMENT_ID,
            "source_palette_size": 32,
            "candidate_evaluations": vocabulary_evaluations,
            "selected_family_count": len(families),
            "families": families,
        },
    )

    matrices: dict[int, np.ndarray] = {}
    vector_rows: dict[int, list[dict[str, Any]]] = {}
    all_cooccurrences: list[dict[str, Any]] = []
    all_cluster_rows: list[dict[str, Any]] = []
    all_outliers: list[dict[str, Any]] = []
    cluster_evaluations: list[dict[str, Any]] = []
    cluster_summaries: list[dict[str, Any]] = []

    for size in PALETTE_SIZES:
        matrix, csv_rows = vectorize(rows_by_scale[size], model, family_ids, samples)
        matrices[size] = matrix
        vector_rows[size] = csv_rows
        write_csv(
            MINING / f"image_color_vectors_{size}.csv",
            csv_rows,
            ["asset_id", "object_id", "catalogue_code", "series_code", *family_ids],
        )
        all_cooccurrences.extend(cooccurrence_rows(matrix, family_ids, size))
        labels, evaluations, summaries = cluster_images(matrix, size)
        cluster_evaluations.extend(evaluations)
        for summary in summaries:
            member_indexes = summary.pop("member_indexes")
            cluster_summaries.append(summary)
            cluster_id = summary["cluster_id"]
            center = np.asarray(summary["centroid"])
            member_matrix = matrix[member_indexes]
            distances = 1 - (member_matrix @ center) / np.maximum(
                np.linalg.norm(member_matrix, axis=1) * np.linalg.norm(center), 1e-12
            )
            for member_index, distance in zip(member_indexes, distances, strict=True):
                sample = samples[member_index]
                all_cluster_rows.append(
                    {
                        "palette_size": size,
                        "asset_id": sample["asset_id"],
                        "object_id": sample["object_id"],
                        "catalogue_code": sample["catalogue_code"],
                        "cluster_id": cluster_id,
                        "distance_to_centroid": float(distance),
                        "is_representative": member_index == summary["representative_index"],
                        "is_boundary_case": member_index == summary["boundary_index"],
                    }
                )
                all_outliers.append(
                    {
                        "palette_size": size,
                        "asset_id": sample["asset_id"],
                        "object_id": sample["object_id"],
                        "catalogue_code": sample["catalogue_code"],
                        "cluster_id": cluster_id,
                        "outlier_score": float(distance),
                    }
                )
            contact_indexes = [summary["representative_index"]] + [
                index for index in member_indexes if index != summary["representative_index"]
            ][:9]
            make_contact_sheet(
                CONTACT_SHEETS / f"{cluster_id}.png",
                f"{cluster_id}: representative first",
                contact_indexes,
                samples,
            )

    write_csv(MINING / "color_cooccurrences.csv", all_cooccurrences)
    write_csv(MINING / "image_clusters.csv", all_cluster_rows)
    write_csv(
        MINING / "outliers.csv", sorted(all_outliers, key=lambda row: (row["palette_size"], -row["outlier_score"])))

    pair_map: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in all_cooccurrences:
        if row["presence_threshold"] == 0.005 and row["case_count"] >= 5:
            pair_map[(row["left_family_id"], row["right_family_id"])][row["palette_size"]] = row
    patterns: list[dict[str, Any]] = []
    for pair, scales in pair_map.items():
        detected = {str(size): bool(size in scales and scales[size]["lift"] > 1.05) for size in PALETTE_SIZES}
        active = [size for size in PALETTE_SIZES if detected[str(size)]]
        if active == [8, 16, 32]:
            role = "global_stable"
        elif active == [16, 32]:
            role = "mid_scale"
        elif active == [32]:
            role = "detail_only"
        else:
            role = "unstable"
        patterns.append(
            {
                "pattern_id": "",
                "color_family_ids": list(pair),
                "detected_at": detected,
                "scale_role": role,
                "measurements": {str(size): scales.get(size) for size in PALETTE_SIZES},
            }
        )
    role_rank = {"global_stable": 0, "mid_scale": 1, "detail_only": 2, "unstable": 3}
    patterns.sort(
        key=lambda row: (
            role_rank[row["scale_role"]],
            -max((entry or {}).get("lift", 0) for entry in row["measurements"].values()),
        )
    )
    for index, pattern in enumerate(patterns, 1):
        pattern["pattern_id"] = f"pattern_{index:03d}"
    write_json(MINING / "candidate_patterns.json", {"experiment_id": EXPERIMENT_ID, "patterns": patterns})

    series_groups: dict[str, list[int]] = defaultdict(list)
    for index, sample in enumerate(samples):
        if sample.get("series_code"):
            series_groups[str(sample["series_code"])].append(index)
    series_rows: list[dict[str, Any]] = []
    for series, indexes in sorted(series_groups.items()):
        if len(indexes) < 2:
            continue
        mean = matrices[16][indexes].mean(axis=0)
        series_rows.append(
            {
                "series_code": series,
                "image_count": len(indexes),
                **{family_id: float(mean[j]) for j, family_id in enumerate(family_ids)},
            }
        )
    write_csv(MINING / "series_color_vectors_16.csv", series_rows)

    draw_global_palettes(families)
    draw_family_distribution(families, matrices)
    draw_cooccurrence_heatmap(all_cooccurrences, families)
    draw_cross_scale(patterns, families)

    image, draw = new_canvas(1100, 420, "Image clustering selected by silhouette score")
    for scale_index, size in enumerate(PALETTE_SIZES):
        candidates = [row for row in cluster_evaluations if row["palette_size"] == size]
        best = max(candidates, key=lambda row: row["silhouette"])
        x0 = 80 + scale_index * 340
        draw.text((x0, 70), f"{size}-color palettes", fill="#111111", font=ImageFont.load_default())
        for row_index, row in enumerate(candidates):
            y = 105 + row_index * 36
            draw.text((x0, y + 5), f"k={row['n_clusters']}", fill="#111111", font=ImageFont.load_default())
            draw.rectangle((x0 + 42, y, x0 + 42 + int(900 * max(row["silhouette"], 0)), y + 20), fill="#5B74A8")
            if row == best:
                draw.text((x0 + 245, y + 5), f"best {row['silhouette']:.3f}", fill="#111111", font=ImageFont.load_default())
    image.save(FIGURES / "image_cluster_summary.png")

    top_outliers = sorted(
        [row for row in all_outliers if row["palette_size"] == 16], key=lambda row: -row["outlier_score"]
    )[:15]
    index_by_asset = {sample["asset_id"]: index for index, sample in enumerate(samples)}
    make_contact_sheet(
        FIGURES / "outlier_contact_sheet.png",
        "Top color-composition outliers at the 16-color scale",
        [index_by_asset[row["asset_id"]] for row in top_outliers],
        samples,
        [f"{row['catalogue_code']}  {row['outlier_score']:.3f}" for row in top_outliers],
    )

    runs = read_jsonl(NORMALIZED / "extraction_runs.jsonl")
    failures = read_jsonl(NORMALIZED / "failures.jsonl")
    summary = {
        "input_assets": len(samples),
        "expected_runs": len(samples) * len(PALETTE_SIZES),
        "completed_runs": sum(row.get("status") == "completed" for row in runs),
        "failed_runs": len(failures),
        "normalized_palette_rows": len(rows),
        "selected_color_family_count": len(families),
        "candidate_patterns": len(patterns),
        "series_with_multiple_images": len(series_rows),
    }
    write_json(
        MINING / "analysis_summary.json",
        {
            "experiment_id": EXPERIMENT_ID,
            "summary": summary,
            "color_vocabulary_evaluations": vocabulary_evaluations,
            "cluster_evaluations": cluster_evaluations,
            "cluster_summaries": cluster_summaries,
        },
    )
    update_experiment_status(summary)


def validate() -> None:
    samples = load_inputs()
    rows = read_jsonl(NORMALIZED / "palettes.jsonl")
    runs = read_jsonl(NORMALIZED / "extraction_runs.jsonl")
    failures = read_jsonl(NORMALIZED / "failures.jsonl")
    assertions: list[tuple[str, bool, str]] = []
    assertions.append(("input_count", len(samples) == 163, f"observed={len(samples)}, expected=163"))
    assertions.append(
        ("run_count", len(runs) == len(samples) * 3, f"observed={len(runs)}, expected={len(samples) * 3}")
    )
    assertions.append(("no_failures", not failures, f"failures={len(failures)}"))
    by_palette: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_palette[(row["asset_id"], row["palette_size"])].append(row)
    frequency_errors = [
        (key, sum(row["frequency"] for row in values))
        for key, values in by_palette.items()
        if not math.isclose(sum(row["frequency"] for row in values), 1.0, abs_tol=1e-8)
    ]
    assertions.append(("frequency_sums", not frequency_errors, f"errors={len(frequency_errors)}"))
    assertions.append(
        (
            "all_scales_present",
            len(by_palette) == len(samples) * 3,
            f"observed={len(by_palette)}, expected={len(samples) * 3}",
        )
    )
    input_assets = {sample["asset_id"] for sample in samples}
    assertions.append(("traceable_ids", all(row["asset_id"] in input_assets and row["object_id"] for row in rows), ""))
    hash_mismatches: list[str] = []
    for sample in samples:
        digest = hashlib.sha256(image_path(sample).read_bytes()).hexdigest()
        if digest != sample["sha256"]:
            hash_mismatches.append(sample["asset_id"])
    assertions.append(("input_hashes", not hash_mismatches, f"mismatches={len(hash_mismatches)}"))

    if str(PYLETTE_ROOT) not in sys.path:
        sys.path.insert(0, str(PYLETTE_ROOT))
    from pylette import extract_colors

    a59_sample = next(sample for sample in samples if sample["object_id"] == "bridges_1980_a59")
    rerun = extract_colors(image_path(a59_sample), palette_size=8, resize=256, mode="OKLab")
    rerun_json = rerun.to_json(filename=None, colorspace="OKLab")
    raw8 = json.loads((RAW / "pylette_oklab_8.json").read_text(encoding="utf-8"))
    recorded_a59 = next(row for row in raw8["palettes"] if row["asset_id"] == a59_sample["asset_id"])
    deterministic = rerun_json is not None and len(rerun_json["colors"]) == len(recorded_a59["colors"])
    if deterministic:
        for observed, expected in zip(rerun_json["colors"], recorded_a59["colors"], strict=True):
            deterministic = deterministic and observed["rgb"] == expected["rgb"]
            deterministic = deterministic and observed["hex"] == expected["hex"]
            deterministic = deterministic and math.isclose(
                observed["frequency"], expected["frequency"], abs_tol=1e-12
            )
            deterministic = deterministic and bool(
                np.allclose(observed["oklab"], expected["oklab"], rtol=0.0, atol=1e-12)
            )
    assertions.append(
        ("a59_deterministic_rerun", deterministic, "OKLab/8 swatches and frequencies within 1e-12")
    )

    a59_rows = [row for row in rows if row["asset_id"] == a59_sample["asset_id"]]
    blue_reproduced = any(row["palette_size"] in (16, 32) and row["oklab"][2] < -0.03 for row in a59_rows)
    red_reproduced = any(row["palette_size"] == 32 and row["oklab"][1] > 0.05 for row in a59_rows)
    assertions.append(("a59_blue_reproduced", blue_reproduced, "expected in 16- or 32-color result"))
    assertions.append(("a59_red_reproduced", red_reproduced, "expected in 32-color result"))

    required_outputs = [
        MINING / "global_color_families.json",
        MINING / "color_cooccurrences.csv",
        MINING / "image_clusters.csv",
        MINING / "candidate_patterns.json",
        MINING / "outliers.csv",
        FIGURES / "global_palettes.png",
        FIGURES / "color_family_distribution.png",
        FIGURES / "color_cooccurrence_heatmap.png",
        FIGURES / "cross_scale_patterns.png",
        FIGURES / "image_cluster_summary.png",
        FIGURES / "outlier_contact_sheet.png",
    ]
    missing_outputs = [str(path.relative_to(HERE)) for path in required_outputs if not path.exists()]
    assertions.append(("required_outputs", not missing_outputs, f"missing={missing_outputs}"))
    report = {
        "experiment_id": EXPERIMENT_ID,
        "validated_at": utc_now(),
        "passed": all(passed for _, passed, _ in assertions),
        "checks": [{"check": name, "passed": passed, "detail": detail} for name, passed, detail in assertions],
    }
    write_json(OUTPUTS / "validation_report.json", report)
    if not report["passed"]:
        failed = ", ".join(name for name, passed, _ in assertions if not passed)
        raise SystemExit(f"Validation failed: {failed}")
    print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "extract", "mine", "validate", "all"), nargs="?", default="all")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--force", action="store_true", help="repeat palette extraction even when raw outputs exist")
    args = parser.parse_args()
    ensure_directories()
    if args.stage in {"prepare", "all"}:
        prepared = prepare_inputs()
        print(f"Prepared {len(prepared)} frozen input records.")
    if args.stage in {"extract", "all"}:
        extract_palettes(max_workers=args.max_workers, force=args.force)
    if args.stage in {"mine", "all"}:
        mine_patterns()
    if args.stage in {"validate", "all"}:
        validate()


if __name__ == "__main__":
    main()
