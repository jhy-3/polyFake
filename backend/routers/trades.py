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
from ..services.analyzer import (
    load_trades_df,
    detect_new_wallet_insider,
    get_flagged_traders,
    detect_gas_anomalies,
    run_full_forensic_analysis,
)

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
    interval: int = Query(60, ge=10, le=21600, description="间隔（秒）"),
    token_id: Optional[str] = Query(None, description="市场筛选"),
):
    """获取交易时间线数据（用于图表），包含各类可疑交易统计"""
    store = get_data_store()
    start_time = datetime.now() - timedelta(hours=hours)
    
    trades = store.get_trades(
        limit=100000,
        token_id=token_id,
        start_time=start_time,
    )
    
    # 按时间段聚合（interval 单位为秒）
    buckets = {}
    interval_seconds = interval
    
    for trade in trades:
        ts = trade.timestamp.timestamp()
        bucket_ts = int(ts // interval_seconds) * interval_seconds
        bucket_key = datetime.fromtimestamp(bucket_ts).isoformat()
        
        if bucket_key not in buckets:
            buckets[bucket_key] = {
                'timestamp': bucket_key,
                'total_count': 0,
                'total_volume': 0,
                # 各类可疑交易统计
                'wash_count': 0,
                'wash_volume': 0,
                'self_trade_count': 0,
                'self_trade_volume': 0,
                'circular_count': 0,
                'circular_volume': 0,
                'atomic_count': 0,
                'atomic_volume': 0,
                'sybil_count': 0,
                'sybil_volume': 0,
                'insider_count': 0,
                'insider_volume': 0,
            }
        
        bucket = buckets[bucket_key]
        bucket['total_count'] += 1
        bucket['total_volume'] += trade.volume
        
        # 统计各类可疑交易
        if trade.is_wash:
            bucket['wash_count'] += 1
            bucket['wash_volume'] += trade.volume
            
            # 根据 wash_type 分类统计
            wash_type = trade.wash_type.upper() if trade.wash_type else ''
            if wash_type == 'SELF_TRADE':
                bucket['self_trade_count'] += 1
                bucket['self_trade_volume'] += trade.volume
            elif wash_type == 'CIRCULAR':
                bucket['circular_count'] += 1
                bucket['circular_volume'] += trade.volume
            elif wash_type == 'ATOMIC_WASH':
                bucket['atomic_count'] += 1
                bucket['atomic_volume'] += trade.volume
            elif wash_type == 'SYBIL_CLUSTER':
                bucket['sybil_count'] += 1
                bucket['sybil_volume'] += trade.volume
            elif wash_type == 'NEW_WALLET_INSIDER':
                bucket['insider_count'] += 1
                bucket['insider_volume'] += trade.volume
    
    # 排序返回
    timeline = sorted(buckets.values(), key=lambda x: x['timestamp'])
    return timeline


# ============================================================================
# 高级取证分析 API
# ============================================================================

@router.get("/analysis/insider")
async def get_insider_trades(
    threshold_multiplier: float = Query(5.0, description="交易规模阈值倍数"),
    account_age_hours: int = Query(24, description="新钱包账龄阈值(小时)"),
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    新钱包内幕交易检测
    
    识别账龄 < 24h 且交易规模 > 市场均值 5 倍的交易
    """
    trades_df = load_trades_df(limit=limit)
    
    if trades_df.empty:
        return {"flagged": [], "count": 0}
    
    flagged = detect_new_wallet_insider(
        trades_df,
        threshold_multiplier=threshold_multiplier,
        account_age_hours=account_age_hours
    )
    
    return {
        "flagged": [
            {
                "tx_hash": f.tx_hash,
                "wallet_address": f.wallet_address,
                "flag_type": f.flag_type,
                "confidence": f.confidence,
                "details": f.details,
            }
            for f in flagged
        ],
        "count": len(flagged),
    }


@router.get("/analysis/high-winrate")
async def get_high_winrate_traders(
    win_rate_threshold: float = Query(0.9, description="胜率阈值 (0-1)"),
    min_trades: int = Query(10, description="最小交易数"),
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    高胜率交易者检测
    
    识别胜率 > 90% 且交易数 > 10 的钱包
    """
    trades_df = load_trades_df(limit=limit)
    
    if trades_df.empty:
        return {"flagged": [], "count": 0}
    
    flagged = get_flagged_traders(
        trades_df,
        win_rate_threshold=win_rate_threshold,
        min_trades=min_trades
    )
    
    return {
        "flagged": [
            {
                "tx_hash": f.tx_hash,
                "wallet_address": f.wallet_address,
                "flag_type": f.flag_type,
                "confidence": f.confidence,
                "details": f.details,
            }
            for f in flagged
        ],
        "count": len(flagged),
    }


@router.get("/analysis/gas-anomaly")
async def get_gas_anomalies(
    gas_multiplier: float = Query(2.0, description="Gas 阈值倍数"),
    block_window: int = Query(10, description="区块窗口大小"),
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    Gas 异常（抢跑）检测
    
    识别 Gas 价格 > 窗口中位数 * 2 的交易
    """
    trades_df = load_trades_df(limit=limit)
    
    if trades_df.empty:
        return {"flagged": [], "count": 0}
    
    flagged = detect_gas_anomalies(
        trades_df,
        gas_multiplier=gas_multiplier,
        block_window=block_window
    )
    
    return {
        "flagged": [
            {
                "tx_hash": f.tx_hash,
                "wallet_address": f.wallet_address,
                "flag_type": f.flag_type,
                "confidence": f.confidence,
                "details": f.details,
            }
            for f in flagged
        ],
        "count": len(flagged),
    }


@router.get("/analysis/full")
async def run_full_analysis(
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    运行完整取证分析
    
    包含：新钱包内幕、高胜率、Gas 异常三种检测
    """
    results = run_full_forensic_analysis(limit=limit)
    
    return {
        "new_wallet_insider": {
            "count": len(results['new_wallet_insider']),
            "flagged": [
                {
                    "tx_hash": f.tx_hash,
                    "wallet_address": f.wallet_address,
                    "confidence": f.confidence,
                }
                for f in results['new_wallet_insider'][:100]  # 限制返回数量
            ],
        },
        "high_win_rate": {
            "count": len(results['high_win_rate']),
            "flagged": [
                {
                    "tx_hash": f.tx_hash,
                    "wallet_address": f.wallet_address,
                    "confidence": f.confidence,
                }
                for f in results['high_win_rate'][:100]
            ],
        },
        "gas_anomaly": {
            "count": len(results['gas_anomaly']),
            "flagged": [
                {
                    "tx_hash": f.tx_hash,
                    "wallet_address": f.wallet_address,
                    "confidence": f.confidence,
                }
                for f in results['gas_anomaly'][:100]
            ],
        },
    }


@router.get("/analysis/flagged-tx")
async def get_flagged_tx_hashes(
    analysis_type: str = Query(..., description="分析类型: insider, high_winrate, gas_anomaly, all"),
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    获取被标记的交易哈希列表
    
    用于前端筛选显示特定类型的可疑交易
    """
    trades_df = load_trades_df(limit=limit)
    
    if trades_df.empty:
        return {"tx_hashes": [], "wallet_addresses": []}
    
    tx_hashes = set()
    wallet_addresses = set()
    
    if analysis_type in ('insider', 'all'):
        flagged = detect_new_wallet_insider(trades_df)
        for f in flagged:
            tx_hashes.add(f.tx_hash)
            wallet_addresses.add(f.wallet_address)
    
    if analysis_type in ('high_winrate', 'all'):
        flagged = get_flagged_traders(trades_df)
        for f in flagged:
            tx_hashes.add(f.tx_hash)
            wallet_addresses.add(f.wallet_address)
    
    if analysis_type in ('gas_anomaly', 'all'):
        flagged = detect_gas_anomalies(trades_df)
        for f in flagged:
            tx_hashes.add(f.tx_hash)
            wallet_addresses.add(f.wallet_address)
    
    return {
        "tx_hashes": list(tx_hashes),
        "wallet_addresses": list(wallet_addresses),
        "count": len(tx_hashes),
    }


# ============================================================================
# 高级取证分析 API (图算法、模式匹配)
# ============================================================================

@router.get("/analysis/advanced/self-trades")
async def detect_self_trading(
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    自交易(刷量)检测
    
    检测 maker == taker 的直接自交易，以及特征相似的协调自交易
    """
    from ..services.advanced_forensics import detect_self_trades
    
    trades_df = load_trades_df(limit=limit)
    
    if trades_df.empty:
        return {"evidence": [], "count": 0}
    
    evidence = detect_self_trades(trades_df)
    
    return {
        "evidence": [
            {
                "type": e.evidence_type,
                "tx_hash": e.tx_hash,
                "addresses": e.addresses,
                "confidence": e.confidence,
                "volume": e.volume,
                "details": e.details,
            }
            for e in evidence
        ],
        "count": len(evidence),
        "total_volume": sum(e.volume for e in evidence),
    }


@router.get("/analysis/advanced/circular-trades")
async def detect_circular_trading(
    window_minutes: int = Query(60, description="时间窗口(分钟)"),
    min_volume: float = Query(100.0, description="最小循环交易量"),
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    循环交易检测 (图算法)
    
    使用 NetworkX 构建资金流向图，检测 A->B->A 或 A->B->C->A 等循环路径
    """
    from ..services.advanced_forensics import detect_circular_trades, circular_paths_to_evidence
    
    trades_df = load_trades_df(limit=limit)
    
    if trades_df.empty:
        return {"paths": [], "evidence": [], "count": 0}
    
    paths = detect_circular_trades(
        trades_df,
        window_minutes=window_minutes,
        min_cycle_volume=min_volume
    )
    evidence = circular_paths_to_evidence(paths)
    
    return {
        "paths": [
            {
                "path": ' -> '.join(p.path) + ' -> ' + p.path[0],
                "tx_hashes": p.tx_hashes[:10],  # 限制数量
                "total_volume": p.total_volume,
                "time_span_minutes": p.time_span_minutes,
                "confidence": p.confidence,
            }
            for p in paths
        ],
        "evidence": [
            {
                "type": e.evidence_type,
                "tx_hash": e.tx_hash,
                "addresses": e.addresses,
                "confidence": e.confidence,
                "volume": e.volume,
                "details": e.details,
            }
            for e in evidence
        ],
        "count": len(paths),
        "total_volume": sum(p.total_volume for p in paths),
    }


@router.get("/analysis/advanced/atomic-wash")
async def detect_atomic_wash(
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    原子化刷量模式检测 (Split-Trade-Merge)
    
    检测同一区块内同一地址的买卖对冲行为
    """
    from ..services.advanced_forensics import detect_atomic_wash_patterns
    
    trades_df = load_trades_df(limit=limit)
    
    if trades_df.empty:
        return {"evidence": [], "count": 0}
    
    evidence = detect_atomic_wash_patterns(trades_df)
    
    return {
        "evidence": [
            {
                "type": e.evidence_type,
                "tx_hash": e.tx_hash,
                "addresses": e.addresses,
                "confidence": e.confidence,
                "volume": e.volume,
                "details": e.details,
            }
            for e in evidence
        ],
        "count": len(evidence),
        "total_volume": sum(e.volume for e in evidence),
    }


@router.get("/analysis/advanced/volume-spikes")
async def detect_volume_spike(
    threshold: float = Query(10.0, description="异常阈值倍数"),
    bin_minutes: int = Query(5, description="时间分箱大小(分钟)"),
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    交易量异常检测
    
    监控 5 分钟交易量，标记超过 1 小时滚动平均 10 倍的时段
    """
    from ..services.advanced_forensics import detect_volume_spikes, volume_spikes_to_evidence
    
    trades_df = load_trades_df(limit=limit)
    
    if trades_df.empty:
        return {"spikes": [], "evidence": [], "count": 0}
    
    spikes = detect_volume_spikes(
        trades_df,
        threshold=threshold,
        bin_minutes=bin_minutes
    )
    evidence = volume_spikes_to_evidence(spikes)
    
    return {
        "spikes": [
            {
                "market_id": s.market_id,
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                "spike_volume": s.spike_volume,
                "baseline_volume": s.baseline_volume,
                "spike_ratio": s.spike_ratio,
                "trade_count": s.trade_count,
                "is_correlated_with_event": s.is_correlated_with_event,
            }
            for s in spikes
        ],
        "evidence": [
            {
                "type": e.evidence_type,
                "tx_hash": e.tx_hash,
                "confidence": e.confidence,
                "volume": e.volume,
                "details": e.details,
            }
            for e in evidence
        ],
        "count": len(spikes),
        "total_spike_volume": sum(s.spike_volume for s in spikes),
    }


@router.get("/analysis/advanced/sybil-clusters")
async def detect_sybil_cluster(
    time_window_seconds: int = Query(10, description="时间窗口(秒)"),
    min_cluster_size: int = Query(3, description="最小集群大小"),
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    女巫攻击集群检测 (协调投注)
    
    检测在 10 秒内对同一市场同方向投注且交易规模相似的钱包群
    """
    from ..services.advanced_forensics import detect_coordinated_clusters, sybil_clusters_to_evidence
    
    trades_df = load_trades_df(limit=limit)
    
    if trades_df.empty:
        return {"clusters": [], "evidence": [], "count": 0}
    
    clusters = detect_coordinated_clusters(
        trades_df,
        time_window_seconds=time_window_seconds,
        min_cluster_size=min_cluster_size
    )
    evidence = sybil_clusters_to_evidence(clusters)
    
    return {
        "clusters": [
            {
                "cluster_id": c.cluster_id,
                "addresses": c.addresses,
                "market_id": c.market_id,
                "side": c.side,
                "trade_count": c.trade_count,
                "total_volume": c.total_volume,
                "confidence": c.confidence,
            }
            for c in clusters
        ],
        "evidence": [
            {
                "type": e.evidence_type,
                "tx_hash": e.tx_hash,
                "addresses": e.addresses,
                "confidence": e.confidence,
                "volume": e.volume,
                "details": e.details,
            }
            for e in evidence
        ],
        "count": len(clusters),
        "total_addresses": sum(len(c.addresses) for c in clusters),
    }


@router.get("/analysis/advanced/market-health")
async def get_market_health_report(
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    综合市场健康评估
    
    运行全部 8 种检测器，输出市场健康评分 (0-100) 和证据列表
    """
    from ..services.advanced_forensics import MarketForensicsReport
    
    trades_df = load_trades_df(limit=limit)
    
    if trades_df.empty:
        return {
            "health_score": 100,
            "risk_level": "LOW",
            "total_trades": 0,
            "evidence_count": 0,
            "message": "No trades to analyze"
        }
    
    reporter = MarketForensicsReport()
    report = reporter.run_full_analysis(trades_df)
    
    return report


@router.get("/analysis/advanced/flagged-tx")
async def get_advanced_flagged_tx(
    analysis_type: str = Query(..., description="分析类型: self_trade, circular, atomic, volume_spike, sybil, all"),
    limit: int = Query(50000, description="分析的交易数量"),
):
    """
    获取高级分析标记的交易哈希列表
    
    用于前端筛选显示特定类型的可疑交易
    """
    from ..services.advanced_forensics import (
        detect_self_trades,
        detect_circular_trades,
        circular_paths_to_evidence,
        detect_atomic_wash_patterns,
        detect_volume_spikes,
        volume_spikes_to_evidence,
        detect_coordinated_clusters,
        sybil_clusters_to_evidence,
    )
    
    trades_df = load_trades_df(limit=limit)
    
    if trades_df.empty:
        return {"tx_hashes": [], "wallet_addresses": [], "count": 0}
    
    tx_hashes = set()
    wallet_addresses = set()
    
    if analysis_type in ('self_trade', 'all'):
        evidence = detect_self_trades(trades_df)
        for e in evidence:
            if e.tx_hash:
                tx_hashes.add(e.tx_hash)
            wallet_addresses.update(e.addresses)
    
    if analysis_type in ('circular', 'all'):
        paths = detect_circular_trades(trades_df)
        evidence = circular_paths_to_evidence(paths)
        for e in evidence:
            if e.tx_hash:
                tx_hashes.add(e.tx_hash)
            wallet_addresses.update(e.addresses)
    
    if analysis_type in ('atomic', 'all'):
        evidence = detect_atomic_wash_patterns(trades_df)
        for e in evidence:
            if e.tx_hash:
                tx_hashes.add(e.tx_hash)
            wallet_addresses.update(e.addresses)
    
    if analysis_type in ('volume_spike', 'all'):
        spikes = detect_volume_spikes(trades_df)
        evidence = volume_spikes_to_evidence(spikes)
        for e in evidence:
            if e.tx_hash:
                tx_hashes.add(e.tx_hash)
    
    if analysis_type in ('sybil', 'all'):
        clusters = detect_coordinated_clusters(trades_df)
        evidence = sybil_clusters_to_evidence(clusters)
        for e in evidence:
            wallet_addresses.update(e.addresses)
    
    return {
        "tx_hashes": list(tx_hashes),
        "wallet_addresses": list(wallet_addresses),
        "count": len(tx_hashes) + len(wallet_addresses),
    }
