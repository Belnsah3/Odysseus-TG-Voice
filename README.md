# Odysseus TG Voice Bot — «Odysseus»

Realtime голосовой AI-ведущий для **видеочата в Telegram**. Заходит в group call
как userbot, слушает участников, думает «мозгом» **Odysseus** и отвечает живым
голосом по-русски.

```
TG VideoChat ──Opus──▶ voice_bridge (pytgcalls userbot, SOCKS5)
                         │  PCM 48k
                         ├─▶ Deepgram STT (nova-3, multi/RU, streaming)
                         │        │ финальный текст
                         ├──▶ odysseus_shim (OpenAI-совместимый :9200)
                         │        └─▶ Odysseus (мозг, :7000, память/скиллы)
                         │                 └─▶ anthropic_proxy (:9100)
                         │                          └─▶ OpenModel.ai /v1/messages
                         │                                   (deepseek-v4-flash)
                         └─◀ ElevenLabs TTS (eleven_multilingual_v2) ──Opus──▶ TG
```

Почему так:
- **Bot API не пускает в видеочат** → нужен userbot на MTProto + `pytgcalls`.
- **OpenModel.ai говорит на Anthropic Messages API** (`/v1/messages`), а не OpenAI
  `/v1/chat/completions`. Поэтому между потребителями и OpenModel стоит
  `anthropic_proxy`, который переводит формат и **выкидывает блоки `thinking`**
  reasoning-модели (иначе бот проговаривал бы размышления вслух).
- **Odysseus не имеет OpenAI-эндпоинта** — у него свой `POST /api/chat_stream`
  (SSE). `odysseus_shim` показывает наружу стандартный `/v1/chat/completions`, а
  внутри водит Odysseus через его родной REST, держа отдельную сессию Odysseus на
  каждый чат (память/скиллы работают пер-чат).

## Состав

| Сервис | Порт | Что делает |
|--------|------|-----------|
| `anthropic_proxy` | 9100 | OpenAI ↔ Anthropic мост к OpenModel.ai, режет `thinking` |
| `odysseus` | 7000 | Мозг (self-hosted AI workspace) |
| `odysseus_shim` | 9200 | OpenAI-совместимая обёртка над Odysseus chat API |
| `voice_bridge` | — | Telethon userbot + pytgcalls + STT/TTS/VAD/оркестрация |
| `chromadb` | — | Векторное хранилище для памяти Odysseus |

## Быстрый старт

### 0. Требования
- Docker + docker compose (или `docker-compose` v1).
- Отдельный Telegram-аккаунт под userbot (не основной — есть риск бана за бота
  в звонке).
- Ключи уже прописаны в `.env` (Telegram, Deepgram, ElevenLabs, OpenModel) и
  SOCKS5-прокси.

### 1. Поднять мозг и аудио-сервисы
```bash
cd ~/projects/odysseus-voice
docker compose up -d --build anthropic_proxy chromadb odysseus odysseus_shim
# проверить мозг (proxy -> OpenModel, затем shim -> Odysseus -> proxy):
./tools/test_brain.sh proxy
./tools/test_brain.sh shim
```
`test_brain.sh shim` должен вернуть короткий русский ответ и поток `content`-дельт.

### 2. Первый вход в Telegram (сессия userbot)

Один раз нужно авторизовать аккаунт userbot. Команда:

```bash
cd ~/projects/odysseus-voice
source .venv/bin/activate
set -a && source .env && set +a
export SESSIONS_DIR="$(pwd)/voice_bridge/sessions"
python tools/tg_setup.py login
```

Telethon спросит **номер телефона** (в формате `+79...`) и **код из Telegram**.
Сессия сохранится в `voice_bridge/sessions/odysseus_userbot.session`.

Без интерактива (если код уже пришёл):

```bash
TG_PHONE=+79XXXXXXXXX TG_CODE=12345 python tools/tg_setup.py login
```

Проверить группы и тестовую:

```bash
python tools/tg_setup.py groups
python tools/tg_setup.py export --chat-id -5214150395
```

### 3. Управление через ЛС (@go_minetik)

Напиши **в личку аккаунту userbot** (не боту!) с того же аккаунта `@go_minetik`:

