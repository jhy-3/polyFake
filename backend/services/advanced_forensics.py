"""
PolySleuth - 高级刷量检测与市场操纵分析

包含：
1. 自交易(刷量)检测
2. 循环交易检测（图算法）
3. 原子化刷量模式检测
4. 交易量异常检测
5. 女巫攻击集群检测
6. 综合市场健康评估
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, field
from collections import defaultdict
from decimal import Decimal
import hashlib

import pandas as pd
import numpy as np

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False
    logging.warning("NetworkX not installed. Circular trade detection will be limited.")

from ..models import SessionLocal, TradeDB

logger = logging.getLogger(__name__)


# ============================================================================
# 数据结构定义
# ============================================================================

@dataclass
class WashTradeEvidence:
    """刷量交易证据"""
    evidence_type: str  # SELF_TRADE, CIRCULAR, ATOMIC, VOLUME_SPIKE, SYBIL_CLUSTER
    tx_hash: str
    addresses: List[str]
    confidence: float
    volume: float
    details: Dict
    timestamp: Optional[datetime] = None


@dataclass
class CircularPath:
    """循环交易路径"""
    path: List[str]  # 地址列表
    tx_hashes: List[str]
    total_volume: float
    time_span_minutes: float
    confidence: float


@dataclass
class SybilCluster:
    """女巫攻击集群"""
    cluster_id: str
    addresses: List[str]
    market_id: str
    side: str  # YES/NO or BUY/SELL
    trade_count: int
    total_volume: float
    win_rate: float
    time_window_seconds: float
    confidence: float


@dataclass
class VolumeSpike:
    """交易量异常"""
    market_id: str
    timestamp: datetime
    spike_volume: float
    baseline_volume: float
    spike_ratio: float
    trade_count: int
    is_correlated_with_event: bool = False
    event_info: Optional[str] = None


@dataclass
class MarketHealthReport:
    """市场健康报告"""
    market_id: str
    health_score: float  # 0-100
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    total_trades: int
    total_volume: float
    wash_trade_ratio: float
    evidence_list: List[WashTradeEvidence]
    summary: Dict


# ============================================================================
# 1. 自交易(刷量)检测
# ============================================================================

def detect_self_trades(trades_df: pd.DataFrame) -> List[WashTradeEvidence]:
    """
    检测自交易（刷量交易）
    
    检测逻辑：
    1. maker_address == taker_address 的直接自交易
    2. 相同 (amount, price, timestamp) 的交易可能来自同一资金来源
    
    Args:
        trades_df: 交易 DataFrame，需包含 maker, taker, size, price, timestamp, tx_hash
    
    Returns:
        刷量交易证据列表
    """
    if trades_df.empty:
        logger.warning("detect_self_trades: 输入 DataFrame 为空")
        return []
    
    logger.info("🔍 开始自交易检测...")
    evidence_list: List[WashTradeEvidence] = []
    
    # 1. 直接自交易检测 (maker == taker)
    direct_self_trades = trades_df[trades_df['maker'] == trades_df['taker']]
    
    for _, trade in direct_self_trades.iterrows():
        evidence = WashTradeEvidence(
            evidence_type="SELF_TRADE_DIRECT",
            tx_hash=trade['tx_hash'],
            addresses=[trade['maker']],
            confidence=0.99,  # 直接自交易置信度极高
            volume=trade['volume'],
            timestamp=trade['timestamp'],
            details={
                'trade_type': 'direct_self_trade',
                'address': trade['maker'],
                'size': trade['size'],
                'price': trade['price'],
                'token_id': trade.get('token_id', ''),
            }
        )
        evidence_list.append(evidence)
    
    logger.info(f"  ✓ 发现 {len(direct_self_trades)} 笔直接自交易")
    
    # 2. 相同交易特征检测（可能的关联自交易）
    # 创建交易特征哈希
    trades_df = trades_df.copy()
    trades_df['trade_signature'] = trades_df.apply(
        lambda x: f"{x['size']:.6f}_{x['price']:.6f}_{x['timestamp'].strftime('%Y%m%d%H%M')}",
        axis=1
    )
    
    # 按特征分组
    signature_groups = trades_df.groupby('trade_signature')
    
    suspicious_pairs = 0
    for signature, group in signature_groups:
        if len(group) >= 2:
            # 检查是否有不同的 maker/taker 对
            makers = set(group['maker'].unique())
            takers = set(group['taker'].unique())
            
            # 如果同一特征的交易涉及少量地址，可能是关联账户
            all_addresses = makers | takers
            if len(all_addresses) <= 4 and len(group) >= 2:
                total_vol = group['volume'].sum()
                
                evidence = WashTradeEvidence(
                    evidence_type="SELF_TRADE_COORDINATED",
                    tx_hash=group['tx_hash'].iloc[0],
                    addresses=list(all_addresses),
                    confidence=min(0.8, 0.5 + len(group) * 0.1),
                    volume=total_vol,
                    timestamp=group['timestamp'].iloc[0],
                    details={
                        'trade_type': 'coordinated_self_trade',
                        'trade_count': len(group),
                        'signature': signature,
                        'addresses': list(all_addresses),
                    }
                )
                evidence_list.append(evidence)
                suspicious_pairs += 1
    
    logger.info(f"  ✓ 发现 {suspicious_pairs} 组协调自交易")
    logger.info(f"✅ 自交易检测完成: 共 {len(evidence_list)} 条证据")
    
    return evidence_list


# ============================================================================
# 2. 循环交易检测（图算法）
# ============================================================================

def detect_circular_trades(
    trades_df: pd.DataFrame,
    window_minutes: int = 60,
    min_cycle_volume: float = 100.0
) -> List[CircularPath]:
    """
    检测循环交易路径
    
    使用 NetworkX 构建资金流向图，检测简单循环：
    - A -> B -> A (二节点循环)
    - A -> B -> C -> A (三节点循环)
    
    Args:
        trades_df: 交易 DataFrame
        window_minutes: 时间窗口（分钟）
        min_cycle_volume: 最小循环交易量
    
    Returns:
        循环路径列表
    """
    if not HAS_NETWORKX:
        logger.warning("NetworkX 未安装，无法进行循环交易检测")
        return []
    
    if trades_df.empty:
        return []
    
    logger.info(f"🔍 开始循环交易检测 (窗口: {window_minutes}分钟)...")
    
    circular_paths: List[CircularPath] = []
    
    # 按时间窗口分组
    trades_df = trades_df.copy()
    trades_df['time_window'] = trades_df['timestamp'].dt.floor(f'{window_minutes}min')
    
    for window, window_trades in trades_df.groupby('time_window'):
        if len(window_trades) < 3:
            continue
        
        # 构建有向图
        G = nx.DiGraph()
        edge_trades = defaultdict(list)  # 记录每条边对应的交易
        
        for _, trade in window_trades.iterrows():
            maker = trade['maker'].lower()
            taker = trade['taker'].lower()
            volume = trade['volume']
            tx_hash = trade['tx_hash']
            
            # 添加边（资金从 taker 流向 maker，因为 taker 买入）
            if trade.get('side', 'BUY') == 'BUY':
                G.add_edge(taker, maker, weight=volume)
                edge_trades[(taker, maker)].append({
                    'tx_hash': tx_hash,
                    'volume': volume,
                    'timestamp': trade['timestamp']
                })
            else:
                G.add_edge(maker, taker, weight=volume)
                edge_trades[(maker, taker)].append({
                    'tx_hash': tx_hash,
                    'volume': volume,
                    'timestamp': trade['timestamp']
                })
        
        # 检测简单循环
        try:
            cycles = list(nx.simple_cycles(G))
            
            for cycle in cycles:
                if len(cycle) < 2 or len(cycle) > 4:
                    continue
                
                # 计算循环总交易量
                cycle_volume = 0
                cycle_tx_hashes = []
                
                for i in range(len(cycle)):
                    from_addr = cycle[i]
                    to_addr = cycle[(i + 1) % len(cycle)]
                    
                    if (from_addr, to_addr) in edge_trades:
                        for tx in edge_trades[(from_addr, to_addr)]:
                            cycle_volume += tx['volume']
                            cycle_tx_hashes.append(tx['tx_hash'])
                
                if cycle_volume >= min_cycle_volume:
                    # 计算置信度（基于循环长度和交易量）
                    confidence = min(0.95, 0.6 + (cycle_volume / 10000) * 0.1)
                    if len(cycle) == 2:
                        confidence = min(0.98, confidence + 0.1)
                    
                    path = CircularPath(
                        path=cycle,
                        tx_hashes=list(set(cycle_tx_hashes)),
                        total_volume=cycle_volume,
                        time_span_minutes=window_minutes,
                        confidence=confidence
                    )
                    circular_paths.append(path)
        
        except Exception as e:
            logger.debug(f"循环检测出错: {e}")
    
    # 去重（基于路径）
    seen_paths = set()
    unique_paths = []
    for path in circular_paths:
        path_key = tuple(sorted(path.path))
        if path_key not in seen_paths:
            seen_paths.add(path_key)
            unique_paths.append(path)
    
    logger.info(f"✅ 循环交易检测完成: 发现 {len(unique_paths)} 条循环路径")
    return unique_paths


def circular_paths_to_evidence(paths: List[CircularPath]) -> List[WashTradeEvidence]:
    """将循环路径转换为证据格式"""
    evidence_list = []
    
    for path in paths:
        evidence = WashTradeEvidence(
            evidence_type="CIRCULAR_TRADE",
            tx_hash=path.tx_hashes[0] if path.tx_hashes else "",
            addresses=path.path,
            confidence=path.confidence,
            volume=path.total_volume,
            details={
                'cycle_path': ' -> '.join(path.path[:4]) + ' -> ' + path.path[0],
                'cycle_length': len(path.path),
                'all_tx_hashes': path.tx_hashes,
                'time_span_minutes': path.time_span_minutes,
            }
        )
        evidence_list.append(evidence)
    
    return evidence_list


# ============================================================================
# 3. 原子化刷量模式检测 (Split-Trade-Merge)
# ============================================================================

def detect_atomic_wash_patterns(
    trades_df: pd.DataFrame,
    logs_df: Optional[pd.DataFrame] = None
) -> List[WashTradeEvidence]:
    """
    检测原子化刷量模式
    
    在单个交易中检测序列：PositionSplit -> OrderFilled -> PositionsMerge
    如果同一用户在同一交易中完成了拆分-交易-合并，则为高置信度刷量
    
    Args:
        trades_df: 交易 DataFrame
        logs_df: 日志 DataFrame（可选，包含 Split/Merge 事件）
    
    Returns:
        刷量证据列表
    """
    logger.info("🔍 开始原子化刷量模式检测...")
    
    evidence_list: List[WashTradeEvidence] = []
    
    # 如果没有日志数据，使用交易数据的启发式检测
    if logs_df is None or logs_df.empty:
        # 启发式：检测同一区块内同一地址的多笔反向交易
        trades_df = trades_df.copy()
        
        # 按区块和地址分组
        for (block, address), group in trades_df.groupby(['block_number', 'maker']):
            if len(group) < 2:
                continue
            
            # 检查是否有买卖对冲
            buys = group[group['side'] == 'BUY']
            sells = group[group['side'] == 'SELL']
            
            if len(buys) > 0 and len(sells) > 0:
                # 计算买卖量差异
                buy_volume = buys['volume'].sum()
                sell_volume = sells['volume'].sum()
                
                # 如果买卖量接近，可能是刷量
                volume_ratio = min(buy_volume, sell_volume) / max(buy_volume, sell_volume) if max(buy_volume, sell_volume) > 0 else 0
                
                if volume_ratio > 0.8:  # 买卖量相差不超过20%
                    confidence = min(0.9, 0.7 + volume_ratio * 0.2)
                    
                    evidence = WashTradeEvidence(
                        evidence_type="ATOMIC_WASH",
                        tx_hash=group['tx_hash'].iloc[0],
                        addresses=[address],
                        confidence=confidence,
                        volume=buy_volume + sell_volume,
                        timestamp=group['timestamp'].iloc[0],
                        details={
                            'pattern': 'buy_sell_hedge',
                            'buy_volume': buy_volume,
                            'sell_volume': sell_volume,
                            'volume_ratio': volume_ratio,
                            'block_number': block,
                            'trade_count': len(group),
                        }
                    )
                    evidence_list.append(evidence)
    
    else:
        # 使用日志数据进行精确检测
        # 按交易哈希分组
        for tx_hash, tx_logs in logs_df.groupby('tx_hash'):
            event_types = set(tx_logs['event_type'].unique())
            
            # 检测 Split-Trade-Merge 模式
            has_split = 'PositionSplit' in event_types or 'Split' in event_types
            has_trade = 'OrderFilled' in event_types or 'Trade' in event_types
            has_merge = 'PositionsMerge' in event_types or 'Merge' in event_types
            
            if has_split and has_trade and has_merge:
                # 获取涉及的地址
                addresses = list(tx_logs['address'].unique()) if 'address' in tx_logs.columns else []
                
                evidence = WashTradeEvidence(
                    evidence_type="ATOMIC_WASH",
                    tx_hash=tx_hash,
                    addresses=addresses,
                    confidence=0.99,  # Split-Trade-Merge 模式置信度极高
                    volume=tx_logs['volume'].sum() if 'volume' in tx_logs.columns else 0,
                    details={
                        'pattern': 'split_trade_merge',
                        'events': list(event_types),
                        'log_count': len(tx_logs),
                    }
                )
                evidence_list.append(evidence)
    
    logger.info(f"✅ 原子化刷量检测完成: 发现 {len(evidence_list)} 条证据")
    return evidence_list


# ============================================================================
# 4. 交易量异常检测
# ============================================================================

def detect_volume_spikes(
    trades_df: pd.DataFrame,
    threshold: float = 10.0,
    bin_minutes: int = 5,
    baseline_hours: float = 1.0,
    news_timestamps: Optional[List[datetime]] = None
) -> List[VolumeSpike]:
    """
    检测交易量异常
    
    监控每个市场的 5 分钟交易量，标记超过 1 小时滚动平均 10 倍的时段
    
    Args:
        trades_df: 交易 DataFrame
        threshold: 异常阈值倍数（默认 10 倍）
        bin_minutes: 时间分箱大小（分钟）
        baseline_hours: 基准计算时间窗口（小时）
        news_timestamps: 新闻/事件时间戳列表（用于关联分析）
    
    Returns:
        交易量异常列表
    """
    if trades_df.empty:
        return []
    
    logger.info(f"🔍 开始交易量异常检测 (阈值: {threshold}x)...")
    
    spikes: List[VolumeSpike] = []
    
    trades_df = trades_df.copy()
    trades_df['time_bin'] = trades_df['timestamp'].dt.floor(f'{bin_minutes}min')
    
    # 按市场分组分析
    for token_id, market_trades in trades_df.groupby('token_id'):
        # 按时间分箱计算交易量
        volume_by_bin = market_trades.groupby('time_bin').agg({
            'volume': 'sum',
            'tx_hash': 'count'
        }).rename(columns={'tx_hash': 'trade_count'})
        
        if len(volume_by_bin) < 3:
            continue
        
        # 计算滚动平均（1小时窗口）
        rolling_window = int(baseline_hours * 60 / bin_minutes)
        volume_by_bin['rolling_avg'] = volume_by_bin['volume'].rolling(
            window=rolling_window,
            min_periods=1
        ).mean().shift(1)  # shift 避免包含当前 bin
        
        # 填充第一个窗口
        volume_by_bin['rolling_avg'] = volume_by_bin['rolling_avg'].fillna(
            volume_by_bin['volume'].expanding().mean()
        )
        
        # 计算异常比率
        volume_by_bin['spike_ratio'] = volume_by_bin['volume'] / volume_by_bin['rolling_avg'].replace(0, 1)
        
        # 筛选异常
        anomalies = volume_by_bin[volume_by_bin['spike_ratio'] > threshold]
        
        for timestamp, row in anomalies.iterrows():
            # 检查是否与新闻事件关联
            is_correlated = False
            event_info = None
            
            if news_timestamps:
                for news_ts in news_timestamps:
                    time_diff = abs((timestamp - news_ts).total_seconds())
                    if time_diff < 3600:  # 1小时内
                        is_correlated = True
                        event_info = f"Event at {news_ts}"
                        break
            
            spike = VolumeSpike(
                market_id=token_id,
                timestamp=timestamp,
                spike_volume=row['volume'],
                baseline_volume=row['rolling_avg'],
                spike_ratio=row['spike_ratio'],
                trade_count=int(row['trade_count']),
                is_correlated_with_event=is_correlated,
                event_info=event_info
            )
            spikes.append(spike)
    
    logger.info(f"✅ 交易量异常检测完成: 发现 {len(spikes)} 次异常")
    return spikes


def volume_spikes_to_evidence(spikes: List[VolumeSpike]) -> List[WashTradeEvidence]:
    """将交易量异常转换为证据格式"""
    evidence_list = []
    
    for spike in spikes:
        # 如果与事件关联，降低置信度（可能是正常的市场反应）
        confidence = 0.7 if spike.is_correlated_with_event else 0.85
        confidence = min(0.95, confidence + (spike.spike_ratio - 10) * 0.01)
        
        evidence = WashTradeEvidence(
            evidence_type="VOLUME_SPIKE",
            tx_hash="",  # 多笔交易
            addresses=[],
            confidence=confidence,
            volume=spike.spike_volume,
            timestamp=spike.timestamp,
            details={
                'market_id': spike.market_id,
                'spike_ratio': spike.spike_ratio,
                'baseline_volume': spike.baseline_volume,
                'trade_count': spike.trade_count,
                'is_correlated_with_event': spike.is_correlated_with_event,
                'event_info': spike.event_info,
            }
        )
        evidence_list.append(evidence)
    
    return evidence_list


# ============================================================================
# 5. 女巫攻击集群检测（协调投注）
# ============================================================================

def detect_coordinated_clusters(
    trades_df: pd.DataFrame,
    time_window_seconds: int = 10,
    min_cluster_size: int = 3,
    size_tolerance: float = 0.2
) -> List[SybilCluster]:
    """
    检测女巫攻击集群（协调投注行为）
    
    检测逻辑：
    - 在 10 秒窗口内
    - 多个钱包对同一市场
    - 押注相同方向（全是 YES 或全是 NO）
    - 交易规模相似
    
    Args:
        trades_df: 交易 DataFrame
        time_window_seconds: 时间窗口（秒）
        min_cluster_size: 最小集群大小
        size_tolerance: 交易规模容差（20%）
    
    Returns:
        女巫集群列表
    """
    if trades_df.empty:
        return []
    
    logger.info(f"🔍 开始女巫集群检测 (窗口: {time_window_seconds}秒)...")
    
    clusters: List[SybilCluster] = []
    
    trades_df = trades_df.copy()
    trades_df['timestamp_sec'] = trades_df['timestamp'].astype('int64') // 10**9
    trades_df['time_window'] = (trades_df['timestamp_sec'] // time_window_seconds) * time_window_seconds
    
    # 按市场、时间窗口、方向分组
    for (token_id, time_window, side), group in trades_df.groupby(['token_id', 'time_window', 'side']):
        if len(group) < min_cluster_size:
            continue
        
        # 获取唯一地址
        makers = set(group['maker'].unique())
        takers = set(group['taker'].unique())
        all_addresses = makers | takers
        
        if len(all_addresses) < min_cluster_size:
            continue
        
        # 检查交易规模是否相似
        sizes = group['size'].values
        mean_size = np.mean(sizes)
        
        if mean_size == 0:
            continue
        
        size_deviations = np.abs(sizes - mean_size) / mean_size
        similar_size_ratio = np.mean(size_deviations < size_tolerance)
        
        if similar_size_ratio < 0.6:  # 至少60%的交易规模相似
            continue
        
        # 计算置信度
        confidence = min(0.95, 0.5 + len(all_addresses) * 0.05 + similar_size_ratio * 0.2)
        
        # 创建集群
        cluster_id = hashlib.md5(
            f"{token_id}_{time_window}_{side}".encode()
        ).hexdigest()[:12]
        
        cluster = SybilCluster(
            cluster_id=cluster_id,
            addresses=list(all_addresses),
            market_id=token_id,
            side=side,
            trade_count=len(group),
            total_volume=group['volume'].sum(),
            win_rate=0.0,  # 需要后续计算
            time_window_seconds=time_window_seconds,
            confidence=confidence
        )
        clusters.append(cluster)
    
    # 合并相邻时间窗口的相似集群
    merged_clusters = _merge_adjacent_clusters(clusters)
    
    logger.info(f"✅ 女巫集群检测完成: 发现 {len(merged_clusters)} 个集群")
    return merged_clusters


def _merge_adjacent_clusters(clusters: List[SybilCluster]) -> List[SybilCluster]:
    """合并相邻时间窗口的相似集群"""
    if not clusters:
        return []
    
    # 按市场和方向分组
    by_market_side = defaultdict(list)
    for cluster in clusters:
        key = (cluster.market_id, cluster.side)
        by_market_side[key].append(cluster)
    
    merged = []
    for (market_id, side), market_clusters in by_market_side.items():
        # 按地址重叠合并
        while True:
            merged_any = False
            i = 0
            while i < len(market_clusters):
                j = i + 1
                while j < len(market_clusters):
                    # 检查地址重叠
                    addr_i = set(market_clusters[i].addresses)
                    addr_j = set(market_clusters[j].addresses)
                    overlap = len(addr_i & addr_j) / max(len(addr_i), len(addr_j))
                    
                    if overlap > 0.5:  # 超过50%重叠，合并
                        # 合并集群
                        market_clusters[i] = SybilCluster(
                            cluster_id=market_clusters[i].cluster_id,
                            addresses=list(addr_i | addr_j),
                            market_id=market_id,
                            side=side,
                            trade_count=market_clusters[i].trade_count + market_clusters[j].trade_count,
                            total_volume=market_clusters[i].total_volume + market_clusters[j].total_volume,
                            win_rate=0.0,
                            time_window_seconds=market_clusters[i].time_window_seconds,
                            confidence=max(market_clusters[i].confidence, market_clusters[j].confidence)
                        )
                        market_clusters.pop(j)
                        merged_any = True
                    else:
                        j += 1
                i += 1
            
            if not merged_any:
                break
        
        merged.extend(market_clusters)
    
    return merged


def sybil_clusters_to_evidence(clusters: List[SybilCluster]) -> List[WashTradeEvidence]:
    """将女巫集群转换为证据格式"""
    evidence_list = []
    
    for cluster in clusters:
        evidence = WashTradeEvidence(
            evidence_type="SYBIL_CLUSTER",
            tx_hash="",  # 多笔交易
            addresses=cluster.addresses,
            confidence=cluster.confidence,
            volume=cluster.total_volume,
            details={
                'cluster_id': cluster.cluster_id,
                'market_id': cluster.market_id,
                'side': cluster.side,
                'trade_count': cluster.trade_count,
                'address_count': len(cluster.addresses),
                'time_window_seconds': cluster.time_window_seconds,
            }
        )
        evidence_list.append(evidence)
    
    return evidence_list


# ============================================================================
# 6. 综合市场健康评估
# ============================================================================

class MarketForensicsReport:
    """
    市场取证报告生成器
    
    整合所有检测器，输出市场健康评分和证据列表
    """
    
    def __init__(self):
        self.detectors_enabled = {
            'self_trades': True,
            'circular_trades': HAS_NETWORKX,
            'atomic_wash': True,
            'volume_spikes': True,
            'sybil_clusters': True,
            'new_wallet_insider': True,
            'high_win_rate': True,
            'gas_anomaly': True,
        }
    
    def run_full_analysis(
        self,
        trades_df: pd.DataFrame,
        logs_df: Optional[pd.DataFrame] = None,
        news_timestamps: Optional[List[datetime]] = None
    ) -> Dict[str, any]:
        """
        运行完整的市场分析
        
        Args:
            trades_df: 交易 DataFrame
            logs_df: 日志 DataFrame（可选）
            news_timestamps: 新闻时间戳（可选）
        
        Returns:
            分析报告字典
        """
        logger.info("🚀 开始完整市场取证分析...")
        
        all_evidence: List[WashTradeEvidence] = []
        detector_results = {}
        
        # 1. 自交易检测
        if self.detectors_enabled['self_trades']:
            self_trade_evidence = detect_self_trades(trades_df)
            all_evidence.extend(self_trade_evidence)
            detector_results['self_trades'] = {
                'count': len(self_trade_evidence),
                'volume': sum(e.volume for e in self_trade_evidence)
            }
        
        # 2. 循环交易检测
        if self.detectors_enabled['circular_trades']:
            circular_paths = detect_circular_trades(trades_df)
            circular_evidence = circular_paths_to_evidence(circular_paths)
            all_evidence.extend(circular_evidence)
            detector_results['circular_trades'] = {
                'count': len(circular_paths),
                'volume': sum(p.total_volume for p in circular_paths)
            }
        
        # 3. 原子化刷量检测
        if self.detectors_enabled['atomic_wash']:
            atomic_evidence = detect_atomic_wash_patterns(trades_df, logs_df)
            all_evidence.extend(atomic_evidence)
            detector_results['atomic_wash'] = {
                'count': len(atomic_evidence),
                'volume': sum(e.volume for e in atomic_evidence)
            }
        
        # 4. 交易量异常检测
        if self.detectors_enabled['volume_spikes']:
            volume_spikes = detect_volume_spikes(trades_df, news_timestamps=news_timestamps)
            spike_evidence = volume_spikes_to_evidence(volume_spikes)
            all_evidence.extend(spike_evidence)
            detector_results['volume_spikes'] = {
                'count': len(volume_spikes),
                'volume': sum(s.spike_volume for s in volume_spikes)
            }
        
        # 5. 女巫集群检测
        if self.detectors_enabled['sybil_clusters']:
            sybil_clusters = detect_coordinated_clusters(trades_df)
            cluster_evidence = sybil_clusters_to_evidence(sybil_clusters)
            all_evidence.extend(cluster_evidence)
            detector_results['sybil_clusters'] = {
                'count': len(sybil_clusters),
                'volume': sum(c.total_volume for c in sybil_clusters),
                'addresses': sum(len(c.addresses) for c in sybil_clusters)
            }
        
        # 6-8. 导入之前的检测器
        try:
            from .analyzer import (
                detect_new_wallet_insider,
                get_flagged_traders,
                detect_gas_anomalies
            )
            
            if self.detectors_enabled['new_wallet_insider']:
                insider_flags = detect_new_wallet_insider(trades_df)
                for f in insider_flags:
                    all_evidence.append(WashTradeEvidence(
                        evidence_type="NEW_WALLET_INSIDER",
                        tx_hash=f.tx_hash,
                        addresses=[f.wallet_address],
                        confidence=f.confidence,
                        volume=f.details.get('trade_size', 0),
                        details=f.details
                    ))
                detector_results['new_wallet_insider'] = {
                    'count': len(insider_flags)
                }
            
            if self.detectors_enabled['high_win_rate']:
                winrate_flags = get_flagged_traders(trades_df)
                for f in winrate_flags:
                    all_evidence.append(WashTradeEvidence(
                        evidence_type="HIGH_WIN_RATE",
                        tx_hash=f.tx_hash,
                        addresses=[f.wallet_address],
                        confidence=f.confidence,
                        volume=0,
                        details=f.details
                    ))
                detector_results['high_win_rate'] = {
                    'count': len(winrate_flags)
                }
            
            if self.detectors_enabled['gas_anomaly']:
                gas_flags = detect_gas_anomalies(trades_df)
                for f in gas_flags:
                    all_evidence.append(WashTradeEvidence(
                        evidence_type="GAS_ANOMALY",
                        tx_hash=f.tx_hash,
                        addresses=[f.wallet_address],
                        confidence=f.confidence,
                        volume=f.details.get('size', 0),
                        details=f.details
                    ))
                detector_results['gas_anomaly'] = {
                    'count': len(gas_flags)
                }
                
        except ImportError as e:
            logger.warning(f"无法导入基础分析器: {e}")
        
        # 计算市场健康评分
        health_score = self._calculate_health_score(trades_df, all_evidence)
        risk_level = self._get_risk_level(health_score)
        
        # 生成报告
        report = {
            'health_score': health_score,
            'risk_level': risk_level,
            'total_trades': len(trades_df),
            'total_volume': trades_df['volume'].sum() if not trades_df.empty else 0,
            'evidence_count': len(all_evidence),
            'evidence_by_type': self._group_evidence_by_type(all_evidence),
            'detector_results': detector_results,
            'top_evidence': self._get_top_evidence(all_evidence, limit=20),
            'suspicious_addresses': self._get_suspicious_addresses(all_evidence),
            'timestamp': datetime.utcnow().isoformat(),
        }
        
        logger.info(f"✅ 市场取证分析完成: 健康评分 {health_score:.1f}/100, 风险等级 {risk_level}")
        
        return report
    
    def _calculate_health_score(
        self,
        trades_df: pd.DataFrame,
        evidence: List[WashTradeEvidence]
    ) -> float:
        """计算市场健康评分 (0-100)"""
        if trades_df.empty:
            return 100.0
        
        total_trades = len(trades_df)
        total_volume = trades_df['volume'].sum()
        
        # 基础分数 100
        score = 100.0
        
        # 根据证据扣分
        for e in evidence:
            penalty = 0
            
            if e.evidence_type == "SELF_TRADE_DIRECT":
                penalty = 5 * e.confidence
            elif e.evidence_type == "SELF_TRADE_COORDINATED":
                penalty = 3 * e.confidence
            elif e.evidence_type == "CIRCULAR_TRADE":
                penalty = 4 * e.confidence
            elif e.evidence_type == "ATOMIC_WASH":
                penalty = 6 * e.confidence
            elif e.evidence_type == "VOLUME_SPIKE":
                penalty = 2 * e.confidence if not e.details.get('is_correlated_with_event') else 0.5
            elif e.evidence_type == "SYBIL_CLUSTER":
                penalty = 5 * e.confidence
            elif e.evidence_type == "NEW_WALLET_INSIDER":
                penalty = 3 * e.confidence
            elif e.evidence_type == "HIGH_WIN_RATE":
                penalty = 2 * e.confidence
            elif e.evidence_type == "GAS_ANOMALY":
                penalty = 1 * e.confidence
            
            # 根据交易量比例调整惩罚
            if total_volume > 0 and e.volume > 0:
                volume_ratio = e.volume / total_volume
                penalty *= (1 + volume_ratio * 2)
            
            score -= penalty
        
        return max(0, min(100, score))
    
    def _get_risk_level(self, score: float) -> str:
        """根据健康评分确定风险等级"""
        if score >= 80:
            return "LOW"
        elif score >= 60:
            return "MEDIUM"
        elif score >= 40:
            return "HIGH"
        else:
            return "CRITICAL"
    
    def _group_evidence_by_type(
        self,
        evidence: List[WashTradeEvidence]
    ) -> Dict[str, int]:
        """按类型分组证据"""
        by_type = defaultdict(int)
        for e in evidence:
            by_type[e.evidence_type] += 1
        return dict(by_type)
    
    def _get_top_evidence(
        self,
        evidence: List[WashTradeEvidence],
        limit: int = 20
    ) -> List[Dict]:
        """获取置信度最高的证据"""
        sorted_evidence = sorted(evidence, key=lambda x: x.confidence, reverse=True)
        
        return [
            {
                'type': e.evidence_type,
                'tx_hash': e.tx_hash,
                'addresses': e.addresses[:5],  # 最多5个地址
                'confidence': e.confidence,
                'volume': e.volume,
                'details': e.details,
            }
            for e in sorted_evidence[:limit]
        ]
    
    def _get_suspicious_addresses(
        self,
        evidence: List[WashTradeEvidence]
    ) -> Dict[str, Dict]:
        """获取可疑地址汇总"""
        address_scores = defaultdict(lambda: {'count': 0, 'total_confidence': 0, 'types': set()})
        
        for e in evidence:
            for addr in e.addresses:
                addr_lower = addr.lower()
                address_scores[addr_lower]['count'] += 1
                address_scores[addr_lower]['total_confidence'] += e.confidence
                address_scores[addr_lower]['types'].add(e.evidence_type)
        
        # 转换格式并排序
        result = {}
        for addr, data in address_scores.items():
            avg_confidence = data['total_confidence'] / data['count'] if data['count'] > 0 else 0
            result[addr] = {
                'evidence_count': data['count'],
                'avg_confidence': avg_confidence,
                'evidence_types': list(data['types']),
                'risk_score': min(100, data['count'] * 10 + avg_confidence * 20)
            }
        
        # 按风险分数排序，返回前50
        sorted_addresses = sorted(result.items(), key=lambda x: x[1]['risk_score'], reverse=True)
        return dict(sorted_addresses[:50])


# ============================================================================
# 便捷函数
# ============================================================================

def run_market_forensics(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = 50000
) -> Dict:
    """
    运行完整市场取证分析的便捷函数
    
    Args:
        start_time: 开始时间
        end_time: 结束时间
        limit: 最大交易数
    
    Returns:
        分析报告
    """
    from .analyzer import load_trades_df
    
    trades_df = load_trades_df(start_time, end_time, limit)
    
    if trades_df.empty:
        return {
            'health_score': 100,
            'risk_level': 'LOW',
            'total_trades': 0,
            'evidence_count': 0,
            'message': 'No trades to analyze'
        }
    
    reporter = MarketForensicsReport()
    return reporter.run_full_analysis(trades_df)
