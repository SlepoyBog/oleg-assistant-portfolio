import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


@dataclass(frozen=True)
class SourceInfo:
    name: str
    relative_path: str
    status: str
    exists: bool


class WorkSources:
    """Read-only access to the external WORK knowledge base."""

    CORE_SOURCES = (
        ("Оперативный статус", "CURRENT_STATUS.md", "оперативный"),
        ("Контекст", "CONTEXT.md", "справочный"),
        ("Журнал изменений", "CHANGELOG.md", "история"),
        ("Монитор ресурса стекол", "Монитор ресурса стекол", "отдельная система"),
        ("Режимы резки", "Режимы резки", "проверять статус данных"),
    )

    def __init__(self, root, catalog_path=None):
        self.root = Path(root) if root else None
        self.catalog_path = Path(catalog_path) if catalog_path else None

    @property
    def available(self):
        return bool(self.root and self.root.is_dir())

    def catalog(self):
        if not self.available:
            return []
        if self.catalog_path and self.catalog_path.is_file():
            items = json.loads(self.catalog_path.read_text(encoding="utf-8-sig"))
            return [
                SourceInfo(
                    item["name"],
                    item["path"],
                    item.get("status", "требует проверки"),
                    (self.root / item["path"]).exists(),
                )
                for item in items
            ]
        return [
            SourceInfo(name, relative_path, status, (self.root / relative_path).exists())
            for name, relative_path, status in self.CORE_SOURCES
        ]

    def catalog_summary(self):
        if not self.available:
            return "Рабочая база не подключена. Проверьте WORK_ROOT в .env."
        items = self.catalog()
        if not items:
            return "Реестр рабочих источников пока пуст."
        lines = ["Рабочие источники:"]
        for item in items:
            availability = "найден" if item.exists else "не найден"
            lines.append(f"• {item.name} — {item.status}; {availability}")
        lines.append("До подтверждения используйте данные только для справки.")
        return "\n".join(lines)

    def status_summary(self, today=None):
        if not self.available:
            return "Рабочая база не подключена. Проверьте WORK_ROOT в .env."

        status_path = self.root / "CURRENT_STATUS.md"
        if not status_path.is_file():
            return "Рабочая база найдена, но CURRENT_STATUS.md отсутствует."

        text = status_path.read_text(encoding="utf-8-sig")
        match = re.search(r"Дата обновления:\s*(\d{4}-\d{2}-\d{2})", text)
        updated = datetime.strptime(match.group(1), "%Y-%m-%d").date() if match else None
        reference_date = today or date.today()
        age = (reference_date - updated).days if updated else None

        tasks = []
        section = re.search(
            r"## Активные задачи\s*(.*?)(?=\n##\s|\Z)", text, flags=re.DOTALL
        )
        if section:
            for line in section.group(1).splitlines():
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if len(cells) >= 3 and cells[0] not in {"Задача", "---"} and not set(cells[0]) <= {"-", ":"}:
                    tasks.append((cells[0], cells[1]))

        heading = "Рабочий статус"
        if updated:
            heading += f" от {updated.isoformat()}"
            if age is not None and age > 30:
                heading += f" (устарел на {age} дн.)"
        else:
            heading += " (дата обновления не найдена)"

        lines = [heading]
        lines.extend(f"• {name} — {status}" for name, status in tasks[:10])
        missing = [item.name for item in self.catalog() if not item.exists]
        if missing:
            lines.append("Не найдены источники: " + ", ".join(missing))
        lines.append("Только чтение: бот не изменяет рабочие файлы.")
        return "\n".join(lines)
