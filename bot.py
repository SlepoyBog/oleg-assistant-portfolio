import asyncio
import ctypes
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import InvalidToken, NetworkError
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, TypeHandler, filters,
)

from storage import CATEGORY_NAMES, Storage
from exporter import export_workbook
from plan_exporter import export_management_plan
from scheduler import Planner
from work_sources import WorkSources
from work_lookup import WorkLookup
from network_watchdog import ConnectivityWatchdog, NetworkWatchdog, PendingUpdateWatchdog

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PAIRING_CODE = os.getenv("PAIRING_CODE", "").strip()
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "").strip()


def local_path(env_name, default):
    value = Path(os.getenv(env_name, default))
    return value if value.is_absolute() else BASE_DIR / value


DB_PATH = local_path("DATABASE_PATH", "data/assistant.db")
BACKUP_DIR = local_path("BACKUP_DIR", "backups")
EXPORT_DIR = local_path("EXPORT_DIR", "exports")
PLAN_TEMPLATE_PATH = local_path(
    "PLAN_TEMPLATE_PATH",
    "templates/weekly-plan.xlsx",
)
PLAN_FILE_PATH = local_path(
    "PLAN_FILE_PATH", "data/weekly-plan.xlsx"
)
WORK_ROOT = os.getenv("WORK_ROOT", "").strip()
work_sources = WorkSources(WORK_ROOT, BASE_DIR / "config" / "work_sources.json")
work_lookup = WorkLookup(WORK_ROOT)
NETWORK_ERROR_LIMIT = 8
CONNECTIVITY_FAILURE_LIMIT = 3
network_watchdog = NetworkWatchdog(limit=NETWORK_ERROR_LIMIT, window_seconds=300)
pending_update_watchdog = PendingUpdateWatchdog(limit=2)
connectivity_watchdog = ConnectivityWatchdog(limit=CONNECTIVITY_FAILURE_LIMIT)
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
store = Storage(DB_PATH)

logger = logging.getLogger("oleg_assistant")
logger.setLevel(logging.INFO)
file_handler = RotatingFileHandler(
    LOG_DIR / "assistant.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(file_handler)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

CATEGORY_ALIASES = {
    "станки": "machines", "станок": "machines",
    "детали": "parts", "деталь": "parts",
    "размеры": "dimensions", "габариты": "dimensions", "тара": "dimensions",
    "материалы": "materials", "материал": "materials", "оснастка": "tooling",
    "закупки": "purchases", "покупки": "purchases",
    "режимы": "cutting_modes", "режим": "cutting_modes",
    "техпроцессы": "processes", "карты": "processes",
}

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["📅 План", "📥 Входящие"],
        ["➕ Задача", "📝 Заметки"],
        ["📦 Операция", "📦 Остатки"],
        ["📅 План Excel", "📊 Excel"],
        ["📜 История", "📦 Остатки"],
        ["➕ Карточка", "🗂 Карточки"],
        ["🔎 Поиск", "ℹ️ Помощь"],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Напишите заметку, дело или команду…",
)

BUTTON_COMMANDS = {
    "📅 план": "план",
    "📅 план excel": "план excel",
    "📥 входящие": "входящие",
    "📝 заметки": "заметки",
    "✅ дела": "дела",
    "📦 остатки": "остатки",
    "📜 история": "история",
    "📊 excel": "экспорт excel",
    "🗂 карточки": "карточки",
    "ℹ️ помощь": "помощь",
}

ENTRY_TYPE_NAMES = {
    "note": "заметка",
    "task": "задача",
    "event": "событие",
    "idea": "идея",
    "log": "запись дня",
}

WEEKDAYS = {
    "понедельник": 0, "понедельника": 0,
    "вторник": 1, "вторника": 1,
    "среда": 2, "среду": 2,
    "четверг": 3, "четверга": 3,
    "пятница": 4, "пятницу": 4,
    "суббота": 5, "субботу": 5,
    "воскресенье": 6,
}


def planning_fields(text, today=None):
    today = today or date.today()
    low = text.casefold()
    planned = None
    if "сегодня" in low:
        planned = today
    elif "завтра" in low:
        planned = today + timedelta(days=1)
    else:
        match = re.search(r"\b([0-3]?\d)[./]([01]?\d)(?:[./](\d{2,4}))?\b", low)
        if match:
            day, month, year = match.groups()
            year = int(year) if year else today.year
            if year < 100:
                year += 2000
            try:
                planned = date(year, int(month), int(day))
            except ValueError:
                planned = None
        else:
            for word, weekday in WEEKDAYS.items():
                if re.search(rf"\b{word}\b", low):
                    days = (weekday - today.weekday()) % 7
                    planned = today + timedelta(days=days)
                    break
    time_match = re.search(r"\b(?:в\s*)?([01]?\d|2[0-3]):([0-5]\d)\b", low)
    scheduled_at = (
        f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
        if time_match else None
    )
    return (
        planned.isoformat() if planned else None,
        scheduled_at,
    )


def capture_kind(raw):
    patterns = (
        ("note", r"^(?:запомни|заметка|запиши)[:\s-]+(.+)$"),
        ("task", r"^(?:нужно|надо|купить|задача|дело)[:\s-]+(.+)$"),
        ("event", r"^(?:событие|встреча)[:\s-]+(.+)$"),
        ("idea", r"^(?:идея|можно попробовать)[:\s-]+(.+)$"),
        ("log", r"^(?:запись дня|сегодня сделал|сделал)[:\s-]+(.+)$"),
    )
    for entry_type, pattern in patterns:
        match = re.match(pattern, raw, re.I)
        if match:
            return entry_type, match.group(1).strip(), "confirmed"
    low = raw.casefold()
    if any(word in low for word in ("встреча", "приедет", "созвон", "совещание")):
        planned_for, scheduled_at = planning_fields(raw)
        if planned_for or scheduled_at:
            return "event", raw, "confirmed"
    return "inbox", raw, "inbox"


def format_plan(rows, title):
    if not rows:
        return f"{title}\nЗапланированных задач и событий нет."
    lines = [title]
    last_date = object()
    for row in rows:
        if row["planned_for"] != last_date:
            last_date = row["planned_for"]
            label = (
                datetime.fromisoformat(last_date).strftime("%d.%m.%Y")
                if last_date else "Без даты"
            )
            lines.append(f"\n{label}")
        marker = "🗓" if row["entry_type"] == "event" else "☐"
        clock = f" {row['scheduled_at']}" if row["scheduled_at"] else ""
        lines.append(f"{marker}{clock} #{row['id']} {row['text']}")
    return "\n".join(lines)


