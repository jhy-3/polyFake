# PolySleuth Pro - Polymarket 链上取证工具

🔍 **专业级链上数据取证分析系统** - 使用 Polygon 链上真实交易数据检测 Polymarket 刷量行为

> **v2.0 架构** - FastAPI 后端 + 原生 JS 前端，支持实时 WebSocket 推送

## 📚 文档导航

- **[架构文档](guidelines/ARCHITECTURE.md)** - 详细的系统架构和技术实现
- **[安全性说明](guidelines/SECURITY.md)** - 检测算法、环境配置和安全最佳实践
- **[技术参考](guidelines/)** - Polymarket 架构与链上数据解码

## ✨ 功能亮点

### 🎨 专业级 UI/UX
- **Cyberpunk 深色主题** - Glassmorphism 卡片设计
- **霓虹 KPI 指标** - 渐变边框动态效果
- **实时数据流** - WebSocket 推送新交易和警报
- **多页面仪表板** - 交易、市场、警报、可视化等模块

### 🔗 真实链上数据
- 直接从 Polygon 链获取交易数据
- 支持 Chainstack、Alchemy、Infura 等高性能 RPC 节点
- 解析 OrderFilled、PositionSplit、PositionsMerge 事件
- **智能市场名称映射** - Token ID 自动转换为市场名称
- **混合存储架构** - 内存缓存 + SQLite 持久化

### 🔬 刷量检测算法（8种）

#### 基础检测
- **新钱包内幕** - 账龄<24h 且交易规模>5倍市场均值
- **高胜率交易** - 胜率>90% 且交易数>10
- **Gas异常** - Gas价格>区块中位数2倍（抢跑检测）

#### 高级检测
- **自交易检测** (Self-Trade): maker == taker，置信度 100%
- **循环交易检测** (Circular): A→B→A 模式，置信度 85%
- **原子刷量检测** (Atomic): Split→Trade→Merge 模式，置信度 90-98%
- **交易量异常** - 5分钟交易量>1小时均值的10倍
- **女巫集群** - 10秒内多钱包同向同规模投注

### 📊 高级可视化
- **实时数据流** - 最新交易滚动展示
- **市场分析图表** - 交易量、刷量比例可视化
- **警报管理** - 可疑交易实时告警
- **系统监控** - 健康状态和统计信息

### 💊 市场健康度
- 健康评分系统 (0-100)
- 风险等级分类 (🟢 LOW / 🟡 MEDIUM / 🟠 HIGH / 🔴 CRITICAL)
- 交易者数量分析
- 可疑地址追踪

## 🚀 快速开始

### 方式一：使用 FastAPI 后端（推荐 v2.0）

#### 1. 安装依赖
```bash
pip install -e .
# 或
pip install -r requirements.txt
```

#### 2. 配置环境
创建 `.env` 文件：
```env
POLYGON_RPC_URL=https://your-polygon-rpc-url
```

#### 3. 启动服务
```bash
# 使用便捷脚本
./run.sh

# 或手动启动
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

#### 4. 访问应用
- 前端界面: http://localhost:8000
- API 文档: http://localhost:8000/docs
- WebSocket: ws://localhost:8000/ws

### 方式二：使用 Streamlit 版本（v1.0 遗留）

```bash
# 专业版仪表板
streamlit run polysleuth/dashboard_pro.py --server.port 8502

# 基础版仪表板
streamlit run polysleuth/dashboard_real.py --server.port 8502
```

## 📁 项目结构

```
polyfake/
├── backend/                  # FastAPI 后端 (v2.0)
│   ├── main.py              # 应用入口
│   ├── config.py            # 配置管理
│   ├── models.py            # 数据模型
│   ├── routers/             # API 路由
│   │   ├── trades.py        # 交易查询
│   │   ├── markets.py       # 市场分析
│   │   ├── alerts.py        # 警报管理
│   │   ├── system.py        # 系统控制
│   │   └── websocket.py     # WebSocket 推送
│   └── services/            # 业务逻辑
│       ├── storage.py       # 数据存储
│       ├── forensics.py     # 基础取证
│       ├── analyzer.py      # 高级分析
│       └── advanced_forensics.py  # 高级检测
├── frontend/                # 原生 JS 前端
│   ├── index.html          # 主仪表板
│   ├── app.js              # 应用逻辑
│   └── styles.css          # Cyberpunk 样式
├── polysleuth/             # Streamlit 版本 (v1.0)
│   ├── dashboard_pro.py    # 专业版
│   ├── dashboard_real.py   # 基础版
│   ├── data_fetcher.py     # 数据获取
│   └── real_forensics.py   # 取证引擎
├── guidelines/             # 项目文档
│   ├── ARCHITECTURE.md     # 架构文档
│   ├── SECURITY.md         # 安全说明
│   └── overview.md         # 项目概览
├── data/                   # 数据存储目录
├── logs/                   # 日志文件
└── README.md              # 本文件
```

## 🔧 核心功能

## 🔧 核心功能

### REST API
- `GET /api/trades` - 交易查询（支持筛选、分页、排序）
- `GET /api/markets` - 市场列表和分析
- `GET /api/alerts` - 警报管理
- `GET /api/system/stats` - 系统统计
- `POST /api/system/fetch` - 手动获取数据
- `POST /api/system/stream/start` - 启动流式监控

### 分析端点
- `/api/trades/analysis/insider` - 新钱包内幕交易
- `/api/trades/analysis/high-winrate` - 高胜率分析
- `/api/trades/analysis/gas-anomaly` - Gas异常检测
- `/api/trades/analysis/advanced/self-trades` - 自交易检测
- `/api/trades/analysis/advanced/circular-trades` - 循环交易
- `/api/trades/analysis/advanced/atomic-wash` - 原子刷量
- `/api/trades/analysis/advanced/market-health` - 市场健康度

### WebSocket 实时推送
```javascript
const ws = new WebSocket('ws://localhost:8000/ws');
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    // 处理 new_trade, new_alert, stats 等消息
};
```

## 🎯 使用场景

- 🔍 **监管合规** - 检测市场操纵行为
- 📊 **数据分析** - 研究预测市场的交易模式
- 🛡️ **风险管理** - 识别高风险市场和可疑账户
- 🎓 **学术研究** - 研究链上数据分析技术

## 🔐 安全提示

详见 [安全性说明](guidelines/SECURITY.md)

- 切勿提交 `.env` 文件到 Git
- 使用专属 RPC 节点并定期轮换密钥
- 遵循最小权限原则配置 API

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## 📄 许可证

MIT License
