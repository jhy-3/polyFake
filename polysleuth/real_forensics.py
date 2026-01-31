"""
PolySleuth - 真实链上数据取证分析器

使用 Chainstack Polygon 节点获取真实的链上交易数据
进行刷量检测和市场健康度分析

所有数据均为真实链上数据，无模拟数据
"""

import os
import json
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from collections import defaultdict
from decimal import Decimal
import logging

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import requests

# 加载环境变量
load_dotenv()

# ============================================================================
# 配置
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 从环境变量读取配置
POLYGON_RPC_URL = os.getenv('POLYGON_RPC_URL', 'https://polygon-rpc.com')
CTF_EXCHANGE = os.getenv('CTF_EXCHANGE_ADDRESS', '0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E')
NEG_RISK_EXCHANGE = os.getenv('NEG_RISK_EXCHANGE_ADDRESS', '0xC5d563A36AE78145C45a50134d48A1215220f80a')
CONDITIONAL_TOKENS = os.getenv('CONDITIONAL_TOKENS_ADDRESS', '0x4D97DCd97eC945f40cF65F87097ACe5EA0476045')
GAMMA_API_URL = os.getenv('GAMMA_API_URL', 'https://gamma-api.polymarket.com')

# 事件签名 (keccak256 哈希)
ORDER_FILLED_TOPIC = Web3.keccak(text="OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)").hex()
POSITION_SPLIT_TOPIC = Web3.keccak(text="PositionSplit(address,address,bytes32,bytes32,uint256[],uint256)").hex()
POSITIONS_MERGE_TOPIC = Web3.keccak(text="PositionsMerge(address,address,bytes32,bytes32,uint256[],uint256)").hex()


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class RealTrade:
    """真实链上交易"""
    tx_hash: str
    block_number: int
    log_index: int
    timestamp: datetime
    contract: str
    
    order_hash: str
    maker: str
    taker: str
    maker_asset_id: int
    taker_asset_id: int
    maker_amount: int
    taker_amount: int
    fee: int
    
    # 计算字段
    side: str = ""
    token_id: str = ""
    price: float = 0.0
    size: float = 0.0
    
    # 取证标记
    is_wash: bool = False
    wash_type: str = ""
    wash_confidence: float = 0.0
    
    def __post_init__(self):
        """计算交易方向和价格"""
        if self.maker_asset_id == 0:
            # Maker 给 USDC，Taker 给 Token -> BUY
            self.side = "BUY"
            self.token_id = str(self.taker_asset_id)
            usdc_amount = self.maker_amount
            token_amount = self.taker_amount
        else:
            # Maker 给 Token，Taker 给 USDC -> SELL
            self.side = "SELL"
            self.token_id = str(self.maker_asset_id)
            usdc_amount = self.taker_amount
            token_amount = self.maker_amount
        
        # 计算价格和规模 (USDC 精度 1e6)
        if token_amount > 0:
            self.price = usdc_amount / token_amount
        self.size = token_amount / 1e6


@dataclass
class TransactionBundle:
    """
    交易捆绑 - 同一 tx_hash 内的所有事件
    用于原子级刷量检测
    """
    tx_hash: str
    block_number: int
    timestamp: datetime
    
    trades: List[RealTrade] = field(default_factory=list)
    has_split: bool = False
    has_merge: bool = False
    split_addresses: set = field(default_factory=set)
    merge_addresses: set = field(default_factory=set)
    
    # 分析结果
    is_atomic_wash: bool = False
    wash_confidence: float = 0.0
    total_volume: Decimal = Decimal(0)
    involved_addresses: set = field(default_factory=set)
    
    def analyze(self):
        """分析交易捆绑，检测原子级刷量"""
        # 收集所有地址
        trade_makers = set()
        trade_takers = set()
        
        for trade in self.trades:
            self.involved_addresses.add(trade.maker.lower())
            self.involved_addresses.add(trade.taker.lower())
            trade_makers.add(trade.maker.lower())
            trade_takers.add(trade.taker.lower())
            self.total_volume += Decimal(str(trade.size * trade.price))
        
        # 原子级刷量检测：Split -> Trade -> Merge 模式
        if self.has_split and self.has_merge and self.trades:
            self.is_atomic_wash = True
            self.wash_confidence = 0.85
            
            # 如果 split 发起者参与了交易
            if self.split_addresses & (trade_makers | trade_takers):
                self.wash_confidence = 0.92
            
            # 如果 split 和 merge 是同一地址
            if self.split_addresses & self.merge_addresses:
                self.wash_confidence = 0.98
            
            # 标记所有交易为刷量
            for trade in self.trades:
                trade.is_wash = True
                trade.wash_type = "ATOMIC"
                trade.wash_confidence = self.wash_confidence


