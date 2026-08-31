# Facebook: что реально есть на GitHub / GitLab и что из этого брать

GitLab по запросам `facebook scraper|monitor|group collector` почти ничего живого не отдаёт. Вся история — GitHub. И она такая: звёзд много, коммитов свежих мало, Meta ломает вёрстку чаще, чем люди успевают чинить.

## Карта репозиториев

### Слой A. Официальный Graph API (единственное, что Meta разрешает)

| Репо | Зачем смотреть | Состояние |
|---|---|---|
| [huandu/facebook](https://github.com/huandu/facebook) | Зрелый Go SDK Graph API, batch, ошибки Meta | живой |
| [casmlab/facebook_group_collector](https://github.com/casmlab/facebook_group_collector) | Сбор группы через Graph + JS SDK, парсинг в схему | заброшен, идея пайплайна полезная |
| [deep-diver/fb-group-post-fetcher](https://github.com/deep-diver/fb-group-post-fetcher) | Посты группы → рассылка. Нужен admin группы + Groups permission | узкий, но честный контракт |
| [jpryda/facebook-multi-scraper](https://github.com/jpryda/facebook-multi-scraper) | Много страниц параллельно, Insights для *своих* Page | Graph API, не скрейп |
| [chenjr0719/Facebook-Page-Crawler](https://github.com/chenjr0719/Facebook-Page-Crawler) | Page posts + comments + reactions через Graph | старый api-version |
| [minimaxir/facebook-page-post-scraper](https://github.com/minimaxir/facebook-page-post-scraper) и форки [umutto/facebook_scrapper](https://github.com/umutto/facebook_scrapper), [isaacmg/fb_scraper](https://github.com/isaacmg/fb_scraper) | Классика 2016–2018. FBLYZE уже думал как Watch: poll + Kafka | Graph v2.*, мёртвые |

Брать: поля поста, long-lived token exchange, инкрементальный `since`, отдельный воркер на каждую page_id.

Не брать: надежду, что `/USER_ID/feed` или чужая группа откроются без App Review.

### Слой B. «Как RSS / монитор» — правильная *форма* продукта

| Репо | Идея, которую копируем | Статус |
|---|---|---|
| [viseshrp/fbn](https://github.com/viseshrp/fbn) | Монитор одной группы: persistent browser profile, SQLite seen-id, poll с джиттером, алерт через Apprise, `doctor` для сессии | ближайший к Watch по смыслу |
| [yshalsager/facebook2rss](https://github.com/yshalsager/facebook2rss) | FastAPI + сохранённая сессия → RSS страниц/групп/профиля/уведомлений | архив 2021, сам автор сдался |
| [DIYgod/RSSHub](https://github.com/DIYgod/RSSHub) | Когда-то были facebook routes, к 2024 их вычистили | Facebook больше не поддерживают |
| Chrome extension «Group & Page Post Scraper» | Пользователь сам открывает группу в своём Chrome, расширение снимает DOM | не код, но честный UX: сессия принадлежит человеку |

Брать у fbn:

1. Логин руками один раз в отдельный профиль. Пароль в конфиг не писать.
2. Seen-set в SQLite, не «все посты каждый раз».
3. Интервал с джиттером (не каждые 30 секунд — бан).
4. Baseline без алертов при первом прогоне.
5. Сессия протухла → коллектор встаёт, а не долбит логин.

### Слой C. Браузерные сборщики групп (2025–2026, ещё дышат)

Это не библиотеки «подключи и забудь». Это чужие CLI, которые *сами* ломаются на каждом редизайне Facebook.

| Репо | Что умеет | Почему не вендорить внутрь Watch |
|---|---|---|
| [jwmoss/forage](https://github.com/jwmoss/forage) (`ForageFacebook` на PyPI, релиз 2026-08-30) | `forage login` → persistent Playwright; `forage scrape URL` → JSON постов/комментов/реакций приватной группы, членом которой вы уже являетесь | живой и ближе всех к «подписки пользователя». Держать *снаружи* |
| [MasuRii/FBScrapeIdeas](https://github.com/MasuRii/FBScrapeIdeas) | Selenium/Playwright + Gemini/Ollama по постам группы | AI уже есть в Watch, скрейпер пусть живёт отдельно |
| [phuc-nt/openclaw-skills facebook-group-monitor](https://github.com/phuc-nt/openclaw-skills) | Склейка скрина ленты → один вызов VLM | идея «лента как картинка» полезна, селекторы — нет |
| [thanh2004nguyen/facebook-group-scraper](https://github.com/thanh2004nguyen/facebook-group-scraper) | Playwright, session json, infinite scroll | типичный одноразовый скрипт |
| [lesander/fbgs](https://github.com/lesander/fbgs) | Selenium + *m.facebook.com*, без css-классов | мобильная вёрстка тоже плывёт |
| [kevinzg/facebook-scraper](https://github.com/kevinzg/facebook-scraper) (~3k★) | `get_posts('page')` без ключа через mbasic | эталон 2020-х, к 2026 частично мёртв, 400+ open issues |
| [FaustRen/facebook-graphql-scraper](https://github.com/FaustRen/facebook-graphql-scraper) | внутренние GraphQL вызовы Facebook | обновлялся в 2026. Не копировать: неофициальный протокол, ToS |

CrowdTangle Meta убила. Замена для исследователей — Content Library API, не для личного мониторинга подписок.

## Что Watch делает с этим

Три канала, без встроенного обхода защиты Facebook.

```
[1] Graph API          страницы, которыми вы админ
[2] import directory   JSON, который выгрузили Forage / fbn / расширение / руками
[3] browser adapter    пустой контракт: если напишете свой — он должен
                       отдавать тот же NormalizedItem
```

Канал 2 — это и есть «развитие идей с GitHub». Forage и fbn остаются отдельными процессами. Watch только:

- читает JSON/JSONL из `data/fb-inbox/`;
- нормализует поля (`text`, `url`, `created_at`, `images`, `author`);
- дедупит по `id`;
- гоняет медиа и правила как любой другой источник.

Так скрейпер можно менять раз в квартал, не трогая LLM-пайплайн.

## Юридическая рамка коротко

Сбор через Graph API своих Page — норма. Сбор группы, где вы админ, после App Review — норма. Чтение своей ленты/чужих групп браузером под своим аккаунтом — нарушение Automated Data Collection Terms Meta, риск бана. Watch это не прячет.
