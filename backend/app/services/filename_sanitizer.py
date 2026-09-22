from __future__ import annotations

import re
from typing import Set, Union, Collection


INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
WHITESPACE_RUNS = re.compile(r'\s+')


def sanitize_test_title_to_filename(title: str, max_length: int = 120) -> str:
    """
    Sanitizes a test case title into a safe, deterministic, human-readable filename base.
    
    Rules:
    - Preserves meaningful words and human readability.
    - Replaces invalid filesystem characters (\\ / : * ? " < > | and control chars) with spaces.
    - Normalizes multiple spaces into a single space.
    - Strips leading and trailing dots, spaces, hyphens, and underscores.
    - Safely truncates at word boundaries if exceeding max_length.
    - Fallback to 'untitled_test' if title is empty or completely stripped.
    - ZERO UUIDs, timestamps, hashes, or random numbers.
    """
    if not title or not isinstance(title, str):
        return "untitled_test"

    # Replace invalid filesystem characters with a space
    cleaned = INVALID_FILENAME_CHARS.sub(" ", title)

    # Normalize whitespace
    cleaned = WHITESPACE_RUNS.sub(" ", cleaned).strip()

    # Strip leading/trailing dots, underscores, hyphens
    cleaned = cleaned.strip(". -_")

    if not cleaned:
        return "untitled_test"

    # Truncate at word boundary if too long
    if len(cleaned) > max_length:
        truncated = cleaned[:max_length]
        # Try to break on the last space
        last_space = truncated.rfind(" ")
        if last_space > max_length // 2:
            cleaned = truncated[:last_space].strip(". -_")
        else:
            cleaned = truncated.strip(". -_")

    return cleaned or "untitled_test"


def resolve_unique_script_filename(
    title: str,
    tc_id: str,
    ext: str,
    existing_filenames: Collection[str],
    current_tc_id: str = "",
) -> str:
    """
    Determines a deterministic, human-readable script filename with collision resolution.

    - Preferred: `<sanitized_title><ext>`
    - If collision exists with a DIFFERENT test case:
      Deterministic collision strategy: `<sanitized_title>__<clean_tc_id><ext>`
    - Never uses timestamps, random numbers, or UUIDs.
    """
    if not ext.startswith("."):
        ext = f".{ext}" if ext else ""

    base = sanitize_test_title_to_filename(title)
    candidate = f"{base}{ext}"

    # If the candidate does not collide, or if the caller indicates it's the same test case, use it
    if candidate not in existing_filenames:
        return candidate

    # If candidate is already in existing_filenames, append __<clean_tc_id>
    clean_tc = re.sub(r'[^a-zA-Z0-9_-]', '', tc_id).strip() or "TC"
    collision_candidate = f"{base}__{clean_tc}{ext}"
    return collision_candidate
