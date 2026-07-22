"""In-memory command lifecycle for the single-process MVP proxy."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from .config import ProxySettings
from .events import (
    COMMAND_CANCELLED_EVENT,
    COMMAND_COMPLETED_EVENT,
    COMMAND_EXPIRED_EVENT,
    COMMAND_LEASED_EVENT,
    COMMAND_QUEUED_EVENT,
    HEARTBEAT_EVENT,
    RESULT_REJECTED_EVENT,
    SHUTDOWN_EVENT,
    log_event,
)

_CHANNEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ChannelError(Exception):
    pass


class PendingLimitError(Exception):
    pass


class CommandStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased_to_1c"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    kind: Literal["result", "error", "cancelled"]
    payload: dict[str, Any] | None = None
    message: str = ""


@dataclass(slots=True)
class BridgeCommand:
    command_id: str
    channel: str
    session_id: str
    request_key: str
    tool: Literal["execute_query", "get_metadata"]
    params: dict[str, Any]
    created_at: float
    future: asyncio.Future[CommandOutcome]
    status: CommandStatus = CommandStatus.QUEUED
    leased_at: float | None = None

    def wire(self) -> dict[str, Any]:
        return {"id": self.command_id, "tool": self.tool, "params": self.params}


@dataclass(slots=True)
class ChannelState:
    queue: deque[str] = field(default_factory=deque)
    active: dict[str, BridgeCommand] = field(default_factory=dict)
    request_index: dict[tuple[str, str], str] = field(default_factory=dict)
    tombstones: set[str] = field(default_factory=set)
    tombstone_order: deque[str] = field(default_factory=deque)
    last_one_c_poll: float | None = None
    one_c_connected: bool = False


class ProxyState:
    def __init__(self, settings: ProxySettings) -> None:
        self.settings = settings
        self._channels: dict[str, ChannelState] = {}
        self._condition = asyncio.Condition()
        self._closed = False
        self._tombstone_limit = max(64, settings.max_pending_per_channel * 16)

    async def touch_poll(self, channel: str) -> ChannelState:
        async with self._condition:
            state = self._get_or_create_channel(channel)
            state.last_one_c_poll = time.monotonic()
            return state

    async def readiness(self, channel: str) -> bool:
        self.validate_channel(channel)
        async with self._condition:
            state = self._channels.get(channel)
            if state is None or state.last_one_c_poll is None:
                return False
            now = time.monotonic()
            connected = (
                now - state.last_one_c_poll <= self.settings.heartbeat_seconds
            )
            self._set_heartbeat(channel, state, connected=connected, now=now)
            return connected

    async def enqueue(
        self,
        *,
        channel: str,
        session_id: str,
        request_key: str,
        tool: Literal["execute_query", "get_metadata"],
        params: dict[str, Any],
    ) -> BridgeCommand:
        loop = asyncio.get_running_loop()
        async with self._condition:
            if self._closed:
                raise RuntimeError("proxy is shutting down")
            state = self._get_or_create_channel(channel)
            if len(state.active) >= self.settings.max_pending_per_channel:
                raise PendingLimitError("pending command limit reached")
            index_key = (session_id, request_key)
            if index_key in state.request_index:
                raise PendingLimitError("duplicate active MCP request id")
            command = BridgeCommand(
                command_id=uuid4().hex,
                channel=channel,
                session_id=session_id,
                request_key=request_key,
                tool=tool,
                params=params,
                created_at=time.monotonic(),
                future=loop.create_future(),
            )
            state.active[command.command_id] = command
            state.request_index[index_key] = command.command_id
            state.queue.append(command.command_id)
            _log_command(COMMAND_QUEUED_EVENT, command, status=command.status.value)
            self._condition.notify_all()
            return command

    async def lease(self, channel: str) -> BridgeCommand | None:
        deadline = time.monotonic() + self.settings.poll_wait_seconds
        async with self._condition:
            state = self._get_or_create_channel(channel)
            now = time.monotonic()
            if (
                state.one_c_connected
                and state.last_one_c_poll is not None
                and now - state.last_one_c_poll > self.settings.heartbeat_seconds
            ):
                self._set_heartbeat(channel, state, connected=False, now=now)
            self._set_heartbeat(channel, state, connected=True, now=now)
            state.last_one_c_poll = now
            while True:
                command = self._take_queued(state)
                if command is not None:
                    command.status = CommandStatus.LEASED
                    command.leased_at = time.monotonic()
                    _log_command(
                        COMMAND_LEASED_EVENT,
                        command,
                        status=command.status.value,
                        now=command.leased_at,
                    )
                    return command
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._closed:
                    return None
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except TimeoutError:
                    return None

    async def submit_result(
        self, channel: str, command_id: str, payload: dict[str, Any]
    ) -> bool:
        async with self._condition:
            state = self._channels.get(channel)
            if state is None:
                return False
            command = state.active.get(command_id)
            if command is None or command.status is not CommandStatus.LEASED:
                return False
            self._finish(
                state,
                command,
                status=CommandStatus.COMPLETED,
                outcome=CommandOutcome(kind="result", payload=payload),
            )
            successful = payload.get("success") is True
            _log_command(
                COMMAND_COMPLETED_EVENT,
                command,
                status="success" if successful else "query_error",
                level=logging.INFO if successful else logging.WARNING,
            )
            return True

    async def fail_result(
        self, channel: str, command_id: str, message: str
    ) -> bool:
        async with self._condition:
            state = self._channels.get(channel)
            if state is None:
                return False
            command = state.active.get(command_id)
            if command is None or command.status is not CommandStatus.LEASED:
                return False
            self._finish(
                state,
                command,
                status=CommandStatus.COMPLETED,
                outcome=CommandOutcome(kind="error", message=message),
            )
            _log_command(
                RESULT_REJECTED_EVENT,
                command,
                status="invalid_result",
                level=logging.WARNING,
            )
            return True

    async def expire(self, command: BridgeCommand) -> bool:
        return await self._terminate(
            command,
            status=CommandStatus.EXPIRED,
            outcome=CommandOutcome(
                kind="error", message="MCP bridge transport timeout"
            ),
            event_name=COMMAND_EXPIRED_EVENT,
            event_status="expired",
            level=logging.WARNING,
        )

    async def cancel(self, command: BridgeCommand, message: str) -> bool:
        return await self._terminate(
            command,
            status=CommandStatus.CANCELLED,
            outcome=CommandOutcome(kind="cancelled", message=message),
            event_name=COMMAND_CANCELLED_EVENT,
            event_status="cancelled",
        )

    async def cancel_request(
        self, channel: str, session_id: str, request_key: str
    ) -> bool:
        async with self._condition:
            state = self._channels.get(channel)
            if state is None:
                return False
            command_id = state.request_index.get((session_id, request_key))
            command = state.active.get(command_id) if command_id else None
            if command is None:
                return False
            self._finish(
                state,
                command,
                status=CommandStatus.CANCELLED,
                outcome=CommandOutcome(kind="cancelled", message="MCP request cancelled"),
            )
            _log_command(
                COMMAND_CANCELLED_EVENT, command, status="cancelled"
            )
            return True

    async def cancel_session(self, channel: str, session_id: str) -> None:
        self.validate_channel(channel)
        async with self._condition:
            state = self._channels.get(channel)
            if state is None:
                return
            commands = [
                command
                for command in state.active.values()
                if command.session_id == session_id
            ]
            for command in commands:
                self._finish(
                    state,
                    command,
                    status=CommandStatus.CANCELLED,
                    outcome=CommandOutcome(
                        kind="cancelled", message="MCP session cancelled"
                    ),
                )
                _log_command(
                    COMMAND_CANCELLED_EVENT, command, status="cancelled"
                )

    async def command_for_result(
        self, channel: str, command_id: str
    ) -> BridgeCommand | None:
        self.validate_channel(channel)
        async with self._condition:
            state = self._channels.get(channel)
            if state is None:
                return None
            command = state.active.get(command_id)
            if command is None or command.status is not CommandStatus.LEASED:
                return None
            return command

    async def shutdown(self) -> None:
        async with self._condition:
            if self._closed:
                return
            self._closed = True
            active_commands = 0
            for state in self._channels.values():
                for command in tuple(state.active.values()):
                    active_commands += 1
                    self._finish(
                        state,
                        command,
                        status=CommandStatus.CANCELLED,
                        outcome=CommandOutcome(
                            kind="error", message="MCP bridge transport unavailable"
                        ),
                    )
                    _log_command(
                        COMMAND_CANCELLED_EVENT, command, status="cancelled"
                    )
            self._condition.notify_all()
            log_event(
                SHUTDOWN_EVENT,
                status="complete",
                channel_count=len(self._channels),
                active_commands=active_commands,
            )

    @staticmethod
    def validate_channel(channel: str) -> None:
        if not _CHANNEL_PATTERN.fullmatch(channel):
            raise ChannelError("invalid channel")

    def _get_or_create_channel(self, channel: str) -> ChannelState:
        self.validate_channel(channel)
        state = self._channels.get(channel)
        if state is not None:
            return state
        if len(self._channels) >= self.settings.max_channels:
            raise ChannelError("channel limit reached")
        state = ChannelState()
        self._channels[channel] = state
        return state

    @staticmethod
    def _take_queued(state: ChannelState) -> BridgeCommand | None:
        while state.queue:
            command_id = state.queue.popleft()
            command = state.active.get(command_id)
            if command is not None and command.status is CommandStatus.QUEUED:
                return command
        return None

    async def _terminate(
        self,
        command: BridgeCommand,
        *,
        status: CommandStatus,
        outcome: CommandOutcome,
        event_name: str,
        event_status: str,
        level: int = logging.INFO,
    ) -> bool:
        async with self._condition:
            state = self._channels.get(command.channel)
            active = state.active.get(command.command_id) if state else None
            if state is None or active is not command:
                return False
            self._finish(state, command, status=status, outcome=outcome)
            _log_command(
                event_name,
                command,
                status=event_status,
                level=level,
            )
            return True

    @staticmethod
    def _set_heartbeat(
        channel: str,
        state: ChannelState,
        *,
        connected: bool,
        now: float,
    ) -> None:
        if state.one_c_connected is connected:
            return
        state.one_c_connected = connected
        level = logging.INFO if connected else logging.WARNING
        status = "connected" if connected else "disconnected"
        if state.last_one_c_poll is None:
            log_event(HEARTBEAT_EVENT, level=level, channel=channel, status=status)
            return
        log_event(
            HEARTBEAT_EVENT,
            level=level,
            channel=channel,
            status=status,
            elapsed_ms=_elapsed_ms(state.last_one_c_poll, now=now),
        )

    def _finish(
        self,
        state: ChannelState,
        command: BridgeCommand,
        *,
        status: CommandStatus,
        outcome: CommandOutcome,
    ) -> None:
        command.status = status
        state.active.pop(command.command_id, None)
        state.request_index.pop((command.session_id, command.request_key), None)
        self._remember_tombstone(state, command.command_id)
        if not command.future.done():
            command.future.set_result(outcome)
        self._condition.notify_all()

    def _remember_tombstone(self, state: ChannelState, command_id: str) -> None:
        state.tombstones.add(command_id)
        state.tombstone_order.append(command_id)
        while len(state.tombstone_order) > self._tombstone_limit:
            expired = state.tombstone_order.popleft()
            state.tombstones.discard(expired)


def _log_command(
    event_name: str,
    command: BridgeCommand,
    *,
    status: str,
    level: int = logging.INFO,
    now: float | None = None,
) -> None:
    log_event(
        event_name,
        level=level,
        command_id=command.command_id,
        channel=command.channel,
        tool=command.tool,
        status=status,
        elapsed_ms=_elapsed_ms(command.created_at, now=now),
    )


def _elapsed_ms(started_at: float, *, now: float | None = None) -> int:
    finished_at = time.monotonic() if now is None else now
    return max(0, int((finished_at - started_at) * 1000))
