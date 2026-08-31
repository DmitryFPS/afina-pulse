# Afina Watch

Отдельный слой экосистемы Afina: **мониторинг Telegram + Facebook** по ключевым словам и смысловым фразам, с разбором текста, фото и видео. Не форк `afina-tdl` и не второй MTProto-клиент.

`afina-tdl` уже умеет жить в Telegram от пользовательской сессии, архивировать каналы/чаты в Mongo + GridFS и публиковать события в Kafka (`relay.events`). Watch только **потребляет** этот поток, обогащает медиа локальными моделями и решает, матчится ли пост правилам.

```
Telegram ──► afina-tdl Relay ──► Kafka / Mongo / GridFS
                                        │
Facebook Graph / browser session ───────┤
                                        ▼
                                 Afina Watch
                    extract → OCR / ASR / VLM → embed + LLM
                                        │
                                        ▼
                              SQLite matches + alerts
```

## Что умеет

| Слой | Задача |
|---|---|
| Telegram collector | Читает `relay.events` и/или Mongo `events` + GridFS. Каналы, группы, чаты, топики — всё, что уже подписано в Relay. |
| Facebook collector | Официальный Graph API (страницы, которыми вы управляете) + опциональный браузерный сборщик своей ленты/групп с **явным риском бана**. |
| Media pipeline | Текст как есть. Фото → OCR + описание VLM. Видео → кадры + дорожка звука → Whisper + VLM по ключевым кадрам. |
| Matching | Точные/regex ключевые слова, семантический поиск по эмбеддингам, финальный вердикт локальной LLM. |
| Alerts | Telegram-бот, webhook, файл/stdout. |
| LLM | Только локально: Ollama / OpenAI-совместимый endpoint (`vLLM`, `llama.cpp` server). Облако не требуется. |

## Железо

Среда, из которой писался этот репозиторий — чужой sandbox (2 vCPU, 2 ГБ RAM, без GPU). **Ваш ПК отсюда не виден.** Перед первым запуском выполните на своей машине:

```bash
bash scripts/probe_hw.sh
```

Скрипт печатает CPU, RAM, NVIDIA/AMD GPU, VRAM, диск и рекомендует конкретные модели.

Ориентиры (локально, без облака):

| Видеокарта | VRAM | Что ставить |
|---|---|---|
| нет / iGPU | — | `qwen2.5:3b` + `nomic-embed-text` + Whisper `tiny`/`base` на CPU. Медленно, но работает. |
| 8 ГБ (3060 / 4060) | 8 | `qwen2.5vl:3b` + `bge-m3` + Whisper `small`. |
| 12 ГБ (3080 / 4070) | 12 | `qwen2.5vl:7b` Q4 + Whisper `medium`. |
| 16–24 ГБ (4080 / 4090 / 3090) | 16–24 | `qwen2.5vl:7b` Q5/Q8 + Whisper `large-v3`. Это целевой профиль. |
| 48 ГБ+ | 48+ | `qwen2.5vl:32b` + `large-v3` параллельно, без очереди. |

Русский + смысл фраз: эмбеддинги `bge-m3` или `intfloat/multilingual-e5-large`. Для вердикта «эта фраза про X, даже если слов нет» — любая instruct-модель 7B+. Для картинок/скринов/видеокадров нужна именно **VL**-модель (`qwen2.5vl`, `qwen2-vl`, `minicpm-v`).

## Почему Telegram не пишется заново

`afina-tdl` / Relay уже решает тяжёлую часть:

- пользовательская MTProto-сессия (`gotd/td`), не Bot API;
- live ingest + catchup;
- медиа в GridFS;
- нормализованные события `post.*` / `message.*` / `comment.created`;
- Kafka-топик `relay.events` после `persist_status=done`;
- UI/REST на `:8090`.

Watch не логинится в Telegram сам. Он подписывается на архив. Один аккаунт — один Relay. Несколько аккаунтов — несколько namespace в `tdl`, Watch читает все.

## Facebook

Разбор репозиториев GitHub/GitLab — `docs/FACEBOOK.md`. С чужих проектов берём форму монитора, не селекторы.

Три канала:

