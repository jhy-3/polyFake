# 🔍 PolySleuth v2.0 - 前后端分离架构

## 架构升级说明

PolySleuth v2.0 采用了完全前后端分离的架构设计，相比 v1.0 的 Streamlit 单体应用，具有以下优势：

### 架构对比

**v1.0 (Streamlit)**:
```
用户 → Streamlit 应用 → Polygon RPC
                ↓
           内存数据处理
```

**v2.0 (FastAPI + WebSocket)**:
```
前端 (HTML/JS) ←→ REST API/WebSocket ←→ 后端服务
                                        ↓
                            内存缓存 + SQLite 持久化
                                        ↓
                                  Polygon RPC
```

### 核心改进

#### 1. 后端 (FastAPI)

**位置**: `backend/`

- **main.py**: 应用入口，生命周期管理
- **config.py**: 配置中心化管理
- **models.py**: 数据模型定义
  - SQLAlchemy ORM: 数据库模型
  - Pydantic: API 请求/响应验证
- **routers/**: REST API 路由模块化
  - `trades.py`: 交易查询接口
  - `markets.py`: 市场分析接口
  - `alerts.py`: 警报管理接口
  - `system.py`: 系统控制接口
  - `websocket.py`: WebSocket 实时推送
- **services/**: 业务逻辑层
  - `storage.py`: 数据存储服务（混合架构）
  - `forensics.py`: 基础取证分析引擎
  - `analyzer.py`: 高级取证分析器（新钱包、胜率、Gas异常）
  - `advanced_forensics.py`: 高级刷量检测（自交易、循环、原子、女巫）

#### 2. 前端 (原生 JS)

**位置**: `frontend/`

- **index.html**: 主仪表板页面
- **simple.html**: 简化版页面
- **test.html**: 测试页面
- **styles.css**: Cyberpunk 主题样式
- **app.js**: 应用逻辑
  - 状态管理
  - API 调用
  - WebSocket 连接
  - 图表渲染
  - 页面导航

#### 3. Streamlit 旧版本（遗留）

**位置**: `polysleuth/`

- **dashboard_pro.py**: 专业版 Streamlit 仪表板
- **dashboard_real.py**: 基础版 Streamlit 仪表板
- **data_fetcher.py**: Polymarket API 数据获取
- **real_forensics.py**: 链上取证引擎

> ⚠️ 注意：Streamlit 版本（v1.0）已被 FastAPI + WebSocket 架构（v2.0）取代，但保留用于参考和对比。

#### 4. 数据存储

**混合架构设计**:

```python
class DataStore:
    # 内存层 (快速)
    _trades: deque(maxlen=50000)  # 最近交易
    _alerts: deque(maxlen=1000)   # 最近警报
    
    # 索引 (优化查询)
    _trades_by_hash: Dict[str, List]
    _trades_by_address: Dict[str, List]
    _trades_by_token: Dict[str, List]
    
    # 持久层 (SQLite)
    # 后台线程每 10 秒同步
```

**优势**:
- ⚡ 查询速度: 内存查询 < 10ms
- 💾 数据安全: 自动持久化，不丢数据
- 🔄 流式写入: 支持高频数据写入
- 📊 历史查询: SQLite 存储完整历史

### API 设计

#### REST API

**交易查询** (`/api/trades`):
- 支持多维度筛选（token_id, address, is_wash, side）
- 分页查询
- 时间范围筛选
- 统计聚合

**市场分析** (`/api/markets`):
- 市场列表（支持排序）
- 热门市场
- 可疑市场（高刷量比例）
- 单一市场详情
- 健康度评分

**警报管理** (`/api/alerts`):
- 警报列表（支持筛选）
- 警报统计
- 确认/处理警报

**系统控制** (`/api/system`):
- 统计信息
- 健康检查
- 手动获取数据
- 流式监控控制

#### WebSocket

**实时推送** (`/ws`):
```json
{
  "type": "new_trade",
  "data": { /* trade object */ },
  "timestamp": "2024-01-01T12:00:00"
}
```

**消息类型**:
- `new_trade`: 新交易通知
- `new_alert`: 新警报通知
- `stats`: 统计更新
- `connected`: 连接成功

**客户端命令**:
```json
{"cmd": "ping"}
{"cmd": "get_stats"}
{"cmd": "get_recent_trades", "limit": 10}
{"cmd": "get_recent_alerts", "limit": 10}
```

### 流式监控机制

#### 后端流式监控

```python
class ForensicsService:
    def start_streaming(self, poll_interval=5.0):
        # 后台线程定期轮询新区块
        while streaming:
            current_block = w3.eth.block_number
            if current_block > last_block:
                # 获取新区块的交易
                logs = w3.eth.get_logs(...)
                for log in logs:
                    trade = decode_order_filled(log)
                    store.add_trade(trade, notify=True)
                
                # 运行检测
                detect_self_trades()
                detect_circular_trades()
