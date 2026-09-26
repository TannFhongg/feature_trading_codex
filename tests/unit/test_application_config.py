from dataclasses import replace
from pathlib import Path

import pytest

from trading_bot.application.config import ApplicationConfig, SecretValue
from trading_bot.application.observability import RuntimeMetrics, redact
from trading_bot.domain import DomainValidationError


def environment(**overrides: str) -> dict[str, str]:
    values = {"TRADING_BOT_CONTROL_TOKEN": "control-token-that-is-at-least-32-chars"}
    values.update(overrides)
    return values


def test_config_defaults_to_dry_run_testnet_and_local_control() -> None:
    config = ApplicationConfig.from_env(environment())

    assert config.environment == "testnet"
    assert config.dry_run
    assert not config.order_submission_enabled
    assert not config.live_trading_enabled
    assert config.control_host == "127.0.0.1"
    assert config.ledger_path == Path("data/trading_bot.db")


def test_config_requires_real_control_secret_and_private_bind() -> None:
    with pytest.raises(DomainValidationError, match="missing required"):
        ApplicationConfig.from_env({})
    with pytest.raises(DomainValidationError, match="placeholder"):
        ApplicationConfig.from_env(
            {"TRADING_BOT_CONTROL_TOKEN": "<control-token-placeholder-value>"}
        )
    with pytest.raises(DomainValidationError, match="public interface"):
        ApplicationConfig.from_env(environment(TRADING_BOT_CONTROL_HOST="0.0.0.0"))


def test_config_keeps_submission_and_mainnet_as_separate_startup_opt_ins() -> None:
    base = ApplicationConfig.from_env(environment())
    with pytest.raises(DomainValidationError, match="dry-run cannot enable"):
        replace(base, order_submission_enabled=True)
    with pytest.raises(DomainValidationError, match="separate live-trading"):
        replace(
            base,
            environment="mainnet",
            dry_run=False,
            order_submission_enabled=True,
            api_key=SecretValue("test-key"),
            api_secret=SecretValue("test-secret"),
        )
    with pytest.raises(DomainValidationError, match="positive number"):
        replace(base, reconciliation_interval_seconds=float("nan"))


def test_secret_repr_and_observability_redaction_never_expose_credentials() -> None:
    secret = SecretValue("this-is-a-secret-control-token-value")
    assert secret.value not in repr(secret)
    payload = redact(
        {
            "api_key": "key-value",
            "Authorization": "Bearer token-value",
            "nested": {"signature": "signature-value"},
            "safe": "visible",
        }
    )
    rendered = repr(payload)
    assert "key-value" not in rendered
    assert "token-value" not in rendered
    assert "signature-value" not in rendered
    assert "visible" in rendered


def test_metrics_render_stable_prometheus_text() -> None:
    metrics = RuntimeMetrics()
    metrics.increment("orders_submitted", 2)
    metrics.gauge("runtime_ready", 1)

    assert metrics.render_prometheus() == (
        "trading_bot_orders_submitted_total 2\ntrading_bot_runtime_ready 1\n"
    )