@dataclass
class MarketHealth:
    """市场健康度"""
    token_id: str
    
    total_volume: Decimal = Decimal(0)
    organic_volume: Decimal = Decimal(0)
    wash_volume: Decimal = Decimal(0)
    
    total_trades: int = 0
    wash_trades: int = 0
    
    unique_traders: set = field(default_factory=set)
    suspicious_addresses: set = field(default_factory=set)
    
    @property
    def wash_ratio(self) -> float:
        if self.total_volume == 0:
            return 0.0
        return float(self.wash_volume / self.total_volume)
    
    @property
    def health_score(self) -> int:
        score = 100
        # 刷量比例扣分
        score -= int(self.wash_ratio * 50)
        # 交易者数量
        num_traders = len(self.unique_traders)
        if num_traders < 5:
            score -= 25
        elif num_traders < 20:
            score -= 15
        elif num_traders < 50:
            score -= 5
        # 可疑地址
        if len(self.suspicious_addresses) > 5:
            score -= 15
        return max(0, min(100, score))


# ============================================================================
# 链上数据获取器
# ============================================================================

class OnChainForensics:
    """
    真实链上数据取证分析器
    
    从 Polygon 链上获取 Polymarket 交易数据并进行分析
    """
    
    def __init__(self, rpc_url: str = None):
        self.rpc_url = rpc_url or POLYGON_RPC_URL
        self.w3 = None
        self._connect()
        
        # 数据存储
        self.trades: List[RealTrade] = []
        self.bundles: List[TransactionBundle] = []
        self.market_health: Dict[str, MarketHealth] = {}
        self.alerts: List[Dict] = []
        
        # 区块时间缓存
        self._block_timestamps: Dict[int, datetime] = {}
        
        # Token ID -> 市场信息映射
        self._token_to_market: Dict[str, Dict] = {}
        self._market_map_loaded = False
        
        # 状态
        self._running = False
        self._last_block = 0
        self._lock = threading.Lock()
    
    def _connect(self):
        """连接到 Polygon 节点"""
        try:
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            # Polygon 是 PoA 链，需要中间件
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            
            if self.w3.is_connected():
                chain_id = self.w3.eth.chain_id
                block = self.w3.eth.block_number
                logger.info(f"✅ 已连接到 Polygon (Chain ID: {chain_id}, Block: {block})")
                logger.info(f"   RPC: {self.rpc_url[:50]}...")
            else:
                logger.error("❌ 无法连接到 Polygon 节点")
        except Exception as e:
            logger.error(f"❌ 连接失败: {e}")
            self.w3 = None
    
    def load_market_map(self, limit: int = 500):
        """
        加载 Token ID -> 市场名称映射
        
        Args:
            limit: 获取的市场数量上限
        """
        if self._market_map_loaded:
            return
        
        try:
            # 尝试相对导入
            try:
                from polysleuth.data_fetcher import GammaAPIClient
            except ImportError:
                from data_fetcher import GammaAPIClient
            
            gamma = GammaAPIClient()
            self._token_to_market = gamma.build_token_to_market_map(limit=limit)
            self._market_map_loaded = True
            logger.info(f"✅ 已加载 {len(self._token_to_market)} 个市场映射")
        except Exception as e:
            logger.warning(f"加载市场映射失败: {e}")
            self._token_to_market = {}
    
    def get_market_name(self, token_id: str) -> str:
        """
        获取 token_id 对应的市场名称
        
        Args:
            token_id: Token ID
        
        Returns:
            市场名称 + 结果方向，如 "Will Trump win? (YES)"
        """
        if not self._market_map_loaded:
            self.load_market_map()
        
        if token_id in self._token_to_market:
            info = self._token_to_market[token_id]
            question = info.get('question', 'Unknown')
            outcome = info.get('outcome', '')
            # 截断过长的问题
            if len(question) > 50:
                question = question[:47] + "..."
            return f"{question} ({outcome})" if outcome else question
        
        return f"Token {token_id[:16]}..."
    
    def get_market_info(self, token_id: str) -> Optional[Dict]:
        """获取 token_id 对应的完整市场信息"""
        if not self._market_map_loaded:
            self.load_market_map()
        
        return self._token_to_market.get(token_id)
    
    def get_markets_summary(self) -> List[Dict]:
        """
        获取所有涉及市场的汇总信息
        按 event/question 聚合，避免 YES/NO 重复显示
        
        Returns:
            按交易量排序的市场列表
        """
        # 确保市场映射已加载
        if not self._market_map_loaded:
            self.load_market_map(limit=1000)
        
        # 尝试导入 API 客户端用于动态获取
        try:
            try:
                from polysleuth.data_fetcher import GammaAPIClient
            except ImportError:
                from data_fetcher import GammaAPIClient
            gamma_api = GammaAPIClient()
        except:
            gamma_api = None
        
        # 先按 token_id 统计
        token_stats = defaultdict(lambda: {
            'token_id': '',
            'question': '',
            'outcome': '',
            'condition_id': '',
            'trade_count': 0,
            'volume': 0.0,
            'wash_count': 0,
            'unique_traders': set(),
        })
        
        # 收集所有需要查询的 token_ids
        unknown_tokens = set()
        
        for trade in self.trades:
            tid = trade.token_id
            stats = token_stats[tid]
            stats['token_id'] = tid
            stats['trade_count'] += 1
            stats['volume'] += trade.size * trade.price
            if trade.is_wash:
                stats['wash_count'] += 1
            stats['unique_traders'].add(trade.maker)
            stats['unique_traders'].add(trade.taker)
            
            # 获取市场名称
            if not stats['question']:
                info = self.get_market_info(tid)
                if info:
                    stats['question'] = info.get('question', '')
                    stats['outcome'] = info.get('outcome', '')
                    stats['condition_id'] = info.get('condition_id', '')
                else:
                    unknown_tokens.add(tid)
        
        # 动态获取未知 token 的市场信息
        if gamma_api and unknown_tokens:
            logger.info(f"🔍 动态获取 {len(unknown_tokens)} 个未知市场...")
            for tid in list(unknown_tokens)[:50]:  # 限制最多查询 50 个
                try:
                    market = gamma_api.get_market_by_token_id(tid)
                    if market:
                        question = market.get('question', '')
                        tokens = market.get('tokens', [])
                        outcome = ''
                        for t in tokens:
                            if str(t.get('token_id', '')) == tid:
                                outcome = t.get('outcome', '').upper()
                                break
                        
                        if question:
                            token_stats[tid]['question'] = question
                            token_stats[tid]['outcome'] = outcome
                            # 缓存到映射
                            self._token_to_market[tid] = {
                                'question': question,
                                'outcome': outcome,
                                'condition_id': market.get('conditionId', ''),
                            }
                except Exception as e:
                    pass
        
        # 按 question (event) 合并 YES/NO
        event_stats = defaultdict(lambda: {
            'question': '',
            'token_ids': [],
            'trade_count': 0,
            'volume': 0.0,
            'wash_count': 0,
            'unique_traders': set(),
            'outcomes': [],
        })
        
        for tid, stats in token_stats.items():
            question = stats['question'] or f"Token {tid[:16]}..."
            event = event_stats[question]
            event['question'] = question
            event['token_ids'].append(tid)
            event['trade_count'] += stats['trade_count']
            event['volume'] += stats['volume']
            event['wash_count'] += stats['wash_count']
            event['unique_traders'].update(stats['unique_traders'])
            if stats['outcome']:
                event['outcomes'].append(stats['outcome'])
        
        # 转换为列表并排序
        result = []
        for question, stats in event_stats.items():
            result.append({
                'question': question,
                'token_ids': stats['token_ids'],
                'trade_count': stats['trade_count'],
                'volume': stats['volume'],
                'wash_count': stats['wash_count'],
                'wash_ratio': stats['wash_count'] / stats['trade_count'] if stats['trade_count'] > 0 else 0,
                'unique_traders': len(stats['unique_traders']),
                'outcomes': list(set(stats['outcomes'])),
            })
        
        # 按交易量降序
        result.sort(key=lambda x: x['volume'], reverse=True)
        return result
    
    def _get_block_timestamp(self, block_number: int) -> datetime:
        """获取区块时间（使用缓存或估算）"""
        if block_number in self._block_timestamps:
            return self._block_timestamps[block_number]
        
        # 使用估算时间（Polygon 约 2 秒一个区块）
        now = datetime.now()
        try:
            current_block = self.w3.eth.block_number
            seconds_ago = (current_block - block_number) * 2
            self._block_timestamps[block_number] = now - timedelta(seconds=seconds_ago)
        except:
            self._block_timestamps[block_number] = now
        
        return self._block_timestamps[block_number]
    
    def _prefetch_block_timestamps(self, block_numbers: List[int]):
        """批量预取区块时间（只获取首尾区块，其余用插值）"""
        unique_blocks = sorted(set(block_numbers) - set(self._block_timestamps.keys()))
        if not unique_blocks:
            return
        
        # 只获取首尾两个区块的真实时间，其余用插值
        first_block = unique_blocks[0]
        last_block = unique_blocks[-1]
        
        logger.info(f"   获取首尾区块时间戳 ({first_block}, {last_block})...")
        
        try:
            # 获取第一个区块时间
            block_data = self.w3.eth.get_block(first_block)
            first_time = datetime.fromtimestamp(block_data['timestamp'])
            self._block_timestamps[first_block] = first_time
            
            # 获取最后一个区块时间
            if first_block != last_block:
                block_data = self.w3.eth.get_block(last_block)
                last_time = datetime.fromtimestamp(block_data['timestamp'])
                self._block_timestamps[last_block] = last_time
            else:
                last_time = first_time
            
            # 计算每个区块的平均时间
            if last_block > first_block:
                total_seconds = (last_time - first_time).total_seconds()
                seconds_per_block = total_seconds / (last_block - first_block)
            else:
                seconds_per_block = 2.0  # Polygon 平均值
            
            # 对所有区块进行插值
            for block_num in unique_blocks:
                if block_num not in self._block_timestamps:
                    offset = block_num - first_block
                    self._block_timestamps[block_num] = first_time + timedelta(seconds=offset * seconds_per_block)
            
            logger.info(f"   ✅ 已为 {len(unique_blocks)} 个区块计算时间戳")
            
        except Exception as e:
            logger.warning(f"获取区块时间失败: {e}，使用估算值")
            # 使用完全估算
            now = datetime.now()
            current_block = self.w3.eth.block_number
            for block_num in unique_blocks:
                if block_num not in self._block_timestamps:
                    seconds_ago = (current_block - block_num) * 2
                    self._block_timestamps[block_num] = now - timedelta(seconds=seconds_ago)
    
    def fetch_recent_trades(self, num_blocks: int = 100) -> List[RealTrade]:
        """
        获取最近区块的真实交易数据
        
        Args:
            num_blocks: 要获取的区块数量
        
        Returns:
            真实交易列表
        """
        if not self.w3 or not self.w3.is_connected():
            logger.error("未连接到节点")
            return []
        
        current_block = self.w3.eth.block_number
        from_block = current_block - num_blocks
        
        logger.info(f"📡 获取区块 {from_block} 到 {current_block} 的交易数据...")
        
        all_trades = []
        all_events = defaultdict(list)  # tx_hash -> events
        all_order_logs = []  # 收集所有 order logs 用于批量预取时间
        
        # 分批获取日志 (Chainstack 支持更大的范围)
        batch_size = 50  # 每批50个区块
        
        for batch_start in range(from_block, current_block + 1, batch_size):
            batch_end = min(batch_start + batch_size - 1, current_block)
            
            try:
                # 获取 OrderFilled 事件
                order_logs = self.w3.eth.get_logs({
                    'address': [CTF_EXCHANGE, NEG_RISK_EXCHANGE],
                    'topics': [[ORDER_FILLED_TOPIC]],
                    'fromBlock': batch_start,
                    'toBlock': batch_end,
                })
                
                # 获取 PositionSplit 事件
                split_logs = self.w3.eth.get_logs({
                    'address': CONDITIONAL_TOKENS,
                    'topics': [[POSITION_SPLIT_TOPIC]],
                    'fromBlock': batch_start,
                    'toBlock': batch_end,
                })
                
                # 获取 PositionsMerge 事件
                merge_logs = self.w3.eth.get_logs({
                    'address': CONDITIONAL_TOKENS,
                    'topics': [[POSITIONS_MERGE_TOPIC]],
                    'fromBlock': batch_start,
                    'toBlock': batch_end,
                })
                
                logger.info(f"   区块 {batch_start}-{batch_end}: {len(order_logs)} 交易, {len(split_logs)} Split, {len(merge_logs)} Merge")
                
                # 先收集所有 logs
                all_order_logs.extend(order_logs)
                
                # 记录 Split/Merge 事件
                for log in split_logs:
                    tx_hash = log['transactionHash'].hex()
                    stakeholder = self._topic_to_address(log['topics'][1])
                    all_events[tx_hash].append(('split', stakeholder))
                
                for log in merge_logs:
                    tx_hash = log['transactionHash'].hex()
                    stakeholder = self._topic_to_address(log['topics'][1])
                    all_events[tx_hash].append(('merge', stakeholder))
                
                time.sleep(0.1)  # 避免请求过快
                
            except Exception as e:
                logger.warning(f"   获取区块 {batch_start}-{batch_end} 失败: {e}")
                continue
        
        # 批量预取所有需要的区块时间戳
        if all_order_logs:
            unique_blocks = list(set(log['blockNumber'] for log in all_order_logs))
            logger.info(f"📦 预取 {len(unique_blocks)} 个唯一区块的时间戳...")
            self._prefetch_block_timestamps(unique_blocks)
        
        # 解析所有 OrderFilled 事件
        logger.info(f"🔄 解析 {len(all_order_logs)} 笔交易...")
        for log in all_order_logs:
            trade = self._decode_order_filled(log)
            if trade:
                all_trades.append(trade)
                tx_hash = trade.tx_hash
                all_events[tx_hash].append(('trade', trade))
        
        # 构建交易捆绑并分析
        self._build_and_analyze_bundles(all_events)
        
        # 更新市场健康度
        self._update_market_health(all_trades)
        
        with self._lock:
            self.trades.extend(all_trades)
            self._last_block = current_block
        
        logger.info(f"✅ 共获取 {len(all_trades)} 笔真实交易")
        
        return all_trades
    
    def _decode_order_filled(self, log: Dict) -> Optional[RealTrade]:
        """解码 OrderFilled 事件"""
        try:
            topics = log['topics']
            data = log['data']
            
            # indexed 参数
            order_hash = topics[1].hex() if len(topics) > 1 else ""
            maker = self._topic_to_address(topics[2]) if len(topics) > 2 else ""
            taker = self._topic_to_address(topics[3]) if len(topics) > 3 else ""
            
            # 非 indexed 参数
            if isinstance(data, str):
                data = bytes.fromhex(data[2:]) if data.startswith('0x') else bytes.fromhex(data)
            
            if len(data) >= 160:  # 5 * 32 bytes
                maker_asset_id = int.from_bytes(data[0:32], 'big')
                taker_asset_id = int.from_bytes(data[32:64], 'big')
                maker_amount = int.from_bytes(data[64:96], 'big')
                taker_amount = int.from_bytes(data[96:128], 'big')
                fee = int.from_bytes(data[128:160], 'big')
            else:
                return None
            
            # 获取区块时间（使用缓存）
            block_number = log['blockNumber']
            timestamp = self._get_block_timestamp(block_number)
            
            return RealTrade(
                tx_hash=log['transactionHash'].hex(),
                block_number=log['blockNumber'],
                log_index=log['logIndex'],
                timestamp=timestamp,
                contract=log['address'],
                order_hash=order_hash,
                maker=maker,
                taker=taker,
                maker_asset_id=maker_asset_id,
                taker_asset_id=taker_asset_id,
                maker_amount=maker_amount,
                taker_amount=taker_amount,
                fee=fee,
            )
        except Exception as e:
            logger.warning(f"解码交易失败: {e}")
            return None
    
    def _topic_to_address(self, topic) -> str:
        """将 topic 转换为地址"""
        if isinstance(topic, bytes):
            return Web3.to_checksum_address(topic[-20:])
        topic_hex = topic.hex() if hasattr(topic, 'hex') else str(topic)
        if topic_hex.startswith('0x'):
            topic_hex = topic_hex[2:]
        return Web3.to_checksum_address('0x' + topic_hex[-40:])
    
    def _build_and_analyze_bundles(self, events: Dict[str, List]):
        """构建并分析交易捆绑"""
        bundles = []
        
        for tx_hash, event_list in events.items():
            trades = [e[1] for e in event_list if e[0] == 'trade']
            splits = [e[1] for e in event_list if e[0] == 'split']
            merges = [e[1] for e in event_list if e[0] == 'merge']
            
            if not trades:
                continue
            
            bundle = TransactionBundle(
                tx_hash=tx_hash,
                block_number=trades[0].block_number,
                timestamp=trades[0].timestamp,
                trades=trades,
                has_split=len(splits) > 0,
                has_merge=len(merges) > 0,
                split_addresses=set(s.lower() for s in splits),
                merge_addresses=set(m.lower() for m in merges),
            )
            
            bundle.analyze()
            bundles.append(bundle)
            
            # 生成警报
            if bundle.is_atomic_wash:
                self.alerts.append({
                    'id': f"ATOMIC_{tx_hash[:16]}",
                    'timestamp': bundle.timestamp.isoformat(),
                    'type': 'ATOMIC_WASH',
                    'tx_hash': tx_hash,
                    'trade_count': len(trades),
                    'volume': float(bundle.total_volume),
                    'confidence': bundle.wash_confidence,
                    'addresses': list(bundle.involved_addresses)[:5],
                })
        
        with self._lock:
            self.bundles.extend(bundles)
    
    def _update_market_health(self, trades: List[RealTrade]):
        """更新市场健康度"""
        for trade in trades:
            token_id = trade.token_id
            
            if token_id not in self.market_health:
                self.market_health[token_id] = MarketHealth(token_id=token_id)
            
            health = self.market_health[token_id]
            volume = Decimal(str(trade.size * trade.price))
            
            health.total_volume += volume
            health.total_trades += 1
            health.unique_traders.add(trade.maker.lower())
            health.unique_traders.add(trade.taker.lower())
            
            if trade.is_wash:
                health.wash_volume += volume
                health.wash_trades += 1
                health.suspicious_addresses.add(trade.maker.lower())
                health.suspicious_addresses.add(trade.taker.lower())
            else:
                health.organic_volume += volume
    
    def detect_self_trades(self):
        """检测自成交"""
        with self._lock:
            for trade in self.trades:
                if trade.maker.lower() == trade.taker.lower():
                    trade.is_wash = True
                    trade.wash_type = "SELF_TRADE"
                    trade.wash_confidence = 1.0
                    
                    # 更新市场健康度
                    if trade.token_id in self.market_health:
                        health = self.market_health[trade.token_id]
                        volume = Decimal(str(trade.size * trade.price))
                        if not hasattr(trade, '_counted_as_wash'):
                            health.wash_volume += volume
                            health.wash_trades += 1
                            health.suspicious_addresses.add(trade.maker.lower())
                            trade._counted_as_wash = True
                    
                    self.alerts.append({
                        'id': f"SELF_{trade.tx_hash[:16]}",
                        'timestamp': trade.timestamp.isoformat(),
                        'type': 'SELF_TRADE',
                        'tx_hash': trade.tx_hash,
                        'trade_count': 1,
                        'volume': trade.size * trade.price,
                        'confidence': 1.0,
                        'addresses': [trade.maker],
                    })
    
    def detect_circular_trades(self, time_window_seconds: int = 60):
        """检测环形交易"""
        with self._lock:
            sorted_trades = sorted(self.trades, key=lambda t: t.timestamp)
            
            for i, trade in enumerate(sorted_trades):
                if trade.is_wash:
                    continue
                
                for j in range(i + 1, len(sorted_trades)):
                    later = sorted_trades[j]
                    
                    time_diff = (later.timestamp - trade.timestamp).total_seconds()
                    if time_diff > time_window_seconds:
                        break
                    
                    # 检测 A→B, B→A 模式
                    if (trade.taker.lower() == later.maker.lower() and
                        trade.maker.lower() == later.taker.lower() and
                        trade.token_id == later.token_id):
                        
                        trade.is_wash = True
                        trade.wash_type = "CIRCULAR"
                        trade.wash_confidence = 0.85
                        
                        later.is_wash = True
                        later.wash_type = "CIRCULAR"
                        later.wash_confidence = 0.85
                        
                        self.alerts.append({
                            'id': f"CIRC_{trade.tx_hash[:8]}_{later.tx_hash[:8]}",
                            'timestamp': trade.timestamp.isoformat(),
                            'type': 'CIRCULAR_TRADE',
                            'tx_hash': trade.tx_hash,
                            'trade_count': 2,
                            'volume': trade.size * trade.price + later.size * later.price,
                            'confidence': 0.85,
                            'addresses': [trade.maker, trade.taker],
                            'time_diff': time_diff,
                        })
    
    def get_summary(self) -> Dict:
        """获取分析摘要"""
        with self._lock:
            total_volume = sum(float(h.total_volume) for h in self.market_health.values())
            wash_volume = sum(float(h.wash_volume) for h in self.market_health.values())
            total_trades = len(self.trades)
            wash_trades = sum(1 for t in self.trades if t.is_wash)
            
            return {
                'total_trades': total_trades,
                'wash_trades': wash_trades,
                'wash_ratio': wash_trades / total_trades if total_trades > 0 else 0,
                'total_volume': total_volume,
                'wash_volume': wash_volume,
                'organic_volume': total_volume - wash_volume,
                'organic_ratio': (total_volume - wash_volume) / total_volume if total_volume > 0 else 1.0,
                'markets_analyzed': len(self.market_health),
                'alerts_count': len(self.alerts),
                'last_block': self._last_block,
            }
    
    def get_wash_trades(self, limit: int = 50) -> List[Dict]:
        """获取刷量交易"""
        with self._lock:
            wash = sorted([t for t in self.trades if t.is_wash],
                         key=lambda x: x.wash_confidence, reverse=True)
            return [
                {
                    'tx_hash': t.tx_hash,
                    'block': t.block_number,
                    'timestamp': t.timestamp.isoformat(),
                    'token_id': t.token_id[:20] + '...' if len(t.token_id) > 20 else t.token_id,
                    'side': t.side,
                    'price': t.price,
                    'size': t.size,
                    'volume': t.size * t.price,
                    'maker': t.maker,
                    'taker': t.taker,
                    'type': t.wash_type,
                    'confidence': t.wash_confidence,
                }
                for t in wash[:limit]
            ]
    
    def get_all_health(self) -> List[Dict]:
        """获取所有市场健康度"""
        with self._lock:
            return sorted([
                {
                    'token_id': h.token_id[:20] + '...' if len(h.token_id) > 20 else h.token_id,
                    'health_score': h.health_score,
                    'wash_ratio': h.wash_ratio,
                    'total_volume': float(h.total_volume),
                    'organic_volume': float(h.organic_volume),
                    'total_trades': h.total_trades,
                    'unique_traders': len(h.unique_traders),
                    'suspicious_count': len(h.suspicious_addresses),
                }
                for h in self.market_health.values()
                if h.total_trades > 0
            ], key=lambda x: x['health_score'])
    
    def get_alerts(self, limit: int = 50) -> List[Dict]:
        """获取警报"""
        with self._lock:
            return sorted(self.alerts, key=lambda x: x['confidence'], reverse=True)[:limit]