def classification_keyboard(entry_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Задача", callback_data=f"entry_kind:task:{entry_id}"),
            InlineKeyboardButton("Заметка", callback_data=f"entry_kind:note:{entry_id}"),
        ],
        [
            InlineKeyboardButton("Событие", callback_data=f"entry_kind:event:{entry_id}"),
            InlineKeyboardButton("Идея", callback_data=f"entry_kind:idea:{entry_id}"),
        ],
        [InlineKeyboardButton("Запись дня", callback_data=f"entry_kind:log:{entry_id}")],
    ])


def duration_keyboard(entry_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("15 мин", callback_data=f"task_duration:15:{entry_id}"),
            InlineKeyboardButton("30 мин", callback_data=f"task_duration:30:{entry_id}"),
            InlineKeyboardButton("45 мин", callback_data=f"task_duration:45:{entry_id}"),
        ],
        [
            InlineKeyboardButton("1 час", callback_data=f"task_duration:60:{entry_id}"),
            InlineKeyboardButton("1,5 часа", callback_data=f"task_duration:90:{entry_id}"),
            InlineKeyboardButton("2 часа", callback_data=f"task_duration:120:{entry_id}"),
        ],
        [InlineKeyboardButton("Другая длительность", callback_data=f"task_duration:custom:{entry_id}")],
    ])


def task_date_keyboard(entry_id, today=None):
    today = today or date.today()
    tomorrow = today + timedelta(days=1)
    days_until_monday = (7 - today.weekday()) % 7 or 7
    next_monday = today + timedelta(days=days_until_monday)

    def button(label, value):
        return InlineKeyboardButton(
            f"{label} {value.strftime('%d.%m')}",
            callback_data=f"task_date:{value.isoformat()}:{entry_id}",
        )

    return InlineKeyboardMarkup([
        [button("Сегодня", today), button("Завтра", tomorrow)],
        [button("Следующий понедельник", next_monday)],
        [InlineKeyboardButton(
            "Другая дата", callback_data=f"task_date:custom:{entry_id}"
        )],
    ])


def priority_keyboard(entry_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Высокий", callback_data=f"task_priority:high:{entry_id}"),
        InlineKeyboardButton("Средний", callback_data=f"task_priority:normal:{entry_id}"),
        InlineKeyboardButton("Низкий", callback_data=f"task_priority:low:{entry_id}"),
    ]])


def reprioritize_keyboard(entry_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Высокий", callback_data=f"task_repriority_value:high:{entry_id}"),
        InlineKeyboardButton("Средний", callback_data=f"task_repriority_value:normal:{entry_id}"),
        InlineKeyboardButton("Низкий", callback_data=f"task_repriority_value:low:{entry_id}"),
    ]])


def deadline_keyboard(entry_id, planned_for):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Без срока", callback_data=f"task_deadline:none:{entry_id}"),
         InlineKeyboardButton("В день выполнения", callback_data=f"task_deadline:{planned_for}:{entry_id}")],
        [InlineKeyboardButton("Другая дата", callback_data=f"task_deadline:custom:{entry_id}")],
    ])


