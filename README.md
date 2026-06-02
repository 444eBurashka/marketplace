# NeoMarket

Микросервисный маркетплейс на FastAPI + PostgreSQL.

## Сервисы
- **B2B** — Кабинет продавца (`:8001`)
- **B2C** — Платформа покупателя (`:8002`)
- **Moderation** — Модерация товаров (`:8003`)

## Быстрый старт
```bash
cp infrastructure/.env.example infrastructure/.env
docker compose -f infrastructure/docker-compose.yml up --build