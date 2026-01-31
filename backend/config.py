"""
PolySleuth Backend Configuration
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 基础路径
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# 数据库
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/polysleuth.db")

# Polygon RPC
POLYGON_RPC_URL = os.getenv(
    "POLYGON_RPC_URL",
    "https://polygon-mainnet.core.chainstack.com/426e31068913765410aa4a1e4e5686e6"
)

# Polymarket 合约地址
CTF_EXCHANGE = os.getenv("CTF_EXCHANGE_ADDRESS", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
NEG_RISK_EXCHANGE = os.getenv("NEG_RISK_EXCHANGE_ADDRESS", "0xC5d563A36AE78145C45a50134d48A1215220f80a")
CONDITIONAL_TOKENS = os.getenv("CONDITIONAL_TOKENS_ADDRESS", "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")

# Gamma API
GAMMA_API_URL = os.getenv("GAMMA_API_URL", "https://gamma-api.polymarket.com")

# API 配置
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 流式监控配置
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "15.0"))
BLOCKS_PER_POLL = int(os.getenv("BLOCKS_PER_POLL", "20"))

# 内存缓存配置
MAX_TRADES_IN_MEMORY = int(os.getenv("MAX_TRADES_IN_MEMORY", "50000"))
MAX_ALERTS_IN_MEMORY = int(os.getenv("MAX_ALERTS_IN_MEMORY", "1000"))