def parse_duration_minutes(text):
    value = text.strip().casefold().replace(",", ".")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:ч|час|часа|часов)", value)
    minutes = round(float(match.group(1)) * 60) if match else None
    if minutes is None:
        match = re.fullmatch(r"(\d+)\s*(?:м|мин|минута|минуты|минут)", value)
        minutes = int(match.group(1)) if match else None
    if not minutes or minutes <= 0:
        return None
    return ((minutes + 14) // 15) * 15


def task_date_prompt(entry_id, prefix):
    return (
        f"{prefix}\nНа какой день добавить задачу?",
        task_date_keyboard(entry_id),
    )


def task_priority_prompt(entry_id, planned_for):
    label = datetime.fromisoformat(planned_for).strftime("%d.%m.%Y")
    return (
        f"Дата: {label}. Какой приоритет?",
        priority_keyboard(entry_id),
    )


def schedule_open_tasks(today=None):
    today = today or date.today()
    week_start = today - timedelta(days=today.weekday())
    rows = [dict(row) for row in store.scheduling_entries()]
    result = Planner().build(rows, week_start, not_before=today)
    store.apply_schedule(result.allocations)
    return result


def refresh_management_plan(today=None):
    today = today or date.today()
    schedule_open_tasks(today)
    try:
        export_management_plan(
            store, PLAN_TEMPLATE_PATH, PLAN_FILE_PATH, today=today
        )
    except (FileNotFoundError, KeyError, OSError, ValueError):
        store.set_setting("plan_dirty", "1")
        logger.warning("Management plan refresh deferred", exc_info=True)
        return False
    store.set_setting("plan_dirty", "0")
    return True


def owner_id():
    value = store.get_setting("owner_id")
    return int(value) if value else None


def private(update):
    return update.effective_chat and update.effective_chat.type == ChatType.PRIVATE


def number(value):
    return float(value.replace(",", "."))


def display_number(value):
    return f"{value:g}".replace(".", ",")


def display_delta(value):
    return f"{value:+g}".replace(".", ",")


def format_inventory_history(rows):
    return "\n".join(
        f"{row['created_at'][:16].replace('T', ' ')} — "
        f"{display_delta(row['delta'])} {row['name']}"
        + (f"; {row['machine']}" if row["machine"] else "")
        for row in rows
    )


def format_card(row):
    card_type = "Деталь" if row["card_type"] == "part" else "Станок"
    lines = [f"{card_type}: {row['name']}"]
    for key, title in (
        ("machine", "Станок"), ("material", "Материал"),
        ("thickness", "Толщина"), ("notes", "Примечание"),
    ):
        if row[key]:
            lines.append(f"{title}: {row[key]}")
    return "\n".join(lines)


async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not private(update) or not update.effective_user or not update.message:
        return
    if owner_id():
        await update.message.reply_text("Помощник уже привязан к владельцу.")
    elif not context.args or context.args[0] != PAIRING_CODE:
        await update.message.reply_text("Неверный код привязки.")
    else:
        store.set_setting("owner_id", update.effective_user.id)
        await update.message.reply_text(
            "Готово. Я принимаю сообщения только от вас. Выберите действие в меню ниже.",
            reply_markup=MAIN_MENU,
        )


def authorized(update):
    return (
        private(update)
        and update.effective_message
        and update.effective_user
        and owner_id()
        and update.effective_user.id == owner_id()
    )


async def process_text(update: Update, raw: str):
    raw = raw.strip()
    low = raw.casefold()
    raw = BUTTON_COMMANDS.get(low, raw)
    low = raw.casefold()

    awaiting_duration = store.get_setting("awaiting_task_duration")
    if awaiting_duration:
        minutes = parse_duration_minutes(raw)
        if not minutes:
            await update.message.reply_text("Не понял длительность. Напишите, например: 3 часа или 75 минут.")
            return
        entry_id = int(awaiting_duration)
        store.set_setting("awaiting_task_duration", "")
        if not store.set_task_duration(entry_id, minutes):
            await update.message.reply_text("Открытая задача уже не найдена.")
            return
        result = schedule_open_tasks()
        allocation = result.allocations.get(entry_id)
        published = refresh_management_plan()
        if allocation:
            suffix = " Excel обновлён." if published else " Excel обновится позже."
            await update.message.reply_text(
                f"Запланировал задачу #{entry_id} на {allocation.planned_for:%d.%m.%Y} "
                f"в {allocation.scheduled_at} на {minutes} мин.{suffix}"
            )
        else:
            await update.message.reply_text(f"Сохранил длительность: {minutes} мин. Свободное время пока не найдено.")
        return

    awaiting_deadline = store.get_setting("awaiting_task_deadline")
    if awaiting_deadline:
        planned_for, _ = planning_fields(raw)
        if not planned_for:
            await update.message.reply_text("Не понял срок. Напишите дату, например: 10.10.2026.")
            return
        entry_id = int(awaiting_deadline)
        store.set_setting("awaiting_task_deadline", "")
        store.set_task_deadline(entry_id, planned_for)
        await update.message.reply_text(
            f"Крайний срок: {datetime.fromisoformat(planned_for):%d.%m.%Y}. Сколько времени потребуется?",
            reply_markup=duration_keyboard(entry_id),
        )
        return

    awaiting_date = store.get_setting("awaiting_task_date")
    if awaiting_date:
        planned_for, _ = planning_fields(raw)
        if not planned_for:
            await update.message.reply_text(
                "Не понял дату. Напишите, например: 05.10, 05.10.2026, "
                "сегодня или завтра."
            )
            return
        entry_id = int(awaiting_date)
        if not store.set_task_planned_for(entry_id, planned_for):
            store.set_setting("awaiting_task_date", "")
            await update.message.reply_text("Открытая задача уже не найдена.")
            return
        store.set_setting("awaiting_task_date", "")
        prompt, markup = task_priority_prompt(entry_id, planned_for)
        await update.message.reply_text(prompt, reply_markup=markup)
        return

    if low == "🔎 поиск":
        await update.message.reply_text(
            "Напишите: найди тара\nНапример: найди защитное стекло",
            reply_markup=MAIN_MENU,
        )
        return

    if low in {"меню", "menu"}:
        await update.message.reply_text("Главное меню открыто.", reply_markup=MAIN_MENU)
        return

    if low in {"помощь", "help", "что ты умеешь"}:
        await update.message.reply_text(
            "Быстрые команды доступны через кнопку «Меню» рядом с полем ввода.\n\n"
            "Заметки и дела:\n"
            "• запомни размер тары 1140х740\n"
            "• нужно купить защитные стекла\n"
            "• измени 1: новый текст\n• удали 1\n"
            "• заметки / дела / найди тара\n"
            "• план / сегодня / завтра / неделя / входящие\n"
            "• план excel — создать таблицу для руководства\n"
            "• событие: встреча завтра в 10:00\n"
            "• идея: попробовать другое сопло\n\n"
            "Карточки:\n"
            "• карточка детали Корпус 150_125; станок=Longxin T28; "
            "материал=09Г2С; толщина=8 мм\n"
            "• покажи карточку Корпус 150_125\n• карточки\n\n"
            "Расходники (разделитель | обязателен):\n"
            "• приход 20 | защитное стекло\n"
            "• расход 2 | защитное стекло | Longxin T28\n"
            "• минимум 5 | защитное стекло\n"
            "• остатки / остаток защитное стекло\n"
            "• история защитное стекло\n\n"
            "• экспорт excel\n"
            "• рабочий статус / /status\n"
            "• источники / /sources\n"
            "• распил ЦПП\n"
            "• режим Ст20 резка\n"
            "• режим Ст20 Ду15/10\n"
            "• резервная копия"
        )
        return

    if low in {"план", "сегодня"}:
        day = date.today().isoformat()
        await update.message.reply_text(format_plan(store.plan_entries(day), "План на сегодня"))
        return

    if low == "завтра":
        day = (date.today() + timedelta(days=1)).isoformat()
        await update.message.reply_text(format_plan(store.plan_entries(day), "План на завтра"))
        return

    if low == "неделя":
        start = date.today()
        end = start + timedelta(days=6)
        await update.message.reply_text(
            format_plan(store.plan_entries(start.isoformat(), end.isoformat()), "План на 7 дней")
        )
        return

    if low in {"план excel", "распорядок excel"}:
        if refresh_management_plan():
            await update.message.reply_text(
                f"Актуальный план обновлён: {PLAN_FILE_PATH}\n"
                "Предыдущая версия сохранена в папке «Архив», если она существовала."
            )
        else:
            await update.message.reply_text(
                "Сейчас не удалось обновить план Excel. Данные сохранены, "
                "бот повторит попытку автоматически."
            )
        return

    if low == "входящие":
        rows = store.inbox_entries()
        if not rows:
            await update.message.reply_text("Неразобранных входящих нет.")
            return
        await update.message.reply_text(
            "Входящие:\n" + "\n".join(f"#{row['id']} {row['text']}" for row in rows)
        )
        for row in rows:
            await update.message.reply_text(
                f"Разобрать #{row['id']}: {row['text']}",
                reply_markup=classification_keyboard(row["id"]),
            )
        return

    if low in {"рабочий статус", "статус работы", "status"}:
        await update.message.reply_text(work_sources.status_summary())
        return

    if low in {"источники", "рабочие источники", "sources"}:
        await update.message.reply_text(work_sources.catalog_summary())
        return

    saw_match = re.match(r"^распил(?:\s+(.+))?$", raw, re.I)
    if saw_match:
        rows = work_lookup.saw_tables(saw_match.group(1) or "")
        if not rows:
            await update.message.reply_text("Утверждённая таблица распила не найдена.")
            return
        text = "Утверждённые таблицы распила:\n" + "\n".join(
            f"• {row['name']}\n  Дата: {row['date']}; статус: {row['status']}"
            for row in rows
        )
        await update.message.reply_text(text)
        return

    mode_match = re.match(r"^режим(?:ы)?(?:\s+(.+))?$", raw, re.I)
    if mode_match:
        query = mode_match.group(1) or ""
        rows = work_lookup.cutting_rows(query)
        sources = work_lookup.cutting_sources(query)
        lines = []
        for row in rows:
            lines.append(
                f"{row['part']} — {row['material']}\n"
                f"Толщина: {row['thickness']}; сопло: {row['nozzle']}\n"
                f"Скорость: {display_number(row['speed'])} м/мин; "
                f"давление: {display_number(row['pressure'])} bar\n"
                f"Мощность: {display_number(row['power'])}%; "
                f"фокус: {display_number(row['focus'])} мм\n"
                f"Статус реестра: утверждено; статус строки Excel: {row['status']}\n"
                f"Источник: {row['source']}"
            )
        if sources:
            lines.append("PDF: " + ", ".join(row["name"] for row in sources))
        if not lines:
            await update.message.reply_text("Утверждённый режим не найден. Уточните материал и операцию или патрубок.")
            return
        await update.message.reply_text("\n\n".join(lines))
        return

    edit = re.match(r"^измени\s+#?(\d+)\s*:\s*(.+)$", raw, re.I)
    if edit:
        entry_id, text = int(edit.group(1)), edit.group(2).strip()
        ok, category = store.edit_entry(entry_id, text)
        await update.message.reply_text(
            f"Запись #{entry_id} изменена [{CATEGORY_NAMES[category]}]."
            if ok else "Запись с таким номером не найдена."
        )
        return

    delete = re.match(r"^удали\s+#?(\d+)$", low)
    if delete:
        entry_id = int(delete.group(1))
        row = store.entry(entry_id)
        if not row:
            await update.message.reply_text("Запись с таким номером не найдена.")
            return
        store.set_setting("pending_delete_id", entry_id)
        store.set_setting("pending_delete_until", time.time() + 300)
        await update.message.reply_text(
            f"Удалить #{entry_id}: «{row['text']}»?\n"
            f"В течение 5 минут отправьте: подтверди удаление {entry_id}"
        )
        return

    confirm = re.match(r"^подтверди удаление\s+#?(\d+)$", low)
    if confirm:
        entry_id = int(confirm.group(1))
        expected = store.get_setting("pending_delete_id")
        expires = float(store.get_setting("pending_delete_until") or 0)
        if expected == str(entry_id) and time.time() <= expires:
            ok = store.delete_entry(entry_id)
            store.set_setting("pending_delete_id", "")
            store.set_setting("pending_delete_until", "0")
            await update.message.reply_text(
                f"Запись #{entry_id} удалена." if ok else "Запись уже отсутствует."
            )
        else:
            await update.message.reply_text("Подтверждение отсутствует или устарело.")
        return

    card = re.match(r"^карточка\s+(детали|станка)\s+(.+)$", raw, re.I)
    if card:
        card_type = "part" if card.group(1).casefold() == "детали" else "machine"
        sections = [section.strip() for section in card.group(2).split(";")]
        name = sections[0]
        fields = {}
        field_aliases = {
            "станок": "machine", "материал": "material", "толщина": "thickness",
            "примечание": "notes", "заметка": "notes",
        }
        for section in sections[1:]:
            pair = re.split(r"\s*=\s*", section, maxsplit=1)
            if len(pair) == 2 and pair[0].casefold() in field_aliases:
                fields[field_aliases[pair[0].casefold()]] = pair[1].strip()
        store.save_card(card_type, name, fields)
        await update.message.reply_text(f"Карточка «{name}» сохранена.")
        return

    show_card = re.match(r"^покажи карточку\s+(.+)$", raw, re.I)
    if show_card:
        row = store.find_card(show_card.group(1))
        await update.message.reply_text(format_card(row) if row else "Карточка не найдена.")
        return

    if low in {"карточки", "карточки деталей", "карточки станков"}:
        card_type = "part" if low == "карточки деталей" else "machine" if low == "карточки станков" else None
        result = store.cards(card_type)
        await update.message.reply_text(
            "\n\n".join(format_card(row) for row in result)
            or "Карточек пока нет."
        )
        return

    movement = re.match(
        r"^(приход|расход)\s+([0-9]+(?:[.,][0-9]+)?)\s*\|\s*([^|]+?)(?:\s*\|\s*(.+))?$",
        raw, re.I,
    )
    if movement:
        operation, quantity, name, machine = movement.groups()
        delta = number(quantity) * (1 if operation.casefold() == "приход" else -1)
        item = store.inventory_change(name, delta, machine)
        warning = (
            "\n⚠ Остаток ниже установленного минимума."
            if item["quantity"] <= item["min_quantity"] and item["min_quantity"] > 0 else ""
        )
        await update.message.reply_text(
            f"Остаток «{name.strip()}»: {display_number(item['quantity'])} {item['unit']}.{warning}"
        )
        return

    minimum = re.match(
        r"^минимум\s+([0-9]+(?:[.,][0-9]+)?)\s*\|\s*(.+)$", raw, re.I
    )
    if minimum:
        value, name = minimum.groups()
        item = store.set_minimum(name, number(value))
        await update.message.reply_text(
            f"Минимальный запас «{name.strip()}»: {display_number(item['min_quantity'])} {item['unit']}."
        )
        return

    if low == "остатки":
        result = store.inventory_list()
        keyboard = [
            [
                InlineKeyboardButton(
                    f"📜 {row['name']}", callback_data=f"stock_history:{row['id']}"
                ),
                InlineKeyboardButton("+1", callback_data=f"stock_adjust:{row['id']}:1"),
                InlineKeyboardButton("−1", callback_data=f"stock_adjust:{row['id']}:-1"),
            ]
            for row in result
        ]
        await update.message.reply_text(
            "\n".join(
                f"{row['name']}: {display_number(row['quantity'])} {row['unit']}"
                + (" ⚠" if row["min_quantity"] > 0 and row["quantity"] <= row["min_quantity"] else "")
                for row in result
            ) or "Расходники пока не добавлены.",
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
        )
        return

    stock = re.match(r"^остаток\s+(.+)$", raw, re.I)
    if stock:
        result = store.inventory_find(stock.group(1))
        await update.message.reply_text(
            "\n".join(
                f"{row['name']}: {display_number(row['quantity'])} {row['unit']}; "
                f"минимум {display_number(row['min_quantity'])}"
                for row in result
            ) or "Расходник не найден."
        )
        return

    history = re.match(r"^история(?:\s+(.+))?$", raw, re.I)
    if history:
        if not history.group(1):
            await update.message.reply_text(
                "Укажите расходник: история защитное стекло"
            )
            return
        rows = store.inventory_history(history.group(1))
        await update.message.reply_text(
            format_inventory_history(rows) or "История расходника не найдена."
        )
        return

    if low in {"экспорт", "экспорт excel", "excel"}:
        filename = f"oleg-assistant-{datetime.now():%Y%m%d-%H%M%S}.xlsx"
        path = export_workbook(store, EXPORT_DIR / filename)
        await update.message.reply_text(
            f"Excel создан локально: exports\\{path.name}"
        )
        return

    if low == "резервная копия":
        path = store.backup(BACKUP_DIR)
        await update.message.reply_text(
            f"Резервная копия создана локально: {path.name}"
        )
        return

    if low in {"заметки", "покажи заметки"}:
        result = store.list_entries("note")
        await update.message.reply_text(
            "\n".join(
                f"{row['id']}. [{CATEGORY_NAMES[row['category']]}] {row['text']}"
                for row in result
            ) or "Заметок пока нет."
        )
        return

    if low in {"дела", "задачи", "покажи дела"}:
        result = store.list_entries("task")
        keyboard = [
            [
                InlineKeyboardButton(f"✅ #{row['id']}", callback_data=f"task_done:{row['id']}"),
                InlineKeyboardButton(f"🗑 #{row['id']}", callback_data=f"task_delete:{row['id']}"),
                InlineKeyboardButton("➡ Завтра", callback_data=f"task_tomorrow:{row['id']}"),
                InlineKeyboardButton("⚑ Приоритет", callback_data=f"task_repriority:{row['id']}"),
            ]
            for row in result
        ]
        await update.message.reply_text(
            "\n".join(
                f"{row['id']}. [{CATEGORY_NAMES[row['category']]}] {row['text']}"
                for row in result
            ) or "Открытых дел нет.",
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
        )
        return

    if low == "категории":
        await update.message.reply_text(
            "Категории: станки, детали, размеры, материалы, оснастка, "
            "закупки, режимы, техпроцессы."
        )
        return

    category_match = re.match(r"^(?:покажи|список)\s+(.+)$", low)
    if category_match and category_match.group(1) in CATEGORY_ALIASES:
        category = CATEGORY_ALIASES[category_match.group(1)]
        result = store.category_entries(category)
        await update.message.reply_text(
            "\n".join(f"{row['id']}. {row['text']}" for row in result)
            or f"В категории «{CATEGORY_NAMES[category]}» записей пока нет."
        )
        return

    search = re.match(r"^(?:найди|поиск)\s+(.+)$", raw, re.I)
    if search:
        result = store.search(search.group(1))
        await update.message.reply_text(
            "\n".join(
                f"{row['id']}. [{CATEGORY_NAMES[row['category']]}] {row['text']}"
                for row in result
            ) or "Ничего не найдено."
        )
        return

    complete = re.match(r"^(?:готово|выполнено|закрыть)\s+#?(\d+)$", low)
    if complete:
        entry_id = int(complete.group(1))
        completed = store.complete(entry_id)
        if completed:
            refresh_management_plan()
        await update.message.reply_text(
            f"Дело {entry_id} выполнено."
            if completed else "Открытое дело с таким номером не найдено."
        )
        return

    kind, text, classification_status = capture_kind(raw)
    planned_for, scheduled_at = planning_fields(text)
    if kind == "event" and not planned_for:
        planned_for = date.today().isoformat()
    entry_id, category = store.add_entry(
        kind, text, planned_for=planned_for, scheduled_at=scheduled_at,
        classification_status=classification_status, original_text=raw,
    )
    if kind == "inbox":
        await update.message.reply_text(
            f"Сохранил во входящие #{entry_id}. Что это?",
            reply_markup=classification_keyboard(entry_id),
        )
        return
    if kind == "task":
        prefix = f"Сохранил задачу #{entry_id} [{CATEGORY_NAMES[category]}]: {text}"
        if planned_for:
            prompt, markup = task_priority_prompt(entry_id, planned_for)
            await update.message.reply_text(f"{prefix}\n{prompt}", reply_markup=markup)
        else:
            prompt, markup = task_date_prompt(entry_id, prefix)
            await update.message.reply_text(prompt, reply_markup=markup)
        return
    when = ""
    if planned_for:
        when = f" на {datetime.fromisoformat(planned_for).strftime('%d.%m.%Y')}"
        if scheduled_at:
            when += f" в {scheduled_at}"
    await update.message.reply_text(
        f"Сохранил: {ENTRY_TYPE_NAMES[kind]} #{entry_id}{when} "
        f"[{CATEGORY_NAMES[category]}]: {text}"
    )


TASK_TEXT, STOCK_ACTION, STOCK_NAME, STOCK_QTY, STOCK_MACHINE = range(5)
CARD_TYPE, CARD_NAME, CARD_MACHINE, CARD_MATERIAL, CARD_THICKNESS = range(10, 15)


async def cancel_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Действие отменено.")
    elif update.message:
        await update.message.reply_text("Действие отменено.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def new_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("Что нужно сделать? Отправьте текст задачи. Для отмены: /cancel")
    return TASK_TEXT


async def new_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    planned_for, scheduled_at = planning_fields(text)
    entry_id, category = store.add_entry(
        "task", text, planned_for=planned_for, scheduled_at=scheduled_at
    )
    prefix = f"Добавил дело #{entry_id} [{CATEGORY_NAMES[category]}]: {text}"
    if planned_for:
        prompt, markup = task_priority_prompt(entry_id, planned_for)
    else:
        prompt, markup = task_date_prompt(entry_id, prefix)
        prefix = ""
    await update.message.reply_text(
        f"{prefix}\n{prompt}".strip(), reply_markup=markup
    )
    context.user_data.clear()
    return ConversationHandler.END


async def stock_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "Выберите операцию:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➕ Приход", callback_data="stock:in"),
                InlineKeyboardButton("➖ Расход", callback_data="stock:out"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="stock:cancel")],
        ]),
    )
    return STOCK_ACTION


