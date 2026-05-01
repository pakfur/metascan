"""Pure duplicate detection logic using perceptual hashing.

This module contains no UI dependencies — used by the FastAPI backend.
"""

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov"}


def _is_video_path(file_path: str) -> bool:
    """Check if a file path refers to a video based on extension."""
    return Path(file_path).suffix.lower() in VIDEO_EXTENSIONS


def _find_groups_single_partition(
    phashes: Dict[str, str],
    threshold: int,
    progress_callback: Optional[Callable[[int, int], bool]],
    comparisons_offset: int,
    total_comparisons: int,
    report_interval: int,
) -> Tuple[List[List[Tuple[str, int]]], int, bool]:
    """Run duplicate grouping on a single partition of same-type files.

    Returns (groups, comparisons_done, was_cancelled).
    """
    import imagehash

    paths = list(phashes.keys())
    hashes = [imagehash.hex_to_hash(phashes[p]) for p in paths]
    n = len(paths)

    visited: Set[int] = set()
    groups: List[List[Tuple[str, int]]] = []
    comparisons_done = comparisons_offset

    for i in range(n):
        if i in visited:
            continue

        group = [(paths[i], 0)]
        visited.add(i)

        for j in range(i + 1, n):
            if j in visited:
                continue
            dist = hashes[i] - hashes[j]
            comparisons_done += 1
            if dist <= threshold:
                group.append((paths[j], dist))
                visited.add(j)

            if progress_callback and comparisons_done % report_interval == 0:
                if not progress_callback(comparisons_done, total_comparisons):
                    if len(group) > 1:
                        groups.append(group)
                    return groups, comparisons_done, True

        if len(group) > 1:
            groups.append(group)

    return groups, comparisons_done, False


def find_phash_duplicate_groups(
    phashes: Dict[str, str],
    threshold: int = 10,
    progress_callback: Optional[Callable[[int, int], bool]] = None,
) -> List[List[Tuple[str, int]]]:
    """Find groups of files with similar perceptual hashes.

    Images and videos are grouped separately — a group will never contain
    both image and video files.

    Args:
        phashes: Dict mapping file_path -> phash hex string.
        threshold: Maximum hamming distance to consider as duplicate.
        progress_callback: Called with (current, total) comparisons.
            Return False to cancel.

    Returns:
        List of groups, where each group is a list of (file_path, distance_to_first) tuples.
    """
    image_hashes = {p: h for p, h in phashes.items() if not _is_video_path(p)}
    video_hashes = {p: h for p, h in phashes.items() if _is_video_path(p)}

    ni = len(image_hashes)
    nv = len(video_hashes)
    total_comparisons = ni * (ni - 1) // 2 + nv * (nv - 1) // 2
    report_interval = max(total_comparisons // 100, 500) if total_comparisons > 0 else 1

    all_groups: List[List[Tuple[str, int]]] = []

    if image_hashes:
        groups, cmp_done, cancelled = _find_groups_single_partition(
            image_hashes,
            threshold,
            progress_callback,
            comparisons_offset=0,
            total_comparisons=total_comparisons,
            report_interval=report_interval,
        )
        all_groups.extend(groups)
        if cancelled:
            return all_groups
    else:
        cmp_done = 0

    if video_hashes:
        groups, cmp_done, cancelled = _find_groups_single_partition(
            video_hashes,
            threshold,
            progress_callback,
            comparisons_offset=cmp_done,
            total_comparisons=total_comparisons,
            report_interval=report_interval,
        )
        all_groups.extend(groups)
        if cancelled:
            return all_groups

    if progress_callback:
        progress_callback(total_comparisons, total_comparisons)

    return all_groups
