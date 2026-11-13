#!/usr/bin/env python3
"""Страж TDD: ни один тест из тега stage-NN-red не удалён и не пропущен (skip/xfail)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SKIP_NAMES = {"skip", "skipif", "xfail"}


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or f"git {' '.join(args)} failed")
    return p.stdout


def tag_files(tag: str) -> list[str]:
    out = git("ls-tree", "-r", "--name-only", tag, "--", "tests")
    return [f for f in out.splitlines() if f.endswith(".py") and Path(f).name.startswith("test_")]


def mentions_skip(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in SKIP_NAMES:
            return True
        if isinstance(sub, ast.Name) and sub.id in SKIP_NAMES:
            return True
    return False


def collect(tree: ast.Module) -> dict[str, bool]:
    """Имя теста (Class.test) -> пропущен ли он (декоратор, pytestmark, pytest.skip в теле)."""
    module_skip = any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in n.targets)
        and mentions_skip(n.value)
        for n in tree.body
    )
    found: dict[str, bool] = {}

    def visit(body: list[ast.stmt], prefix: str, inherited: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                cls_skip = inherited or any(mentions_skip(d) for d in node.decorator_list)
                visit(node.body, f"{prefix}{node.name}.", cls_skip)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                skipped = (
                    inherited
                    or any(mentions_skip(d) for d in node.decorator_list)
                    or any(mentions_skip(s) for s in node.body)
                )
                found[f"{prefix}{node.name}"] = skipped

    visit(tree.body, "", module_skip)
    return found


def check_tag(tag: str) -> list[str]:
    problems: list[str] = []
    for path in tag_files(tag):
        original = collect(ast.parse(git("show", f"{tag}:{path}")))
        current_file = Path(path)
        if not current_file.exists():
            problems.append(f"{path}: файл с тестами удалён ({', '.join(original)})")
            continue
        current = collect(ast.parse(current_file.read_text()))
        for name in original:
            if name not in current:
                problems.append(f"{path}: тест {name} удалён")
            elif current[name]:
                problems.append(f"{path}: тест {name} пропущен (skip/xfail)")
    return problems


def main(argv: list[str]) -> int:
    try:
        tags = argv or git("tag", "--merged", "HEAD", "-l", "stage-*-red").split()
        if not tags:
            print("ОШИБКА: нет ни одного тега stage-NN-red в истории HEAD", file=sys.stderr)
            return 1
        problems = [f"[{t}] {p}" for t in tags for p in check_tag(t)]
    except RuntimeError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        return 1
    if problems:
        print("TDD-страж: исходные тесты нарушены:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"TDD-страж: ок ({len(tags)} тег(ов): {' '.join(tags)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