```

#### WebSocket 通知

```python
# storage.py
def add_trade(self, trade, notify=True):
    # 添加到内存
    self._trades.append(trade)
    
    # WebSocket 通知
    if notify:
        self._notify_ws('new_trade', trade)
```

#### 前端实时更新

```javascript
// app.js
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    
    switch (msg.type) {
        case 'new_trade':
            addToLiveFeed(msg.data);
            updateStats();
            break;
        case 'new_alert':
            showToast(`🚨 新警报: ${msg.data.alert_type}`);
            break;
    }
};
```

### 部署架构

#### 开发环境

```bash
# 单机部署
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

# 访问
http://localhost:8000  # 前端
http://localhost:8000/docs  # API 文档
ws://localhost:8000/ws  # WebSocket
```

#### 生产环境建议

```nginx
# Nginx 配置
upstream polysleuth_backend {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name polysleuth.example.com;
    
    # 前端静态文件
    location / {
        root /path/to/frontend;
        try_files $uri /index.html;
    }
    
    # API 代理
    location /api {
        proxy_pass http://polysleuth_backend;
    }
    
    # WebSocket 代理
    location /ws {
        proxy_pass http://polysleuth_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

```bash
# 使用 Gunicorn + Uvicorn Workers
gunicorn backend.main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000
```

### 性能优化

#### 1. 内存管理

```python
# 限制内存中的数据量
_trades: deque(maxlen=50000)  # 自动淘汰旧数据
_alerts: deque(maxlen=1000)

# 定期清理过期索引
def _cleanup_indexes(self):
    # 清理超过 24 小时的索引
    ...
```

#### 2. 数据库优化

```python
# 批量插入
for trade in trades_batch:
    db.add(trade)
db.commit()  # 一次性提交

# 使用索引
Index('ix_trade_unique', 'tx_hash', 'log_index', unique=True)
```

#### 3. 查询优化

```python
# 优先从内存查询
trades = self._trades  # 内存查询
if need_more:
    db_trades = db.query(TradeDB)...  # 数据库查询
```

### 监控与日志

```python
# 统一日志格式
logging.basicConfig(
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)

# 关键指标记录
logger.info(f"✅ 获取 {count} 笔交易")
logger.info(f"🔴 检测到 {wash_count} 笔刷量交易")
logger.info(f"💾 同步 {saved_count} 笔交易到数据库")
```

### 错误处理

```python
# API 层
@router.get("/trades")
async def get_trades(...):
    try:
        trades = store.get_trades(...)
        return trades
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 服务层
def _sync_to_db(self):
    try:
        db.commit()
    except Exception as e:
        logger.error(f"同步失败: {e}")
        db.rollback()
```

### 安全性

```python
# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制域名
    allow_methods=["*"],
    allow_headers=["*"],
)

# 输入验证
class TradeQuery(BaseModel):
    limit: int = Query(100, ge=1, le=5000)
    token_id: Optional[str] = None
    ...
```

### 测试

```bash
# 测试 API
curl http://localhost:8000/api/system/stats

# 测试 WebSocket
wscat -c ws://localhost:8000/ws

# 手动获取数据
curl -X POST http://localhost:8000/api/system/fetch?blocks=100

# 启动流式监控
curl -X POST http://localhost:8000/api/system/stream/start?poll_interval=5
```

## 总结

PolySleuth v2.0 的前后端分离架构提供了：

✅ **模块化设计** - 前后端独立开发与部署  
✅ **高性能** - 混合存储 + 异步 API  
✅ **实时性** - WebSocket 推送  
✅ **可扩展** - 微服务架构，易于横向扩展  
✅ **可维护** - 清晰的分层架构  
✅ **开发友好** - RESTful API + 自动文档

相比 Streamlit 版本，v2.0 更适合生产环境使用，支持更大规模的数据处理和并发访问。
