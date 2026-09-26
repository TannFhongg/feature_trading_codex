import asyncio
from pathlib import Path

import httpx

from trading_bot.application import ApplicationConfig, SecretValue, build_application


async def wait_ready(client: httpx.AsyncClient) -> None:
    for _ in range(100):
        response = await client.get("/ready")
        if response.status_code == 200:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("runtime did not become ready")


def test_control_api_authenticates_audits_and_replays_commands_without_safety_bypass() -> None:
    async def scenario() -> None:
        token = "control-token-value-that-is-32-chars"
        application = await build_application(
            ApplicationConfig(
                control_token=SecretValue(token),
                ledger_path=Path(":memory:"),
            )
        )
        transport = httpx.ASGITransport(app=application.api)
        async with httpx.AsyncClient(transport=transport, base_url="http://control") as client:
            unauthorized = await client.post(
                "/commands/start",
                json={"command_id": "start-1"},
            )
            assert unauthorized.status_code == 401

            headers = {"Authorization": f"Bearer {token}"}
            failed = await client.post(
                "/commands/pause",
                headers=headers,
                json={"command_id": "pause-before-start"},
            )
            assert failed.status_code == 409
            failed_record = await application.ledger.get_control_command("pause-before-start")
            assert failed_record is not None
            assert failed_record.status.value == "FAILED"

            started = await client.post(
                "/commands/start",
                headers=headers,
                json={"command_id": "start-1"},
            )
            assert started.status_code == 200
            assert started.json()["result_state"] == "PAUSED"
            await wait_ready(client)

            resumed = await client.post(
                "/commands/resume",
                headers=headers,
                json={"command_id": "resume-1"},
            )
            replayed = await client.post(
                "/commands/resume",
                headers=headers,
                json={"command_id": "resume-1"},
            )
            assert resumed.status_code == 200
            assert resumed.json() == replayed.json()
            assert resumed.json()["result_state"] == "RUNNING"

            status_response = await client.get("/status")
            assert status_response.json()["state"] == "RUNNING"
            assert "order_submission_enabled" not in status_response.text
            assert "live_trading_enabled" not in status_response.text
            assert (await client.get("/metrics")).status_code == 200
            assert (await client.post("/config", headers=headers, json={})).status_code == 404
            invalid = await client.post(
                "/commands/pause",
                headers=headers,
                json={"command_id": "pause-1", "order_submission_enabled": True},
            )
            assert invalid.status_code == 422

            stopped = await client.post(
                "/commands/stop",
                headers=headers,
                json={"command_id": "stop-1"},
            )
            assert stopped.status_code == 200
            assert stopped.json()["result_state"] == "STOPPED"
            assert await application.ledger.get_control_command("resume-1") is not None
        await application.close()

    asyncio.run(scenario())
