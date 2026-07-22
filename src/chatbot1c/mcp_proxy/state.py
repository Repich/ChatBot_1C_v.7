"""In-memory command lifecycle for the single-process MVP proxy."""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from .config import ProxySettings

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
            return (
                time.monotonic() - state.last_one_c_poll
                <= self.settings.heartbeat_seconds
            )

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
            self._condition.notify_all()
            return command

    async def lease(self, channel: str) -> BridgeCommand | None:
        deadline = time.monotonic() + self.settings.poll_wait_seconds
        async with self._condition:
            state = self._get_or_create_channel(channel)
            state.last_one_c_poll = time.monotonic()
            while True:
                command = self._take_queued(state)
                if command is not None:
                    command.status = CommandStatus.LEASED
                    command.leased_at = time.monotonic()
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
            return True

    async def expire(self, command: BridgeCommand) -> bool:
        return await self._terminate(
            command,
            status=CommandStatus.EXPIRED,
            outcome=CommandOutcome(
                kind="error", message="MCP bridge transport timeout"
            ),
        )

    async def cancel(self, command: BridgeCommand, message: str) -> bool:
        return await self._terminate(
            command,
            status=CommandStatus.CANCELLED,
            outcome=CommandOutcome(kind="cancelled", message=message),
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
            self._closed = True
            for state in self._channels.values():
                for command in tuple(state.active.values()):
                    self._finish(
                        state,
                        command,
                        status=CommandStatus.CANCELLED,
                        outcome=CommandOutcome(
                            kind="error", message="MCP bridge transport unavailable"
                        ),
                    )
            self._condition.notify_all()

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
    ) -> bool:
        async with self._condition:
            state = self._channels.get(command.channel)
            active = state.active.get(command.command_id) if state else None
            if state is None or active is not command:
                return False
            self._finish(state, command, status=status, outcome=outcome)
            return True

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
