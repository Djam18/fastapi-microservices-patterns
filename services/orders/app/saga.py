"""Saga pattern — distributed transaction coordination.

Problem: creating an order requires:
1. Reserve inventory (inventory service)
2. Charge payment (payment service)
3. Confirm order (orders service)

If step 2 fails, step 1 must be rolled back.
In a monolith: one DB transaction handles this. In microservices: sagas.

Choreography-based saga (used here):
  - Each service publishes events and listens for compensating events.
  - No central orchestrator. More decoupled, harder to trace.

Orchestration-based saga (alternative):
  - A saga orchestrator sends commands and tracks state.
  - Easier to reason about, introduces coordinator coupling.

This file implements a choreography saga for the order creation flow.
"""

import asyncio
import json
import logging
import os
import uuid
from enum import Enum

logger = logging.getLogger(__name__)

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")


class SagaStep(str, Enum):
    RESERVE_INVENTORY = "reserve_inventory"
    CHARGE_PAYMENT    = "charge_payment"
    CONFIRM_ORDER     = "confirm_order"


class SagaStatus(str, Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    COMPENSATING = "compensating"
    COMPENSATED  = "compensated"


# In-memory saga log — would be a DB table in production
# Key: saga_id, Value: saga state dict
_saga_store: dict[str, dict] = {}


def create_saga(order_id: str, user_id: int, items: list) -> str:
    saga_id = str(uuid.uuid4())
    _saga_store[saga_id] = {
        "saga_id":  saga_id,
        "order_id": order_id,
        "user_id":  user_id,
        "items":    items,
        "status":   SagaStatus.PENDING,
        "steps":    [],
        "compensations": [],
    }
    logger.info(f"[saga] created saga_id={saga_id} for order_id={order_id}")
    return saga_id


async def advance_saga(saga_id: str, step: str, success: bool, payload: dict = None) -> None:
    saga = _saga_store.get(saga_id)
    if not saga:
        logger.warning(f"[saga] unknown saga_id={saga_id}")
        return

    saga["steps"].append({"step": step, "success": success, "payload": payload or {}})

    if not success:
        logger.warning(f"[saga] step {step!r} failed — starting compensation for saga_id={saga_id}")
        saga["status"] = SagaStatus.COMPENSATING
        await _compensate(saga)
        return

    # Check if all steps completed
    completed_steps = {s["step"] for s in saga["steps"] if s["success"]}
    all_steps       = {SagaStep.RESERVE_INVENTORY, SagaStep.CHARGE_PAYMENT, SagaStep.CONFIRM_ORDER}
    if all_steps.issubset(completed_steps):
        saga["status"] = SagaStatus.COMPLETED
        logger.info(f"[saga] saga_id={saga_id} completed successfully")
    else:
        saga["status"] = SagaStatus.RUNNING


async def _compensate(saga: dict) -> None:
    """Run compensating transactions in reverse order."""
    completed_steps = [s["step"] for s in saga["steps"] if s["success"]]

    for step in reversed(completed_steps):
        try:
            if step == SagaStep.RESERVE_INVENTORY:
                await _publish("inventory.release", {"order_id": saga["order_id"], "items": saga["items"]})
                logger.info(f"[saga] compensated: released inventory for order_id={saga['order_id']}")
            elif step == SagaStep.CHARGE_PAYMENT:
                await _publish("payment.refund", {"order_id": saga["order_id"], "user_id": saga["user_id"]})
                logger.info(f"[saga] compensated: issued refund for order_id={saga['order_id']}")
        except Exception as e:
            logger.error(f"[saga] compensation failed for step={step!r}: {e}")

    saga["status"] = SagaStatus.COMPENSATED


async def _publish(routing_key: str, payload: dict) -> None:
    try:
        import aio_pika
        conn    = await aio_pika.connect_robust(RABBITMQ_URL)
        async with conn:
            channel  = await conn.channel()
            exchange = await channel.declare_exchange("saga", aio_pika.ExchangeType.TOPIC, durable=True)
            message  = aio_pika.Message(body=json.dumps(payload).encode())
            await exchange.publish(message, routing_key=routing_key)
    except Exception as e:
        logger.warning(f"[saga] publish failed ({routing_key}): {e}")


def get_saga(saga_id: str) -> dict | None:
    return _saga_store.get(saga_id)
