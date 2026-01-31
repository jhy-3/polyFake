"""
PolySleuth - REST API 路由

市场相关接口
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Query, HTTPException

from ..models import MarketSummary, MarketHealth
from ..services.storage import get_data_store
from ..services.forensics import get_forensics_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/markets", tags=["Markets"])


@router.get("", response_model=List[MarketSummary])
async def get_markets(
    limit: int = Query(50, ge=1, le=500, description="返回数量"),
    sort_by: str = Query("volume", description="排序字段: volume, wash_ratio, trade_count"),
    hours: int = Query(24, ge=1, le=168, description="时间范围（小时）"),
):
    """
    获取市场摘要列表
    
    返回每个市场的交易统计、刷量比例等
    """
    logger.info(f"[get_markets] 开始处理请求: limit={limit}, sort_by={sort_by}, hours={hours}")
    
    store = get_data_store()
    start_time = datetime.now() - timedelta(hours=hours)
    
    # 获取所有交易
    logger.info("[get_markets] 获取交易数据...")
    trades = store.get_trades(limit=100000, start_time=start_time)
    logger.info(f"[get_markets] 获取到 {len(trades)} 笔交易")
    
    if not trades:
        logger.info("[get_markets] 无交易数据，返回空列表")
        return []
    
    # 批量获取所有 token_id 对应的 slug（避免重复锁获取）
    logger.info("[get_markets] 预先获取市场缓存...")
    unique_token_ids = set(t.token_id for t in trades)
    
    # 一次性获取所有市场信息（仅使用缓存，避免请求超时）
    token_slug_map = {}
    event_info_cache = {}
    market_info_cache = {}  # token_id -> market_info

    def _is_valid_question(text: str) -> bool:
        if not text:
            return False
        return not text.lower().startswith('token ')

    def _is_valid_polymarket_url(url: str) -> bool:
        if not url:
            return False
        if "polymarket.com/event/" not in url:
            return False
        return True

    def _normalize_slug(slug: str, market_id: str) -> str:
        if slug and market_id and slug.endswith(f"-{market_id}"):
            return slug[:-(len(market_id) + 1)]
        return slug
    
    for token_id in unique_token_ids:
        slug = store.get_slug_by_token_id(token_id)
        token_slug_map[token_id] = slug

        if slug and slug not in event_info_cache:
            event_info = store.get_event_by_slug(slug)
            event_info_cache[slug] = event_info

        # 获取市场信息（不进行网络拉取）
        market_info = store.get_market_info(token_id, fetch_if_missing=False)
        market_info_cache[token_id] = market_info

        # 如果通过市场信息拿到了 slug，则补充映射
        if not token_slug_map[token_id] and market_info.get('slug'):
            token_slug_map[token_id] = market_info.get('slug')
    
    logger.info(f"[get_markets] 缓存 {len(token_slug_map)} 个token映射, {len(event_info_cache)} 个事件")
    
    # 按事件（slug）聚合
    event_stats = {}
    
    logger.info("[get_markets] 开始聚合市场数据...")
    
    for trade in trades:
        # 使用预缓存的 slug
        slug = token_slug_map.get(trade.token_id)
        
        # 如果没有 slug，则使用 token_id 作为 fallback
        if not slug:
            slug = f"token_{trade.token_id}"
        
        if slug not in event_stats:
            # 使用预缓存的事件信息
            event_info = event_info_cache.get(slug)
            market_info = market_info_cache.get(trade.token_id, {})

            if event_info and _is_valid_question(event_info.get('question', '')):
                question = event_info.get('question')
                market_id = event_info.get('market_id', '')
                normalized_slug = _normalize_slug(slug, market_id)
                polymarket_url = (
                    f"https://polymarket.com/event/{normalized_slug}"
                    if normalized_slug else None
                )
                resolved_slug = normalized_slug
            elif _is_valid_question(market_info.get('question', '')):
                question = market_info.get('question')
                market_id = market_info.get('market_id', '')
                normalized_slug = _normalize_slug(market_info.get('slug'), market_id)
                polymarket_url = (
                    f"https://polymarket.com/event/{normalized_slug}"
                    if normalized_slug
                    else market_info.get('polymarket_url')
                )
                resolved_slug = normalized_slug
            else:
                # 无有效名称则跳过该市场
                continue

            if not _is_valid_polymarket_url(polymarket_url):
                # 缺少有效事件ID链接则跳过
                continue
            
            event_stats[slug] = {
                'question': question,
                'slug': resolved_slug if (resolved_slug and not resolved_slug.startswith('token_')) else None,
                'polymarket_url': polymarket_url,
                'total_trades': 0,
                'wash_trades': 0,
                'total_volume': 0,
                'wash_volume': 0,
                'unique_makers': set(),
                'unique_takers': set(),
                # 跟踪每个 token_id 的交易量，用于选择代表性 token_id
                'token_volumes': {},
            }
        
        event_stats[slug]['total_trades'] += 1
        event_stats[slug]['total_volume'] += trade.volume
        event_stats[slug]['unique_makers'].add(trade.maker.lower())
        event_stats[slug]['unique_takers'].add(trade.taker.lower())
        
        # 记录每个 token 的交易量
        token_id = trade.token_id
        if token_id not in event_stats[slug]['token_volumes']:
            event_stats[slug]['token_volumes'][token_id] = 0
        event_stats[slug]['token_volumes'][token_id] += trade.volume
        
        if trade.is_wash:
            event_stats[slug]['wash_trades'] += 1
            event_stats[slug]['wash_volume'] += trade.volume
    
    logger.info(f"[get_markets] 聚合完成，共 {len(event_stats)} 个市场")
    
    # 转换为列表
    markets = []
    for slug, stats in event_stats.items():
        wash_ratio = (
            stats['wash_trades'] / stats['total_trades'] * 100
            if stats['total_trades'] > 0 else 0
        )
        
        # 选择交易量最大的 token_id 作为代表
        if stats['token_volumes']:
            primary_token_id = max(stats['token_volumes'].items(), key=lambda x: x[1])[0]
        else:
            primary_token_id = slug.replace('token_', '') if slug.startswith('token_') else '0'
        
        markets.append(MarketSummary(
            token_id=primary_token_id,
            question=stats['question'],
            slug=stats.get('slug'),
            polymarket_url=stats.get('polymarket_url'),
            total_trades=stats['total_trades'],
            wash_trades=stats['wash_trades'],
            total_volume=round(stats['total_volume'], 2),
            wash_volume=round(stats['wash_volume'], 2),
            wash_ratio=round(wash_ratio, 2),
            unique_traders=len(stats['unique_makers'] | stats['unique_takers']),
        ))
    
    # 排序
    if sort_by == 'volume':
        markets.sort(key=lambda x: x.total_volume, reverse=True)
    elif sort_by == 'wash_ratio':
        markets.sort(key=lambda x: x.wash_ratio, reverse=True)
    elif sort_by == 'trade_count':
        markets.sort(key=lambda x: x.total_trades, reverse=True)
    
    logger.info(f"[get_markets] 返回 {min(limit, len(markets))} 个市场")
    return markets[:limit]


@router.get("/hot", response_model=List[MarketSummary])
async def get_hot_markets(
    limit: int = Query(10, ge=1, le=50, description="返回数量"),
    hours: int = Query(24, ge=1, le=168, description="时间范围"),
):
    """获取热门市场（按交易量排序）"""
    return await get_markets(limit=limit, sort_by="volume", hours=hours)


@router.get("/suspicious", response_model=List[MarketSummary])
async def get_suspicious_markets(
    limit: int = Query(10, ge=1, le=50, description="返回数量"),
    min_wash_ratio: float = Query(10.0, ge=0, le=100, description="最低刷量比例"),
    min_trades: int = Query(10, ge=1, description="最少交易数"),
    hours: int = Query(24, ge=1, le=168, description="时间范围"),
):
    """获取可疑市场（高刷量比例）"""
    markets = await get_markets(limit=200, sort_by="wash_ratio", hours=hours)
    
    suspicious = [
        m for m in markets
        if m.wash_ratio >= min_wash_ratio and m.total_trades >= min_trades
    ]
    
    return suspicious[:limit]


@router.get("/{token_id}")
async def get_market_detail(
    token_id: str,
    hours: int = Query(24, ge=1, le=168, description="时间范围"),
):
    """获取单个市场详情"""
    store = get_data_store()
    start_time = datetime.now() - timedelta(hours=hours)
    
    trades = store.get_trades(limit=10000, token_id=token_id, start_time=start_time)
    
    if not trades:
        raise HTTPException(status_code=404, detail="市场不存在或无交易")
    
    market_name = store.get_market_name(token_id)
    
    # 统计
    total_volume = sum(t.volume for t in trades)
    wash_trades = [t for t in trades if t.is_wash]
    wash_volume = sum(t.volume for t in wash_trades)
    
    # 地址统计
    makers = set(t.maker.lower() for t in trades)
    takers = set(t.taker.lower() for t in trades)
    all_traders = makers | takers
    
    # 按时间聚合
    hourly_stats = {}
    for trade in trades:
        hour_key = trade.timestamp.strftime('%Y-%m-%d %H:00')
        if hour_key not in hourly_stats:
            hourly_stats[hour_key] = {
                'timestamp': hour_key,
                'trades': 0,
                'volume': 0,
                'wash_trades': 0,
                'wash_volume': 0,
            }
        hourly_stats[hour_key]['trades'] += 1
        hourly_stats[hour_key]['volume'] += trade.volume
        if trade.is_wash:
            hourly_stats[hour_key]['wash_trades'] += 1
            hourly_stats[hour_key]['wash_volume'] += trade.volume
    
    # 顶级交易者
    trader_volumes = {}
    for trade in trades:
        for addr in [trade.maker, trade.taker]:
            if addr.lower() not in trader_volumes:
                trader_volumes[addr.lower()] = {'address': addr, 'volume': 0, 'count': 0}
            trader_volumes[addr.lower()]['volume'] += trade.volume / 2  # 平分
            trader_volumes[addr.lower()]['count'] += 1
    
    top_traders = sorted(
        trader_volumes.values(),
        key=lambda x: x['volume'],
        reverse=True
    )[:20]
    
    return {
        'token_id': token_id,
        'market_name': market_name,
        'summary': {
            'total_trades': len(trades),
            'wash_trades': len(wash_trades),
            'total_volume': round(total_volume, 2),
            'wash_volume': round(wash_volume, 2),
            'wash_ratio': round(len(wash_trades) / len(trades) * 100, 2) if trades else 0,
            'unique_makers': len(makers),
            'unique_takers': len(takers),
            'unique_traders': len(all_traders),
        },
        'hourly_timeline': sorted(hourly_stats.values(), key=lambda x: x['timestamp']),
        'top_traders': top_traders,
    }


@router.get("/{token_id}/health", response_model=MarketHealth)
async def get_market_health(
    token_id: str,
    hours: int = Query(24, ge=1, le=168, description="时间范围"),
):
    """
    获取市场健康度评分
    
    评分维度：
    - 刷量比例（越低越好）
    - 交易者多样性（越高越好）
    - 价格稳定性（越高越好）
    """
    store = get_data_store()
    start_time = datetime.now() - timedelta(hours=hours)
    
    trades = store.get_trades(limit=10000, token_id=token_id, start_time=start_time)
    
    if not trades:
        raise HTTPException(status_code=404, detail="市场不存在或无交易")
    
    # 刷量评分 (0-100, 越高越健康)
    wash_ratio = sum(1 for t in trades if t.is_wash) / len(trades) if trades else 0
    wash_score = max(0, 100 - wash_ratio * 100)
    
    # 交易者多样性评分
    makers = set(t.maker.lower() for t in trades)
    takers = set(t.taker.lower() for t in trades)
    all_traders = makers | takers
    diversity_score = min(100, len(all_traders) * 2)  # 50个交易者满分
    
    # 价格稳定性评分
    prices = [t.price for t in trades if t.price > 0]
    if len(prices) > 1:
        avg_price = sum(prices) / len(prices)
        variance = sum((p - avg_price) ** 2 for p in prices) / len(prices)
        stability_score = max(0, 100 - variance * 1000)
    else:
        stability_score = 50  # 默认中等
    
    # 综合评分
    overall = (wash_score * 0.5 + diversity_score * 0.3 + stability_score * 0.2)
    
    # 健康等级
    if overall >= 80:
        health_grade = "A"
    elif overall >= 60:
        health_grade = "B"
    elif overall >= 40:
        health_grade = "C"
    elif overall >= 20:
        health_grade = "D"
    else:
        health_grade = "F"
    
    return MarketHealth(
        token_id=token_id,
        market_name=store.get_market_name(token_id),
        overall_score=round(overall, 1),
        health_grade=health_grade,
        wash_score=round(wash_score, 1),
        diversity_score=round(diversity_score, 1),
        stability_score=round(stability_score, 1),
        wash_ratio=round(wash_ratio * 100, 2),
        unique_traders=len(all_traders),
        total_trades=len(trades),
    )
