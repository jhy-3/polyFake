"""
PolySleuth - FastAPI 主入口

一个专业的 Polymarket 刷量交易取证分析系统
"""
import logging
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from .config import API_HOST, API_PORT, DEBUG, LOG_LEVEL
from .models import init_db
from .routers import (
    trades_router,
    markets_router,
    alerts_router,
    system_router,
    websocket_router,
    setup_ws_callbacks,
)
from .services.storage import get_data_store
from .services.forensics import get_forensics_service

# 配置日志
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("🚀 PolySleuth 后端启动中...")
    
    # 初始化数据库
    init_db()
    logger.info("✅ 数据库初始化完成")
    
    # 初始化存储服务
    store = get_data_store()
    logger.info(f"✅ 存储服务就绪 (内存: {store.get_stats().total_trades} 笔交易)")
    
    # 初始化取证服务
    forensics = get_forensics_service()
    if forensics.is_connected():
        logger.info(f"✅ 已连接到 Polygon (Block: {forensics.get_current_block()})")
    else:
        logger.warning("⚠️ Polygon 节点连接失败，部分功能不可用")
    
    # 设置 WebSocket 回调
    setup_ws_callbacks()
    logger.info("✅ WebSocket 回调已配置")
    
    # 自动获取初始数据（后台执行，避免阻塞启动）
    if forensics.is_connected():
        def _warmup_fetch():
            logger.info("📡 正在获取初始链上数据...")
            count = forensics.fetch_recent_trades(100)
            logger.info(f"✅ 已获取 {count} 笔交易")

        threading.Thread(target=_warmup_fetch, daemon=True).start()
    
    logger.info("🎉 PolySleuth 后端启动完成!")
    logger.info(f"📍 API 地址: http://{API_HOST}:{API_PORT}")
    logger.info(f"📖 文档地址: http://{API_HOST}:{API_PORT}/docs")
    
    yield
    
    # 关闭
    logger.info("🛑 PolySleuth 后端关闭中...")
    
    # 停止流式监控
    if forensics.is_streaming():
        forensics.stop_streaming()
    
    # 停止存储同步
    store.stop()
    
    logger.info("👋 PolySleuth 已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="PolySleuth API",
    description="""
# 🔍 PolySleuth - Polymarket 刷量取证分析系统

专业的链上数据分析与刷量交易检测 API。

## 功能模块

- **交易 (Trades)**: 查询、筛选、统计链上交易
- **市场 (Markets)**: 市场摘要、健康度评分、可疑市场检测
- **警报 (Alerts)**: 刷量警报管理与统计
- **系统 (System)**: 数据获取、流式监控控制
- **WebSocket**: 实时数据推送

## 数据来源

直接从 Polygon 链上获取 Polymarket CTF Exchange 的真实交易数据。
    """,
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(trades_router, prefix="/api")
app.include_router(markets_router, prefix="/api")
app.include_router(alerts_router, prefix="/api")
app.include_router(system_router, prefix="/api")
app.include_router(websocket_router)

# 静态文件 - 前端
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def root():
    """根路由 - 返回前端页面或 API 信息"""
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    
    return {
        "name": "PolySleuth API",
        "version": "2.0.0",
        "docs": "/docs",
        "websocket": "/ws",
    }


@app.get("/api")
async def api_info():
    """API 信息"""
    forensics = get_forensics_service()
    store = get_data_store()
    stats = store.get_stats()
    
    return {
        "name": "PolySleuth API",
        "version": "2.0.0",
        "status": "running",
        "chain_connected": forensics.is_connected(),
        "is_streaming": forensics.is_streaming(),
        "stats": {
            "total_trades": stats.total_trades,
            "total_alerts": stats.total_alerts,
            "wash_trade_count": stats.wash_trade_count,
        },
        "endpoints": {
            "trades": "/api/trades",
            "markets": "/api/markets",
            "alerts": "/api/alerts",
            "system": "/api/system",
            "websocket": "/ws",
            "docs": "/docs",
        }
    }


# 运行入口
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=DEBUG,
    )
