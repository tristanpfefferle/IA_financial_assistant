"""Regression tests preventing invalid dotted tool names from reappearing."""

from __future__ import annotations

from pathlib import Path


def test_repo_contains_no_legacy_dotted_finance_tool_names() -> None:
    """Ensure legacy dotted finance tool prefixes do not exist in tracked source/docs files."""

    repo_root = Path(__file__).resolve().parents[1]
    target_suffixes = {".py", ".md", ".ts", ".tsx"}

    matches: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix not in target_suffixes:
            continue

        relative_path = path.relative_to(repo_root)
        if any(part in {".git", ".venv", "node_modules", "dist", "build"} for part in relative_path.parts):
            continue

        content = path.read_text(encoding="utf-8")
        legacy_prefix = "finance" + ".transactions."
        if legacy_prefix in content:
            matches.append(str(relative_path))

    assert not matches, f"Legacy dotted tool prefix found in: {', '.join(matches)}"
