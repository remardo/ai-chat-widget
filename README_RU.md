# AI Chat Widget

🇬🇧 [English version](README.md)

Готовый AI чат-виджет для любого сайта. Работает с OpenAI, Claude, Gemini, GigaChat, YandexGPT, Ollama и любым OpenAI-совместимым API.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

## Возможности

- **Видит контекст страницы** — бот знает URL, заголовки, контент и выделенный текст
- **Любой AI провайдер** — OpenAI, Claude, Gemini, GigaChat, YandexGPT, DeepSeek, Qwen, Ollama, OpenRouter
- **Одна строка кода** — просто добавьте `<script>` тег
- **Self-hosted** — полный контроль над данными
- **База знаний** — загружайте знания из markdown файлов
- **История чата** — сохраняется между перезагрузками страницы
- **Telegram уведомления** — получайте алерты об эскалациях и отзывах
- **Безопасность** — rate limiting, детекция атак, блокировка IP
- **Приватность** — исключайте чувствительные страницы из контекста
- **Markdown** — форматирование в ответах
- **Адаптивный дизайн** — работает на всех устройствах

## Быстрый старт

### 1. Клонируйте и настройте

```bash
git clone https://github.com/gmen1057/ai-chat-widget.git
cd ai-chat-widget
cp backend/.env.example backend/.env
# Отредактируйте backend/.env — добавьте API ключ
```

### 2. Запустите через Docker (рекомендуется)

```bash
docker-compose up -d
```

### 3. Или запустите локально

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Примечание по RAG (`zvec`):
- Полноценный `zvec` режим официально поддерживается на Linux/macOS (Python 3.10-3.12).
- На Windows локально backend работает с fallback-режимом без `zvec`.
- Для полноценного RAG на Windows используйте Docker (контейнер Linux).

### 4. Добавьте на сайт

```html
<script
  src="https://ваш-сервер.com/widget/widget.js"
  data-server="https://ваш-сервер.com"
  data-title="Поддержка"
  data-welcome="Привет! Чем могу помочь?"
  data-placeholder="Введите сообщение..."
  data-position="bottom-right"
></script>
```

Готово! Виджет появится на вашем сайте.

## Настройка AI провайдера

Отредактируйте `backend/.env`:

**OpenAI** (по умолчанию):
```env
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=sk-xxx
AI_MODEL=gpt-4o-mini
```

**Claude (Anthropic)**:
```env
AI_BASE_URL=https://api.anthropic.com/v1
AI_API_KEY=sk-ant-xxx
AI_MODEL=claude-sonnet-4-20250514
```

**Google Gemini**:
```env
AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
AI_API_KEY=AIza...
AI_MODEL=gemini-2.0-flash-exp
```

**GigaChat (Сбер)**:
```env
AI_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1
AI_MODEL=GigaChat
GIGACHAT_CREDENTIALS=base64_credentials
```

**YandexGPT**:
```env
AI_BASE_URL=https://llm.api.cloud.yandex.net/foundationModels/v1
AI_API_KEY=ваш-api-key
AI_MODEL=yandexgpt-lite
YANDEX_FOLDER_ID=ваш-folder-id
```

**Ollama** (локально, бесплатно):
```env
AI_BASE_URL=http://localhost:11434/v1
AI_API_KEY=ollama
AI_MODEL=llama3.2
```

Полный список провайдеров в `backend/.env.example`.

## Параметры виджета

| Атрибут | По умолчанию | Описание |
|---------|--------------|----------|
| `data-server` | Обязательный | URL вашего бэкенда |
| `data-title` | "Chat" | Заголовок виджета |
| `data-welcome` | "Привет!" | Приветственное сообщение |
| `data-placeholder` | "Сообщение..." | Плейсхолдер поля ввода |
| `data-position` | "bottom-right" | Позиция: `bottom-right`, `bottom-left` |
| `data-primary-color` | "#2563eb" | Основной цвет (hex) |
| `data-include` | "" | Показывать только на этих страницах |
| `data-exclude` | "" | Не показывать на этих страницах |
| `data-private` | "" | Не собирать контент на этих страницах |

## Контекст страницы

Бот автоматически видит контекст текущей страницы:

| Контекст | Описание |
|----------|----------|
| **URL** | Адрес текущей страницы |
| **Заголовок** | Title страницы |
| **Описание** | Meta description |
| **Заголовки** | Структура H1, H2 |
| **Контент** | Текст страницы |
| **Выделенный текст** | Текст, который пользователь выделил мышкой |

