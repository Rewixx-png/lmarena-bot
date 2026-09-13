"""Telegram HTML formatting for events and model cards."""
import html as _html

from tracker import DEANONYMIZED, NEW_MODEL, RENAMED, REMOVED, STATS_UPDATE


def esc(value) -> str:
    return _html.escape(str(value)) if value is not None else "—"


def _fmt_rating(r) -> str:
    return f"{r:.0f}" if isinstance(r, (int, float)) else esc(r)


def _fmt_votes(v) -> str:
    v = v or 0
    return f"{int(v):,}".replace(",", " ")


def _fmt_context(n) -> str:
    if not n:
        return "—"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:g}M"
    return f"{n / 1_000:,}K" if n >= 1_000 else str(n)


def _license_emoji(lic) -> str:
    low = (lic or "").lower()
    if "open" in low:
        return "🔓"
    if "propriet" in low or "closed" in low:
        return "🔒"
    return "❔"


def model_card(m: dict, header: str | None = None) -> str:
    """One model's full metrics card."""
    lines = []
    if header:
        lines.append(header)

    name = esc(m.get("display_name"))
    anon = m.get("is_anonymous")
    lines.append(f"<b>{name}</b>{' 🎭' if anon else ''}")

    if m.get("organization"):
        lines.append(f"🏢 Организация: <b>{esc(m['organization'])}</b>")
    if m.get("license"):
        lines.append(f"{_license_emoji(m['license'])} Лицензия: {esc(m['license'])}")
    if m.get("model_url"):
        lines.append(f"🔗 <a href=\"{esc(m['model_url'])}\">Карточка модели</a>")

    rating = m.get("rating")
    if rating is not None:
        lo, hi = m.get("rating_lower"), m.get("rating_upper")
        ci = f" (95% CI {_fmt_rating(lo)}–{_fmt_rating(hi)})" if lo is not None and hi is not None else ""
        lines.append(f"🎯 Arena Elo: <b>{_fmt_rating(rating)}</b>{ci}")
    if m.get("rank") is not None:
        lines.append(f"🏅 Ранг: <b>#{m['rank']}</b>")
    if m.get("votes") is not None:
        lines.append(f"🗳️ Битвы: <b>{_fmt_votes(m['votes'])}</b>")
    if m.get("context_length"):
        lines.append(f"📏 Контекст: <b>{_fmt_context(m['context_length'])}</b> токенов")
    if m.get("input_price") is not None:
        lines.append(f"💰 Цена: ${esc(m['input_price'])} / ${esc(m['output_price'])} за 1M токенов")
    mods = m.get("modalities") or []
    if mods:
        lines.append(f"🧩 Специализации: {', '.join(esc(x) for x in mods)}")
    if m.get("release_type"):
        lines.append(f"📦 Тип релиза: {esc(m['release_type'])}")
    return "\n".join(lines)


def format_event(ev) -> str:
    header = {
        NEW_MODEL: "🆕 <b>Новая модель в арене</b>",
        DEANONYMIZED: "🔄 <b>Деанонимизация / Переименование</b>",
        RENAMED: "✏️ <b>Модель переименована</b>",
        REMOVED: "❌ <b>Модель покинула лидерборд</b>",
        STATS_UPDATE: "📊 <b>Обновление лидерборда</b>",
    }.get(ev.type, "ℹ️ <b>Событие</b>")

    lines = [header, ""]

    if ev.type == NEW_MODEL:
        lines.append(model_card(ev.new))
    elif ev.type in (DEANONYMIZED, RENAMED):
        old_name = esc((ev.extra.get("old_name") if ev.type == DEANONYMIZED else ev.old.get("display_name")))
        new_name = esc(ev.new["display_name"])
        if ev.type == DEANONYMIZED:
            lines.append(f"🎭 <s>{old_name}</s>  →  <b>{new_name}</b>")
            lines.append("Анонимная тест-модель раскрыта под реальным названием.")
        else:
            lines.append(f"<s>{old_name}</s>  →  <b>{new_name}</b>")
        lines.append("")
        lines.append(model_card(ev.new))
    elif ev.type == REMOVED:
        lines.append(model_card(ev.old, f"<s>{esc(ev.old['display_name'])}</s>"))
    elif ev.type == STATS_UPDATE:
        o, n = ev.old, ev.new
        name = esc(n["display_name"])
        lines.append(f"<b>{name}</b>")
        if o.get("rank") != n.get("rank"):
            lines.append(f"🏅 Ранг: #{o['rank']} → #{n['rank']}")
        if (o.get("rating") or 0) != (n.get("rating") or 0):
            delta = (n["rating"] or 0) - (o["rating"] or 0)
            sign = "+" if delta >= 0 else ""
            lines.append(f"🎯 Elo: {_fmt_rating(o['rating'])} → {_fmt_rating(n['rating'])} ({sign}{delta:.1f})")
        if o.get("votes") != n.get("votes"):
            lines.append(f"🗳️ Битвы: {_fmt_votes(o['votes'])} → {_fmt_votes(n['votes'])}")

    return "\n".join(lines)


def format_top(models: list[dict], n: int = 10) -> str:
    """Top-N leaderboard list."""
    lines = [f"🏆 <b>Топ-{min(n, len(models))} арены</b>", ""]
    for m in models[:n]:
        rank = m.get("rank")
        name = esc(m.get("display_name"))
        org = esc(m.get("organization") or "")
        rating = _fmt_rating(m.get("rating")) if m.get("rating") is not None else "—"
        org_part = f" <i>({org})</i>" if org else ""
        lines.append(f"{rank}. <b>{name}</b>{org_part} — Elo {rating}")
    return "\n".join(lines)