1. Graph API — страницы, которыми вы админ (и группы после App Review, если вы админ).
2. `data/fb-inbox/` — JSON/JSONL из [Forage](https://github.com/jwmoss/forage), [fbn](https://github.com/viseshrp/fbn) или расширения. Watch нормализует и гоняет через тот же LLM-пайплайн.
3. `facebook_browser` — пустой контракт. Вендорить чужой скрейпер внутрь Watch бессмысленно: Facebook ломает DOM быстрее релиза.

Личная лента и чужие группы через Graph API недоступны. Браузер под своей сессией — ToS Meta и риск бана.

## Поток одного поста

1. Коллектор нормализует событие в `NormalizedItem` (платформа, чат, автор, текст, список медиа, permalink, время).
2. Media pipeline скачивает файлы (GridFS / URL / локальный кэш).
3. Тип файла:
   - изображение → OCR + VLM caption;
   - видео/кружок/voice → `ffmpeg` вытаскивает аудио и N кадров → Whisper + VLM;
   - документ-картинка / PDF-страница → OCR + VLM.
4. Склеивается `search_blob`: оригинал + OCR + ASR + caption.
5. Matcher:
   - keyword / regex (мгновенно);
   - cosine similarity эмбеддинга к каждой смысловой фразе (порог в конфиге);
   - опционально LLM-классификатор с JSON-ответом `{match, score, why, tags}`.
6. При попадании — запись в SQLite и алерт.

LLM не гоняется по каждому стикеру. Сначала дешёвые фильтры, VLM/LLM — только если есть медиа или keyword/semantic уже намекнули, либо если правило помечено `always_llm: true`.

## Быстрый старт

```bash
# 1) Python 3.11+
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 2) Железо и модели
bash scripts/probe_hw.sh
bash scripts/pull_models.sh        # тянет рекомендованные модели в Ollama

# 3) Конфиг
cp .env.example .env
cp configs/watch.example.yaml configs/watch.yaml
# отредактируйте keywords / phrases / telegram.kafka / facebook.tokens

# 4) Рядом должен работать Relay
#    docker compose -f /path/to/afina-tdl/docker-compose.relay.yml up -d
#    UI: http://localhost:8090

# 5) Догон за 7 дней, потом живой монитор
python -m afina_watch backfill --days 7 --config configs/watch.yaml
python -m afina_watch --config configs/watch.yaml

# 6) Когда окно закрыто — упаковать сырьё + медиа + матчи
python -m afina_watch archive --close --config configs/watch.yaml
```

Архив пишется в `data/archive/watch_<ts>_<N>items.zip`: `items.jsonl`, `manifest.json`, папка `media/`. Горячее SQLite после `--close` помечается `archived=1`. Просроченное старше `hot_days` пакует `archive` без `--close`. Facebook за те же 7 дней: `forage scrape <group> --days 7 -o data/fb-inbox/week.json`.

Опционально вместе с Redis (очередь медиа) и отдельным API:

```bash
docker compose -f deploy/docker-compose.yml up -d
python -m afina_watch serve --config configs/watch.yaml
```

## Конфиг правил (суть)

```yaml
rules:
  - id: sanctions-talk
    enabled: true
    keywords: ["санкц", "обход ограничений", " SDN "]
    phrases:
      - "как провести платёж в обход ограничений"
      - "серые схемы поставки оборудования"
    semantic_threshold: 0.72
    always_llm: false
    sources:
      telegram: ["*"]          # все маршруты Relay
      facebook: ["page:123", "group:456"]
    actions: ["telegram_alert", "store"]

  - id: visual-only
    enabled: true
    keywords: []
    phrases: ["скрин переписки с реквизитами", "таблица с паспортными данными"]
    semantic_threshold: 0.68
    always_llm: true           # VLM обязателен: текст в посте может быть пустым
    sources:
      telegram: ["*"]
      facebook: ["*"]
```

Смысловая фраза — не keyword. Эмбеддинг фразы сравнивается с эмбеддингом `search_blob`. LLM потом подтверждает или отбраковывает ложные срабатывания.

## Структура репозитория

```
afina_watch/          код
  collectors/         telegram_relay, facebook_graph, facebook_browser
  media/              ffmpeg, ocr, asr, vision
  nlp/                keywords, embeddings, llm, matcher
  store/              sqlite
  alerts/             telegram bot, webhook
  api/                FastAPI: matches, health, rules
configs/
scripts/              probe_hw.sh, pull_models.sh
deploy/               docker-compose
```

## Зависимости от Afina Relay

Минимально нужное в сети Watch:

| Сервис | Зачем |
|---|---|
| Kafka `relay.events` | живой поток |
| MongoDB `relay.events` + GridFS | догон, медиа, ретраи |
| Relay REST `:8090` | health, список маршрутов |

Если Kafka нет (вы запускаете Relay в single-process и топик не используете) — включите `telegram.mode: mongo_tail` и Watch будет ходить хвостом по `_id` / `created_at`.

## Чего здесь нет и не будет

- Второго MTProto-клиента. Сессия уже есть в `tdl`.
- Облачных Vision API. Медиа не уезжает с машины, если вы сами не прописали внешний endpoint.
- Гарантии, что Facebook отдаст личную ленту легально. Не отдаст.
- Магического «подключился — видит вообще всё в соцсетях». Видно только то, на что есть сессия/токен и что коллектор реально забрал.

## Лицензия

Назначение — личный/внутренний мониторинг источников, на которые у вас есть доступ. Соблюдайте ToS Telegram и Meta, законы о персональных данных и авторском праве. Код каркаса: MIT.
