"""
PolySleuth - REST API 路由

交易相关接口
"""
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Query, HTTPException

from ..models import TradeResponse, AlertResponse
from ..services.storage import get_data_store
from ..services.forensics import get_forensics_service

router = APIRouter(prefix="/trades", tags=["Trades"])


@router.get("", response_model=List[TradeResponse])
async def get_trades(
    limit: int = Query(100, ge=1, le=5000, description="返回数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    token_id: Optional[str] = Query(None, description="市场 Token ID"),
    address: Optional[str] = Query(None, description="地址筛选"),
    is_wash: Optional[bool] = Query(None, description="刷量筛选"),
    side: Optional[str] = Query(None, description="买卖方向"),
    start_time: Optional[datetime] = Query(None, description="开始时间"),
    end_time: Optional[datetime] = Query(None, description="结束时间"),
):
    """
    获取交易列表
    
    支持多种筛选条件：
    - token_id: 按市场筛选
    - address: 按 maker 或 taker 地址筛选
    - is_wash: 筛选刷量/非刷量交易
    - side: BUY 或 SELL
    - start_time / end_time: 时间范围
    """
    store = get_data_store()
    
    trades = store.get_trades(
        limit=limit,
        offset=offset,
        token_id=token_id,
        address=address,
        is_wash=is_wash,
        side=side,
        start_time=start_time,
        end_time=end_time,
    )
    
    result = []
    for t in trades:
        market_info = store.get_market_info(t.token_id)
        # 确保交易哈希有0x前缀用于Polygonscan
        tx_hash_with_prefix = t.tx_hash if t.tx_hash.startswith('0x') else f'0x{t.tx_hash}'
        result.append(TradeResponse(
            tx_hash=t.tx_hash,
            log_index=t.log_index,
            block_number=t.block_number,
            timestamp=t.timestamp,
            contract=t.contract,
            order_hash=t.order_hash,
            maker=t.maker,
            taker=t.taker,
            token_id=t.token_id,
            market_name=market_info['name'],
            market_slug=market_info['slug'],
            polymarket_url=market_info['polymarket_url'],
            polyscan_url=f"https://polygonscan.com/tx/{tx_hash_with_prefix}",
            side=t.side,
            price=t.price,
            size=t.size,
            volume=t.volume,
            fee=t.fee,
            is_wash=t.is_wash,
            wash_type=t.wash_type,
            wash_confidence=t.wash_confidence,
        ))
    return result


@router.get("/count")
async def get_trade_count(
    token_id: Optional[str] = Query(None, description="市场 Token ID"),
    is_wash: Optional[bool] = Query(None, description="刷量筛选"),
    hours: int = Query(24, ge=1, le=168, description="时间范围（小时）"),
):
    """获取交易数量统计"""
    store = get_data_store()
    start_time = datetime.now() - timedelta(hours=hours)
    
    trades = store.get_trades(
        limit=100000,
        token_id=token_id,
        is_wash=is_wash,
        start_time=start_time,
    )
    
    total_volume = sum(t.volume for t in trades)
    wash_count = sum(1 for t in trades if t.is_wash)
    wash_volume = sum(t.volume for t in trades if t.is_wash)
    
    return {
        "total_count": len(trades),
        "wash_count": wash_count,
        "total_volume": round(total_volume, 2),
        "wash_volume": round(wash_volume, 2),
        "wash_ratio": round(wash_count / len(trades) * 100, 2) if trades else 0,
    }


@router.get("/by-hash/{tx_hash}")
async def get_trade_by_hash(tx_hash: str):
    """根据交易哈希获取交易"""
    store = get_data_store()
    trade = store.get_trade_by_hash(tx_hash)
    
    if not trade:
        raise HTTPException(status_code=404, detail="交易不存在")
    
    market_info = store.get_market_info(trade.token_id)
    tx_hash_with_prefix = trade.tx_hash if trade.tx_hash.startswith('0x') else f'0x{trade.tx_hash}'
    return TradeResponse(
        tx_hash=trade.tx_hash,
        log_index=trade.log_index,
        block_number=trade.block_number,
        timestamp=trade.timestamp,
        contract=trade.contract,
        order_hash=trade.order_hash,
        maker=trade.maker,
        taker=trade.taker,
        token_id=trade.token_id,
        market_name=market_info['name'],
        market_slug=market_info['slug'],
        polymarket_url=market_info['polymarket_url'],
        polyscan_url=f"https://polygonscan.com/tx/{tx_hash_with_prefix}",
        side=trade.side,
        price=trade.price,
        size=trade.size,
        volume=trade.volume,
        fee=trade.fee,
        is_wash=trade.is_wash,
        wash_type=trade.wash_type,
        wash_confidence=trade.wash_confidence,
    )


@router.get("/by-address/{address}")
async def get_trades_by_address(
    address: str,
    limit: int = Query(100, ge=1, le=1000),
    include_wash: bool = Query(True, description="包含刷量交易"),
):
    """获取指定地址的所有交易"""
    store = get_data_store()
    
    is_wash = None if include_wash else False
    trades = store.get_trades(limit=limit, address=address, is_wash=is_wash)
    
    # 统计
    total_volume = sum(t.volume for t in trades)
    buy_count = sum(1 for t in trades if t.side == "BUY")
    sell_count = sum(1 for t in trades if t.side == "SELL")
    wash_count = sum(1 for t in trades if t.is_wash)
    
    # 构建响应列表
    trade_responses = []
    for t in trades:
        market_info = store.get_market_info(t.token_id)
        tx_hash_with_prefix = t.tx_hash if t.tx_hash.startswith('0x') else f'0x{t.tx_hash}'
        trade_responses.append(TradeResponse(
            tx_hash=t.tx_hash,
            log_index=t.log_index,
            block_number=t.block_number,
            timestamp=t.timestamp,
            contract=t.contract,
            order_hash=t.order_hash,
            maker=t.maker,
            taker=t.taker,
            token_id=t.token_id,
            market_name=market_info['name'],
            market_slug=market_info['slug'],
            polymarket_url=market_info['polymarket_url'],
            polyscan_url=f"https://polygonscan.com/tx/{tx_hash_with_prefix}",
            side=t.side,
            price=t.price,
            size=t.size,
            volume=t.volume,
            fee=t.fee,
            is_wash=t.is_wash,
            wash_type=t.wash_type,
            wash_confidence=t.wash_confidence,
        ))
    
    return {
        "address": address,
        "trades": trade_responses,
        "stats": {
            "total_trades": len(trades),
            "total_volume": round(total_volume, 2),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "wash_count": wash_count,
            "wash_ratio": round(wash_count / len(trades) * 100, 2) if trades else 0,
        }
    }


@router.get("/timeline")
async def get_trade_timeline(
    hours: int = Query(24, ge=1, le=168, description="时间范围"),
    interval: int = Query(1, ge=1, le=360, description="间隔（分钟）"),
    token_id: Optional[str] = Query(None, description="市场筛选"),
):
    """获取交易时间线数据（用于图表）"""
    store = get_data_store()
    start_time = datetime.now() - timedelta(hours=hours)
    
    trades = store.get_trades(
        limit=100000,
        token_id=token_id,
        start_time=start_time,
    )
    
    # 按时间段聚合
    buckets = {}
    interval_seconds = interval * 60
    
    for trade in trades:
        ts = trade.timestamp.timestamp()
        bucket_ts = int(ts // interval_seconds) * interval_seconds
        bucket_key = datetime.fromtimestamp(bucket_ts).isoformat()
        
        if bucket_key not in buckets:
            buckets[bucket_key] = {
                'timestamp': bucket_key,
                'total_count': 0,
                'wash_count': 0,
                'total_volume': 0,
                'wash_volume': 0,
            }
        
        buckets[bucket_key]['total_count'] += 1
        buckets[bucket_key]['total_volume'] += trade.volume
        
        if trade.is_wash:
            buckets[bucket_key]['wash_count'] += 1
            buckets[bucket_key]['wash_volume'] += trade.volume
    
    # 排序返回
    timeline = sorted(buckets.values(), key=lambda x: x['timestamp'])
    return timeline
