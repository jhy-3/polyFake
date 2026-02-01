"""
PolySleuth - 高级取证分析器

提供三种核心检测功能：
1. 新钱包内幕交易检测
2. 胜率与交易频率分析
3. Gas 异常（抢跑）检测
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

import pandas as pd
import numpy as np

from ..models import SessionLocal, TradeDB, MarketCacheDB

logger = logging.getLogger(__name__)


# ============================================================================
# 数据结构定义
# ============================================================================

@dataclass
class FlaggedTrade:
    """被标记的可疑交易"""
    tx_hash: str
    wallet_address: str
    flag_type: str
    confidence: float
    details: Dict


@dataclass
class TraderAnalysis:
    """交易者分析结果"""
    wallet_address: str
    win_rate: float
    total_trades: int
    trade_frequency: float  # trades per hour
    flagged: bool
    details: Dict


# ============================================================================
# 辅助函数
# ============================================================================

def load_trades_df(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = 100000
) -> pd.DataFrame:
    """
    从数据库加载交易数据到 DataFrame
    
    Args:
        start_time: 开始时间
        end_time: 结束时间
        limit: 最大记录数
    
    Returns:
        交易 DataFrame
    """
    db = SessionLocal()
    try:
        query = db.query(TradeDB)
        
        if start_time:
            query = query.filter(TradeDB.timestamp >= start_time)
        if end_time:
            query = query.filter(TradeDB.timestamp <= end_time)
        
        query = query.order_by(TradeDB.timestamp.desc()).limit(limit)
        trades = query.all()
        
        if not trades:
            return pd.DataFrame()
        
        # 转换为 DataFrame
        data = [{
            'tx_hash': t.tx_hash,
            'log_index': t.log_index,
            'block_number': t.block_number,
            'timestamp': t.timestamp,
            'contract': t.contract,
            'order_hash': t.order_hash,
            'maker': t.maker.lower(),
            'taker': t.taker.lower(),
            'token_id': t.token_id,
            'side': t.side,
            'price': t.price,
            'size': t.size,
            'volume': t.volume,
            'fee': t.fee,
            'is_wash': t.is_wash,
            'wash_type': t.wash_type,
            'wash_confidence': t.wash_confidence,
        } for t in trades]
        
        df = pd.DataFrame(data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df
        
    finally:
        db.close()


def load_markets_df() -> pd.DataFrame:
    """
    从数据库加载市场信息到 DataFrame
    
    Returns:
        市场信息 DataFrame
    """
    db = SessionLocal()
    try:
        markets = db.query(MarketCacheDB).all()
        
        if not markets:
            return pd.DataFrame()
        
        data = [{
            'token_id': m.token_id,
            'question': m.question,
            'slug': m.slug,
            'outcome': m.outcome,
            'condition_id': m.condition_id,
            'market_id': m.market_id,
            'updated_at': m.updated_at,
        } for m in markets]
        
        return pd.DataFrame(data)
        
    finally:
        db.close()


def get_wallet_first_trade_time(trades_df: pd.DataFrame) -> pd.DataFrame:
    """
    获取每个钱包的首次交易时间
    
    Args:
        trades_df: 交易 DataFrame
    
    Returns:
        钱包首次交易时间 DataFrame
    """
    # 合并 maker 和 taker 获取所有钱包
    maker_first = trades_df.groupby('maker')['timestamp'].min().reset_index()
    maker_first.columns = ['wallet', 'first_trade_time']
    
    taker_first = trades_df.groupby('taker')['timestamp'].min().reset_index()
    taker_first.columns = ['wallet', 'first_trade_time']
    
    # 合并并取最早时间
    all_wallets = pd.concat([maker_first, taker_first])
    wallet_first = all_wallets.groupby('wallet')['first_trade_time'].min().reset_index()
    
    return wallet_first


# ============================================================================
# 1. 新钱包内幕交易检测
# ============================================================================

def detect_new_wallet_insider(
    trades_df: pd.DataFrame,
    threshold_multiplier: float = 5.0,
    account_age_hours: int = 24
) -> List[FlaggedTrade]:
    """
    检测新钱包内幕交易
    
    识别逻辑：
    1. 钱包在首次交易时账龄 < 24小时（基于该钱包在数据集中的首次出现时间）
    2. 交易规模 > 该市场平均交易规模的 5 倍
    
    Args:
        trades_df: 交易 DataFrame，需包含 maker, taker, token_id, size, timestamp 列
        threshold_multiplier: 交易规模阈值倍数，默认 5 倍
        account_age_hours: 账龄阈值（小时），默认 24 小时
    
    Returns:
        被标记的可疑交易列表
    """
    if trades_df.empty:
        logger.warning("detect_new_wallet_insider: 输入 DataFrame 为空")
        return []
    
    logger.info(f"🔍 开始新钱包内幕交易检测 (阈值倍数: {threshold_multiplier}, 账龄: {account_age_hours}h)")
    
    flagged_trades: List[FlaggedTrade] = []
    
    # 1. 计算每个市场的平均交易规模
    market_avg_size = trades_df.groupby('token_id')['size'].mean().to_dict()
    
    # 2. 获取每个钱包的首次交易时间
    wallet_first_trade = get_wallet_first_trade_time(trades_df)
    wallet_first_dict = dict(zip(wallet_first_trade['wallet'], wallet_first_trade['first_trade_time']))
    
    # 3. 数据集的最早时间（用于判断"新钱包"）
    data_start_time = trades_df['timestamp'].min()
    age_threshold = timedelta(hours=account_age_hours)
    
    # 4. 遍历每笔交易进行检测
    for _, trade in trades_df.iterrows():
        tx_hash = trade['tx_hash']
        token_id = trade['token_id']
        trade_size = trade['size']
        trade_time = trade['timestamp']
        
        # 检查 maker 和 taker
        for wallet_col in ['maker', 'taker']:
            wallet = trade[wallet_col]
            first_trade_time = wallet_first_dict.get(wallet)
            
            if first_trade_time is None:
                continue
            
            # 计算账龄：钱包首次交易距离数据集开始的时间
            # 如果首次交易在数据集开始后很短时间内，视为"新钱包"
            wallet_age = first_trade_time - data_start_time
            is_new_wallet = wallet_age < age_threshold
            
            # 检查是否为该钱包的早期交易（首次交易后24小时内）
            is_early_trade = (trade_time - first_trade_time) < age_threshold
            
            if not (is_new_wallet and is_early_trade):
                continue
            
            # 检查交易规模
            avg_size = market_avg_size.get(token_id, 0)
            if avg_size <= 0:
                continue
            
            size_ratio = trade_size / avg_size
            
            if size_ratio > threshold_multiplier:
                confidence = min(0.95, 0.5 + (size_ratio - threshold_multiplier) * 0.05)
                
                flagged = FlaggedTrade(
                    tx_hash=tx_hash,
                    wallet_address=wallet,
                    flag_type="NEW_WALLET_INSIDER",
                    confidence=confidence,
                    details={
                        'wallet_age_hours': wallet_age.total_seconds() / 3600,
                        'trade_size': trade_size,
                        'market_avg_size': avg_size,
                        'size_ratio': size_ratio,
                        'token_id': token_id,
                        'trade_time': trade_time.isoformat(),
                        'first_trade_time': first_trade_time.isoformat(),
                    }
                )
                flagged_trades.append(flagged)
                logger.debug(f"⚠️ 新钱包内幕: {wallet[:10]}... 规模比: {size_ratio:.1f}x")
    
    logger.info(f"✅ 新钱包内幕检测完成: 发现 {len(flagged_trades)} 笔可疑交易")
    return flagged_trades


# ============================================================================
# 2. 胜率与交易频率分析
# ============================================================================

def analyze_trader_performance(
    trades_df: pd.DataFrame,
    markets_df: Optional[pd.DataFrame] = None,
    win_rate_threshold: float = 0.9,
    min_trades: int = 10,
    time_window_hours: int = 24
) -> List[TraderAnalysis]:
    """
    分析交易者的胜率和交易频率
    
    识别逻辑：
    1. 计算每个钱包的胜率：(正确预测数) / (总交易数)
    2. 计算交易频率：指定时间窗口内的交易数量
    3. 标记胜率 > 90% 且交易数 > 10 的钱包
    
    注意：由于市场结果数据可能不完整，胜率计算基于价格变动推断
    - 买入后价格上涨 或 卖出后价格下跌 视为"成功"
    
    Args:
        trades_df: 交易 DataFrame
        markets_df: 市场信息 DataFrame（可选，用于获取结果）
        win_rate_threshold: 胜率阈值，默认 90%
        min_trades: 最小交易数阈值，默认 10
        time_window_hours: 时间窗口（小时），默认 24
    
    Returns:
        交易者分析结果列表
    """
    if trades_df.empty:
        logger.warning("analyze_trader_performance: 输入 DataFrame 为空")
        return []
    
    logger.info(f"🔍 开始交易者胜率分析 (胜率阈值: {win_rate_threshold*100}%, 最小交易数: {min_trades})")
    
    results: List[TraderAnalysis] = []
    
    # 1. 收集所有钱包地址
    all_wallets = set(trades_df['maker'].unique()) | set(trades_df['taker'].unique())
    
    # 2. 按市场和时间排序，用于计算价格变动
    trades_sorted = trades_df.sort_values(['token_id', 'timestamp'])
    
    # 3. 计算每个市场的价格变动（用于推断胜率）
    # 创建价格变动列
    trades_sorted['next_price'] = trades_sorted.groupby('token_id')['price'].shift(-1)
    trades_sorted['price_change'] = trades_sorted['next_price'] - trades_sorted['price']
    
    # 4. 判断交易是否"成功"
    # BUY + 价格上涨 = 成功 | SELL + 价格下跌 = 成功
    def is_successful_trade(row):
        if pd.isna(row['price_change']):
            return None  # 无法判断
        if row['side'] == 'BUY' and row['price_change'] > 0:
            return True
        if row['side'] == 'SELL' and row['price_change'] < 0:
            return True
        return False
    
    trades_sorted['is_success'] = trades_sorted.apply(is_successful_trade, axis=1)
    
    # 5. 时间窗口计算
    time_window = timedelta(hours=time_window_hours)
    latest_time = trades_df['timestamp'].max()
    window_start = latest_time - time_window
    
    # 6. 分析每个钱包
    for wallet in all_wallets:
        # 获取该钱包的所有交易（作为 maker 或 taker）
        wallet_trades = trades_sorted[
            (trades_sorted['maker'] == wallet) | (trades_sorted['taker'] == wallet)
        ].copy()
        
        if wallet_trades.empty:
            continue
        
        total_trades = len(wallet_trades)
        
        # 计算时间窗口内的交易数
        recent_trades = wallet_trades[wallet_trades['timestamp'] >= window_start]
        recent_count = len(recent_trades)
        
        # 计算交易频率（每小时）
        if total_trades > 1:
            time_span = (wallet_trades['timestamp'].max() - wallet_trades['timestamp'].min())
            hours_span = max(time_span.total_seconds() / 3600, 1)
            trade_frequency = total_trades / hours_span
        else:
            trade_frequency = 0
        
        # 计算胜率
        successful_trades = wallet_trades['is_success'].sum()
        total_judged = wallet_trades['is_success'].notna().sum()
        
        if total_judged > 0:
            win_rate = successful_trades / total_judged
        else:
            win_rate = 0
        
        # 判断是否标记
        flagged = (
            win_rate >= win_rate_threshold and 
            recent_count >= min_trades
        )
        
        analysis = TraderAnalysis(
            wallet_address=wallet,
            win_rate=win_rate,
            total_trades=total_trades,
            trade_frequency=trade_frequency,
            flagged=flagged,
            details={
                'recent_trades_count': recent_count,
                'successful_trades': int(successful_trades) if not pd.isna(successful_trades) else 0,
                'total_judged_trades': int(total_judged),
                'time_window_hours': time_window_hours,
                'first_trade': wallet_trades['timestamp'].min().isoformat(),
                'last_trade': wallet_trades['timestamp'].max().isoformat(),
            }
        )
        results.append(analysis)
    
    # 7. 按胜率排序，返回标记的和高胜率的
    results.sort(key=lambda x: (x.flagged, x.win_rate), reverse=True)
    
    flagged_count = sum(1 for r in results if r.flagged)
    logger.info(f"✅ 交易者分析完成: 分析 {len(results)} 个钱包, 标记 {flagged_count} 个可疑")
    
    return results


def get_flagged_traders(
    trades_df: pd.DataFrame,
    win_rate_threshold: float = 0.9,
    min_trades: int = 10
) -> List[FlaggedTrade]:
    """
    获取被标记的高胜率交易者的交易记录
    
    Args:
        trades_df: 交易 DataFrame
        win_rate_threshold: 胜率阈值
        min_trades: 最小交易数
    
    Returns:
        被标记的交易列表
    """
    analyses = analyze_trader_performance(
        trades_df, 
        win_rate_threshold=win_rate_threshold,
        min_trades=min_trades
    )
    
    flagged_wallets = {a.wallet_address for a in analyses if a.flagged}
    
    flagged_trades: List[FlaggedTrade] = []
    
    for _, trade in trades_df.iterrows():
        for wallet_col in ['maker', 'taker']:
            wallet = trade[wallet_col]
            if wallet in flagged_wallets:
                # 找到对应的分析结果
                analysis = next((a for a in analyses if a.wallet_address == wallet), None)
                if analysis:
                    flagged_trades.append(FlaggedTrade(
                        tx_hash=trade['tx_hash'],
                        wallet_address=wallet,
                        flag_type="HIGH_WIN_RATE",
                        confidence=min(0.95, analysis.win_rate),
                        details={
                            'win_rate': analysis.win_rate,
                            'total_trades': analysis.total_trades,
                            'trade_frequency': analysis.trade_frequency,
                            **analysis.details
                        }
                    ))
    
    return flagged_trades


# ============================================================================
# 3. Gas 异常（抢跑）检测
# ============================================================================

def detect_gas_anomalies(
    trades_df: pd.DataFrame,
    gas_multiplier: float = 2.0,
    block_window: int = 10
) -> List[FlaggedTrade]:
    """
    检测 Gas 价格异常（潜在抢跑交易）
    
    识别逻辑：
    1. 计算每个区块（或 10 个区块窗口）内的 Gas 中位数
    2. 标记 Gas 价格 > 中位数 * 2 的交易
    
    注意：当前交易数据可能不包含 gas_price 字段，
    此函数需要额外的链上数据或使用 fee 字段作为代理
    
    Args:
        trades_df: 交易 DataFrame，需包含 block_number, fee 列
        gas_multiplier: Gas 阈值倍数，默认 2 倍
        block_window: 区块窗口大小，默认 10
    
    Returns:
        被标记的可疑交易列表
    """
    if trades_df.empty:
        logger.warning("detect_gas_anomalies: 输入 DataFrame 为空")
        return []
    
    logger.info(f"🔍 开始 Gas 异常检测 (倍数阈值: {gas_multiplier}, 区块窗口: {block_window})")
    
    flagged_trades: List[FlaggedTrade] = []
    
    # 使用 fee 作为 gas 的代理指标（实际 gas_price 需要从链上获取）
    # 如果数据中有 gas_price 列则使用它
    gas_column = 'gas_price' if 'gas_price' in trades_df.columns else 'fee'
    
    if gas_column not in trades_df.columns:
        logger.warning(f"detect_gas_anomalies: 数据中缺少 {gas_column} 列")
        return []
    
    # 1. 计算区块窗口
    trades_df = trades_df.copy()
    trades_df['block_window'] = (trades_df['block_number'] // block_window) * block_window
    
    # 2. 计算每个窗口的 Gas 中位数
    window_median = trades_df.groupby('block_window')[gas_column].median().to_dict()
    
    # 3. 检测异常
    for _, trade in trades_df.iterrows():
        block_win = trade['block_window']
        trade_gas = trade[gas_column]
        median_gas = window_median.get(block_win, 0)
        
        if median_gas <= 0:
            continue
        
        gas_ratio = trade_gas / median_gas
        
        if gas_ratio > gas_multiplier:
            confidence = min(0.95, 0.5 + (gas_ratio - gas_multiplier) * 0.1)
            
            # 检查 maker 和 taker
            for wallet_col in ['maker', 'taker']:
                wallet = trade[wallet_col]
                
                flagged = FlaggedTrade(
                    tx_hash=trade['tx_hash'],
                    wallet_address=wallet,
                    flag_type="GAS_ANOMALY_FRONTRUN",
                    confidence=confidence,
                    details={
                        'gas_value': trade_gas,
                        'median_gas': median_gas,
                        'gas_ratio': gas_ratio,
                        'block_number': trade['block_number'],
                        'block_window': block_win,
                        'token_id': trade['token_id'],
                        'side': trade['side'],
                        'size': trade['size'],
                    }
                )
                flagged_trades.append(flagged)
    
    # 去重（同一笔交易可能标记了 maker 和 taker）
    seen = set()
    unique_flagged = []
    for f in flagged_trades:
        key = (f.tx_hash, f.wallet_address)
        if key not in seen:
            seen.add(key)
            unique_flagged.append(f)
    
    logger.info(f"✅ Gas 异常检测完成: 发现 {len(unique_flagged)} 笔可疑交易")
    return unique_flagged


# ============================================================================
# 综合分析接口
# ============================================================================

def run_full_forensic_analysis(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = 50000
) -> Dict[str, List[FlaggedTrade]]:
    """
    运行完整的取证分析
    
    Args:
        start_time: 开始时间
        end_time: 结束时间
        limit: 最大交易数
    
    Returns:
        按检测类型分组的标记交易
    """
    logger.info("🚀 开始完整取证分析...")
    
    # 加载数据
    trades_df = load_trades_df(start_time, end_time, limit)
    
    if trades_df.empty:
        logger.warning("无交易数据可分析")
        return {
            'new_wallet_insider': [],
            'high_win_rate': [],
            'gas_anomaly': [],
        }
    
    logger.info(f"📊 加载 {len(trades_df)} 笔交易进行分析")
    
    # 运行三种检测
    results = {
        'new_wallet_insider': detect_new_wallet_insider(trades_df),
        'high_win_rate': get_flagged_traders(trades_df),
        'gas_anomaly': detect_gas_anomalies(trades_df),
    }
    
    # 统计
    total_flagged = sum(len(v) for v in results.values())
    logger.info(f"✅ 取证分析完成: 共标记 {total_flagged} 笔可疑交易")
    
    return results


def get_flagged_summary(results: Dict[str, List[FlaggedTrade]]) -> pd.DataFrame:
    """
    将标记结果转换为汇总 DataFrame
    
    Args:
        results: run_full_forensic_analysis 的返回结果
    
    Returns:
        汇总 DataFrame
    """
    all_flagged = []
    
    for flag_type, trades in results.items():
        for trade in trades:
            all_flagged.append({
                'tx_hash': trade.tx_hash,
                'wallet_address': trade.wallet_address,
                'flag_type': trade.flag_type,
                'confidence': trade.confidence,
                **trade.details
            })
    
    if not all_flagged:
        return pd.DataFrame()
    
    df = pd.DataFrame(all_flagged)
    
    # 按置信度排序
    df = df.sort_values('confidence', ascending=False)
    
    return df
