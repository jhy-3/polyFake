"""
PolySleuth - 取证服务

负责：
- 链上数据获取
- 实时刷量检测
- 流式监控
- 全部安全分析自动运行
"""
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable
from decimal import Decimal
import logging
import requests
from collections import defaultdict

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from ..config import (
    POLYGON_RPC_URL, CTF_EXCHANGE, NEG_RISK_EXCHANGE,
    GAMMA_API_URL, POLL_INTERVAL, BLOCKS_PER_POLL
)
from .storage import get_data_store, MemoryTrade, MemoryAlert

logger = logging.getLogger(__name__)

# 事件签名
ORDER_FILLED_TOPIC = Web3.keccak(text="OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)").hex()


class ForensicsService:
    """取证分析服务 - 实时安全检测"""
    
    def __init__(self, rpc_url: str = POLYGON_RPC_URL):
        self.rpc_url = rpc_url
        self.w3: Optional[Web3] = None
        self.store = get_data_store()
        
        # 流式监控
        self._streaming = False
        self._stream_thread: Optional[threading.Thread] = None
        self._callbacks: List[Callable] = []
        
        # 实时分析统计（每种检测类型的计数）
        self._analysis_stats = {
            'insider': 0,           # 新钱包内幕
            'high_winrate': 0,      # 高胜率
            'gas_anomaly': 0,       # Gas异常
            'self_trade': 0,        # 自交易
            'circular': 0,          # 循环交易
            'atomic': 0,            # 原子刷量
            'sybil': 0,             # 女巫集群
            'volume_spike': 0,      # 交易量异常
        }
        self._analysis_lock = threading.Lock()
        
        # 用于高级检测的缓存
        self._recent_trades_cache = []  # 最近交易缓存（用于模式检测）
        self._wallet_first_trade = {}   # 钱包首次交易时间
        self._wallet_trade_stats = defaultdict(lambda: {'wins': 0, 'total': 0, 'volume': 0})
        self._market_volume_bins = defaultdict(lambda: defaultdict(float))  # 市场交易量分箱
        
        # 缓存
        self._block_timestamps: Dict[int, datetime] = {}
        self._market_map_thread: Optional[threading.Thread] = None
        self._connect_thread: Optional[threading.Thread] = None
        
        # 后台连接节点（避免阻塞启动）
        self._connect_thread = threading.Thread(
            target=self._connect,
            daemon=True,
        )
        self._connect_thread.start()

        # 后台加载市场映射（避免阻塞启动）
        self._market_map_thread = threading.Thread(
            target=self._load_market_map,
            daemon=True,
        )
        self._market_map_thread.start()
    
    def _connect(self):
        """连接到 Polygon 节点"""
        try:
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            
            if self.w3.is_connected():
                chain_id = self.w3.eth.chain_id
                block = self.w3.eth.block_number
                logger.info(f"✅ 已连接到 Polygon (Chain ID: {chain_id}, Block: {block})")
                return True
            else:
                logger.error("❌ 无法连接到 Polygon 节点")
                return False
        except Exception as e:
            logger.error(f"❌ 连接失败: {e}")
            self.w3 = None
            return False
    
    def _load_market_map(self):
        """加载市场名称映射（只获取活跃市场）"""
        all_markets = []
        try:
            # 只获取活跃市场
            logger.info("📡 开始获取活跃市场...")
            offset = 0
            page_size = 500
            max_markets = 3000
            retry_count = 0
            max_retries = 3
            
            while offset < max_markets:
                try:
                    resp = requests.get(
                        f"{GAMMA_API_URL}/markets",
                        params={'active': 'true', 'limit': page_size, 'offset': offset, 'closed': 'false'},
                        timeout=30
                    )
                    resp.raise_for_status()
                    markets = resp.json()
                    
                    if not markets:
                        break
                    
                    all_markets.extend(markets)
                    logger.info(f"  ✓ 活跃市场第 {offset//page_size + 1} 页: {len(markets)} 个")
                    
                    if len(markets) < page_size:
                        break
                    
                    offset += page_size
                    retry_count = 0
                    
                except Exception as e:
                    retry_count += 1
                    if retry_count >= max_retries:
                        logger.warning(f"获取活跃市场第 {offset//page_size + 1} 页失败（已重试{max_retries}次）: {e}")
                        break
                    logger.warning(f"获取活跃市场第 {offset//page_size + 1} 页失败，重试 {retry_count}/{max_retries}: {e}")
                    import time
                    time.sleep(2)
            
            logger.info(f"✅ 总共获取到 {len(all_markets)} 个活跃市场")
            
            count = 0
            event_count = 0
            for market in all_markets:
                question = market.get('question', '')
                # 重要：Polymarket URL 需要 event slug，不是 market slug
                # event slug 在 events[0].slug 中，market.slug 是 market slug（无法打开页面）
                events = market.get('events', [])
                event_slug = events[0].get('slug', '') if events else ''
                slug = event_slug or market.get('slug', '')  # 优先使用 event slug
                condition_id = market.get('conditionId', '')
                
                # 从 clobTokenIds 获取（新格式）
                clob_token_ids = market.get('clobTokenIds', '')
                if clob_token_ids:
                    import json
                    try:
                        token_ids = json.loads(clob_token_ids) if isinstance(clob_token_ids, str) else clob_token_ids
                    except Exception as e:
                        logger.warning(f"解析 clobTokenIds 失败: {e}")
                        token_ids = []
                    
                    outcomes_str = market.get('outcomes', '')
                    try:
                        outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
                    except:
                        outcomes = ['YES', 'NO']
                    
                    # 获取 event ID（Polymarket URL 使用）
                    market_id = (
                        market.get('eventId', '')
                        or market.get('event_id', '')
                        or market.get('id', '')
                        or market.get('marketId', '')
                    )
                    
                    # 为每个 token 存储信息
                    for idx, tid in enumerate(token_ids):
                        if tid:
                            outcome = outcomes[idx] if idx < len(outcomes) else f'Outcome {idx}'
                            self.store.cache_market(str(tid), {
                                'question': question,
                                'slug': slug,
                                'outcome': outcome,
                                'condition_id': condition_id,
                                'market_id': market_id,
                            })
                            count += 1
                    
                    # 建立 slug 到 token_ids 和 condition_id 的映射
                    if slug:
                        self.store.cache_market_event(slug, {
                            'question': question,
                            'slug': slug,
                            'condition_id': condition_id,
                            'token_ids': [str(tid) for tid in token_ids if tid],
                            'market_id': market_id,
                        })
                        event_count += 1
                
                # 从 tokens 获取（旧格式，作为后备）
                tokens = market.get('tokens', [])
                token_ids_old = []
                market_id = (
                    market.get('eventId', '')
                    or market.get('event_id', '')
                    or market.get('id', '')
                    or market.get('marketId', '')
                )
                for token in tokens:
                    tid = str(token.get('token_id', ''))
                    outcome = token.get('outcome', '').upper()
                    if tid:
                        self.store.cache_market(tid, {
                            'question': question,
                            'slug': slug,
                            'outcome': outcome,
                            'condition_id': condition_id,
                            'market_id': market_id,
                        })
                        token_ids_old.append(tid)
                        count += 1
                
                # 为旧格式也建立 event 映射
                if slug and token_ids_old:
                    self.store.cache_market_event(slug, {
                        'question': question,
                        'slug': slug,
                        'condition_id': condition_id,
                        'token_ids': token_ids_old,
                        'market_id': market_id,
                    })
                    event_count += 1
            
            logger.info(f"✅ 加载 {count} 个市场映射，{event_count} 个事件映射")
        except Exception as e:
            logger.warning(f"加载市场映射失败: {e}")
    
    def is_connected(self) -> bool:
        """检查连接状态"""
        return self.w3 is not None and self.w3.is_connected()
    
    def get_current_block(self) -> int:
        """获取当前区块"""
        if self.w3:
            return self.w3.eth.block_number
        return 0
    
    def get_analysis_stats(self) -> Dict[str, int]:
        """获取实时分析统计"""
        with self._analysis_lock:
            return dict(self._analysis_stats)
    
    def _increment_analysis_stat(self, stat_type: str, count: int = 1):
        """增加分析统计计数"""
        with self._analysis_lock:
            if stat_type in self._analysis_stats:
                self._analysis_stats[stat_type] += count
    
    # ========================================================================
    # 实时安全分析（对每笔交易自动运行）
    # ========================================================================
    
    def analyze_trade_realtime(self, trade: MemoryTrade) -> Dict[str, any]:
        """
        实时分析单笔交易，运行全部安全检测
        
        返回检测结果字典
        """
        results = {
            'is_suspicious': False,
            'detections': [],
            'analysis_types': []
        }
        
        # 1. 自交易检测 (maker == taker)
        if trade.maker.lower() == trade.taker.lower():
            results['is_suspicious'] = True
            results['detections'].append('SELF_TRADE')
            results['analysis_types'].append('self_trade')
            self._increment_analysis_stat('self_trade')
            
            # 标记并创建警报
            self.store.mark_wash_trade(
                trade.tx_hash, trade.log_index,
                "SELF_TRADE", 1.0
            )
            alert = MemoryAlert(
                alert_id=f"SELF_{trade.tx_hash[:16]}_{trade.log_index}",
                timestamp=trade.timestamp,
                alert_type="SELF_TRADE",
                severity="HIGH",
                tx_hash=trade.tx_hash,
                token_id=trade.token_id,
                trade_count=1,
                volume=trade.volume,
                confidence=1.0,
                addresses=[trade.maker],
            )
            self.store.add_alert(alert)
        
        # 2. 新钱包内幕检测
        insider_result = self._check_new_wallet_insider(trade)
        if insider_result:
            results['is_suspicious'] = True
            results['detections'].append('NEW_WALLET_INSIDER')
            results['analysis_types'].append('insider')
            self._increment_analysis_stat('insider')
            
            alert = MemoryAlert(
                alert_id=f"INSIDER_{trade.tx_hash[:16]}",
                timestamp=trade.timestamp,
                alert_type="NEW_WALLET_INSIDER",
                severity="HIGH",
                tx_hash=trade.tx_hash,
                token_id=trade.token_id,
                trade_count=1,
                volume=trade.volume,
                confidence=insider_result['confidence'],
                addresses=[insider_result['wallet']],
            )
            self.store.add_alert(alert)
        
        # 3. 循环交易检测（与最近交易对比）
        circular_result = self._check_circular_trade(trade)
        if circular_result:
            results['is_suspicious'] = True
            results['detections'].append('CIRCULAR_TRADE')
            results['analysis_types'].append('circular')
            self._increment_analysis_stat('circular')
            
            self.store.mark_wash_trade(
                trade.tx_hash, trade.log_index,
                "CIRCULAR", 0.85
            )
            alert = MemoryAlert(
                alert_id=f"CIRC_{trade.tx_hash[:8]}",
                timestamp=trade.timestamp,
                alert_type="CIRCULAR_TRADE",
                severity="MEDIUM",
                tx_hash=trade.tx_hash,
                token_id=trade.token_id,
                trade_count=2,
                volume=trade.volume + circular_result.get('paired_volume', 0),
                confidence=0.85,
                addresses=[trade.maker, trade.taker],
            )
            self.store.add_alert(alert)
        
        # 4. 原子刷量检测（同区块买卖对冲）
        atomic_result = self._check_atomic_wash(trade)
        if atomic_result:
            results['is_suspicious'] = True
            results['detections'].append('ATOMIC_WASH')
            results['analysis_types'].append('atomic')
            self._increment_analysis_stat('atomic')
            
            self.store.mark_wash_trade(
                trade.tx_hash, trade.log_index,
                "ATOMIC_WASH", 0.9
            )
            alert = MemoryAlert(
                alert_id=f"ATOMIC_{trade.tx_hash[:16]}",
                timestamp=trade.timestamp,
                alert_type="ATOMIC_WASH",
                severity="HIGH",
                tx_hash=trade.tx_hash,
                token_id=trade.token_id,
                trade_count=1,
                volume=trade.volume,
                confidence=0.9,
                addresses=[trade.maker],
            )
            self.store.add_alert(alert)
        
        # 5. 女巫集群检测（短时间内多钱包同向投注）
        sybil_result = self._check_sybil_cluster(trade)
        if sybil_result:
            results['is_suspicious'] = True
            results['detections'].append('SYBIL_CLUSTER')
            results['analysis_types'].append('sybil')
            self._increment_analysis_stat('sybil')
            
            alert = MemoryAlert(
                alert_id=f"SYBIL_{trade.token_id[:8]}_{int(trade.timestamp.timestamp())}",
                timestamp=trade.timestamp,
                alert_type="SYBIL_CLUSTER",
                severity="MEDIUM",
                tx_hash=trade.tx_hash,
                token_id=trade.token_id,
                trade_count=sybil_result['cluster_size'],
                volume=sybil_result['total_volume'],
                confidence=sybil_result['confidence'],
                addresses=sybil_result['addresses'],
            )
            self.store.add_alert(alert)
        
        # 6. 交易量异常检测
        spike_result = self._check_volume_spike(trade)
        if spike_result:
            results['is_suspicious'] = True
            results['detections'].append('VOLUME_SPIKE')
            results['analysis_types'].append('volume_spike')
            self._increment_analysis_stat('volume_spike')
            
            alert = MemoryAlert(
                alert_id=f"SPIKE_{trade.token_id[:8]}_{int(trade.timestamp.timestamp())}",
                timestamp=trade.timestamp,
                alert_type="VOLUME_SPIKE",
                severity="MEDIUM",
                tx_hash=trade.tx_hash,
                token_id=trade.token_id,
                trade_count=1,
                volume=spike_result['spike_volume'],
                confidence=spike_result['confidence'],
                addresses=[],
            )
            self.store.add_alert(alert)
        
        # 更新缓存
        self._update_trade_caches(trade)
        
        return results
    
    def _check_new_wallet_insider(self, trade: MemoryTrade) -> Optional[Dict]:
        """
        检测新钱包内幕交易
        
        真正可疑的特征：
        1. 钱包在系统中首次出现就进行超大额交易（>$1000）
        2. 钱包账龄很短（<24h）就进行大额交易（>$2000）
        
        注意：需要足够的缓存数据才能准确判断，否则所有钱包都会被误判为"新钱包"
        """
        # 需要至少累积100笔交易才开始检测，避免启动时误报
        if len(self._recent_trades_cache) < 100:
            # 仍然记录钱包首次交易时间，但不触发警报
            for wallet in [trade.maker.lower(), trade.taker.lower()]:
                if wallet not in self._wallet_first_trade:
                    self._wallet_first_trade[wallet] = trade.timestamp
            return None
        
        for wallet in [trade.maker.lower(), trade.taker.lower()]:
            # 检查是否是新钱包
            if wallet not in self._wallet_first_trade:
                self._wallet_first_trade[wallet] = trade.timestamp
                
                # 新钱包超大额交易（>$1000）才触发
                if trade.volume > 1000:
                    return {
                        'wallet': wallet,
                        'confidence': min(0.9, 0.5 + trade.volume / 5000),
                        'volume': trade.volume
                    }
            else:
                # 检查账龄
                first_trade_time = self._wallet_first_trade[wallet]
                age_hours = (trade.timestamp - first_trade_time).total_seconds() / 3600
                
                # 账龄<24h 且 超大额交易（>$2000）
                if age_hours < 24 and trade.volume > 2000:
                    return {
                        'wallet': wallet,
                        'confidence': min(0.85, 0.4 + trade.volume / 10000),
                        'volume': trade.volume,
                        'age_hours': age_hours
                    }
        
        return None
    
    def _check_circular_trade(self, trade: MemoryTrade) -> Optional[Dict]:
        """检测循环交易（A→B, B→A 模式）"""
        # 查找最近60秒内的反向交易
        cutoff_time = trade.timestamp - timedelta(seconds=60)
        
        for recent in reversed(self._recent_trades_cache[-500:]):
            if recent.timestamp < cutoff_time:
                break
            
            # 检测 A→B, B→A 模式
            if (recent.token_id == trade.token_id and
                recent.taker.lower() == trade.maker.lower() and
                recent.maker.lower() == trade.taker.lower()):
                
                # 标记配对交易
                self.store.mark_wash_trade(
                    recent.tx_hash, recent.log_index,
                    "CIRCULAR", 0.85
                )
                
                return {
                    'paired_tx': recent.tx_hash,
                    'paired_volume': recent.volume
                }
        
        return None
    
    def _check_atomic_wash(self, trade: MemoryTrade) -> Optional[Dict]:
        """检测原子刷量（同区块买卖对冲）"""
        # 查找同区块、同地址的反向交易
        for recent in reversed(self._recent_trades_cache[-100:]):
            if recent.block_number != trade.block_number:
                continue
            
            if (recent.maker.lower() == trade.maker.lower() and
                recent.token_id == trade.token_id and
                recent.side != trade.side):
                
                # 检查交易量是否相近
                volume_ratio = min(recent.volume, trade.volume) / max(recent.volume, trade.volume) if max(recent.volume, trade.volume) > 0 else 0
                
                if volume_ratio > 0.8:
                    return {
                        'paired_tx': recent.tx_hash,
                        'volume_ratio': volume_ratio
                    }
        
        return None
    
    def _check_sybil_cluster(self, trade: MemoryTrade) -> Optional[Dict]:
        """
        检测女巫集群（短时间内多钱包协同投注）
        
        真正的女巫攻击特征：
        1. 极短时间窗口内（5秒内）大量不同地址同向操作
        2. 交易金额高度相似（可能是脚本批量执行）
        3. 参与地址数量异常多（>=5个不同地址）
        4. 总交易量较大（排除小额正常交易）
        """
        # 查找5秒窗口内同市场同方向的交易（更严格的时间窗口）
        cutoff_time = trade.timestamp - timedelta(seconds=5)
        cluster_trades = [trade]
        
        for recent in reversed(self._recent_trades_cache[-200:]):
            if recent.timestamp < cutoff_time:
                break
            
            if (recent.token_id == trade.token_id and
                recent.side == trade.side and
                recent.maker.lower() != trade.maker.lower()):
                
                # 检查交易规模是否高度相似（可能是脚本批量下单）
                if trade.volume > 0 and recent.volume > 0:
                    size_ratio = min(recent.volume, trade.volume) / max(recent.volume, trade.volume)
                    # 更严格：交易金额相似度需要>85%
                    if size_ratio > 0.85:
                        cluster_trades.append(recent)
        
        # 必须满足更严格的条件
        addresses = list(set(t.maker.lower() for t in cluster_trades))
        total_volume = sum(t.volume for t in cluster_trades)
        
        # 条件：至少5个不同地址 且 总交易量>$50（排除小额正常交易）
        if len(addresses) >= 5 and total_volume >= 50:
            return {
                'cluster_size': len(addresses),
                'addresses': addresses[:10],
                'total_volume': total_volume,
                'confidence': min(0.9, 0.5 + len(addresses) * 0.08)
            }
        
        return None
    
    def _check_volume_spike(self, trade: MemoryTrade) -> Optional[Dict]:
        """检测交易量异常"""
        # 5分钟分箱
        bin_key = trade.timestamp.strftime('%Y%m%d%H') + str(trade.timestamp.minute // 5)
        market_id = trade.token_id
        
        # 更新当前分箱
        self._market_volume_bins[market_id][bin_key] += trade.volume
        current_volume = self._market_volume_bins[market_id][bin_key]
        
        # 计算过去1小时的平均值
        volumes = list(self._market_volume_bins[market_id].values())
        if len(volumes) >= 6:  # 至少有30分钟数据
            baseline = sum(volumes[:-1]) / (len(volumes) - 1)
            
            if baseline > 0 and current_volume > baseline * 10:
                return {
                    'spike_volume': current_volume,
                    'baseline_volume': baseline,
                    'spike_ratio': current_volume / baseline,
                    'confidence': min(0.85, 0.5 + (current_volume / baseline - 10) * 0.02)
                }
        
        return None
    
    def _update_trade_caches(self, trade: MemoryTrade):
        """更新交易缓存"""
        self._recent_trades_cache.append(trade)
        
        # 保持缓存大小
        if len(self._recent_trades_cache) > 2000:
            self._recent_trades_cache = self._recent_trades_cache[-1500:]
        
        # 清理过期的 volume bins（保留最近2小时）
        cutoff = (trade.timestamp - timedelta(hours=2)).strftime('%Y%m%d%H')
        for market_id in list(self._market_volume_bins.keys()):
            bins = self._market_volume_bins[market_id]
            for bin_key in list(bins.keys()):
                if bin_key < cutoff:
                    del bins[bin_key]
    
    # ========================================================================
    # 数据获取
    # ========================================================================
    
    def fetch_recent_trades(self, num_blocks: int = 100) -> int:
        """获取最近交易并自动分析"""
        if not self.is_connected():
            logger.error("节点未连接")
            return 0
        
        try:
            current_block = self.w3.eth.block_number
            from_block = current_block - num_blocks
            
            logger.info(f"📡 获取区块 {from_block} 到 {current_block} 的交易...")
            
            # 获取两个交易所的日志
            trades_count = 0
            suspicious_count = 0
            
            for exchange_addr in [CTF_EXCHANGE, NEG_RISK_EXCHANGE]:
                logs = self.w3.eth.get_logs({
                    'fromBlock': from_block,
                    'toBlock': current_block,
                    'address': Web3.to_checksum_address(exchange_addr),
                    'topics': [ORDER_FILLED_TOPIC],
                })
                
                for log in logs:
                    trade = self._decode_order_filled(log, exchange_addr)
                    if trade:
                        self.store.add_trade(trade, notify=False)
                        trades_count += 1
                        
                        # 实时分析每笔交易
                        analysis = self.analyze_trade_realtime(trade)
                        if analysis['is_suspicious']:
                            suspicious_count += 1
            
            logger.info(f"✅ 获取 {trades_count} 笔交易，检测到 {suspicious_count} 笔可疑")
            
            # 通知前端更新分析统计
            self._notify_analysis_stats()
            
            return trades_count
        
        except Exception as e:
            logger.error(f"获取交易失败: {e}")
            return 0
    
    def _notify_analysis_stats(self):
        """通知前端更新分析统计"""
        stats = self.get_analysis_stats()
        
        # 计算健康评分
        total_suspicious = sum(stats.values())
        total_trades = len(self._recent_trades_cache)
        
        if total_trades > 0:
            suspicious_ratio = total_suspicious / total_trades
            health_score = max(0, min(100, 100 - suspicious_ratio * 200))
        else:
            health_score = 100
        
        risk_level = 'LOW'
        if health_score < 40:
            risk_level = 'CRITICAL'
        elif health_score < 60:
            risk_level = 'HIGH'
        elif health_score < 80:
            risk_level = 'MEDIUM'
        
        self.store._notify_ws('analysis_stats', {
            'stats': stats,
            'health_score': health_score,
            'risk_level': risk_level,
            'total_evidence': total_suspicious,
        })
    
    def _decode_order_filled(self, log, exchange: str) -> Optional[MemoryTrade]:
        """解码 OrderFilled 事件"""
        try:
            topics = log['topics']
            data = log['data']
            
            # 解码 topics
            order_hash = topics[1].hex() if len(topics) > 1 else ""
            maker = "0x" + topics[2].hex()[-40:] if len(topics) > 2 else ""
            taker = "0x" + topics[3].hex()[-40:] if len(topics) > 3 else ""
            
            # 解码 data (5 个 uint256: makerAssetId, takerAssetId, makerAmountFilled, takerAmountFilled, fee)
            if isinstance(data, str):
                data = bytes.fromhex(data[2:])
            
            values = []
            for i in range(5):
                offset = i * 32
                if offset + 32 <= len(data):
                    values.append(int.from_bytes(data[offset:offset+32], 'big'))
                else:
                    values.append(0)
            
            maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee = values
            
            # 计算交易方向和价格
            if maker_asset_id == 0:
                side = "BUY"
                token_id = str(taker_asset_id)
                usdc_amount = maker_amount
                token_amount = taker_amount
            else:
                side = "SELL"
                token_id = str(maker_asset_id)
                usdc_amount = taker_amount
                token_amount = maker_amount
            
            price = usdc_amount / token_amount if token_amount > 0 else 0
            size = token_amount / 1e6
            volume = size * price
            
            # 获取区块时间
            block_number = log['blockNumber']
            timestamp = self._get_block_timestamp(block_number)
            
            return MemoryTrade(
                tx_hash=log['transactionHash'].hex(),
                log_index=log['logIndex'],
                block_number=block_number,
                timestamp=timestamp,
                contract=exchange,
                order_hash=order_hash,
                maker=maker,
                taker=taker,
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                volume=volume,
                fee=fee,
            )
        
        except Exception as e:
            logger.debug(f"解码失败: {e}")
            return None
    
    def _get_block_timestamp(self, block_number: int) -> datetime:
        """获取区块时间"""
        if block_number in self._block_timestamps:
            return self._block_timestamps[block_number]
        
        now = datetime.now()
        try:
            current_block = self.w3.eth.block_number
            seconds_ago = (current_block - block_number) * 2
            self._block_timestamps[block_number] = now - timedelta(seconds=seconds_ago)
        except:
            self._block_timestamps[block_number] = now
        
        return self._block_timestamps[block_number]
    
    # ========================================================================
    # 刷量检测
    # ========================================================================
    
    def detect_self_trades(self):
        """检测自成交"""
        trades = self.store.get_trades(limit=10000, is_wash=False)
        detected = 0
        
        for trade in trades:
            if trade.maker.lower() == trade.taker.lower():
                self.store.mark_wash_trade(
                    trade.tx_hash, trade.log_index,
                    "SELF_TRADE", 1.0
                )
                
                # 添加警报
                alert = MemoryAlert(
                    alert_id=f"SELF_{trade.tx_hash[:16]}_{trade.log_index}",
                    timestamp=trade.timestamp,
                    alert_type="SELF_TRADE",
                    severity="HIGH",
                    tx_hash=trade.tx_hash,
                    token_id=trade.token_id,
                    trade_count=1,
                    volume=trade.volume,
                    confidence=1.0,
                    addresses=[trade.maker],
                )
                self.store.add_alert(alert)
                detected += 1
        
        if detected:
            logger.info(f"🔴 检测到 {detected} 笔自成交")
    
    def detect_circular_trades(self, time_window: int = 60):
        """检测环形交易"""
        trades = self.store.get_trades(limit=10000, is_wash=False)
        
        # 按时间排序
        sorted_trades = sorted(trades, key=lambda t: t.timestamp)
        detected = 0
        
        for i, trade in enumerate(sorted_trades):
            if trade.is_wash:
                continue
            
            for j in range(i + 1, len(sorted_trades)):
                later = sorted_trades[j]
                
                time_diff = (later.timestamp - trade.timestamp).total_seconds()
                if time_diff > time_window:
                    break
                
                # 检测 A→B, B→A 模式
                if (trade.taker.lower() == later.maker.lower() and
                    trade.maker.lower() == later.taker.lower() and
                    trade.token_id == later.token_id):
                    
                    self.store.mark_wash_trade(
                        trade.tx_hash, trade.log_index,
                        "CIRCULAR", 0.85
                    )
                    self.store.mark_wash_trade(
                        later.tx_hash, later.log_index,
                        "CIRCULAR", 0.85
                    )
                    
                    alert = MemoryAlert(
                        alert_id=f"CIRC_{trade.tx_hash[:8]}_{later.tx_hash[:8]}",
                        timestamp=trade.timestamp,
                        alert_type="CIRCULAR_TRADE",
                        severity="MEDIUM",
                        tx_hash=trade.tx_hash,
                        token_id=trade.token_id,
                        trade_count=2,
                        volume=trade.volume + later.volume,
                        confidence=0.85,
                        addresses=[trade.maker, trade.taker],
                    )
                    self.store.add_alert(alert)
                    detected += 1
        
        if detected:
            logger.info(f"🟠 检测到 {detected} 组环形交易")
    
    # ========================================================================
    # 流式监控
    # ========================================================================
    
    def start_streaming(self, poll_interval: float = POLL_INTERVAL,
                       blocks_per_poll: int = BLOCKS_PER_POLL):
        """启动流式监控"""
        if self._streaming:
            return
        
        self._streaming = True
        self._stream_thread = threading.Thread(
            target=self._stream_loop,
            args=(poll_interval, blocks_per_poll),
            daemon=True
        )
        self._stream_thread.start()
        logger.info(f"📺 流式监控已启动 (间隔: {poll_interval}s, 每次: {blocks_per_poll} 区块)")
    
    def stop_streaming(self):
        """停止流式监控"""
        self._streaming = False
        if self._stream_thread:
            self._stream_thread.join(timeout=5)
        logger.info("📺 流式监控已停止")
    
    def is_streaming(self) -> bool:
        """是否正在流式监控"""
        return self._streaming
    
    def _stream_loop(self, poll_interval: float, blocks_per_poll: int):
        """流式监控循环 - 实时获取并分析每笔交易"""
        last_block = self.get_current_block()
        
        while self._streaming:
            time.sleep(poll_interval)
            
            try:
                current_block = self.get_current_block()
                
                if current_block > last_block:
                    new_blocks = min(current_block - last_block, blocks_per_poll)
                    from_block = current_block - new_blocks
                    
                    # 获取新交易并实时分析
                    for exchange_addr in [CTF_EXCHANGE, NEG_RISK_EXCHANGE]:
                        try:
                            logs = self.w3.eth.get_logs({
                                'fromBlock': from_block,
                                'toBlock': current_block,
                                'address': Web3.to_checksum_address(exchange_addr),
                                'topics': [ORDER_FILLED_TOPIC],
                            })
                            
                            for log in logs:
                                trade = self._decode_order_filled(log, exchange_addr)
                                if trade:
                                    # 先添加交易
                                    self.store.add_trade(trade, notify=True)
                                    
                                    # 实时分析每笔交易
                                    analysis = self.analyze_trade_realtime(trade)
                                    
                                    # 如果发现可疑，通知前端
                                    if analysis['is_suspicious']:
                                        self.store._notify_ws('suspicious_trade', {
                                            'trade': {
                                                'tx_hash': trade.tx_hash,
                                                'maker': trade.maker,
                                                'taker': trade.taker,
                                                'volume': trade.volume,
                                                'token_id': trade.token_id,
                                            },
                                            'detections': analysis['detections'],
                                            'analysis_types': analysis['analysis_types'],
                                        })
                        except Exception as e:
                            logger.debug(f"获取日志失败: {e}")
                    
                    last_block = current_block
                    
                    # 通知分析统计更新
                    self._notify_analysis_stats()
                    
                    # 通知统计更新
                    stats = self.store.get_stats()
                    stats.is_streaming = True
                    self.store._notify_ws('stats', stats)
            
            except Exception as e:
                logger.error(f"流式监控错误: {e}")


# ============================================================================
# 全局实例
# ============================================================================

_forensics_service: Optional[ForensicsService] = None


def get_forensics_service() -> ForensicsService:
    """获取取证服务实例（单例）"""
    global _forensics_service
    if _forensics_service is None:
        _forensics_service = ForensicsService()
    return _forensics_service
