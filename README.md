# FastAPI Microservices Patterns

Exploring microservices architecture with FastAPI.
Coming from a Django monolith background — this is a big mental shift.

Each service is a separate FastAPI app with its own database.
Communication: REST for sync, RabbitMQ for async event streaming.

## Services

| Service          | Port | Responsibility                     |
|-----------------|------|------------------------------------|
| `users`          | 8001 | Identity, JWT issuance             |
| `orders`         | 8002 | Order lifecycle, payment state     |
| `notifications`  | 8003 | Email/push via RabbitMQ consumer   |
| `gateway`        | 8000 | Route all external traffic         |

## Patterns Implemented

- **Shared event schemas** — Pydantic models in `shared/events/`
- **Choreography saga** — distributed transactions with compensation
- **Circuit breaker** — CLOSED / OPEN / HALF_OPEN with recovery timeout
- **API Gateway** — prefix routing + httpx proxying
- **Event consumer** — RabbitMQ `aio_pika` subscriber in notifications service

## Run (Docker Compose)

```bash
docker compose up
# Gateway available at http://localhost:8000
# RabbitMQ management at http://localhost:15672
```

## Test

```bash
cd services/users && pytest app/tests.py -v
cd services/orders && pytest app/tests.py -v
```

## Key Learnings

**Hardest parts of going from monolith to microservices:**

1. **Distributed transactions** — no ACID across services. Sagas replace DB transactions.
2. **Data ownership** — each service owns its tables. No cross-service JOINs.
3. **Observability** — need distributed tracing (Jaeger/Zipkin). `logger.info` isn't enough.
4. **Local development** — Docker Compose complexity grows fast. Consider Tilt or Skaffold.
5. **Failure modes** — partial failures are now normal. Circuit breakers, retries, timeouts required.

**When NOT to use microservices:** For anything with < 10 engineers or < 100k req/day, a modular
Django monolith is simpler to operate and debug. Microservices are an organisational pattern as
much as a technical one — they make sense when teams need independent deployment cadences.
