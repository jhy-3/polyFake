"""
PolySleuth - Polymarket 真实链上数据取证工具

使用 Polygon 链上真实数据进行刷量检测和市场健康度分析
所有数据均来自真实链上交易，无模拟数据
"""

from polysleuth.real_forensics import (
    OnChainForensics,
    StreamingMonitor,
    RealTrade,
    TransactionBundle,
    MarketHealth,
    get_forensics,
    get_monitor,
    POLYGON_RPC_URL,
    CTF_EXCHANGE,
    NEG_RISK_EXCHANGE,
    CONDITIONAL_TOKENS,
)

from polysleuth.data_fetcher import (
    PolymarketDataFetcher,
)

__version__ = "0.2.0"
__all__ = [
    # 核心取证
    "OnChainForensics",
    "StreamingMonitor",
    "RealTrade",
    "TransactionBundle",
    "MarketHealth",
    "get_forensics",
    "get_monitor",
    # 常量
    "POLYGON_RPC_URL",
    "CTF_EXCHANGE",
    "NEG_RISK_EXCHANGE",
    "CONDITIONAL_TOKENS",
    # API 数据获取
    "PolymarketDataFetcher",
]