async def stock_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "stock:cancel":
        await query.edit_message_text("Действие отменено.")
        return ConversationHandler.END
    context.user_data["stock_operation"] = query.data.split(":")[1]
    await query.edit_message_text("Введите наименование расходника:")
    return STOCK_NAME


async def stock_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stock_name"] = update.message.text.strip()
    await update.message.reply_text("Введите количество числом, например: 2")
    return STOCK_QTY


async def stock_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        quantity = number(update.message.text.strip())
        if quantity <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Введите положительное число, например: 2")
        return STOCK_QTY
    context.user_data["stock_quantity"] = quantity
    if context.user_data["stock_operation"] == "out":
        await update.message.reply_text("Укажите станок или отправьте «пропустить»:")
        return STOCK_MACHINE
    return await finish_stock(update, context, None)


async def stock_machine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    machine = None if value.casefold() in {"пропустить", "-", "нет"} else value
    return await finish_stock(update, context, machine)


async def finish_stock(update, context, machine):
    operation = context.user_data["stock_operation"]
    quantity = context.user_data["stock_quantity"]
    delta = quantity if operation == "in" else -quantity
    name = context.user_data["stock_name"]
    item = store.inventory_change(name, delta, machine)
    action = "Приход" if operation == "in" else "Расход"
    warning = (
        " ⚠ Остаток ниже установленного минимума."
        if item["quantity"] <= item["min_quantity"] and item["min_quantity"] > 0 else ""
    )
    await update.message.reply_text(
        f"{action} сохранён. Остаток «{name}»: "
        f"{display_number(item['quantity'])} {item['unit']}.{warning}",
        reply_markup=MAIN_MENU,
    )
    context.user_data.clear()
    return ConversationHandler.END


