"""Folder renaming rules for duplicated copies."""

from __future__ import annotations


def generate_folder_name(original_name: str, index: int) -> str:
    """Generate a renamed folder for the given 1-based copy index.

    Rules:
    - Every text segment before an underscore gets the copy index appended.
    - If the final segment is numeric, increment it by the index.
    - If the last segment is not numeric, append the index to it as well.

    Examples:
        joe_mead_100, 1  -> joe1_mead1_101
        joe_mead_100, 2  -> joe2_mead2_102
        project_backup, 1 -> project1_backup1
        simple, 3          -> simple3
    """
    if index < 1:
        raise ValueError("index must be a positive integer")

    name = original_name.strip()
    if not name:
        raise ValueError("original_name must not be empty")

    parts = name.split("_")
    if len(parts) == 1:
        part = parts[0]
        if part.isdigit():
            return str(int(part) + index)
        return f"{part}{index}"

    renamed: list[str] = []
    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1
        if is_last and part.isdigit():
            renamed.append(str(int(part) + index))
        else:
            renamed.append(f"{part}{index}")
    return "_".join(renamed)


def preview_names(original_name: str, count: int, limit: int = 8) -> list[str]:
    """Return a short preview list of generated folder names."""
    if count < 1:
        return []
    names = [generate_folder_name(original_name, i) for i in range(1, min(count, limit) + 1)]
    if count > limit:
        names.append(f"... +{count - limit} more")
    return names