| Команда | Действие |
|---------|----------|
| `/odysseus call` | Создать/зайти в видеочат **тестовой группы** `-5214150395` |
| `/odysseus join` | Войти в уже открытый звонок тестовой группы |
| `/odysseus leave` | Выйти из звонка |
| `/odysseus groups` | Список групп (📞 = есть звонок) |
| `/odysseus info` | Данные тестовой группы в JSON |
| `/odysseus help` | Все команды |

Можно указать другой chat_id: `/odysseus join -1001234567890`

### 4. Запуск voice_bridge
1. Подними мозг: `docker compose up -d anthropic_proxy chromadb odysseus odysseus_shim`
2. Залогинься: `python tools/tg_setup.py login` (см. выше)
3. Запусти мост:
```bash
./tools/run_local.sh
# или в docker: docker compose up -d voice_bridge && docker compose logs -f voice_bridge
```
4. В **ЛС userbot-аккаунту** напиши: `/odysseus call`

### 5. (альтернатива) Команды из самой группы
| Команда | Действие |
|---------|----------|
| `/odysseus join` | войти в активный видеочат текущего чата |
| `/odysseus leave` | выйти из звонка |
| `/odysseus mute` / `/odysseus unmute` | замолчать / снова говорить |
| `/odysseus reset` | очистить память беседы для этого чата |
| `/odysseus prompt <текст>` | сменить персону на лету |
| `/odysseus say <текст>` | произнести конкретную фразу |
| `/odysseus help` | список команд |

## Конфигурация (`.env`)
Все ключи и параметры — в `.env`. Полезное:
- `OPENMODEL_MODEL` — модель мозга (по умолчанию `deepseek-v4-flash`). Любая
  модель OpenModel с протоколом `messages` подойдёт без правок кода.
- `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL_ID` — голос Odysseusа.
- `TTS_SAMPLE_RATE` — частота PCM от ElevenLabs (по умолчанию 24000, апсемплится
  до 48k для звонка). Если твой тариф даёт `pcm_48000` — поставь 48000.
- `PROXY_URL` — SOCKS5 для Telegram. Есть HTTP-fallback `PROXY_URL_HTTP`.
- `BRAIN_BACKEND` / `BRAIN_URL` — выбор мозга (`odysseus` по умолчанию; см. Plan B).

Персона Odysseusа — в `prompts/persona_odysseus.txt` (правится без пересборки).

BRAIN_URL=http://host.docker.internal:8642/v1
BRAIN_API_KEY=<твой ключ Odysseus>
```

## Troubleshooting
- **Telethon просит код снова** — нет файла сессии или сменился аккаунт. Проверь
  `voice_bridge/sessions/`. Запусти `./tools/run_local.sh` и введи код.
- **`/odysseus join` молчит** — нет активного видеочата в чате, либо userbot не имеет
  прав. Открой group call и повтори. Проверь `docker compose logs -f voice_bridge`.
- **Нет звука от Odysseusа** — проверь ElevenLabs ключ/лимит (free ~10k символов/мес).
  В логах `tts` будут ошибки. Понизь `TTS_SAMPLE_RATE` если тариф не даёт 48k.
- **Бот «думает вслух»** — должно быть исключено (proxy режет `thinking`); если
  всплывает — проверь, что запросы идут через `anthropic_proxy`, а не напрямую.
- **SOCKS5 нестабилен** — поменяй `PROXY_URL` на значение `PROXY_URL_HTTP`.
- **`docker compose` не найден** — используй `docker-compose` (v1) теми же
  командами.
- **WebRTC-медиа не идёт через прокси** — это нормально: SOCKS5 покрывает только
  сигналинг MTProto (Telethon). Сам медиапоток звонка идёт напрямую к серверам TG.

## Безопасность
- Используй **отдельный** TG-аккаунт под userbot.
- `.env` и `*.session` — в `.gitignore`, не коммить их.
- Сервисы слушают только `127.0.0.1`; наружу ничего не торчит. Odysseus запущен с
  `AUTH_ENABLED=false`, потому что доступен лишь по приватной docker-сети — не
  публикуй порт 7000 в интернет без включённой авторизации.

## Тесты
```bash
. .venv/bin/activate
pytest tests/ -q                 # unit (proxy-конвертация и пр.)
RUN_LIVE=1 pytest tests/ -q      # + живой запрос к OpenModel
```