async def card_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "Что создаём?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Деталь", callback_data="card:part"),
                InlineKeyboardButton("Станок", callback_data="card:machine"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="card:cancel")],
        ]),
    )
    return CARD_TYPE


async def card_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "card:cancel":
        await query.edit_message_text("Действие отменено.")
        return ConversationHandler.END
    context.user_data["card_type"] = query.data.split(":")[1]
    await query.edit_message_text("Введите название:")
    return CARD_NAME


async def card_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["card_name"] = update.message.text.strip()
    await update.message.reply_text("Укажите станок или отправьте «пропустить»:")
    return CARD_MACHINE


async def card_machine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    if value.casefold() not in {"пропустить", "-", "нет"}:
        context.user_data["machine"] = value
    await update.message.reply_text("Укажите материал или отправьте «пропустить»:")
    return CARD_MATERIAL


async def card_material(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    if value.casefold() not in {"пропустить", "-", "нет"}:
        context.user_data["material"] = value
    await update.message.reply_text("Укажите толщину или отправьте «пропустить»:")
    return CARD_THICKNESS


async def card_thickness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    if value.casefold() not in {"пропустить", "-", "нет"}:
        context.user_data["thickness"] = value
    card_kind = context.user_data["card_type"]
    name = context.user_data["card_name"]
    fields = {
        key: context.user_data[key]
        for key in ("machine", "material", "thickness")
        if key in context.user_data
    }
    store.save_card(card_kind, name, fields)
    await update.message.reply_text(f"Карточка «{name}» сохранена.", reply_markup=MAIN_MENU)
    context.user_data.clear()
    return ConversationHandler.END


async def task_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    query = update.callback_query
    if not query or not authorized(update):
        return
    await query.answer()
    action, entry_text = query.data.split(":", 1)
    entry_id = int(entry_text)
    if action == "task_done":
        completed = store.complete(entry_id)
        text = f"Дело #{entry_id} выполнено." if completed else "Дело уже закрыто."
        if completed:
            refresh_management_plan()
        await query.edit_message_text(text)
    elif action == "task_tomorrow":
        planned_for = (date.today() + timedelta(days=1)).isoformat()
        if store.set_task_planned_for(entry_id, planned_for):
            schedule_open_tasks()
            refresh_management_plan()
            await query.edit_message_text(
                f"Задача #{entry_id} перенесена на {(date.today() + timedelta(days=1)):%d.%m.%Y}."
            )
        else:
            await query.edit_message_text("Открытая задача уже не найдена.")
    elif action == "task_repriority":
        await query.edit_message_text(
            f"Выберите новый приоритет задачи #{entry_id}:",
            reply_markup=reprioritize_keyboard(entry_id),
        )
    else:
        row = store.entry(entry_id)
        if not row:
            await query.edit_message_text("Дело уже удалено.")
            return
        await query.edit_message_text(
            f"Удалить дело #{entry_id}: «{row['text']}»?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Да, удалить", callback_data=f"task_delete_confirm:{entry_id}"),
                InlineKeyboardButton("Отмена", callback_data=f"task_delete_cancel:{entry_id}"),
            ]]),
        )


