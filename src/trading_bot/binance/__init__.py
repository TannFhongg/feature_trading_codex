"""Public Binance USD-M Futures integration; no trading operations are exposed."""

from trading_bot.binance.errors import (
    BinanceAdapterError,
    BinanceHttpError,
    BinanceProtocolError,
    BinanceStaleStreamError,
    BinanceTransportError,
)
from trading_bot.binance.models import (
    MAINNET_REST_BASE_URL,
    MAINNET_WEBSOCKET_BASE_URL,
    TESTNET_REST_BASE_URL,
    TESTNET_WEBSOCKET_BASE_URL,
    AggregateTrade,
    BinancePublicConfig,
    BookTicker,
    MarketEvent,
    MarketStreamHealth,
    MarketStreamKind,
    MarketStreamState,
    MarkPrice,
    TimeSync,
)
from trading_bot.binance.parsing import parse_market_message, parse_server_time, parse_symbol_rules
from trading_bot.binance.rest import BinancePublicRestClient, HttpxJsonTransport, JsonTransport
from trading_bot.binance.stream import (
    BinanceMarketStream,
    MessageSource,
    WebsocketsMessageSource,
)

__all__ = [
    "MAINNET_REST_BASE_URL",
    "MAINNET_WEBSOCKET_BASE_URL",
    "TESTNET_REST_BASE_URL",
    "TESTNET_WEBSOCKET_BASE_URL",
    "AggregateTrade",
    "BinanceAdapterError",
    "BinanceHttpError",
    "BinanceMarketStream",
    "BinanceProtocolError",
    "BinancePublicConfig",
    "BinancePublicRestClient",
    "BinanceStaleStreamError",
    "BinanceTransportError",
    "BookTicker",
    "HttpxJsonTransport",
    "JsonTransport",
    "MarkPrice",
    "MarketEvent",
    "MarketStreamHealth",
    "MarketStreamKind",
    "MarketStreamState",
    "MessageSource",
    "TimeSync",
    "WebsocketsMessageSource",
    "parse_market_message",
    "parse_server_time",
    "parse_symbol_rules",
]
