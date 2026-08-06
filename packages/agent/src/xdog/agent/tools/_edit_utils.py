"""Edit utilities — fuzzy matching, BOM, line endings, diff generation."""

from __future__ import annotations

import difflib
import unicodedata
from typing import Any


def detect_line_ending(content: str) -> str:
    """Detect the dominant line ending in *content*."""
    crlf_idx = content.find("\r\n")
    lf_idx = content.find("\n")
    if lf_idx == -1:
        return "\n"
    if crlf_idx == -1:
        return "\n"
    return "\r\n" if crlf_idx < lf_idx else "\n"


def normalize_to_lf(text: str) -> str:
    """Normalize all line endings to ``\\n``."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    """Restore *text* line endings to *ending*."""
    if ending == "\r\n":
        return text.replace("\n", "\r\n")
    return text


def strip_bom(content: str) -> tuple[str, str]:
    """Strip UTF-8 BOM if present. Returns ``(bom, text_without_bom)``."""
    if content.startswith("\ufeff"):
        return ("\ufeff", content[1:])
    return ("", content)


def normalize_for_fuzzy(text: str) -> str:
    """Normalize text for fuzzy matching."""
    text = unicodedata.normalize("NFKC", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    for ch in "\u2018\u2019\u201a\u201b":
        text = text.replace(ch, "'")
    for ch in "\u201c\u201d\u201e\u201f":
        text = text.replace(ch, '"')
    for ch in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212":
        text = text.replace(ch, "-")
    for ch in "\u00a0\u202f\u205f\u3000":
        text = text.replace(ch, " ")
    for cp in range(0x2002, 0x200b):
        text = text.replace(chr(cp), " ")
    return text


def fuzzy_find_text(
    content: str, old_text: str
) -> tuple[bool, int, int, bool, str]:
    """Find *old_text* in *content*, trying exact match first then fuzzy.

    Returns ``(found, index, match_length, used_fuzzy, content_for_replacement)``.
    """
    idx = content.find(old_text)
    if idx != -1:
        return (True, idx, len(old_text), False, content)
    fuzzy_content = normalize_for_fuzzy(content)
    fuzzy_old = normalize_for_fuzzy(old_text)
    idx = fuzzy_content.find(fuzzy_old)
    if idx == -1:
        return (False, -1, 0, False, content)
    return (True, idx, len(fuzzy_old), True, fuzzy_content)


def count_occurrences(content: str, old_text: str) -> int:
    """Count occurrences of *old_text* in *content* using fuzzy normalization."""
    fuzzy_content = normalize_for_fuzzy(content)
    fuzzy_old = normalize_for_fuzzy(old_text)
    return fuzzy_content.count(fuzzy_old)


def apply_edits(
    normalized_content: str,
    edits: list[dict[str, str]],
    path: str,
) -> tuple[str, str]:
    """Apply one or more exact-text replacements to LF-normalized content.

    Returns ``(base_content, new_content)``.
    """
    n_edits = len(edits)
    norm_edits = [
        {"old": normalize_to_lf(e["old_string"]), "new": normalize_to_lf(e["new_string"])}
        for e in edits
    ]
    for i, e in enumerate(norm_edits):
        if not e["old"]:
            label = f"edits[{i}]." if n_edits > 1 else ""
            raise ValueError(f"{label}old_string must not be empty in {path}.")

    initial_matches = [fuzzy_find_text(normalized_content, e["old"]) for e in norm_edits]
    base_content = (
        normalize_for_fuzzy(normalized_content)
        if any(m[3] for m in initial_matches)
        else normalized_content
    )

    matched: list[dict[str, Any]] = []
    for i, e in enumerate(norm_edits):
        found, idx, length, used_fuzzy, _ = fuzzy_find_text(base_content, e["old"])
        if not found:
            if n_edits == 1:
                raise ValueError(
                    f"Could not find the exact text in {path}. "
                    "The old text must match exactly including all whitespace and newlines."
                )
            raise ValueError(
                f"Could not find edits[{i}] in {path}. "
                "The oldText must match exactly including all whitespace and newlines."
            )
        occurrences = count_occurrences(base_content, e["old"])
        if occurrences > 1:
            if n_edits == 1:
                raise ValueError(
                    f"Found {occurrences} occurrences of the text in {path}. "
                    "The text must be unique. Please provide more context to make it unique."
                )
            raise ValueError(
                f"Found {occurrences} occurrences of edits[{i}] in {path}. "
                "Each oldText must be unique. Please provide more context."
            )
        matched.append({"edit_index": i, "match_index": idx, "match_length": length, "new_text": e["new"]})

    matched.sort(key=lambda m: m["match_index"])
    for j in range(1, len(matched)):
        prev = matched[j - 1]
        curr = matched[j]
        if prev["match_index"] + prev["match_length"] > curr["match_index"]:
            raise ValueError(
                f"edits[{prev['edit_index']}] and edits[{curr['edit_index']}] "
                f"overlap in {path}. Merge them into one edit or target disjoint regions."
            )

    new_content = base_content
    for m in reversed(matched):
        new_content = (
            new_content[: m["match_index"]]
            + m["new_text"]
            + new_content[m["match_index"] + m["match_length"] :]
        )

    if base_content == new_content:
        if n_edits == 1:
            raise ValueError(f"No changes made to {path}. The replacement produced identical content.")
        raise ValueError(f"No changes made to {path}. The replacements produced identical content.")

    return (base_content, new_content)


def generate_diff_string(
    old_content: str, new_content: str, context_lines: int = 4
) -> tuple[str, int | None]:
    """Generate a diff string with line numbers and limited context.

    Returns ``(diff_text, first_changed_line)``.
    """
    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")
    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    opcodes = sm.get_opcodes()
    max_line_num = max(len(old_lines), len(new_lines))
    line_num_width = len(str(max_line_num))
    output: list[str] = []
    first_changed_line: int | None = None

    for i, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag in ("replace", "delete", "insert"):
            if first_changed_line is None:
                first_changed_line = j1 + 1
            if tag in ("replace", "delete"):
                for k in range(i1, i2):
                    line_num = str(k + 1).rjust(line_num_width)
                    output.append(f"-{line_num} {old_lines[k]}")
            if tag in ("replace", "insert"):
                for k in range(j1, j2):
                    line_num = str(k + 1).rjust(line_num_width)
                    output.append(f"+{line_num} {new_lines[k]}")
        elif tag == "equal":
            equal_lines = list(range(i1, i2))
            total = len(equal_lines)
            prev_is_change = i > 0 and opcodes[i - 1][0] != "equal"
            next_is_change = i < len(opcodes) - 1 and opcodes[i + 1][0] != "equal"
            if not prev_is_change and not next_is_change:
                continue
            lines_to_show = equal_lines
            skip_start = 0
            skip_end = 0
            if not prev_is_change:
                skip_start = max(0, total - context_lines)
                lines_to_show = equal_lines[skip_start:]
            if not next_is_change and len(lines_to_show) > context_lines:
                skip_end = len(lines_to_show) - context_lines
                lines_to_show = lines_to_show[:context_lines]
            if prev_is_change and next_is_change and total > context_lines * 2:
                leading = equal_lines[:context_lines]
                trailing = equal_lines[-context_lines:]
                gap = total - context_lines * 2
                for k in leading:
                    line_num = str(k + 1).rjust(line_num_width)
                    output.append(f" {line_num} {old_lines[k]}")
                if gap > 0:
                    output.append(f" {''.rjust(line_num_width)} ...")
                for k in trailing:
                    line_num = str(k + 1).rjust(line_num_width)
                    output.append(f" {line_num} {old_lines[k]}")
                continue
            if skip_start > 0:
                output.append(f" {''.rjust(line_num_width)} ...")
            for k in lines_to_show:
                line_num = str(k + 1).rjust(line_num_width)
                output.append(f" {line_num} {old_lines[k]}")
            if skip_end > 0:
                output.append(f" {''.rjust(line_num_width)} ...")

    return ("\n".join(output), first_changed_line)
