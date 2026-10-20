# SPDX-License-Identifier: GPL-3.0-only
"""Every import of the app's own modules points at something that exists.

Parses each source file rather than trusting the test suite to reach every
import: a module moved between subpackages, or a name renamed, fails here
even if no other test happens to import that path.
"""

import ast
import importlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = sorted([*ROOT.joinpath("nightlight").rglob("*.py"), ROOT / "main.py", *ROOT.joinpath("tools").glob("*.py")])
# Modules that only import on their own platform.
PLATFORM_ONLY = {"nightlight.hardware.kde": "linux"}


def module_name(path: pathlib.Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def own_imports(path: pathlib.Path):
    """(line, module, names) for each import of nightlight or main."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "nightlight":
                    yield node.lineno, alias.name, []
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0 and node.module.split(".")[0] == "nightlight":
            yield node.lineno, node.module, [alias.name for alias in node.names]


def module_exists(name: str) -> bool:
    relative = pathlib.Path(*name.split("."))
    return (ROOT / relative).with_suffix(".py").is_file() or (ROOT / relative / "__init__.py").is_file()


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_own_imports_resolve(path):
    problems = []
    for line, module, names in own_imports(path):
        if not module_exists(module):
            problems.append(f"line {line}: no module {module}")
            continue
        for name in names:
            if name == "*" or module_exists(f"{module}.{name}"):
                continue
            source = ROOT.joinpath(*module.split("."))
            source = source / "__init__.py" if source.is_dir() else source.with_suffix(".py")
            tree = ast.parse(source.read_text(encoding="utf-8"))
            defined = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))}
            defined |= {name.id for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))
                        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                        for name in ast.walk(target) if isinstance(name, ast.Name)}
            defined |= {alias.asname or alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                        for alias in node.names}
            if name not in defined:
                problems.append(f"line {line}: {module} has no {name}")
    assert problems == []


@pytest.mark.parametrize("name", sorted(module_name(p) for p in SOURCES if p.parts[-2:] != ("tools", p.name)))
def test_every_module_imports(name):
    platform = PLATFORM_ONLY.get(name)
    if platform and not sys.platform.startswith(platform):
        pytest.skip(f"{name} only imports on {platform}")
    importlib.import_module(name)
