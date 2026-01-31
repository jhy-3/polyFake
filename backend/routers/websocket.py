"""
PolySleuth - WebSocket 路由

实时数据推送
"""
import asyncio
import threading
import json
from datetime import datetime
from typing import Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import logging

from ..services.storage import get_data_store
from ..services.forensics import get_forensics_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])


class ConnectionManager:
    """WebSocket 连接管理器"""
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
    
    async def connect(self, websocket: WebSocket):
        """接受新连接"""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"📱 新 WebSocket 连接，当前连接数: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        """断开连接"""
        self.active_connections.discard(websocket)
        logger.info(f"📴 WebSocket 断开，当前连接数: {len(self.active_connections)}")
    
    async def broadcast(self, message: dict):
        """广播消息到所有连接"""
        if not self.active_connections:
            return
        
        message_json = json.dumps(message, default=str)
        
        dead_connections = set()
        for connection in self.active_connections:
            try:
                await connection.send_text(message_json)
            except Exception:
                dead_connections.add(connection)
        
        # 清理死连接
        for conn in dead_connections:
            self.active_connections.discard(conn)


# 全局连接管理器
manager = ConnectionManager()

_broadcast_loop: asyncio.AbstractEventLoop | None = None
_broadcast_thread: threading.Thread | None = None


def _ensure_broadcast_loop() -> asyncio.AbstractEventLoop:
    """确保有可用的事件循环用于跨线程广播"""
    global _broadcast_loop, _broadcast_thread

    if _broadcast_loop and _broadcast_loop.is_running():
        return _broadcast_loop

    _broadcast_loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(_broadcast_loop)
        _broadcast_loop.run_forever()

    _broadcast_thread = threading.Thread(target=_run_loop, daemon=True)
    _broadcast_thread.start()
    return _broadcast_loop


def setup_ws_callbacks():
    """设置 WebSocket 回调"""
    store = get_data_store()
    forensics = get_forensics_service()
    loop = _ensure_broadcast_loop()

    def on_new_data(message: dict):
        """收到新数据时广播"""
        msg_type = message.get('type')
        data = message.get('data')

        type_map = {
            'trade': 'new_trade',
            'alert': 'new_alert',
        }
        outbound_type = type_map.get(msg_type, msg_type)

        asyncio.run_coroutine_threadsafe(
            manager.broadcast({
                'type': outbound_type,
                'data': data,
                'timestamp': datetime.now().isoformat(),
            }),
            loop,
        )

        # 同步推送统计，保证仪表盘实时更新
        if msg_type in {'trade', 'alert'}:
            stats = store.get_stats()
            stats.is_streaming = forensics.is_streaming()
            asyncio.run_coroutine_threadsafe(
                manager.broadcast({
                    'type': 'stats',
                    'data': stats.__dict__,
                    'timestamp': datetime.now().isoformat(),
                }),
                loop,
            )

    store.register_ws_callback(on_new_data)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket 实时数据流
    
    消息格式：
    {
        "type": "new_trade" | "new_alert" | "stats",
        "data": { ... },
        "timestamp": "2024-01-01T12:00:00"
    }
    
    客户端可发送命令：
    - {"cmd": "subscribe", "topics": ["trades", "alerts", "stats"]}
    - {"cmd": "ping"}
    """
    await manager.connect(websocket)
    
    # 发送初始状态
    store = get_data_store()
    forensics = get_forensics_service()
    
    stats = store.get_stats()
    stats.is_streaming = forensics.is_streaming()
    
    await websocket.send_json({
        'type': 'connected',
        'data': {
            'message': 'Welcome to PolySleuth WebSocket',
            'stats': stats.__dict__,
        },
        'timestamp': datetime.now().isoformat(),
    })
    
    try:
        while True:
            # 等待客户端消息
            data = await websocket.receive_text()
            
            try:
                msg = json.loads(data)
                cmd = msg.get('cmd', '')
                
                if cmd == 'ping':
                    await websocket.send_json({
                        'type': 'pong',
                        'timestamp': datetime.now().isoformat(),
                    })
                
                elif cmd == 'get_stats':
                    stats = store.get_stats()
                    stats.is_streaming = forensics.is_streaming()
                    await websocket.send_json({
                        'type': 'stats',
                        'data': stats.__dict__,
                        'timestamp': datetime.now().isoformat(),
                    })
                
                elif cmd == 'get_recent_trades':
                    limit = msg.get('limit', 10)
                    trades = store.get_trades(limit=limit)
                    await websocket.send_json({
                        'type': 'recent_trades',
                        'data': [t.__dict__ for t in trades],
                        'timestamp': datetime.now().isoformat(),
                    })
                
                elif cmd == 'get_recent_alerts':
                    limit = msg.get('limit', 10)
                    alerts = store.get_alerts(limit=limit)
                    await websocket.send_json({
                        'type': 'recent_alerts',
                        'data': [a.__dict__ for a in alerts],
                        'timestamp': datetime.now().isoformat(),
                    })
            
            except json.JSONDecodeError:
                await websocket.send_json({
                    'type': 'error',
                    'data': {'message': 'Invalid JSON'},
                    'timestamp': datetime.now().isoformat(),
                })
    
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.get("/ws/stats")
async def ws_stats():
    """获取 WebSocket 连接统计"""
    return {
        'active_connections': len(manager.active_connections),
    }
