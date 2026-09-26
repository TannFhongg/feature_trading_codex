"""Executable entry point for the P6 Control API process."""

import argparse
import asyncio
from dataclasses import replace

import uvicorn

from trading_bot.application import ApplicationConfig, build_application


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safety-first grid trading bot runtime")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate environment configuration without opening DB or network resources",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="force deterministic local adapters and disable every exchange mutation",
    )
    return parser


async def _serve(config: ApplicationConfig) -> None:
    application = await build_application(config)
    try:
        await application.runtime.start()
        server = uvicorn.Server(
            uvicorn.Config(
                application.api,
                host=config.control_host,
                port=config.control_port,
                log_config=None,
                access_log=False,
            )
        )
        await server.serve()
    finally:
        await application.close()


def main() -> int:
    args = _parser().parse_args()
    config = ApplicationConfig.from_env()
    if args.dry_run:
        config = replace(
            config,
            dry_run=True,
            order_submission_enabled=False,
            live_trading_enabled=False,
        )
    if args.check_config:
        print(
            "configuration valid: "
            f"environment={config.environment} dry_run={str(config.dry_run).lower()} "
            f"submission={str(config.order_submission_enabled).lower()} "
            f"live={str(config.live_trading_enabled).lower()}"
        )
        return 0
    asyncio.run(_serve(config))
    return 0
