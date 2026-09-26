"""Authenticated idempotent Control API and durable command service."""

from collections.abc import Callable
from dataclasses import asdict
from hmac import compare_digest
from time import time_ns
from typing import Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from trading_bot.application.config import SecretValue
from trading_bot.application.errors import ControlCommandError
from trading_bot.application.runtime import ApplicationOrchestrator
from trading_bot.domain import (
    CommandStatus,
    ControlCommandRecord,
    RuntimeCommand,
    StrategyState,
)


def _wall_clock_ms() -> int:
    return time_ns() // 1_000_000


class ControlAuditLedger(Protocol):
    async def begin_control_command(self, record: ControlCommandRecord) -> bool: ...

    async def complete_control_command(
        self,
        command_id: str,
        status: CommandStatus,
        result_state: StrategyState,
        completed_at_ms: int,
        *,
        detail_code: str | None = None,
    ) -> bool: ...

    async def get_control_command(self, command_id: str) -> ControlCommandRecord | None: ...


class ControlService:
    """Claim command IDs before invoking the runtime and replay terminal results safely."""

    def __init__(
        self,
        runtime: ApplicationOrchestrator,
        ledger: ControlAuditLedger,
        *,
        clock_ms: Callable[[], int] = _wall_clock_ms,
    ) -> None:
        self._runtime = runtime
        self._ledger = ledger
        self._clock_ms = clock_ms

    async def execute(
        self,
        command_id: str,
        command: RuntimeCommand,
        *,
        actor: str,
    ) -> ControlCommandRecord:
        pending = ControlCommandRecord(
            command_id=command_id,
            command=command,
            actor=actor,
            status=CommandStatus.PENDING,
            requested_at_ms=self._clock_ms(),
        )
        claimed = await self._ledger.begin_control_command(pending)
        if not claimed:
            existing = await self._ledger.get_control_command(command_id)
            if existing is None:
                raise ControlCommandError("claimed command record is unavailable")
            return existing

        try:
            result_state = await self._dispatch(command, command_id)
        except Exception as error:
            detail_code = type(error).__name__
            await self._ledger.complete_control_command(
                command_id,
                CommandStatus.FAILED,
                self._runtime.state,
                self._clock_ms(),
                detail_code=detail_code,
            )
            raise ControlCommandError(f"control command failed with {detail_code}") from error

        await self._ledger.complete_control_command(
            command_id,
            CommandStatus.SUCCEEDED,
            result_state,
            self._clock_ms(),
        )
        completed = await self._ledger.get_control_command(command_id)
        if completed is None:
            raise ControlCommandError("completed command record is unavailable")
        return completed

    async def _dispatch(
        self,
        command: RuntimeCommand,
        command_id: str,
    ) -> StrategyState:
        if command is RuntimeCommand.STATUS:
            return self._runtime.state
        if command is RuntimeCommand.START:
            return await self._runtime.start()
        if command is RuntimeCommand.PAUSE:
            return await self._runtime.pause()
        if command is RuntimeCommand.RESUME:
            return await self._runtime.resume()
        if command is RuntimeCommand.STOP:
            return await self._runtime.stop()
        if command is RuntimeCommand.EMERGENCY_STOP:
            return await self._runtime.emergency_stop(command_id)
        raise AssertionError(f"unsupported runtime command {command.value}")


class CommandBody(BaseModel):
    """Strict body: configuration and trading opt-ins are intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


def create_control_api(
    runtime: ApplicationOrchestrator,
    service: ControlService,
    token: SecretValue,
) -> FastAPI:
    """Build a local/private API with bearer auth on every audited command."""

    app = FastAPI(
        title="Trading Bot Control API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )

    async def authorize(authorization: str | None = Header(default=None)) -> str:
        scheme, _, credential = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not compare_digest(credential, token.value):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="unauthorized",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return "operator"

    @app.get("/health")
    async def health() -> dict[str, object]:
        snapshot = runtime.status()
        return {
            "alive": True,
            "state": snapshot.state.value,
            "error": snapshot.last_error_code,
        }

    @app.get("/ready")
    async def ready() -> JSONResponse:
        snapshot = runtime.status()
        return JSONResponse(
            status_code=200 if snapshot.ready else 503,
            content={"ready": snapshot.ready, "state": snapshot.state.value},
        )

    @app.get("/status")
    async def runtime_status() -> dict[str, object]:
        return _status_payload(runtime)

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        runtime.status()
        return runtime.metrics.render_prometheus()

    @app.post("/commands/{command}")
    async def command(
        command: RuntimeCommand,
        body: CommandBody,
        actor: str = Depends(authorize),
    ) -> dict[str, object]:
        try:
            record = await service.execute(body.command_id, command, actor=actor)
        except ControlCommandError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _command_payload(record)

    return app


def _status_payload(runtime: ApplicationOrchestrator) -> dict[str, object]:
    status_snapshot = runtime.status()
    payload = asdict(status_snapshot)
    payload["state"] = status_snapshot.state.value
    payload["breaker_state"] = status_snapshot.breaker_state.value
    return payload


def _command_payload(record: ControlCommandRecord) -> dict[str, object]:
    return {
        "command_id": record.command_id,
        "command": record.command.value,
        "status": record.status.value,
        "result_state": None if record.result_state is None else record.result_state.value,
        "detail_code": record.detail_code,
        "requested_at_ms": record.requested_at_ms,
        "completed_at_ms": record.completed_at_ms,
    }
