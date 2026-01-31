"""
PolySleuth - REST API 路由

系统相关接口
"""
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query

from ..models import SystemStats
from ..services.storage import get_data_store
from ..services.forensics import get_forensics_service

router = APIRouter(prefix="/system", tags=["System"])


@router.get("/stats", response_model=SystemStats)
async def get_system_stats():
    """获取系统统计信息"""
    store = get_data_store()
    forensics = get_forensics_service()
    
    stats = store.get_stats()
    stats.is_streaming = forensics.is_streaming()
    
    return stats


@router.get("/health")
async def health_check():
    """健康检查"""
    forensics = get_forensics_service()
    
    return {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'chain_connected': forensics.is_connected(),
        'current_block': forensics.get_current_block() if forensics.is_connected() else 0,
    }


@router.post("/fetch")
async def fetch_trades(
    blocks: int = Query(100, ge=10, le=1000, description="获取区块数"),
):
    """
    手动获取链上交易
    
    从链上获取最近 N 个区块的交易数据
    """
    forensics = get_forensics_service()
    
    if not forensics.is_connected():
        raise HTTPException(status_code=503, detail="链上节点未连接")
    
    count = forensics.fetch_recent_trades(blocks)
    
    store = get_data_store()
    stats = store.get_stats()
    
    return {
        'success': True,
        'fetched_trades': count,
        'current_stats': {
            'total_trades': stats.total_trades,
            'total_alerts': stats.total_alerts,
            'wash_trade_count': stats.wash_trade_count,
        }
    }


@router.post("/stream/start")
async def start_streaming(
    poll_interval: float = Query(5.0, ge=1.0, le=60.0, description="轮询间隔（秒）"),
    blocks_per_poll: int = Query(10, ge=1, le=100, description="每次轮询区块数"),
):
    """启动流式监控"""
    forensics = get_forensics_service()
    
    if not forensics.is_connected():
        raise HTTPException(status_code=503, detail="链上节点未连接")
    
    if forensics.is_streaming():
        return {'status': 'already_streaming'}
    
    forensics.start_streaming(poll_interval, blocks_per_poll)
    
    return {
        'status': 'started',
        'poll_interval': poll_interval,
        'blocks_per_poll': blocks_per_poll,
    }


@router.post("/stream/stop")
async def stop_streaming():
    """停止流式监控"""
    forensics = get_forensics_service()
    
    if not forensics.is_streaming():
        return {'status': 'not_streaming'}
    
    forensics.stop_streaming()
    
    return {'status': 'stopped'}


@router.get("/stream/status")
async def get_stream_status():
    """获取流式监控状态"""
    forensics = get_forensics_service()
    
    return {
        'is_streaming': forensics.is_streaming(),
        'is_connected': forensics.is_connected(),
        'current_block': forensics.get_current_block() if forensics.is_connected() else 0,
    }


@router.post("/clear")
async def clear_data():
    """清空所有数据（谨慎使用）"""
    store = get_data_store()
    
    # 清空内存
    with store._lock:
        store._trades.clear()
        store._alerts.clear()
        store._trade_by_hash.clear()
        store._trades_by_address.clear()
        store._trades_by_token.clear()
    
    return {
        'success': True,
        'message': '内存数据已清空',
    }
