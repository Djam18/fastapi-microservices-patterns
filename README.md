# FastAPI Microservices Patterns

Exploring microservices architecture with FastAPI.
Coming from a Django monolith background — this is a big mental shift.

Each service is a separate FastAPI app with its own database.
Communication: REST for sync, RabbitMQ/Redis Streams for async.

## Services

- `services/users/` — user identity, JWT issuance
- `services/orders/` — order lifecycle, payment state
- `services/notifications/` — email/push, subscribes to events

## Run (Docker Compose)

```bash
docker compose up
```
