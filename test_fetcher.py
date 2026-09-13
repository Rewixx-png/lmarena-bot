"""Smoke test: fetch the arena leaderboard and validate parsed structure."""
import asyncio
import sys

from fetcher import fetch_snapshot


async def main() -> int:
    print("Fetching LM Arena leaderboard...")
    snap = await fetch_snapshot()
    entries = snap["entries"]
    assert entries, "no entries parsed"

    print(f"source:       {snap['source']}")
    print(f"snapshot ts:  {snap['snapshot_ts']}")
    print(f"models:       {len(entries)}")

    required = ("model_key", "display_name", "rating", "rank", "votes", "license")
    for e in entries:
        for f in required:
            assert f in e and e[f] is not None, f"missing {f!r} in {e!r}"

    top = entries[0]
    assert top["rank"] == 1, f"expected rank 1, got {top['rank']}"
    assert 500 < top["rating"] < 3000, f"rating out of range: {top['rating']}"

    anon = [e for e in entries if e["is_anonymous"]]
    print(f"anonymous:    {len(anon)}")

    print("\nTop 5:")
    for e in entries[:5]:
        print(f"  #{e['rank']:>3}  {e['display_name']:38s} {e['organization']:16s} "
              f"elo={e['rating']:.0f}  votes={e['votes']}  lic={e['license']}")

    print("\nOK: fetcher works.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
