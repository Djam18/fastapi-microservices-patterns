from fastapi import FastAPI, HTTPException, Depends, Header, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, mapped_column, Mapped
from sqlalchemy import select, String, Integer, Text
from typing import Optional
import os, httpx, json, logging, datetime, uuid as _uuid

# Orders service — owns the order aggregate.
# Service-to-service calls: httpx async client to verify tokens with users service.
# This is the "synchronous query" pattern — works but creates coupling.
# Better pattern: each service verifies the JWT itself (shared secret or JWKS).
# I'll implement the shared-secret approach here since it's simpler.

logger = logging.getLogger(__name__)

DATABASE_URL     = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./orders.db")
USERS_URL        = os.environ.get("USERS_SERVICE_URL", "http://localhost:8001")
JWT_SECRET       = os.environ.get("JWT_SECRET", "dev-secret-change-me")
RABBITMQ_URL     = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

engine           = create_async_engine(DATABASE_URL, echo=False)
AsyncSession_    = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class OrderModel(Base):
    __tablename__ = "orders"
    id:          Mapped[str]  = mapped_column(String(36), primary_key=True)
    user_id:     Mapped[int]  = mapped_column(Integer, nullable=False)
    status:      Mapped[str]  = mapped_column(String(50), default="pending")
    total_cents: Mapped[int]  = mapped_column(Integer, default=0)
    items_json:  Mapped[str]  = mapped_column(Text, default="[]")
    created_at:  Mapped[str]  = mapped_column(String(32))


app = FastAPI(title="Orders Service", version="0.1.0")


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSession_() as session:
        yield session


# ──────────────────────────────────────────
#  Auth dependency — verify JWT locally
# ──────────────────────────────────────────

import jwt

def _require_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing token")
    token = authorization.removeprefix("Bearer ")
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")


# ──────────────────────────────────────────
#  Schemas
# ──────────────────────────────────────────

class OrderItemSchema(BaseModel):
    sku:         str
    quantity:    int
    unit_price_cents: int


class OrderCreate(BaseModel):
    items: list[OrderItemSchema]


class OrderResponse(BaseModel):
    id:          str
    user_id:     int
    status:      str
    total_cents: int
    items:       list[dict]
    created_at:  str


# ──────────────────────────────────────────
#  Message publishing (best-effort)
# ──────────────────────────────────────────

async def _publish_event(event_type: str, payload: dict) -> None:
    """Publish event to RabbitMQ. Fails silently — orders still persist."""
    try:
        import aio_pika
        conn = await aio_pika.connect_robust(RABBITMQ_URL)
        async with conn:
            channel = await conn.channel()
            exchange = await channel.declare_exchange("orders", aio_pika.ExchangeType.TOPIC, durable=True)
            message  = aio_pika.Message(
                body=json.dumps({"event_type": event_type, **payload}).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            )
            await exchange.publish(message, routing_key=event_type)
    except Exception as e:
        logger.warning(f"event publish failed ({event_type}): {e}")


# ──────────────────────────────────────────
#  Routes
# ──────────────────────────────────────────

@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    body: OrderCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(_require_user),
):
    total = sum(i.quantity * i.unit_price_cents for i in body.items)
    order = OrderModel(
        id=str(_uuid.uuid4()),
        user_id=user["uid"],
        status="pending",
        total_cents=total,
        items_json=json.dumps([i.dict() for i in body.items]),
        created_at=datetime.datetime.utcnow().isoformat(),
    )
    db.add(order)
    await db.commit()

    await _publish_event("order.created", {
        "order_id":    order.id,
        "user_id":     order.user_id,
        "total_cents": order.total_cents,
        "items":       json.loads(order.items_json),
    })

    return OrderResponse(
        id=order.id, user_id=order.user_id, status=order.status,
        total_cents=order.total_cents, items=json.loads(order.items_json),
        created_at=order.created_at,
    )


@app.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(_require_user),
):
    order = (await db.execute(select(OrderModel).where(OrderModel.id == order_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    if order.user_id != user["uid"] and user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "access denied")
    return OrderResponse(
        id=order.id, user_id=order.user_id, status=order.status,
        total_cents=order.total_cents, items=json.loads(order.items_json),
        created_at=order.created_at,
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "orders"}
