#!/usr/bin/env python3
"""Build the seed-Pokémon encounter table from Serebii research-area pages."""
from __future__ import annotations

import argparse
import html
import json
import re
import urllib.request
from datetime import date
from pathlib import Path


FIELDS = {
    "シアンの砂浜": "cyanbeach",
    "トープ洞窟": "taupehollow",
    "ウノハナ雪原": "snowdroptundra",
    "ラピスラズリ湖畔": "lapislakeside",
    "ゴールド旧発電所": "oldgoldpowerplant",
    "アンバー渓谷": "ambercanyon",
}
SLEEP_TYPES = {"Dozing": "うとうと", "Snoozing": "すやすや", "Slumbering": "ぐっすり"}


def parse_page(source: str) -> dict[str, list[str]]:
    source = source.split("Sleep Style Unlock Chart", 1)[0]
    result = {}
    for english, japanese in SLEEP_TYPES.items():
        start = source.find(f"<h3>{english}</h3>")
        if start < 0:
            result[japanese] = []
            continue
        ends = [source.find(f"<h3>{other}</h3>", start + 1)
                for other in SLEEP_TYPES if source.find(f"<h3>{other}</h3>", start + 1) >= 0]
        block = source[start:min(ends) if ends else len(source)]
        names = re.findall(
            r'<td class="cen"><a href="/pokemonsleep/pokemon/[^\"]+\.shtml"><u>([^<]+)</u></a></td>',
            block,
        )
        result[japanese] = list(dict.fromkeys(html.unescape(name) for name in names))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html-dir", type=Path)
    parser.add_argument("--out", type=Path, default=Path("data/seed_encounters.json"))
    args = parser.parse_args()
    fields = {}
    for field, slug in FIELDS.items():
        if args.html_dir:
            source = (args.html_dir / f"sleepbox-{slug}.html").read_text(errors="replace")
        else:
            url = f"https://www.serebii.net/pokemonsleep/locations/{slug}.shtml"
            source = urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "replace")
        fields[field] = parse_page(source)
    payload = {"source": "https://www.serebii.net/pokemonsleep/locations/",
               "updated": date.today().isoformat(), "fields": fields}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
