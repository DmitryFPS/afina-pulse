from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from afina_watch.models import NormalizedItem


class Collector(Protocol):
    name: str

    async def stream(self) -> AsyncIterator[NormalizedItem]:
        ...
