from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from afina_watch.config import FacebookCfg
from afina_watch.models import NormalizedItem

log = logging.getLogger(__name__)


class FacebookBrowserCollector:
    """Опциональный сборщик личной ленты / групп через локальный браузерный профиль.

    По умолчанию выключен. Meta это запрещает ToS. Реализацию автоматизации
    (селекторы, обход логина, скрытие автоматизации) сюда специально не кладём:
    если решите идти этим путём — пишите тонкий адаптер сами под свой профиль
    и свою ответственность. Этот класс только фиксирует контракт и отказывается
    работать без явного opt-in.
    """

    name = "facebook_browser"

    def __init__(self, cfg: FacebookCfg):
        self.cfg = cfg

    async def stream(self) -> AsyncIterator[NormalizedItem]:
        if not self.cfg.browser.enabled:
            return
        log.error(
            "facebook.browser.enabled=true, но встроенного скрейпера нет. "
            "Graph API — единственный поддерживаемый путь в этом репозитории. "
            "Личная лента официально недоступна."
        )
        return
        yield  # pragma: no cover — делает функцию async generator
