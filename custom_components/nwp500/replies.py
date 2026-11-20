"""Waiting for a heater's reply to a read it is asked over MQTT.

The reads are published, then answered asynchronously on the heater's
topic: a reply is handed to whoever waits for it on that heater, and a
read with no reply is asked again before it is left to the next refresh.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

_LOGGER = logging.getLogger(__name__)

Request = Callable[[str], Awaitable[bool]]


class Replies:
    """The reads of one kind waiting on each heater's reply, by MAC."""

    def __init__(self, label: str) -> None:
        #: What is read, for the log lines.
        self.label = label
        self._waiting: dict[str, list[asyncio.Future[dict[str, Any]]]] = {}

    def __contains__(self, mac_address: object) -> bool:
        """Whether a read waits on `mac_address`'s reply."""
        return mac_address in self._waiting

    def resolve(self, mac_address: str, reply: dict[str, Any]) -> None:
        """Hand `reply` to every read waiting on the heater.

        A waiter an earlier reply resolved is left as it is.
        """
        for waiter in self._waiting.pop(mac_address, []):
            if not waiter.done():
                waiter.set_result(reply)

    async def fetch(
        self, mac_address: str, request: Request, timeout: float
    ) -> tuple[bool, dict[str, Any] | None]:
        """Publish one read and wait for the heater's reply.

        `timeout` is required rather than defaulted: a default binds the
        constant at definition time, so patching it -- as the tests do --
        would leave this path sleeping the real five seconds.

        Returns:
            (published, reply). `published` is False when the request never
            went out, in which case `reply` is always None -- a caller that
            reports a silent device needs those apart, since a read racing
            with MQTT teardown never asked at all.
        """
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._waiting.setdefault(mac_address, []).append(waiter)

        try:
            if not await request(mac_address):
                return False, None
            # Only the wait is guarded. A failure to publish is already
            # reported as False by the manager, which catches its own
            # transport errors, so the two cannot be confused.
            try:
                return True, await asyncio.wait_for(waiter, timeout=timeout)
            except TimeoutError:
                # DEBUG: the retry reports the final failure.
                _LOGGER.debug(
                    "Timed out after %.0fs waiting for the %s of %s",
                    timeout,
                    self.label,
                    mac_address,
                )
                return True, None
        finally:
            pending = self._waiting.get(mac_address)
            if pending and waiter in pending:
                pending.remove(waiter)
            if pending is not None and not pending:
                self._waiting.pop(mac_address, None)

    async def read_with_retry(
        self,
        mac_address: str,
        request: Request,
        *,
        attempts: int,
        timeout: float,
    ) -> bool:
        """Publish a read, wait for the reply, and ask again if none comes.

        For the on-demand reads, which are answered asynchronously over
        MQTT and are otherwise only repeated on the refresh cycle twenty
        minutes later. Best-effort: never raises, and leaves a device that
        will not answer to the next cycle.

        Returns:
            bool: True if the device answered.
        """
        for attempt in range(1, attempts + 1):
            published, reply = await self.fetch(mac_address, request, timeout)
            if reply is not None:
                _LOGGER.debug(
                    "Read the %s of %s on attempt %d",
                    self.label,
                    mac_address,
                    attempt,
                )
                return True
            if not published:
                # Nothing was asked, so the device owes no answer.
                _LOGGER.debug(
                    "The %s request could not be sent for %s",
                    self.label,
                    mac_address,
                )
                return False
            _LOGGER.debug(
                "No %s reply for %s (attempt %d of %d)",
                self.label,
                mac_address,
                attempt,
                attempts,
            )

        _LOGGER.warning(
            "The device %s did not report its %s after %d attempts; the "
            "next refresh will retry",
            mac_address,
            self.label,
            attempts,
        )
        return False