# ============================================================================
# 流式监控
# ============================================================================

class StreamingMonitor:
    """流式监控器 - 持续获取新数据"""
    
    def __init__(self, forensics: OnChainForensics):
        self.forensics = forensics
        self._running = False
        self._thread = None
        self._callbacks: List[Callable] = []
    
    def add_callback(self, callback: Callable):
        self._callbacks.append(callback)
    
    def _notify(self, event: Dict):
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    def start(self, poll_interval: float = 10.0, blocks_per_poll: int = 20):
        """启动流式监控"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(poll_interval, blocks_per_poll),
            daemon=True
        )
        self._thread.start()
        logger.info("📡 流式监控已启动")
    
    def stop(self):
        """停止监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("⏹️ 流式监控已停止")
    
    def _poll_loop(self, interval: float, blocks: int):
        while self._running:
            try:
                trades = self.forensics.fetch_recent_trades(num_blocks=blocks)
                
                if trades:
                    # 运行检测
                    self.forensics.detect_self_trades()
                    self.forensics.detect_circular_trades()
                    
                    summary = self.forensics.get_summary()
                    self._notify({
                        'type': 'update',
                        'new_trades': len(trades),
                        **summary,
                    })
                
            except Exception as e:
                logger.error(f"轮询错误: {e}")
            
            time.sleep(interval)
    
    @property
    def is_running(self) -> bool:
        return self._running


