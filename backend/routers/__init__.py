"""
PolySleuth - REST API 路由包
"""
from .trades import router as trades_router
from .markets import router as markets_router
from .alerts import router as alerts_router
from .system import router as system_router
from .websocket import router as websocket_router, setup_ws_callbacks

__all__ = [
    'trades_router',
    'markets_router', 
    'alerts_router',
    'system_router',
    'websocket_router',
    'setup_ws_callbacks',
]
