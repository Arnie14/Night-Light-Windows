# SPDX-License-Identifier: GPL-3.0-only
"""Lints the Markdown documents: structure, links and anchors, tables, and
the project's writing rules. No external linter: the checks here are the
ones that have gone wrong in these documents before.

Archived reports (docs/history/) are checked for structure and links only;
they record how things were, old names and numbers included.
"""

import itertools
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = sorted([*ROOT.glob("*.md"), *ROOT.joinpath("docs").rglob("*.md")])
CURRENT = [path for path in DOCS if "history" not in path.relative_to(ROOT).parts]
FENCE = re.compile(r"^(```|~~~)")


def ids(paths):
    return [str(p.relative_to(ROOT)) for p in paths]


def prose_lines(path):
    """(line number, text) outside code fences."""
    inside = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if FENCE.match(line.strip()):
            inside = not inside
            continue
        if not inside:
            yield number, line


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path):
    found, counts = set(), {}
    for _number, line in prose_lines(path):
        if match := re.match(r"^#{1,6} (.+)$", line):
            base = slug(match.group(1))
            n = counts.get(base, 0)
            counts[base] = n + 1
            found.add(base if n == 0 else f"{base}-{n}")
    return found


def table_cells(line: str) -> int:
    """Cells in a table row, ignoring pipes inside code spans."""
    stripped = re.sub(r"`[^`]*`", "``", line.strip())
    return len(stripped.strip("|").split("|"))


@pytest.mark.parametrize("path", DOCS, ids=ids(DOCS))
def test_structure(path):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    problems = []
    headings = [(n, len(m.group(1))) for n, line in prose_lines(path) if (m := re.match(r"^(#{1,6}) ", line))]
    if not headings or headings[0][1] != 1 or lines[0].strip() != lines[headings[0][0] - 1].strip():
        problems.append("the document starts with one # title")
    if sum(1 for _n, level in headings if level == 1) != 1:
        problems.append("exactly one # title")
    for (_n0, before), (n, level) in itertools.pairwise(headings):
        if level > before + 1:
            problems.append(f"line {n}: heading level jumps from {before} to {level}")
    if sum(1 for line in lines if FENCE.match(line.strip())) % 2:
        problems.append("a code fence isn't closed")
    for n, line in enumerate(lines, 1):
        if line != line.rstrip():
            problems.append(f"line {n}: trailing whitespace")
        if "\t" in line:
            problems.append(f"line {n}: tab")
    rows = list(prose_lines(path))
    for i, (_n, line) in enumerate(rows):
        if re.match(r"^\s*\|?\s*:?-{3,}", line) and "|" in line and i > 0:
            width = table_cells(rows[i - 1][1])
            for m, row in rows[i + 1:]:
                if "|" not in row or not row.strip():
                    break
                if table_cells(row) != width:
                    problems.append(f"line {m}: {table_cells(row)} cells, the header has {width}")
    assert problems == []


@pytest.mark.parametrize("path", DOCS, ids=ids(DOCS))
def test_links_and_anchors_resolve(path):
    problems = []
    for n, line in prose_lines(path):
        for target in re.findall(r"\]\(([^)\s]+)\)", line):
            if re.match(r"^[a-z]+:", target) or target.startswith("mailto:"):
                continue
            file_part, _, anchor = target.partition("#")
            linked = (path.parent / file_part).resolve() if file_part else path
            if not linked.exists():
                problems.append(f"line {n}: {target}: no such file")
            elif anchor and linked.suffix == ".md" and anchor not in anchors(linked):
                problems.append(f"line {n}: {target}: no such heading")
    assert problems == []


# The project's writing rules (docs/development.md#writing-for-this-project).
RULES = [
    (re.compile(r"Anurag", re.I), "the author's name appears only inside the app"),
    (re.compile(r"\bthis (laptop|PC|machine|computer)\b", re.I), 'the hardware is "the test laptop"'),
    (re.compile("[\U0001F300-\U0001FAFF✅❌➖✔]"), "no emoji status marks"),
    (re.compile(r"\b\d{3,} (tests|passed)\b"), "no quoted test counts: CI reports them"),
    (re.compile(r"Windows 10 and 11|Windows 10/11"), "Windows 10 isn't tested: don't claim it"),
    (re.compile(r"Night-Light-Windows"), "the repository is github.com/Arnie14/WinLight"),
]


@pytest.mark.parametrize("path", CURRENT, ids=ids(CURRENT))
def test_writing_rules(path):
    problems = []
    for n, line in prose_lines(path):
        for pattern, rule in RULES:
            if pattern.search(line):
                problems.append(f"line {n}: {rule}")
    assert problems == []


def test_the_rules_catch_what_they_should():
    samples = {
        "Created by Anurag Jha": 0, "Tested on this PC": 1, "Status: ✅": 2, "651 tests pass": 3,
        "Windows 10 and 11, 64-bit": 4, "github.com/Arnie14/Night-Light-Windows": 5,
    }
    for text, index in samples.items():
        assert RULES[index][0].search(text), text
    assert slug("3. Gamma ramps: night light, dimming and calibrations") == "3-gamma-ramps-night-light-dimming-and-calibrations"
