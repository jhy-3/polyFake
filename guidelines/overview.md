# PolySleuth —— 链上预测市场取证与反操纵雷达

> **文档状态**: 本文档为项目早期构思阶段的愿景规划（v0.1），部分功能已在当前版本实现。  
> **当前版本**: v2.0 - FastAPI 后端 + 原生 JS 前端架构  
> **实施情况**: ✅ 已实现 | 🚧 部分实现 | ⏸️ 计划中

---

这是一个为 Polymarket 量身定制的链上数据取证分析项目文档。它基于《Polymarket 架构与链上数据解码》底层机制，结合 DeFi 和 AI 领域的专业技术。

---

# 项目名称：PolySleuth —— 链上预测市场取证与反操纵雷达

## 1. 项目愿景 (Executive Summary)

**PolySleuth** 是一个针对 Polymarket 的垂直安全与数据分析平台。不同于普通的行情看板，PolySleuth 旨在通过深度解析链上日志（Logs），构建“证据链”，识别预测市场中的**虚假交易（Wash Trading）**、**市场操纵（Market Manipulation）以及异常资金流向**。

**核心价值**：为交易者提供“市场真实性评分（Organic Score）”，为项目方提供反作弊证据。

---

## 2. 痛点分析 (Problem Statement)

**✅ 已验证并实现**

目前 Polymarket 生态存在以下数据盲区：

1. **虚假繁荣**：某些市场的 Volume 可能由少数地址反复倒手（Self-Trading）刷出，误导散户入场。✅ 已实现自交易检测
2. **隐形操纵**：巨鲸可能通过分拆钱包（Sybil Attacks）在"负风险（Negative Risk）"市场中进行复杂的套利或操纵，普通前端无法察觉。✅ 已实现女巫集群检测
3. **数据黑盒**：官方 API 提供聚合数据，但缺乏基于原子级链上交易（Atomic On-chain Transactions）的取证工具。✅ 已实现链上数据解码

---

## 3. 技术架构 (Technical Architecture)

**实施状态**: 🚧 核心架构已实现，采用 FastAPI + 混合存储架构

### 3.1 数据层：链上证据链构建器 (The Evidence Chain)

**✅ 已实现** - 基于 `backend/services/forensics.py` 和 `storage.py`

* **数据源**：Polygon RPC（支持 Chainstack、Alchemy、Infura）
* **监听合约**：
  * ✅ `CTF Exchange` (二元市场撮合)
  * ⏸️ `NegRisk_CTFExchange` (多选市场撮合) - 计划中
  * ✅ `ConditionalTokens` (底层资产铸造与销毁)

* **核心解析逻辑（Trade Decoder & Market Decoder）**：
  * ✅ **交易事件**：解析 `OrderFilled`，提取 `maker` / `taker` / `price`。
  * ✅ **资金流转**：监听 `PositionSplit`（资金注入/铸造）和 `PositionsMerge`（资金赎回/销毁）。
  * ⏸️ **复杂转换**：监听 `PositionsConverted`（负风险市场中 No 转 Yes 的操作）- 计划中

### 3.2 分析层：图算法与启发式检测 (The Brain)

**✅ 已实现** - 基于 `backend/services/analyzer.py` 和 `advanced_forensics.py`

* **图数据库（Graph Construction）**：
  * ✅ 节点（Nodes）：钱包地址（EOA）
  * ✅ 边（Edges）：交易对手关系（Traded_With）、资金流转
  * 🚧 使用 NetworkX 进行图分析

* **检测算法**：
  * ✅ **闭环检测 (Cycle Detection)**：识别循环交易模式
  * ✅ **同源聚类 (Sybil Clustering)**：女巫集群检测
  * ✅ **原子刷量检测**：Split-Trade-Merge 模式识别

### 3.3 展示层：取证看板 (The Dashboard)

**✅ 已实现** - 前后端分离架构

* ✅ **市场健康度评分**：0-100分，综合8种检测算法
* ✅ **实时推送**：WebSocket 实时数据流
* ✅ **可视化展示**：交易列表、市场分析、警报管理
* ✅ **REST API**：完整的 API 文档（/docs）

---

## 4. 核心功能模块 (Key Features)

### 模块 A：原子级刷量检测器 (Atomic Wash Trade Detector)

**原理**：利用链上数据的原子性。
**检测逻辑**：
扫描同一 `tx_hash` 内的事件序列。如果一个 Transaction 满足以下模式，标记为**“强刷量”**：