async def task_duration_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    query = update.callback_query
    if not query or not authorized(update):
        return
    await query.answer()
    _, duration_text, entry_text = query.data.split(":", 2)
    entry_id = int(entry_text)
    if duration_text == "custom":
        store.set_setting("awaiting_task_duration", str(entry_id))
        await query.edit_message_text("Напишите длительность сообщением: например, 3 часа или 75 минут.")
        return
    duration = int(duration_text)
    if not store.set_task_duration(entry_id, duration):
        await query.edit_message_text("Открытая задача уже не найдена.")
        return
    result = schedule_open_tasks()
    allocation = result.allocations.get(entry_id)
    published = refresh_management_plan()
    if not allocation:
        reason = result.unscheduled.get(entry_id, "нет свободного времени")
        await query.edit_message_text(
            f"Длительность задачи #{entry_id}: {duration} мин. "
            f"Пока не запланировал: {reason}."
        )
        return
    suffix = " План Excel обновлён." if published else " План Excel обновится позже."
    await query.edit_message_text(
        f"Запланировал задачу #{entry_id} на "
        f"{allocation.planned_for.strftime('%d.%m.%Y')} в "
        f"{allocation.scheduled_at} на {duration} мин.{suffix}"
    )


async def task_date_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    query = update.callback_query
    if not query or not authorized(update):
        return
    await query.answer()
    _, value, entry_text = query.data.split(":", 2)
    entry_id = int(entry_text)
    if value == "custom":
        store.set_setting("awaiting_task_date", str(entry_id))
        await query.edit_message_text(
            "Напишите дату сообщением: например, 05.10 или 05.10.2026."
        )
        return
    if not store.set_task_planned_for(entry_id, value):
        await query.edit_message_text("Открытая задача уже не найдена.")
        return
    prompt, markup = task_priority_prompt(entry_id, value)
    await query.edit_message_text(prompt, reply_markup=markup)


async def task_priority_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    query = update.callback_query
    if not query or not authorized(update):
        return
    await query.answer()
    _, priority, entry_text = query.data.split(":", 2)
    entry_id = int(entry_text)
    if not store.set_task_priority(entry_id, priority):
        await query.edit_message_text("Открытая задача уже не найдена.")
        return
    labels = {"high": "высокий", "normal": "средний", "low": "низкий"}
    row = store.entry(entry_id)
    await query.edit_message_text(
        f"Приоритет: {labels[priority]}. Указать крайний срок?",
        reply_markup=deadline_keyboard(entry_id, row["planned_for"]),
    )


async def task_deadline_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    query = update.callback_query
    if not query or not authorized(update):
        return
    await query.answer()
    _, value, entry_text = query.data.split(":", 2)
    entry_id = int(entry_text)
    if value == "custom":
        store.set_setting("awaiting_task_deadline", str(entry_id))
        await query.edit_message_text("Напишите крайний срок, например: 10.10.2026.")
        return
    deadline = None if value == "none" else value
    if not store.set_task_deadline(entry_id, deadline):
        await query.edit_message_text("Открытая задача уже не найдена.")
        return
    label = "без крайнего срока" if deadline is None else f"срок {datetime.fromisoformat(deadline):%d.%m.%Y}"
    await query.edit_message_text(
        f"Задача {label}. Сколько времени потребуется?",
        reply_markup=duration_keyboard(entry_id),
    )


async def task_repriority_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    query = update.callback_query
    if not query or not authorized(update):
        return
    await query.answer()
    _, priority, entry_text = query.data.split(":", 2)
    entry_id = int(entry_text)
    if not store.set_task_priority(entry_id, priority):
        await query.edit_message_text("Открытая задача уже не найдена.")
        return
    schedule_open_tasks()
    refresh_management_plan()
    label = {"high": "высокий", "normal": "средний", "low": "низкий"}[priority]
    await query.edit_message_text(f"Приоритет задачи #{entry_id}: {label}.")


