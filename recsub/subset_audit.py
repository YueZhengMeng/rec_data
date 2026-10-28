"""Shared read-only audits for REC-8K derived subset annotations."""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


def read_json(path_: str | Path) -> dict[str, Any]:
    path_ = Path(path_)
    with path_.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"Expected an image-keyed JSON object: {path_}")
    return data


def point_key(point: Any) -> tuple[float | int, float | int]:
    if not isinstance(point, list) or len(point) != 2:
        raise ValueError(f"Invalid point (expected [x, y]): {point!r}")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in point):
        raise ValueError(f"Invalid non-finite or non-numeric point: {point!r}")
    return tuple(point)  # type: ignore[return-value]


def unique_points(points: list[Any]) -> tuple[list[Any], int]:
    seen: set[tuple] = set()
    result = []
    for point in points:
        key = point_key(point)
        if key in seen:
            continue
        seen.add(key)
        result.append(point)
    return result, len(points) - len(result)


def duplicate_report(data: Mapping[str, Any]) -> dict[str, Any]:
    details = []
    total = unique_total = 0
    for image, captions in data.items():
        if not isinstance(captions, dict):
            raise ValueError(f"Image records must be objects: {image}")
        for caption, record in captions.items():
            points = record.get("points", [])
            if not isinstance(points, list):
                raise ValueError(f"points is not a list: {image}/{caption}")
            keys = [point_key(point) for point in points]
            counts = Counter(keys)
            total += len(keys)
            unique_total += len(counts)
            duplicate_entries = len(keys) - len(counts)
            if duplicate_entries:
                details.append({"image": image, "caption": caption,
                                "point_count": len(keys), "unique_coordinates": len(counts),
                                "duplicate_entries": duplicate_entries,
                                "duplicate_coordinates": [list(key) for key, n in counts.items() if n > 1]})
    return {"caption_count": sum(len(captions) for captions in data.values()),
            "total_points": total, "unique_coordinates": unique_total,
            "duplicate_entries": total - unique_total,
            "captions_with_duplicates": len(details), "details": details}


def _pair_rows(subsets: Mapping[str, Any], original: Mapping[str, Any]) -> list[tuple]:
    pairs = []
    for image, captions in subsets.items():
        if not isinstance(captions, dict):
            raise ValueError(f"Subset image records must be objects: {image}")
        for caption, record in captions.items():
            polarity = record.get("subset_polarity")
            root = record.get("root_caption")
            mate_caption = record.get("complement_caption")
            mate = captions.get(mate_caption) if isinstance(mate_caption, str) else None
            if polarity not in ("yes", "no"):
                raise ValueError(f"Invalid subset_polarity: {image}/{caption}")
            if not isinstance(root, str) or root not in original.get(image, {}):
                raise ValueError(f"Root caption is absent from raw annotations: {image}/{caption}")
            if mate is None:
                raise ValueError(f"Missing complementary caption: {image}/{caption}")
            expected = "no" if polarity == "yes" else "yes"
            if (mate.get("subset_polarity") != expected or mate.get("root_caption") != root
                    or mate.get("complement_caption") != caption):
                raise ValueError(f"Invalid bidirectional complementary link: {image}/{caption}")
            if polarity == "yes":
                pairs.append((image, root, caption, record, mate_caption, mate))
    return pairs


def audit_pair_overlaps(subsets: Mapping[str, Any], original: Mapping[str, Any]) -> dict[str, Any]:
    overlap_pairs = []
    coverage_failures = []
    pairs = _pair_rows(subsets, original)
    for image, root, yes_caption, yes_record, no_caption, no_record in pairs:
        yes_points = {point_key(point) for point in yes_record.get("points", [])}
        no_points = {point_key(point) for point in no_record.get("points", [])}
        overlap = yes_points & no_points
        source_points = {point_key(point) for point in original[image][root].get("points", [])}
        if overlap:
            source_counts = Counter(point_key(point) for point in original[image][root].get("points", []))
            overlap_pairs.append({"image": image, "root_caption": root,
                                  "yes_caption": yes_caption, "no_caption": no_caption,
                                  "overlapping_unique_coordinates": len(overlap),
                                  "overlap_coordinates": [list(point) for point in sorted(overlap)],
                                  "all_overlap_coordinates_duplicated_in_source": all(
                                      source_counts[point] > 1 for point in overlap)})
        union = yes_points | no_points
        if union != source_points:
            coverage_failures.append({"image": image, "root_caption": root,
                                      "yes_caption": yes_caption, "no_caption": no_caption,
                                      "yes_union_no_unique_coordinates": len(union),
                                      "source_unique_coordinates": len(source_points),
                                      "missing_from_pair": [list(point) for point in sorted(source_points - union)],
                                      "outside_source": [list(point) for point in sorted(union - source_points)]})
    return {"complementary_pair_count": len(pairs),
            "complementary_pairs_with_coordinate_overlap": len(overlap_pairs),
            "overlap_pair_details": overlap_pairs,
            "complementary_pairs_with_coverage_mismatch": len(coverage_failures),
            "coverage_mismatch_details": coverage_failures}


def root_text_conflicts(subsets: Mapping[str, Any], original: Mapping[str, Any]) -> list[dict[str, Any]]:
    conflicts = []
    for image, captions in subsets.items():
        for caption, record in captions.items():
            root = record.get("root_caption")
            source = original.get(image, {}).get(root)
            if not isinstance(root, str) or source is None:
                continue
            generated_caption = f"{record.get('class', '')} {record.get('attribute', '')}".strip()
            same_key = caption == root
            same_generated = generated_caption == root
            source_points = Counter(point_key(point) for point in source.get("points", []))
            subset_points = Counter(point_key(point) for point in record.get("points", []))
            if same_key or same_generated:
                conflicts.append({"image": image, "caption": caption, "root_caption": root,
                                  "same_caption_key": same_key, "generated_caption": generated_caption,
                                  "same_generated_caption": same_generated,
                                  "subset_point_count": sum(subset_points.values()),
                                  "root_point_count": sum(source_points.values()),
                                  "point_multiset_equal": subset_points == source_points,
                                  "subset_record": record, "source_root_record": source})
    return conflicts


def same_text_different_points(subsets: Mapping[str, Any], original: Mapping[str, Any]) -> list[dict[str, Any]]:
    conflicts = []
    for image, captions in subsets.items():
        for caption, record in captions.items():
            source = original.get(image, {}).get(caption)
            if source is None:
                continue
            subset_points = Counter(point_key(point) for point in record.get("points", []))
            source_points = Counter(point_key(point) for point in source.get("points", []))
            if subset_points != source_points:
                conflicts.append({"image": image, "caption": caption,
                                  "subset_point_count": sum(subset_points.values()),
                                  "source_point_count": sum(source_points.values()),
                                  "subset_polarity": record.get("subset_polarity"),
                                  "subset_record": record, "source_record": source})
    return conflicts
