"""LM Arena monitoring Telegram bot (aiogram v3).

Periodically polls the arena leaderboard, diffs snapshots, and posts change
events to a channel and/or an admin chat. Provides interactive commands.
"""
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from config import parse_chat_id, settings
from database import Database
from fetcher import FetchError, fetch_snapshot
from formatter import esc, format_event, format_top, model_card
from tracker import DEANONYMIZED, NEW_MODEL, REMOVED, RENAMED, STATS_UPDATE, diff

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lmarena")

db = Database(settings.database_path)
router = Router()

_bot: Bot | None = None
_check_lock = asyncio.Lock()

state = {
    "last_check_at": None,
    "last_snapshot_ts": None,
    "last_model_count": 0,
    "last_error": None,
    "last_events": 0,
}

HELP_TEXT = (
    "🤖 <b>LM Arena Монитор</b>\n\n"
    "Слежу за лидербордом <a href=\"https://arena.ai/leaderboard\">LM Arena</a> "
    "и сообщаю о новых моделях, деанонимизации и изменениях рейтинга.\n\n"
    "<b>Команды:</b>\n"
    "/status — статус мониторинга\n"
    "/top — топ-10 моделей прямо сейчас\n"
    "/check — принудительная проверка\n"
    "/find &lt;имя&gt; — поиск карточки модели"
)


async def send_notification(text: str) -> None:
    targets = []
    for raw in (settings.channel_id, settings.admin_chat_id):
        cid = parse_chat_id(raw)
        if cid:
            targets.append(cid)
    for cid in dict.fromkeys(targets):
        try:
            await _bot.send_message(cid, text, disable_web_page_preview=True)
        except Exception as e:  # noqa: BLE001
            log.warning("send to %s failed: %s", cid, e)


async def do_check(notify: bool = True) -> list:
    """Fetch, diff, persist and (optionally) notify. Returns events."""
    async with _check_lock:
        snap = await fetch_snapshot()
        entries = snap["entries"]
        new = {m["model_key"]: m for m in entries}
        old = await db.get_all_models()
        events = diff(old, new, settings)

        now_iso = datetime.now(timezone.utc).isoformat()
        await db.upsert_models(entries, now_iso)
        for ev in events:
            details = {
                "old_name": (ev.old or {}).get("display_name"),
                "old_rank": (ev.old or {}).get("rank"),
                "old_rating": (ev.old or {}).get("rating"),
                "new_rank": (ev.new or {}).get("rank"),
                "new_rating": (ev.new or {}).get("rating"),
                **ev.extra,
            }
            await db.record_history(now_iso, ev.type, ev.model_key, ev.display_name, details)

        state.update(
            last_check_at=datetime.now(timezone.utc),
            last_snapshot_ts=snap.get("snapshot_ts"),
            last_model_count=len(entries),
            last_events=len(events),
            last_error=None,
        )

        if notify and _bot is not None and events:
            for ev in events:
                try:
                    await send_notification(format_event(ev))
                except Exception as e:  # noqa: BLE001
                    log.error("notify failed for %s: %s", ev.type, e)
        return events


async def poller() -> None:
    # Establish baseline silently on first run.
    try:
        await do_check(notify=False)
        log.info("baseline established: %d models (snapshot %s)",
                 state["last_model_count"], state["last_snapshot_ts"])
    except Exception as e:  # noqa: BLE001
        state["last_error"] = str(e)
        log.error("initial fetch failed: %s", e)

    while True:
        await asyncio.sleep(settings.check_interval_seconds)
        try:
            events = await do_check(notify=True)
            if events:
                log.info("check: %d events", len(events))
        except Exception as e:  # noqa: BLE001
            state["last_error"] = str(e)
            log.error("check failed: %s", e)


def _summarize(events: list) -> str:
    counts = {NEW_MODEL: 0, DEANONYMIZED: 0, RENAMED: 0, REMOVED: 0, STATS_UPDATE: 0}
    for ev in events:
        counts[ev.type] = counts.get(ev.type, 0) + 1
    parts = []
    if counts[NEW_MODEL]:
        parts.append(f"🆕 новых: {counts[NEW_MODEL]}")
    if counts[DEANONYMIZED]:
        parts.append(f"🔄 деанонимизаций: {counts[DEANONYMIZED]}")
    if counts[RENAMED]:
        parts.append(f"✏️ переименований: {counts[RENAMED]}")
    if counts[REMOVED]:
        parts.append(f"❌ удалено: {counts[REMOVED]}")
    if counts[STATS_UPDATE]:
        parts.append(f"📊 обновлений: {counts[STATS_UPDATE]}")
    return ", ".join(parts) if parts else "изменений нет"


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("status"))
async def status(message: Message) -> None:
    last = state["last_check_at"]
    last_str = last.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if last else "ещё не было"
    lines = [
        "📡 <b>Статус мониторинга</b>",
        "",
        f"Статус: <b>{'❌ ошибка' if state['last_error'] else '✅ работает'}</b>",
        f"Последняя проверка: <b>{last_str}</b>",
        f"Снапшот арены: <b>{esc(state['last_snapshot_ts'])}</b>",
        f"Моделей на доске: <b>{state['last_model_count']}</b>",
        f"Интервал: <b>{settings.check_interval_seconds} с</b>",
    ]
    if state["last_error"]:
        lines.append(f"Последняя ошибка: <code>{esc(state['last_error'])}</code>")
    await message.answer("\n".join(lines))


@router.message(Command("top"))
async def top(message: Message) -> None:
    models = await db.get_models_ordered()
    if not models:
        await message.answer("⏳ Данных пока нет, запускаю первую проверку…")
        try:
            await do_check(notify=False)
        except FetchError as e:
            await message.answer(f"❌ Не удалось получить данные: {esc(str(e))}")
            return
        models = await db.get_models_ordered()
    await message.answer(format_top(models, 10))


@router.message(Command("check"))
async def check(message: Message) -> None:
    await message.answer("⏳ Запускаю принудительную проверку…")
    try:
        events = await do_check(notify=True)
    except FetchError as e:
        await message.answer(f"❌ Ошибка получения данных: {esc(str(e))}")
        return
    summary = _summarize(events)
    await message.answer(f"✅ Проверка завершена: {summary}.\nМоделей: {state['last_model_count']}")


@router.message(Command("find"))
async def find(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Использование: /find &lt;имя модели&gt;")
        return
    results = await db.search_models(query)
    if not results:
        await message.answer(f"🔍 По «{esc(query)}» ничего не найдено.")
        return
    for m in results[:3]:
        await message.answer(model_card(m), disable_web_page_preview=True)


async def main() -> None:
    global _bot
    if not settings.bot_token:
        log.error("BOT_TOKEN is empty — set it in .env")
        raise SystemExit(1)

    await db.connect()
    _bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    asyncio.create_task(poller())

    try:
        await _bot.delete_webhook(drop_pending_updates=True)
        log.info("bot polling started")
        await dp.start_polling(_bot)
    finally:
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
