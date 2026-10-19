import tempfile
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from work_sources import WorkSources


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
    root = Path(directory)
    (root / "CURRENT_STATUS.md").write_text(
        """# CURRENT STATUS

Дата обновления: 2026-07-24

## Активные задачи

| Задача | Статус | Следующий шаг |
|---|---|---|
| Вибрация T28 | Пауза | Выбрать направление |
| Техкарта | Расчётная | Контрольный рез |

## Заблокировано
""",
        encoding="utf-8",
    )
    (root / "CONTEXT.md").write_text("context", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("log", encoding="utf-8")

    sources = WorkSources(root)
    summary = sources.status_summary(today=date(2026, 9, 21))
    assert "устарел на 59 дн." in summary
    assert "Вибрация T28 — Пауза" in summary
    assert "Техкарта — Расчётная" in summary
    assert "Только чтение" in summary
    assert "Монитор ресурса стекол" in summary

    catalog_path = root / "catalog.json"
    catalog_path.write_text(
        '[{"name":"Проверяемый файл","path":"draft.xlsx","status":"требует проверки"}]',
        encoding="utf-8",
    )
    catalog_sources = WorkSources(root, catalog_path)
    catalog_summary = catalog_sources.catalog_summary()
    assert "Проверяемый файл — требует проверки; не найден" in catalog_summary
    assert "только для справки" in catalog_summary

missing = WorkSources(Path(directory) / "missing")
assert "не подключена" in missing.status_summary()
assert missing.catalog() == []

print("all checks passed")
