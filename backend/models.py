"""
PolySleuth - 数据模型定义

Pydantic 模型用于 API 请求/响应
SQLAlchemy 模型用于数据库持久化
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from enum import Enum

from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, Index, create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from .config import DATABASE_URL

# SQLAlchemy 设置
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ============================================================================
# 枚举类型
# ============================================================================

class WashType(str, Enum):
    NONE = "NONE"
    SELF_TRADE = "SELF_TRADE"
    CIRCULAR = "CIRCULAR"
    ATOMIC = "ATOMIC"


class AlertSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ============================================================================
# SQLAlchemy ORM 模型 (数据库)
# ============================================================================

class TradeDB(Base):
    """交易记录表"""
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    tx_hash = Column(String(66), nullable=False, index=True)
    log_index = Column(Integer, nullable=False)
    block_number = Column(Integer, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    contract = Column(String(42), nullable=False)
    
    order_hash = Column(String(66))
    maker = Column(String(42), nullable=False, index=True)
    taker = Column(String(42), nullable=False, index=True)
    token_id = Column(String(80), nullable=False, index=True)
    side = Column(String(4))  # BUY/SELL
    price = Column(Float)
    size = Column(Float)
    volume = Column(Float)
    fee = Column(Integer)
    
    # 刷量检测结果
    is_wash = Column(Boolean, default=False, index=True)
    wash_type = Column(String(20), default="NONE")
    wash_confidence = Column(Float, default=0.0)
    
    # 复合唯一索引
    __table_args__ = (
        Index('ix_trade_unique', 'tx_hash', 'log_index', unique=True),
    )


class AlertDB(Base):
    """警报记录表"""
    __tablename__ = "alerts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(String(64), unique=True, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    alert_type = Column(String(20), nullable=False, index=True)
    severity = Column(String(10), nullable=False)
    
    tx_hash = Column(String(66), index=True)
    token_id = Column(String(80))
    trade_count = Column(Integer, default=1)
    volume = Column(Float, default=0.0)
    confidence = Column(Float, default=0.0)
    
    addresses = Column(Text)  # JSON 序列化的地址列表
    details = Column(Text)    # JSON 序列化的详情
    
    is_read = Column(Boolean, default=False)


class MarketCacheDB(Base):
    """市场信息缓存表"""
    __tablename__ = "market_cache"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    token_id = Column(String(80), unique=True, nullable=False, index=True)
    question = Column(Text)
    slug = Column(String(200))
    outcome = Column(String(10))
    condition_id = Column(String(66))
    market_id = Column(String(80))
    updated_at = Column(DateTime, default=datetime.utcnow)


# ============================================================================
# Pydantic 模型 (API)
# ============================================================================

class TradeBase(BaseModel):
    """交易基础模型"""
    tx_hash: str
    log_index: int
    block_number: int
    timestamp: datetime
    contract: str
    order_hash: Optional[str] = None
    maker: str
    taker: str
    token_id: str
    side: str
    price: float
    size: float
    volume: float
    fee: int = 0
    
    is_wash: bool = False
    wash_type: str = "NONE"
    wash_confidence: float = 0.0


class TradeResponse(TradeBase):
    """交易响应模型"""
    id: Optional[int] = None
    market_name: Optional[str] = None
    market_slug: Optional[str] = None
    polymarket_url: Optional[str] = None
    polyscan_url: Optional[str] = None
    
    class Config:
        from_attributes = True


class AlertBase(BaseModel):
    """警报基础模型"""
    alert_id: str
    timestamp: datetime
    alert_type: str
    severity: str
    tx_hash: Optional[str] = None
    token_id: Optional[str] = None
    trade_count: int = 1
    volume: float = 0.0
    confidence: float = 0.0
    addresses: List[str] = []


class AlertResponse(AlertBase):
    """警报响应模型"""
    id: Optional[int] = None
    market_name: Optional[str] = None
    acknowledged: bool = False
    
    class Config:
        from_attributes = True


class MarketSummary(BaseModel):
    """市场汇总模型"""
    token_id: str
    question: str
    slug: Optional[str] = None
    polymarket_url: Optional[str] = None
    total_trades: int
    wash_trades: int
    total_volume: float
    wash_volume: float
    wash_ratio: float
    unique_traders: int


class MarketHealth(BaseModel):
    """市场健康度模型"""
    token_id: str
    market_name: str
    overall_score: float
    health_grade: str
    wash_score: float
    diversity_score: float
    stability_score: float
    wash_ratio: float
    unique_traders: int
    total_trades: int


class SystemStats(BaseModel):
    """系统统计模型"""
    total_trades: int = 0
    total_alerts: int = 0
    wash_trade_count: int = 0
    total_volume: float = 0.0
    wash_volume: float = 0.0
    unique_markets: int = 0
    unique_traders: int = 0
    is_streaming: bool = False


class StreamConfig(BaseModel):
    """流式监控配置"""
    poll_interval: float = 15.0
    blocks_per_poll: int = 20
    num_blocks: int = 100


# ============================================================================
# WebSocket 消息模型
# ============================================================================

class WSMessage(BaseModel):
    """WebSocket 消息"""
    type: str  # "trade", "alert", "stats", "connected"
    data: Any


class WSTradeMessage(BaseModel):
    """新交易消息"""
    type: str = "trade"
    data: TradeResponse


class WSAlertMessage(BaseModel):
    """新警报消息"""
    type: str = "alert"
    data: AlertResponse


class WSStatsMessage(BaseModel):
    """统计更新消息"""
    type: str = "stats"
    data: SystemStats


# ============================================================================
# 初始化数据库
# ============================================================================

def init_db():
    """创建所有表"""
    Base.metadata.create_all(bind=engine)

    # 轻量迁移：补充 market_id 列
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(market_cache)"))
        cols = {row[1] for row in result.fetchall()}
        if "market_id" not in cols:
            conn.execute(text("ALTER TABLE market_cache ADD COLUMN market_id VARCHAR(80)"))
            conn.commit()


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