1. `PositionSplit` (用 USDC 铸造 Token)
2. `OrderFilled` (Maker=自己, Taker=自己，或者通过机器人账号互倒)
3. `PositionsMerge` (将 Token 销毁换回 USDC)
**输出**：该 Tx 产生的 Volume 被标记为“Fake Volume”。

### 模块 B：负风险套利监控 (Negative Risk Exploit Monitor)

**原理**：基于文档中提到的 `NegRiskAdapter` 机制。
**检测逻辑**：
监控 `PositionsConverted` 事件。当市场发生剧烈波动时，攻击者可能大量将垃圾市场的 NO 头寸转换为热门市场的 YES 头寸并抛售。
**难点攻克**：需要追踪 TokenID 的变异（从 TokenID_A 变为 TokenID_Set_B）。
**价值**：这是 Polymarket 特有的机制，针对此功能的分析极具黑客松获奖潜力。

### 模块 C：做市商 vs 操纵者指纹识别

**原理**：区分正常提供流动性（MM）和恶意刷量。
**算法**：

* **MM 特征**：高频挂单，PNL（盈亏）波动小，资金长期留存。
* **刷量特征**：只吃单（Taker），快速进出，目的是刷积分或误导，通常伴随 Gas 亏损。

---

## 5. 数据分析难点与解决方案 (Data Challenges)

| 难点 | 描述 | 解决方案 (你的优势) |
| --- | --- | --- |
| **TokenID 爆炸** | 负风险市场中，一次 Convert 操作涉及多个 TokenID 的销毁与生成。 | 实现一个**动态映射表**。基于文档中的 `keccak256` 算法，预先计算所有 Outcome 的 TokenID，建立反向索引。 |
| **高并发撮合** | 一个 `OrdersMatched` 可能包含数十个 `OrderFilled`。 | 并不把它们视为独立意图。将同一 `tx_hash` 下的所有 `OrderFilled` 聚合为一个**“撮合批次”**进行整体分析。 |
| **地址匿名性** | 无法直接知道谁是操控者。 | 使用 **Time-Series Clustering**（时间序列聚类）。如果地址 A 和地址 B 总是在相差 1 秒内进行反向操作，判定为同一实体。 |

---

## 6. 开发路线图 (24-48小时 Hackathon Plan)

* **阶段一：基础设施 (0-12小时)**
* 部署 Python/Rust 脚本，连接 Polygon RPC。
* 实现文档中的 `Trade Decoder`，将最近 3 天的热门市场（如体育赛事）日志爬取并存入 PostgreSQL。
* **产出**：一张包含清洗后交易数据的 `trades` 表。


* **阶段二：核心算法 (12-30小时)**
* 编写 SQL 或 Python Pandas 脚本，计算每个 Market 的“自成交比例”。
* 构建简单的 NetworkX 图，找出关联钱包。
* **产出**：一份 JSON 报告，列出 Top 10 虚假交易市场。


* **阶段三：前端与展示 (30-48小时)**
* 使用 Streamlit 或 Next.js 搭建界面。
* 输入：Market Slug (例如 `nba-lakers-vs-warriors`)。
* 输出：真实交易量 vs 名义交易量对比图。



---

## 7. 为什么这个项目能赢？ (Winning Pitch)

1. **切中痛点**：Polymarket 目前最大的争议就是数据真实性。官方和机构用户都渴望一个“净水器”。
2. **技术硬核**：完全基于你提供的文档进行底层解码（CTF 框架、负风险机制），展示了极强的技术理解力，而非仅仅调用 API。
3. **安全叙事**：将预测市场与“链上取证”结合，这是一个非常性感的赛道（类似于 Etherscan + Dune Analytics 的结合体）。

---

### 附录：核心数据结构设计 (Schema)

建议在数据库设计中加入专门的**取证字段**：

```sql
-- 扩展 trades 表，增加取证标记
ALTER TABLE trades ADD COLUMN is_atomic_wash BOOLEAN DEFAULT FALSE; -- 是否为原子级刷量
ALTER TABLE trades ADD COLUMN funding_source_address VARCHAR(42);   -- 资金来源父钱包
ALTER TABLE trades ADD COLUMN linked_cluster_id VARCHAR(32);        -- 归属的操纵团伙ID

-- 市场健康度表
CREATE TABLE market_health (
    market_id INTEGER,
    organic_volume DECIMAL, -- 剔除刷量后的真实成交量
    wash_trading_ratio FLOAT, -- 刷量比例
    unique_active_traders INTEGER, -- 真实活跃用户数（剔除机器人）
    risk_level VARCHAR(10) -- LOW, MEDIUM, HIGH, CRITICAL
);

```