# ============================================================================
# 全局实例
# ============================================================================

_forensics: Optional[OnChainForensics] = None
_monitor: Optional[StreamingMonitor] = None


def get_forensics() -> OnChainForensics:
    global _forensics
    if _forensics is None:
        _forensics = OnChainForensics()
    return _forensics


def get_monitor() -> StreamingMonitor:
    global _monitor, _forensics
    if _forensics is None:
        _forensics = OnChainForensics()
    if _monitor is None:
        _monitor = StreamingMonitor(_forensics)
    return _monitor


# ============================================================================
# 测试
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("PolySleuth - 真实链上数据取证分析")
    print("=" * 60)
    print(f"RPC: {POLYGON_RPC_URL[:50]}...")
    print()
    
    forensics = OnChainForensics()
    
    if not forensics.w3 or not forensics.w3.is_connected():
        print("❌ 无法连接到节点，退出")
        exit(1)
    
    # 获取最近 100 个区块的数据
    print("\n📡 获取最近 100 个区块的真实交易数据...")
    trades = forensics.fetch_recent_trades(num_blocks=100)
    
    if trades:
        # 运行检测
        print("\n🔍 运行刷量检测算法...")
        forensics.detect_self_trades()
        forensics.detect_circular_trades()
        
        # 显示结果
        summary = forensics.get_summary()
        print(f"\n📊 分析摘要:")
        print(f"   总交易数: {summary['total_trades']}")
        print(f"   可疑交易: {summary['wash_trades']}")
        print(f"   刷量比例: {summary['wash_ratio']:.2%}")
        print(f"   总交易量: ${summary['total_volume']:,.2f}")
        print(f"   有机比例: {summary['organic_ratio']:.2%}")
        print(f"   警报数量: {summary['alerts_count']}")
        
        # 显示可疑交易
        wash_trades = forensics.get_wash_trades(limit=5)
        if wash_trades:
            print(f"\n🚨 Top 可疑交易:")
            for i, t in enumerate(wash_trades, 1):
                print(f"   {i}. [{t['type']}] {t['tx_hash'][:20]}... ${t['volume']:.2f} (置信度: {t['confidence']:.0%})")
        
        # 显示市场健康度
        health = forensics.get_all_health()[:5]
        if health:
            print(f"\n🏥 低健康度市场:")
            for h in health:
                emoji = "🔴" if h['health_score'] < 40 else "🟠" if h['health_score'] < 60 else "🟡"
                print(f"   {emoji} {h['token_id']} - 分数: {h['health_score']}, 刷量: {h['wash_ratio']:.1%}")
    else:
        print("⚠️ 未获取到交易数据")
    
    print("\n✅ 测试完成!")
