from __future__ import annotations

import re


def keyword_hits(text: str, keywords: list[str], casefold: bool = True) -> list[str]:
    if not text or not keywords:
        return []
    hay = text.casefold() if casefold else text
    hits: list[str] = []
    for raw in keywords:
        if not raw:
            continue
        if raw.startswith("/") and raw.endswith("/") and len(raw) > 2:
            flags = re.IGNORECASE if casefold else 0
            if re.search(raw[1:-1], text, flags):
                hits.append(raw)
            continue
        needle = raw.casefold() if casefold else raw
        if needle in hay:
            hits.append(raw)
    return hits
