"""Генератор docs/db/metrics.md из каталога metrics/definitions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from xcopilot.metrics.catalog import MetricDefinition, load_definitions


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _dimensions(dimensions: list[str]) -> str:
    if not dimensions:
        return "—"
    return ", ".join(f"`{d}`" for d in dimensions)


def render_metrics_md(definitions: list[MetricDefinition] | None = None) -> str:
    if definitions is None:
        definitions = load_definitions()
    definitions = sorted(definitions, key=lambda d: d.key)

    lines: list[str] = [
        "# Каталог метрик",
        "",
        (
            "> Генерируется командой `make db-docs` из `src/xcopilot/metrics/definitions/*.sql` "
            "(`xcopilot.metrics.docs`). Не редактировать вручную."
        ),
        "",
        f"Метрик: {len(definitions)}.",
        "",
        "## Как читать",
        "",
        (
            "Единицы: `ratio` — доля 0..1; `count` — штуки; `micro_usd` — миллионные доли "
            "доллара (BIGINT-деньги); `minutes` — минуты. Зерно: `day` — день по поясу "
            "аккаунта, `week` — неделя пн–вс. «Сегодня» считается на лету."
        ),
        "",
        "## Сводка",
        "",
        "| Ключ | Что показывает | Единица | Зерно | Измерения |",
        "|---|---|---|---|---|",
    ]

    for metric in definitions:
        description = f" {metric.description}" if metric.description else ""
        lines.append(
            f"| `{metric.key}` | {_cell(metric.title)}.{_cell(description)} | "
            f"`{metric.unit}` | `{metric.grain}` | {_dimensions(metric.dimensions)} |"
        )

    for metric in definitions:
        lines.extend(["", f"## `{metric.key}`", "", metric.title, ""])
        if metric.description:
            lines.extend([metric.description, ""])
        dimensions_part = (
            f" · измерения: {', '.join(f'`{d}`' for d in metric.dimensions)}"
            if metric.dimensions
            else ""
        )
        lines.extend(
            [
                f"Единица: `{metric.unit}` · зерно: `{metric.grain}` · версия: {metric.version}"
                f"{dimensions_part}",
                "",
                "Формула:",
                "",
                "```sql",
                metric.formula_sql,
                "```",
            ]
        )

    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    root = root or Path.cwd()
    docs_dir = root / "docs" / "db"
    metrics_path = docs_dir / "metrics.md"

    rendered = render_metrics_md()

    if args.check:
        current = metrics_path.read_text() if metrics_path.exists() else None
        if current != rendered:
            print("metrics-docs: устарело, запусти make db-docs", file=sys.stderr)
            return 1
        return 0

    docs_dir.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(rendered)
    print("metrics-docs: обновлено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
