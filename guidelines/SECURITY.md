# 🔒 安全性说明

## 🛡️ 高级安全检测功能

### 检测器总览

PolySleuth 现已支持 **8 种** 高级刷量与市场操纵检测算法：

| 检测器 | 类型 | 描述 | 置信度 |
|--------|------|------|--------|
| 🆕 新钱包内幕 | 基础 | 账龄<24h 且交易规模>5倍市场均值 | 高 |
| 🎯 高胜率交易 | 基础 | 胜率>90% 且交易数>10 | 中 |
| ⛽ Gas异常 | 基础 | Gas价格>区块中位数2倍 | 中 |
| 🔄 自交易 | 高级 | maker==taker 或特征相同的关联交易 | 极高 |
| 🔗 循环交易 | 高级 | A→B→A 或 A→B→C→A 的资金流转 | 高 |
| ⚛️ 原子刷量 | 高级 | 同区块买卖对冲 (Split-Trade-Merge) | 极高 |
| 📈 交易量异常 | 高级 | 5分钟交易量>1小时均值的10倍 | 中 |
| 👥 女巫集群 | 高级 | 10秒内多钱包同向同规模投注 | 高 |

### 算法详解

#### 1. 自交易 (Self-Trade) 检测

```python
# 直接自交易: maker == taker
direct_self = trades[trades['maker'] == trades['taker']]

# 协调自交易: 相同 (amount, price, timestamp) 的多笔交易
signature = f"{size}_{price}_{timestamp}"
coordinated = trades.groupby('signature').filter(lambda x: len(x) >= 2)
```

#### 2. 循环交易 (Circular Trade) 检测

使用 **NetworkX** 图算法检测资金循环：

```python
import networkx as nx

G = nx.DiGraph()
for trade in trades:
    G.add_edge(taker, maker, weight=volume)

# 检测简单循环 (2-4节点)
cycles = nx.simple_cycles(G)
```

#### 3. 原子刷量 (Atomic Wash) 检测

检测同一区块内的买卖对冲：

```python
# 同一区块、同一地址的买卖交易
for (block, address), group in trades.groupby(['block_number', 'maker']):
    buys = group[group['side'] == 'BUY']
    sells = group[group['side'] == 'SELL']
    
    # 如果买卖量相差<20%，则为可疑
    if abs(buy_vol - sell_vol) / max(buy_vol, sell_vol) < 0.2:
        flag_as_atomic_wash()
```

#### 4. 交易量异常 (Volume Spike) 检测

```python
# 5分钟分箱
trades['bin'] = trades['timestamp'].dt.floor('5min')

# 1小时滚动平均
rolling_avg = trades.groupby('bin')['volume'].sum().rolling('1H').mean()

# 超过10倍均值则标记
spikes = volume_by_bin[volume_by_bin['spike_ratio'] > 10]
```

#### 5. 女巫集群 (Sybil Cluster) 检测

```python
# 10秒时间窗口内
# 同市场、同方向、交易规模相似(±20%)的多个钱包
for (market, window, side), group in trades.groupby([...]):
    if len(unique_addresses) >= 3:
        size_deviation = (sizes - mean_size) / mean_size
        if (size_deviation < 0.2).mean() > 0.6:
            flag_as_sybil_cluster()
```

### 市场健康评分

综合所有检测器结果，计算 0-100 的健康评分：

| 评分 | 风险等级 | 描述 |
|------|----------|------|
| 80-100 | ✅ LOW | 市场健康 |
| 60-79 | ⚠️ MEDIUM | 存在一些可疑活动 |
| 40-59 | 🔶 HIGH | 存在明显的操纵迹象 |
| 0-39 | 🚨 CRITICAL | 市场严重被操纵 |

### API 端点

```bash
# 基础分析
GET /trades/analysis/insider
GET /trades/analysis/high-winrate
GET /trades/analysis/gas-anomaly
GET /trades/analysis/full

# 高级分析
GET /trades/analysis/advanced/self-trades
GET /trades/analysis/advanced/circular-trades
GET /trades/analysis/advanced/atomic-wash
GET /trades/analysis/advanced/volume-spikes
GET /trades/analysis/advanced/sybil-clusters

# 综合报告
GET /trades/analysis/advanced/market-health
```

---

## 环境变量配置

### ⚠️ 重要提示

**请勿将包含敏感信息的 `.env` 文件提交到 Git 仓库！**

本项目使用 `.env` 文件管理敏感配置，包括：
- RPC 节点 API 密钥
- 数据库连接字符串
- 其他敏感配置

### 配置步骤

1. **复制环境变量模板**
   ```bash
   cp .env.example .env
   ```

2. **编辑 `.env` 文件**
   - 将 `POLYGON_RPC_URL` 替换为你的专属 RPC 节点地址
   - 根据需要调整其他配置

3. **验证 `.gitignore`**
   - 确认 `.env` 已添加到 `.gitignore`
   - 运行 `git status` 确保 `.env` 不会被跟踪

### RPC 节点获取

推荐的 Polygon RPC 提供商：

- **Chainstack** (推荐)
  - 注册: https://chainstack.com
  - 免费额度: 300万请求/月
  - URL 格式: `https://polygon-mainnet.core.chainstack.com/YOUR_API_KEY`

- **Alchemy**
  - 注册: https://www.alchemy.com
  - 免费额度: 300万计算单元/月
  - URL 格式: `https://polygon-mainnet.g.alchemy.com/v2/YOUR_API_KEY`

- **Infura**
  - 注册: https://infura.io
  - 免费额度: 100k 请求/天
  - URL 格式: `https://polygon-mainnet.infura.io/v3/YOUR_API_KEY`

### 安全最佳实践

✅ **应该做的：**
- 使用 `.env` 文件存储所有敏感信息
- 定期轮换 API 密钥
- 为不同环境使用不同的配置文件（`.env.development`, `.env.production`）
- 限制 API 密钥的访问权限和速率

❌ **不应该做的：**
- 在代码中硬编码 API 密钥
- 将 `.env` 文件提交到版本控制系统
- 在公共论坛或聊天中分享 API 密钥
- 使用生产环境的密钥进行本地开发

### 泄露应对

如果不慎泄露了 API 密钥：

1. **立即撤销/删除泄露的密钥**
2. **生成新的密钥**
3. **更新 `.env` 文件**
4. **检查是否有未授权使用**
5. **如果已提交到 Git，使用 `git-filter-repo` 或 `BFG Repo-Cleaner` 清理历史**

### Git 历史清理

如果已经提交了包含密钥的文件：

```bash
# 使用 git-filter-repo (推荐)
pip install git-filter-repo
git filter-repo --invert-paths --path .env

# 或使用 BFG Repo-Cleaner
java -jar bfg.jar --delete-files .env
```

⚠️ **注意**: 清理 Git 历史会改变提交哈希，需要强制推送。

## 依赖安全

定期更新依赖以修复安全漏洞：

```bash
# 检查过期依赖
pip list --outdated

# 更新依赖
pip install --upgrade -r requirements.txt

# 安全审计
pip install safety
safety check
```

## 报告安全问题

如发现安全漏洞，请通过以下方式报告：
- 创建 GitHub Issue（标记为 security）
- 或发送邮件至项目维护者

请勿公开披露未修复的漏洞。
