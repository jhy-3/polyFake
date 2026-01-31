"""
PolySleuth - REST API 路由

警报相关接口
"""
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Query

from ..models import AlertResponse
from ..services.storage import get_data_store

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.get("", response_model=List[AlertResponse])
async def get_alerts(
    limit: int = Query(100, ge=1, le=1000, description="返回数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    severity: Optional[str] = Query(None, description="严重程度: HIGH, MEDIUM, LOW"),
    alert_type: Optional[str] = Query(None, description="警报类型: SELF_TRADE, CIRCULAR_TRADE"),
    token_id: Optional[str] = Query(None, description="市场筛选"),
    hours: int = Query(24, ge=1, le=168, description="时间范围"),
):
    """
    获取警报列表
    
    支持按严重程度、类型、市场筛选
    """
    store = get_data_store()
    start_time = datetime.now() - timedelta(hours=hours)
    
    alerts = store.get_alerts(
        limit=limit,
        offset=offset,
        severity=severity,
        alert_type=alert_type,
        start_time=start_time,
    )
    
    # 如果有 token_id 筛选
    if token_id:
        alerts = [a for a in alerts if a.token_id == token_id]
    
    return [AlertResponse(
        alert_id=a.alert_id,
        timestamp=a.timestamp,
        alert_type=a.alert_type,
        severity=a.severity,
        tx_hash=a.tx_hash,
        token_id=a.token_id,
        market_name=store.get_market_name(a.token_id),
        trade_count=a.trade_count,
        volume=a.volume,
        confidence=a.confidence,
        addresses=a.addresses,
        acknowledged=a.acknowledged,
    ) for a in alerts]


@router.get("/stats")
async def get_alert_stats(
    hours: int = Query(24, ge=1, le=168, description="时间范围"),
):
    """获取警报统计"""
    store = get_data_store()
    start_time = datetime.now() - timedelta(hours=hours)
    
    alerts = store.get_alerts(limit=10000, start_time=start_time)
    
    # 按类型统计
    by_type = {}
    by_severity = {}
    total_volume = 0
    
    for alert in alerts:
        # 按类型
        if alert.alert_type not in by_type:
            by_type[alert.alert_type] = {'count': 0, 'volume': 0}
        by_type[alert.alert_type]['count'] += 1
        by_type[alert.alert_type]['volume'] += alert.volume
        
        # 按严重程度
        if alert.severity not in by_severity:
            by_severity[alert.severity] = {'count': 0, 'volume': 0}
        by_severity[alert.severity]['count'] += 1
        by_severity[alert.severity]['volume'] += alert.volume
        
        total_volume += alert.volume
    
    # 按时间聚合（每小时）
    hourly = {}
    for alert in alerts:
        hour_key = alert.timestamp.strftime('%Y-%m-%d %H:00')
        if hour_key not in hourly:
            hourly[hour_key] = {'timestamp': hour_key, 'count': 0}
        hourly[hour_key]['count'] += 1
    
    return {
        'total_alerts': len(alerts),
        'total_volume': round(total_volume, 2),
        'by_type': by_type,
        'by_severity': by_severity,
        'hourly_timeline': sorted(hourly.values(), key=lambda x: x['timestamp']),
    }


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    """确认警报（标记已处理）"""
    store = get_data_store()
    
    success = store.acknowledge_alert(alert_id)
    
    return {
        'success': success,
        'alert_id': alert_id,
    }


@router.get("/recent")
async def get_recent_alerts(
    limit: int = Query(10, ge=1, le=50, description="返回数量"),
):
    """获取最近的警报（用于仪表盘）"""
    store = get_data_store()
    
    alerts = store.get_alerts(limit=limit)
    
    return [AlertResponse(
        alert_id=a.alert_id,
        timestamp=a.timestamp,
        alert_type=a.alert_type,
        severity=a.severity,
        tx_hash=a.tx_hash,
        token_id=a.token_id,
        market_name=store.get_market_name(a.token_id),
        trade_count=a.trade_count,
        volume=a.volume,
        confidence=a.confidence,
        addresses=a.addresses,
        acknowledged=a.acknowledged,
    ) for a in alerts]