async def task_delete_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    query = update.callback_query
    if not query or not authorized(update):
        return
    await query.answer()
    action, entry_text = query.data.rsplit(":", 1)
    entry_id = int(entry_text)
    if action == "task_delete_confirm":
        deleted = store.delete_entry(entry_id)
        text = f"Дело #{entry_id} удалено." if deleted else "Дело уже удалено."
        if deleted:
            refresh_management_plan()
    else:
        text = "Удаление отменено."
    await query.edit_message_text(text)


async def entry_kind_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    query = update.callback_query
    if not query or not authorized(update):
        return
    await query.answer()
    _, entry_type, entry_text = query.data.split(":", 2)
    entry_id = int(entry_text)
    row = store.entry(entry_id)
    if not row:
        await query.edit_message_text("Запись уже удалена.")
        return
    planned_for, scheduled_at = planning_fields(row["original_text"] or row["text"])
    if entry_type == "event" and not planned_for:
        planned_for = date.today().isoformat()
    if not store.reclassify(
        entry_id, entry_type, planned_for=planned_for, scheduled_at=scheduled_at
    ):
        await query.edit_message_text("Не удалось изменить тип записи.")
        return
    when = ""
    if planned_for:
        when = f" на {datetime.fromisoformat(planned_for).strftime('%d.%m.%Y')}"
        if scheduled_at:
            when += f" в {scheduled_at}"
    if entry_type == "task":
        prefix = f"#{entry_id} — задача{when}: {row['text']}"
        if planned_for:
            prompt, markup = task_priority_prompt(entry_id, planned_for)
            await query.edit_message_text(f"{prefix}\n{prompt}", reply_markup=markup)
        else:
            prompt, markup = task_date_prompt(entry_id, prefix)
            await query.edit_message_text(prompt, reply_markup=markup)
    else:
        await query.edit_message_text(
            f"#{entry_id} — {ENTRY_TYPE_NAMES[entry_type]}{when}: {row['text']}"
        )


async def inventory_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    query = update.callback_query
    if not query or not authorized(update):
        return
    await query.answer()
    action, item_text, *rest = query.data.split(":")
    item_id = int(item_text)
    item = store.inventory_item(item_id)
    if not item:
        await query.edit_message_text("Расходник уже отсутствует.")
        return
    if action == "stock_history":
        rows = store.inventory_history(item_id)
        await query.edit_message_text(
            format_inventory_history(rows) or "История расходника пуста."
        )
        return
    delta = int(rest[0])
    await query.edit_message_text(
        f"Подтвердите изменение «{item['name']}» на {display_delta(delta)} {item['unit']}.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "Подтвердить",
                callback_data=f"stock_adjust_confirm:{item_id}:{delta}",
            ),
            InlineKeyboardButton(
                "Отмена",
                callback_data=f"stock_adjust_cancel:{item_id}:{delta}",
            ),
        ]]),
    )


async def inventory_adjust_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    query = update.callback_query
    if not query or not authorized(update):
        return
    await query.answer()
    action, item_text, delta_text = query.data.split(":")
    if action == "stock_adjust_cancel":
        await query.edit_message_text("Изменение остатка отменено.")
        return
    item_id = int(item_text)
    item = store.inventory_item(item_id)
    if not item:
        await query.edit_message_text("Расходник уже отсутствует.")
        return
    updated = store.inventory_change(item["name"], int(delta_text))
    await query.edit_message_text(
        f"Остаток «{item['name']}»: "
        f"{display_number(updated['quantity'])} {updated['unit']}."
    )


def conversation_handlers():
    cancel = [
        CommandHandler("cancel", cancel_wizard),
        MessageHandler(filters.Regex(r"^❌ Отмена$"), cancel_wizard),
    ]
    return [
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r"^➕ Задача$"), new_task_start)],
            states={TASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_task_text)]},
            fallbacks=cancel,
        ),
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r"^📦 Операция$"), stock_start)],
            states={
                STOCK_ACTION: [CallbackQueryHandler(stock_action, pattern=r"^stock:")],
                STOCK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, stock_name)],
                STOCK_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, stock_quantity)],
                STOCK_MACHINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, stock_machine)],
            },
            fallbacks=cancel,
        ),
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r"^➕ Карточка$"), card_start)],
            states={
                CARD_TYPE: [CallbackQueryHandler(card_type, pattern=r"^card:")],
                CARD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_name)],
                CARD_MACHINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_machine)],
                CARD_MATERIAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_material)],
                CARD_THICKNESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_thickness)],
            },
            fallbacks=cancel,
        ),
    ]

async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not private(update) or not update.message or not update.message.text:
        return
    if not authorized(update):
        await update.message.reply_text("Помощник не привязан к этому аккаунту.")
        return
    await process_text(update, update.message.text)


async def command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not private(update) or not update.message:
        return
    if not authorized(update):
        await update.message.reply_text("Помощник не привязан к этому аккаунту.")
        return
    command_parts = update.message.text.split(maxsplit=1)
    command_name = command_parts[0].split("@")[0].casefold()
    command_argument = command_parts[1] if len(command_parts) > 1 else ""
    commands = {
        "/start": "меню",
        "/menu": "меню",
        "/help": "помощь",
        "/notes": "заметки",
        "/tasks": "дела",
        "/stock": "остатки",
        "/cards": "карточки",
        "/export": "экспорт excel",
        "/backup": "резервная копия",
        "/plan": "план",
        "/planexcel": "план excel",
        "/inbox": "входящие",
        "/status": "рабочий статус",
        "/sources": "источники",
        "/saw": "распил",
        "/mode": "режим",
    }
    raw = commands.get(command_name, "помощь")
    if command_name in {"/saw", "/mode"} and command_argument:
        raw += " " + command_argument
    await process_text(update, raw)


async def setup_bot(application: Application):
    try:
        await application.bot.set_my_commands(
            [
            BotCommand("menu", "Показать главное меню"),
            BotCommand("help", "Примеры и подсказки"),
            BotCommand("notes", "Показать заметки"),
            BotCommand("tasks", "Показать открытые дела"),
            BotCommand("stock", "Показать остатки"),
            BotCommand("cards", "Показать карточки"),
            BotCommand("export", "Создать локальный Excel-экспорт"),
            BotCommand("backup", "Создать резервную копию"),
            BotCommand("plan", "Показать план на сегодня"),
            BotCommand("planexcel", "Создать план Excel для руководства"),
            BotCommand("inbox", "Разобрать входящие записи"),
            BotCommand("status", "Показать рабочий статус"),
            BotCommand("sources", "Показать реестр источников"),
            BotCommand("saw", "Найти утверждённую таблицу распила"),
            BotCommand("mode", "Найти утверждённый режим резки"),
            BotCommand("cancel", "Отменить текущее действие"),
            ]
        )
    except NetworkError:
        logger.warning("Could not update bot commands; continuing startup")
    application.create_task(monitor_pending_updates(application))
    application.create_task(monitor_dirty_plan())
    application.create_task(monitor_daily_tasks(application))