**Пример:** Пользователь на странице `/pricing` спрашивает "Сколько стоит Pro?" — бот видит содержимое страницы с ценами и даёт точный ответ.

**Пример:** Пользователь выделяет текст "Enterprise план" и спрашивает "Расскажи подробнее" — бот понимает, о чём речь.

## База знаний

Добавьте markdown файлы в папку `knowledge/`:

```
knowledge/
  about.md      # О компании
  faq.md        # Частые вопросы
  pricing.md    # Цены
```

Бот будет использовать эту информацию для ответов.

### Автообновление из Supabase

Если цены/акции/карточки дверей часто меняются, можно синхронизировать их в markdown-файл базы знаний:

```bash
cd backend
python scripts/sync_supabase_knowledge.py \
  --output ../knowledge/supabase-live-rag.md \
  --reload-url http://127.0.0.1:8080/api/chat/knowledge/reload
```

Скрипт:
- читает таблицы `SUPABASE_TABLE_DOORS`, `SUPABASE_TABLE_PROMOTIONS`, `SUPABASE_TABLE_COMPANY`,
- обновляет файл `knowledge/supabase-live-rag.md`,
- (опционально) дергает endpoint перезагрузки знаний без рестарта сервиса.

Для cron (каждые 30 минут):

```cron
*/30 * * * * cd /opt/ai-chat-widget/backend && /opt/ai-chat-widget/backend/venv/bin/python scripts/sync_supabase_knowledge.py --output ../knowledge/supabase-live-rag.md --reload-url http://127.0.0.1:8080/api/chat/knowledge/reload >> /var/log/ai-chat-sync.log 2>&1
```

## Telegram уведомления

Получайте уведомления когда пользователь:
- Просит связать с человеком ("хочу поговорить с оператором")
- Сообщает о проблеме ("не работает", "ошибка")
- Оставляет положительный отзыв ("спасибо", "отлично")
- Оставляет негативный отзыв ("плохо", "не помогло")

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdef...
TELEGRAM_CHAT_ID=ваш_chat_id
```

Как узнать chat_id:
1. Напишите боту [@userinfobot](https://t.me/userinfobot)
2. Он ответит вашим chat_id

## Хранение данных

**JSON** (по умолчанию, для разработки):
```env
STORAGE_TYPE=json
```

**SQLite** (один сервер):
```env
STORAGE_TYPE=sqlite
```

**PostgreSQL** (продакшен):
```env
STORAGE_TYPE=postgres
DATABASE_URL=postgresql://user:pass@localhost/chatbot
```

## Продакшен деплой

### Docker Compose

```bash
cp backend/.env.example backend/.env
# Отредактируйте .env

docker-compose up -d
docker-compose logs -f  # Просмотр логов
```

### Systemd + Nginx

1. Создайте сервис:
```bash
sudo nano /etc/systemd/system/ai-chat-widget.service
```

```ini
[Unit]
Description=AI Chat Widget
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/ai-chat-widget/backend
ExecStart=/opt/ai-chat-widget/backend/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
Restart=always

[Install]
WantedBy=multi-user.target
```

2. Запустите:
```bash
sudo systemctl enable ai-chat-widget
sudo systemctl start ai-chat-widget
```

3. Настройте Nginx:
```nginx
server {
    listen 443 ssl;
    server_name chat.example.com;

    ssl_certificate /etc/letsencrypt/live/chat.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/chat.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Структура проекта

```
ai-chat-widget/
├── backend/
│   ├── app/
│   │   ├── api/chat.py       # API эндпоинты
│   │   ├── services/
│   │   │   ├── ai_service.py # Интеграция с AI
│   │   │   ├── telegram.py   # Telegram уведомления
│   │   │   ├── security.py   # Безопасность
│   │   │   └── storage/      # Хранение данных
│   │   └── config.py         # Конфигурация
│   └── .env                  # Настройки
├── widget/
│   └── widget.js             # Виджет (всё в одном файле)
├── knowledge/                # База знаний
├── docker-compose.yml
└── Dockerfile
```

## Безопасность

- **Rate Limiting** — ограничение запросов в минуту/час
- **Детекция атак** — SQL injection, XSS, prompt injection
- **Блокировка IP** — автоматический бан атакующих
- **Система страйков** — прогрессивные наказания
- **Валидация** — лимиты длины сообщений

## Лицензия

MIT License — используйте как хотите.

## Поддержка

- Issues: [GitHub Issues](https://github.com/gmen1057/ai-chat-widget/issues)
- Telegram: [@bzc_e](https://t.me/bzc_e)
