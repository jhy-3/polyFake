"""
PolySleuth - 取证服务

负责：
- 链上数据获取
- 刷量检测
- 流式监控
"""
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable
from decimal import Decimal
import logging
import requests

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
    """取证分析服务"""
    
    def __init__(self, rpc_url: str = POLYGON_RPC_URL):
        self.rpc_url = rpc_url
        self.w3: Optional[Web3] = None
        self.store = get_data_store()
        
        # 流式监控
        self._streaming = False
        self._stream_thread: Optional[threading.Thread] = None
        self._callbacks: List[Callable] = []
        
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
    
    # ========================================================================
    # 数据获取
    # ========================================================================
    
    def fetch_recent_trades(self, num_blocks: int = 100) -> int:
        """获取最近交易"""
        if not self.is_connected():
            logger.error("节点未连接")
            return 0
        
        try:
            current_block = self.w3.eth.block_number
            from_block = current_block - num_blocks
            
            logger.info(f"📡 获取区块 {from_block} 到 {current_block} 的交易...")
            
            # 获取两个交易所的日志
            trades_count = 0
            
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
            
            logger.info(f"✅ 获取 {trades_count} 笔交易")
            
            # 运行检测
            self.detect_self_trades()
            self.detect_circular_trades()
            
            return trades_count
        
        except Exception as e:
            logger.error(f"获取交易失败: {e}")
            return 0
    
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
        """流式监控循环"""
        last_block = self.get_current_block()
        
        while self._streaming:
            time.sleep(poll_interval)
            
            try:
                current_block = self.get_current_block()
                
                if current_block > last_block:
                    new_blocks = min(current_block - last_block, blocks_per_poll)
                    from_block = current_block - new_blocks
                    
                    # 获取新交易
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
                                    self.store.add_trade(trade, notify=True)
                        except Exception as e:
                            logger.debug(f"获取日志失败: {e}")
                    
                    # 运行检测
                    self.detect_self_trades()
                    self.detect_circular_trades()
                    
                    last_block = current_block
                    
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
