"""Circuit breaker pattern for inter-service HTTP calls.

Problem: if orders service calls users service and users is down,
every request waits for a timeout before failing. Under load this
cascades — threads pile up waiting, memory grows, whole system degrades.

Circuit breaker: track failures. After N failures, "open" the circuit.
Fail fast for a cooldown period. Then try again (half-open state).

States:
  CLOSED     — normal operation, requests pass through
  OPEN       — fast-fail, no requests sent downstream
  HALF_OPEN  — one probe request allowed, then CLOSED or OPEN again

Django equivalent: none built-in. Celery has retry limits but not this.
Comparable to resilience4j in the Java world.
"""

import asyncio
import time
import logging
from enum import Enum
from typing import Callable, Awaitable, TypeVar, Any

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerError(Exception):
    """Raised when the circuit is OPEN and the call is rejected."""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_timeout:  float = 30.0,
        success_threshold: int = 2,
    ) -> None:
        self.name              = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self.success_threshold = success_threshold

        self._state          = CircuitState.CLOSED
        self._failure_count  = 0
        self._success_count  = 0
        self._opened_at: float | None = None
        self._lock           = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def call(self, func: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        async with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - (self._opened_at or 0)
                if elapsed < self.recovery_timeout:
                    raise CircuitBreakerError(
                        f"Circuit {self.name!r} is OPEN — rejecting request "
                        f"(retry in {self.recovery_timeout - elapsed:.1f}s)"
                    )
                # Transition to HALF_OPEN for probe
                self._state         = CircuitState.HALF_OPEN
                self._success_count = 0
                logger.info(f"[circuit] {self.name!r} → HALF_OPEN")

        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            await self._on_failure(exc)
            raise

        await self._on_success()
        return result

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._failure_count += 1
            self._success_count  = 0
            logger.warning(
                f"[circuit] {self.name!r} failure #{self._failure_count}: {exc}"
            )
            if (
                self._state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)
                and self._failure_count >= self.failure_threshold
            ):
                self._state     = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.error(
                    f"[circuit] {self.name!r} → OPEN after {self._failure_count} failures"
                )

    async def _on_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    logger.info(f"[circuit] {self.name!r} → CLOSED (recovered)")

    def get_stats(self) -> dict:
        return {
            "name":           self.name,
            "state":          self._state,
            "failure_count":  self._failure_count,
            "success_count":  self._success_count,
        }


# ──────────────────────────────────────────
#  Global registry
# ──────────────────────────────────────────

_registry: dict[str, CircuitBreaker] = {}


def get_breaker(service: str, **kwargs) -> CircuitBreaker:
    """Get or create a circuit breaker for a named service."""
    if service not in _registry:
        _registry[service] = CircuitBreaker(service, **kwargs)
    return _registry[service]
