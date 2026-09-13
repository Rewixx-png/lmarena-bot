"""Async fetch + parse of the LM Arena leaderboard.

Primary source:  https://arena.ai/leaderboard?_rsc=1  (Next.js RSC payload, ~1.2 MB)
Fallback:        https://arena.ai/leaderboard         (full SSR HTML, ~5 MB)

The page embeds two things we need:
  * `initialModels`  - the model catalog (capabilities per model)
  * `entries`        - ranked text leaderboard (elo/rank/votes/license/context/...)
                        for the `text-overall-style_control` slug (the main Overall board).
"""
import asyncio
import json
import re

import httpx

from config import settings

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# The main text (chat) leaderboard slug embedded in the page.
TEXT_SLUG_MARKER = "text-overall-style_control/leaderboard-snapshots/latest"

ANON_RE = re.compile(
    r"(mystery|anonymous|anon\b|im[-_]also|gpt2[-_]?chatbot|chatbot[-_]?\d|"
    r"test[-_]?chatbot|chatbot[-_]?test|secret[-_]?chatbot|hidden|unknown[-_]?model)",
    re.IGNORECASE,
)

_MODALITY_ORDER = ["text", "image", "video", "audio", "file", "web", "search"]


class FetchError(RuntimeError):
    pass


def _extract_array(text: str, key: str):
    """Return the raw JSON array substring that follows `key":` in `text`.

    Handles both single-escaped (RSC) and double-escaped (full HTML) quoting.
    """
    for probe in (key + '":[', key + '\\":[', key + '":['):
        start = text.find(probe)
        if start != -1:
            break
    else:
        return None
    b = text.find("[", start)
    depth = 0
    in_str = False
    esc = False
    i = b
    while i < len(text):
        c = text[i]
        if esc:
            esc = False
            i += 1
            continue
        if c == "\\":
            esc = True
            i += 1
            continue
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return text[b:i + 1]
        i += 1
    return None


def _decode(raw: str):
    """Parse a raw JSON array, tolerating double-escaped (HTML) payloads."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Full HTML keeps the JSON inside a JS string literal: unescape one level.
        return json.loads(json.loads('"' + raw + '"'))


def _extract_text_entries(text: str):
    i = text.find(TEXT_SLUG_MARKER)
    if i == -1:
        return None
    j = text.find('entries":[', i)
    if j == -1:
        j = text.find('entries\\":[', i)
    if j == -1:
        return None
    b = text.find("[", j)
    depth = 0
    in_str = False
    esc = False
    k = b
    while k < len(text):
        c = text[k]
        if esc:
            esc = False
            k += 1
            continue
        if c == "\\":
            esc = True
            k += 1
            continue
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return _decode(text[b:k + 1])
        k += 1
    return None


def _extract_snapshot_ts(text: str):
    """`voteCutoffISOString` follows each leaderboard's entries array."""
    i = text.find(TEXT_SLUG_MARKER)
    if i == -1:
        return None
    j = text.find('voteCutoffISOString', i)
    if j == -1:
        return None
    m = re.search(r'voteCutoffISOString\\?":\\?"([0-9T:.Z-]+)', text[j:j + 80])
    return m.group(1) if m else None


def _modalities(capabilities) -> list[str]:
    if not capabilities:
        return []
    mods = set()
    for side in ("inputCapabilities", "outputCapabilities"):
        for k, v in (capabilities.get(side) or {}).items():
            if v:  # True, or a non-empty dict for image/audio
                mods.add(k)
    return [m for m in _MODALITY_ORDER if m in mods]


def _build_capability_map(catalog: list[dict]) -> dict[str, dict]:
    m = {}
    for item in catalog:
        caps = item.get("capabilities")
        if not caps:
            continue
        for key in (item.get("name"), item.get("displayName"), item.get("publicName")):
            if key and key not in m:
                m[key] = caps
    return m


def _lookup_capabilities(cap_map, model_key, display_name) -> dict | None:
    candidates = [model_key, display_name]
    if model_key.endswith("-text"):
        candidates.append(model_key[:-5])
    if model_key.endswith("-thinking"):
        candidates.append(model_key[:-9])
    for c in candidates:
        if c in cap_map:
            return cap_map[c]
    return None


def _normalize_entry(entry, cap_map) -> dict:
    model_key = entry.get("modelKey") or ""
    display_name = entry.get("modelDisplayName") or model_key
    caps = _lookup_capabilities(cap_map, model_key, display_name)
    # Anonymity follows the *displayed* name: arena keeps an anonymous-style
    # modelKey (e.g. `august26-chatbot1-fmme`) even after the model is revealed,
    # so keying off modelKey would mislabel de-anonymized models.
    anon = bool(ANON_RE.search(display_name))
    return {
        "model_key": model_key,
        "display_name": display_name,
        "organization": entry.get("modelOrganization") or "",
        "rating": entry.get("rating"),
        "rating_upper": entry.get("ratingUpper"),
        "rating_lower": entry.get("ratingLower"),
        "rank": entry.get("rank"),
        "votes": entry.get("votes") or 0,
        "license": entry.get("license") or "",
        "context_length": entry.get("contextLength"),
        "input_price": entry.get("inputPricePerMillion"),
        "output_price": entry.get("outputPricePerMillion"),
        "model_url": entry.get("modelUrl") or "",
        "release_type": entry.get("releaseType"),
        "modalities": _modalities(caps),
        "is_anonymous": anon,
    }


def parse_leaderboard(text: str) -> dict:
    """Parse raw page payload into a snapshot: {entries, snapshot_ts}."""
    entries_raw = _extract_text_entries(text)
    if entries_raw is None:
        return {"entries": [], "snapshot_ts": None}

    catalog_raw = _extract_array(text, "initialModels")
    catalog = _decode(catalog_raw) if catalog_raw else []
    cap_map = _build_capability_map(catalog)

    entries = [_normalize_entry(e, cap_map) for e in entries_raw]
    return {"entries": entries, "snapshot_ts": _extract_snapshot_ts(text)}


async def fetch_snapshot() -> dict:
    """Fetch and parse the leaderboard, trying RSC first then full HTML."""
    timeout = httpx.Timeout(30.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        last_err: Exception | None = None
        sources = [
            (settings.rsc_url, {"RSC": "1"}),
            (settings.html_url, {}),
        ]
        for url, extra in sources:
            for attempt in range(settings.max_retries):
                headers = {
                    "User-Agent": USER_AGENTS[attempt % len(USER_AGENTS)],
                    "Accept": "text/x-component, text/html;q=0.9, */*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    **extra,
                }
                try:
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                    snap = parse_leaderboard(resp.text)
                    if snap["entries"]:
                        snap["source"] = url
                        return snap
                    last_err = FetchError(f"empty entries from {url}")
                except Exception as e:  # noqa: BLE001 - retry any transient error
                    last_err = e
                    await asyncio.sleep(2 * (attempt + 1))
        raise FetchError(f"all sources failed: {last_err}")