async def monitor_dirty_plan(interval_seconds=60):
    while True:
        await asyncio.sleep(interval_seconds)
        if store.get_setting("plan_dirty") != "1":
            continue
        await asyncio.to_thread(refresh_management_plan)


async def run_daily_maintenance(application, current=None):
    current = current or datetime.now()
    day_key = current.date().isoformat()
    owner = owner_id()
    if store.get_setting("last_task_rollover") != day_key:
        moved = store.carry_over_open_tasks(day_key)
        if moved:
            schedule_open_tasks(current.date())
            refresh_management_plan(current.date())
        store.set_setting("last_task_rollover", day_key)
    if not owner:
        return
    if current.hour == 8 and store.get_setting("last_morning_plan") != day_key:
        await application.bot.send_message(
            chat_id=owner,
            text=format_plan(store.plan_entries(day_key), "План на сегодня"),
        )
        store.set_setting("last_morning_plan", day_key)
    if current.hour == 16 and current.minute >= 30 and store.get_setting("last_evening_check") != day_key:
        await application.bot.send_message(
            chat_id=owner,
            text=format_plan(store.plan_entries(day_key), "Незавершённые задачи на сегодня"),
        )
        store.set_setting("last_evening_check", day_key)


async def monitor_daily_tasks(application, interval_seconds=30):
    while True:
        try:
            await run_daily_maintenance(application)
        except NetworkError:
            logger.warning("Daily maintenance could not reach Telegram; will retry")
        except Exception:
            logger.exception("Daily maintenance failed; will retry")
        await asyncio.sleep(interval_seconds)


async def monitor_pending_updates(application, interval_seconds=30):
    request = HTTPXRequest(proxy=TELEGRAM_PROXY) if TELEGRAM_PROXY else None
    async with Bot(TOKEN, request=request) as probe:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                info = await probe.get_webhook_info()
            except NetworkError:
                logger.warning("Pending-update watchdog could not reach Telegram")
                if connectivity_watchdog.failed():
                    logger.critical(
                        "Connectivity watchdog requested restart after %s failed probes",
                        CONNECTIVITY_FAILURE_LIMIT,
                    )
                    connectivity_watchdog.succeeded()
                    application.stop_running()
                    return
                continue
            connectivity_watchdog.succeeded()
            if pending_update_watchdog.observe(info.pending_update_count):
                logger.critical(
                    "Pending-update watchdog requested restart; pending=%s",
                    info.pending_update_count,
                )
                pending_update_watchdog.observe(0)
                application.stop_running()
                return


async def handle_error(update, context):
    error_name = type(context.error).__name__ if context.error else "UnknownError"
    logger.error("Update handler failed: %s", error_name)
    if isinstance(context.error, NetworkError) and network_watchdog.record_error():
        logger.critical(
            "Network watchdog requested restart after %s errors",
            NETWORK_ERROR_LIMIT,
        )
        network_watchdog.reset()
        context.application.stop_running()
        return
    if update and getattr(update, "effective_message", None):
        try:
            await update.effective_message.reply_text(
                "Не удалось выполнить действие. Попробуйте ещё раз или отправьте /help."
            )
        except Exception:
            logger.error("Could not send an error message to the owner")


async def mark_update_received(update, context):
    del update, context
    network_watchdog.reset()


def acquire_single_instance():
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\OlegAssistantBot")
    if not mutex:
        raise RuntimeError("Не удалось создать блокировку процесса")
    if ctypes.windll.kernel32.GetLastError() == 183:
        raise RuntimeError("Бот уже запущен")
    return mutex


def main():
    instance_mutex = acquire_single_instance()
    (BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "data" / "bot.pid").write_text(str(os.getpid()), encoding="ascii")
    store.initialize()
    store.backup(BACKUP_DIR)
    if not TOKEN:
        raise RuntimeError("В .env не задан TELEGRAM_BOT_TOKEN")
    if not PAIRING_CODE and not owner_id():
        raise RuntimeError("В .env не задан PAIRING_CODE")
    builder = Application.builder().token(TOKEN).post_init(setup_bot)
    if TELEGRAM_PROXY:
        builder = builder.proxy(TELEGRAM_PROXY).get_updates_proxy(TELEGRAM_PROXY)
    app = builder.build()
    app.add_handler(TypeHandler(Update, mark_update_received), group=-1)
    app.add_handler(CommandHandler("claim", claim))
    for handler in conversation_handlers():
        app.add_handler(handler)
    for name in ("start", "menu", "help", "notes", "tasks", "stock", "cards", "export", "backup", "plan", "planexcel", "inbox", "status", "sources", "saw", "mode"):
        app.add_handler(CommandHandler(name, command))
    app.add_handler(CallbackQueryHandler(task_button, pattern=r"^task_(done|delete|tomorrow|repriority):\d+$"))
    app.add_handler(CallbackQueryHandler(task_delete_decision, pattern=r"^task_delete_(confirm|cancel):\d+$"))
    app.add_handler(CallbackQueryHandler(
        entry_kind_decision, pattern=r"^entry_kind:(note|task|event|idea|log):\d+$"
    ))
    app.add_handler(CallbackQueryHandler(
        task_date_decision, pattern=r"^task_date:(?:\d{4}-\d{2}-\d{2}|custom):\d+$"
    ))
    app.add_handler(CallbackQueryHandler(
        task_priority_decision, pattern=r"^task_priority:(?:high|normal|low):\d+$"
    ))
    app.add_handler(CallbackQueryHandler(
        task_deadline_decision, pattern=r"^task_deadline:(?:\d{4}-\d{2}-\d{2}|none|custom):\d+$"
    ))
    app.add_handler(CallbackQueryHandler(
        task_repriority_decision, pattern=r"^task_repriority_value:(?:high|normal|low):\d+$"
    ))
    app.add_handler(CallbackQueryHandler(
        task_duration_decision, pattern=r"^task_duration:(15|30|45|60|90|120|custom):\d+$"
    ))
    app.add_handler(CallbackQueryHandler(
        inventory_button, pattern=r"^stock_(history:\d+|adjust:\d+:-?1)$"
    ))
    app.add_handler(CallbackQueryHandler(
        inventory_adjust_decision,
        pattern=r"^stock_adjust_(confirm|cancel):\d+:-?1$",
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))
    app.add_error_handler(handle_error)
    logger.info("Bot started")
    app.run_polling(drop_pending_updates=False)


def run(main_function=main):
    try:
        main_function()
    except InvalidToken:
        logger.critical("Telegram rejected the configured bot token")
        return 2
    except Exception:
        logger.exception("Bot stopped unexpectedly")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())










