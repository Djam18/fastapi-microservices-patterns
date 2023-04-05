import asyncio
import json
import os
import logging
import smtplib
from email.mime.text import MIMEText

# Notifications service — event consumer pattern.
# Subscribes to the "orders" RabbitMQ exchange, handles events.
# This is the async messaging half of microservices.
# Django: Celery tasks triggered by signals. Here: separate service, long-running consumer.
# The key insight: this service has zero API calls — it only reacts to events.
# Decoupled by design. Orders service doesn't know notifications exists.

logger = logging.getLogger(__name__)

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
SMTP_HOST    = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "1025"))  # MailHog default for dev


# ──────────────────────────────────────────
#  Email helpers
# ──────────────────────────────────────────

def _send_email(to: str, subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"]    = "noreply@example.com"
    msg["To"]      = to
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.sendmail("noreply@example.com", [to], msg.as_string())
        logger.info(f"[email] sent to={to!r} subject={subject!r}")
    except Exception as e:
        logger.error(f"[email] failed to={to!r}: {e}")


def _lookup_email(user_id: int) -> str:
    """In prod: call users service or maintain local projection.
    For now: stub that returns a test address.
    """
    return f"user-{user_id}@example.com"


# ──────────────────────────────────────────
#  Event handlers
# ──────────────────────────────────────────

def handle_order_created(payload: dict) -> None:
    user_id  = payload.get("user_id")
    order_id = payload.get("order_id")
    total    = payload.get("total_cents", 0) / 100
    email    = _lookup_email(user_id)

    _send_email(
        to=email,
        subject=f"Order #{order_id[:8]} confirmed",
        body=f"Your order has been placed!\n\nOrder ID: {order_id}\nTotal: ${total:.2f}\n\nThank you!",
    )


def handle_order_paid(payload: dict) -> None:
    user_id  = payload.get("user_id")
    order_id = payload.get("order_id")
    amount   = payload.get("amount_cents", 0) / 100
    email    = _lookup_email(user_id)

    _send_email(
        to=email,
        subject=f"Payment received — Order #{order_id[:8]}",
        body=f"Payment confirmed!\n\nOrder ID: {order_id}\nAmount paid: ${amount:.2f}\n\nYour order is being processed.",
    )


def handle_order_cancelled(payload: dict) -> None:
    user_id  = payload.get("user_id")
    order_id = payload.get("order_id")
    reason   = payload.get("reason", "no reason provided")
    email    = _lookup_email(user_id)

    _send_email(
        to=email,
        subject=f"Order #{order_id[:8]} cancelled",
        body=f"Your order has been cancelled.\n\nOrder ID: {order_id}\nReason: {reason}",
    )


HANDLERS = {
    "order.created":   handle_order_created,
    "order.paid":      handle_order_paid,
    "order.cancelled": handle_order_cancelled,
}


# ──────────────────────────────────────────
#  RabbitMQ consumer
# ──────────────────────────────────────────

async def start_consumer() -> None:
    import aio_pika

    logger.info("[consumer] connecting to RabbitMQ...")
    conn = await aio_pika.connect_robust(RABBITMQ_URL)

    async with conn:
        channel  = await conn.channel()
        exchange = await channel.declare_exchange("orders", aio_pika.ExchangeType.TOPIC, durable=True)
        queue    = await channel.declare_queue("notifications.orders", durable=True)

        # Subscribe to all order events
        await queue.bind(exchange, routing_key="order.*")

        logger.info("[consumer] listening for order.* events")

        async with queue.iterator() as msgs:
            async for message in msgs:
                async with message.process():
                    try:
                        payload    = json.loads(message.body)
                        event_type = payload.get("event_type", "")
                        handler    = HANDLERS.get(event_type)
                        if handler:
                            handler(payload)
                            logger.info(f"[consumer] handled {event_type}")
                        else:
                            logger.warning(f"[consumer] no handler for {event_type!r}")
                    except Exception as e:
                        logger.error(f"[consumer] processing error: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_consumer())
