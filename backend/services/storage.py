"""
PolySleuth - 数据存储服务

支持：
- SQLite 持久化存储
- 内存缓存快速查询
- 流式数据写入
- 自动同步机制
"""
import json
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Callable, Any
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal
import logging

from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from ..models import (
    TradeDB, AlertDB, MarketCacheDB,
    TradeResponse, AlertResponse, MarketSummary, MarketHealth, SystemStats,
    SessionLocal, init_db
)
from ..config import MAX_TRADES_IN_MEMORY, MAX_ALERTS_IN_MEMORY

logger = logging.getLogger(__name__)


# ============================================================================
# 内存数据结构
# ============================================================================

@dataclass
class MemoryTrade:
    """内存中的交易记录"""
    tx_hash: str
    log_index: int
    block_number: int
    timestamp: datetime
    contract: str
    order_hash: str
    maker: str
    taker: str
    token_id: str
    side: str
    price: float
    size: float
    volume: float
    fee: int = 0
    
    is_wash: bool = False
    wash_type: str = "NONE"
    wash_confidence: float = 0.0
    
    _persisted: bool = False  # 是否已持久化到数据库


@dataclass
class MemoryAlert:
    """内存中的警报"""
    alert_id: str
    timestamp: datetime
    alert_type: str
    severity: str
    tx_hash: str = ""
    token_id: str = ""
    trade_count: int = 1
    volume: float = 0.0
    confidence: float = 0.0
    addresses: List[str] = field(default_factory=list)
    details: Dict = field(default_factory=dict)
    
    _persisted: bool = False


@dataclass  
class MarketHealthData:
    """市场健康度数据"""
    token_id: str
    total_trades: int = 0
    wash_trades: int = 0
    total_volume: Decimal = Decimal(0)
    organic_volume: Decimal = Decimal(0)
    wash_volume: Decimal = Decimal(0)
    unique_traders: Set[str] = field(default_factory=set)
    suspicious_addresses: Set[str] = field(default_factory=set)
    
    @property
    def wash_ratio(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.wash_trades / self.total_trades
    
    @property
    def health_score(self) -> float:
        """计算健康度评分 (0-100)"""
        score = 100.0
        
        # 刷量比例扣分 (最多 -50)
        score -= min(self.wash_ratio * 100, 50)
        
        # 交易者数量加分
        trader_count = len(self.unique_traders)
        if trader_count >= 100:
            score += 10
        elif trader_count >= 50:
            score += 5
        elif trader_count < 5:
            score -= 10
        
        # 可疑地址扣分
        if len(self.suspicious_addresses) > 0:
            suspicious_ratio = len(self.suspicious_addresses) / max(trader_count, 1)
            score -= min(suspicious_ratio * 30, 20)
        
        return max(0, min(100, score))


# ============================================================================
# 数据存储服务
# ============================================================================

class DataStore:
    """
    数据存储服务
    
    架构:
    - 内存层: 快速读写，存储最近数据
    - 持久层: SQLite 存储历史数据
    - 同步机制: 后台线程定期同步到数据库
    """
    
    def __init__(self, sync_interval: float = 10.0):
        # 内存存储
        self._trades: deque[MemoryTrade] = deque(maxlen=MAX_TRADES_IN_MEMORY)
        self._alerts: deque[MemoryAlert] = deque(maxlen=MAX_ALERTS_IN_MEMORY)
        self._market_health: Dict[str, MarketHealthData] = {}
        self._market_cache: Dict[str, Dict] = {}  # token_id -> market info
        self._event_cache: Dict[str, Dict] = {}  # slug -> event info (包含所有token_ids)
        self._market_fetch_queue: deque[str] = deque()
        self._market_fetch_pending: Set[str] = set()
        self._market_fetch_thread: Optional[threading.Thread] = None
        
        # 索引
        self._trades_by_hash: Dict[str, List[MemoryTrade]] = defaultdict(list)
        self._trades_by_address: Dict[str, List[MemoryTrade]] = defaultdict(list)
        self._trades_by_token: Dict[str, List[MemoryTrade]] = defaultdict(list)
        
        # 统计
        self._total_trades = 0
        self._total_wash = 0
        self._total_volume = 0.0
        self._wash_volume = 0.0
        self._last_block = 0
        
        # 同步控制
        self._sync_interval = sync_interval
        self._sync_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.RLock()
        self._pending_trades: List[MemoryTrade] = []
        self._pending_alerts: List[MemoryAlert] = []
        
        # WebSocket 回调
        self._ws_callbacks: List[Callable] = []
        
        # 初始化数据库
        init_db()
        
        # 从数据库加载缓存
        self._load_market_cache()

        # 启动市场信息后台补全线程
        self._start_market_fetcher()

    def _start_market_fetcher(self):
        if self._market_fetch_thread and self._market_fetch_thread.is_alive():
            return
        self._market_fetch_thread = threading.Thread(target=self._market_fetch_loop, daemon=True)
        self._market_fetch_thread.start()

    def _schedule_market_fetch(self, token_id: str):
        if not token_id:
            return
        with self._lock:
            if token_id in self._market_fetch_pending:
                return
            self._market_fetch_pending.add(token_id)
            self._market_fetch_queue.append(token_id)

    def _market_fetch_loop(self):
        """后台补全缺失的市场信息"""
        while True:
            try:
                token_id = None
                with self._lock:
                    if self._market_fetch_queue:
                        token_id = self._market_fetch_queue.popleft()
                if not token_id:
                    time.sleep(1)
                    continue

                # 如果已经有了则跳过
                info = self._market_cache.get(token_id, {})
                if info.get('question'):
                    with self._lock:
                        self._market_fetch_pending.discard(token_id)
                    continue

                try:
                    import requests
                    resp = requests.get(
                        f"https://gamma-api.polymarket.com/markets/{token_id}",
                        timeout=5
                    )
                    if resp.status_code == 200:
                        market_data = resp.json()
                        if market_data and isinstance(market_data, dict):
                            question = market_data.get('question', '')
                            outcome = market_data.get('outcome', '')
                            # 重要：从 events[0].slug 获取 event slug（Polymarket URL 需要）
                            events = market_data.get('events', [])
                            event_slug = events[0].get('slug', '') if events else ''
                            slug = event_slug or market_data.get('slug', '')
                            market_id = str(
                                market_data.get('eventId', '')
                                or market_data.get('event_id', '')
                                or market_data.get('market_id', '')
                                or market_data.get('id', '')
                            )

                            if question:
                                self.cache_market(token_id, {
                                    'question': question,
                                    'slug': slug,
                                    'outcome': outcome,
                                    'market_id': market_id,
                                })
                except Exception as e:
                    logger.debug(f"后台补全市场失败: {token_id[:16]}... - {e}")
                finally:
                    with self._lock:
                        self._market_fetch_pending.discard(token_id)
            except Exception:
                time.sleep(1)
    
    def _load_market_cache(self):
        """从数据库加载市场缓存"""
        try:
            db = SessionLocal()
            try:
                markets = db.query(MarketCacheDB).all()
                for m in markets:
                    self._market_cache[m.token_id] = {
                        'question': m.question,
                        'slug': m.slug,
                        'outcome': m.outcome,
                        'condition_id': m.condition_id,
                        'market_id': getattr(m, 'market_id', ''),
                    }
                logger.info(f"✅ 从数据库加载 {len(self._market_cache)} 个市场缓存")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"加载市场缓存失败: {e}")
    
    def start_sync(self):
        """启动后台同步线程"""
        if self._running:
            return
        
        self._running = True
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()
        logger.info("✅ 后台同步线程已启动")
    
    def stop_sync(self):
        """停止后台同步"""
        self._running = False
        if self._sync_thread:
            self._sync_thread.join(timeout=5)
        # 最后一次同步
        self._sync_to_db()
        logger.info("✅ 后台同步线程已停止")
    
    def stop(self):
        """停止服务（别名）"""
        self.stop_sync()
    
    def _sync_loop(self):
        """后台同步循环"""
        while self._running:
            time.sleep(self._sync_interval)
            try:
                self._sync_to_db()
            except Exception as e:
                logger.error(f"同步失败: {e}")
    
    def _sync_to_db(self):
        """同步内存数据到数据库"""
        with self._lock:
            trades_to_save = self._pending_trades.copy()
            alerts_to_save = self._pending_alerts.copy()
            self._pending_trades.clear()
            self._pending_alerts.clear()
        
        if not trades_to_save and not alerts_to_save:
            return
        
        db = SessionLocal()
        try:
            # 批量插入交易（使用 INSERT OR IGNORE 语义）
            if trades_to_save:
                saved_count = 0
                for trade in trades_to_save:
                    try:
                        # 先检查是否存在
                        exists = db.query(TradeDB).filter(
                            TradeDB.tx_hash == trade.tx_hash,
                            TradeDB.log_index == trade.log_index
                        ).first()
                        
                        if not exists:
                            db_trade = TradeDB(
                                tx_hash=trade.tx_hash,
                                log_index=trade.log_index,
                                block_number=trade.block_number,
                                timestamp=trade.timestamp,
                                contract=trade.contract,
                                order_hash=trade.order_hash,
                                maker=trade.maker,
                                taker=trade.taker,
                                token_id=trade.token_id,
                                side=trade.side,
                                price=trade.price,
                                size=trade.size,
                                volume=trade.volume,
                                fee=trade.fee,
                                is_wash=trade.is_wash,
                                wash_type=trade.wash_type,
                                wash_confidence=trade.wash_confidence,
                            )
                            db.add(db_trade)
                            saved_count += 1
                    except Exception as e:
                        pass  # 忽略重复
                
                try:
                    db.commit()
                    if saved_count > 0:
                        logger.debug(f"💾 同步 {saved_count} 笔交易到数据库")
                except Exception as e:
                    # 静默处理重复键错误（多线程竞争条件）
                    if 'UNIQUE constraint failed' not in str(e):
                        logger.error(f"交易同步失败: {e}")
                    db.rollback()
            
            # 批量插入警报
            if alerts_to_save:
                saved_count = 0
                for alert in alerts_to_save:
                    try:
                        exists = db.query(AlertDB).filter(
                            AlertDB.alert_id == alert.alert_id
                        ).first()
                        
                        if not exists:
                            db_alert = AlertDB(
                                alert_id=alert.alert_id,
                                timestamp=alert.timestamp,
                                alert_type=alert.alert_type,
                                severity=alert.severity,
                                tx_hash=alert.tx_hash,
                                token_id=alert.token_id,
                                trade_count=alert.trade_count,
                                volume=alert.volume,
                                confidence=alert.confidence,
                                addresses=json.dumps(alert.addresses),
                                details=json.dumps(alert.details),
                            )
                            db.add(db_alert)
                            saved_count += 1
                    except Exception as e:
                        pass
                
                try:
                    db.commit()
                    if saved_count > 0:
                        logger.debug(f"💾 同步 {saved_count} 个警报到数据库")
                except Exception as e:
                    # 静默处理重复键错误（多线程竞争条件）
                    if 'UNIQUE constraint failed' not in str(e):
                        logger.error(f"警报同步失败: {e}")
                    db.rollback()
        
        except Exception as e:
            # 只记录非重复键的错误
            if 'UNIQUE constraint failed' not in str(e):
                logger.error(f"数据库同步失败: {e}")
            try:
                db.rollback()
            except:
                pass
        finally:
            db.close()
    
    # ========================================================================
    # 写入接口
    # ========================================================================
    
    def add_trade(self, trade: MemoryTrade, notify: bool = True):
        """添加交易（流式写入）"""
        with self._lock:
            # 添加到内存
            self._trades.append(trade)
            
            # 更新索引
            self._trades_by_hash[trade.tx_hash].append(trade)
            self._trades_by_address[trade.maker.lower()].append(trade)
            self._trades_by_address[trade.taker.lower()].append(trade)
            self._trades_by_token[trade.token_id].append(trade)
            
            # 更新统计
            self._total_trades += 1
            self._total_volume += trade.volume
            if trade.block_number > self._last_block:
                self._last_block = trade.block_number
            
            if trade.is_wash:
                self._total_wash += 1
                self._wash_volume += trade.volume
            
            # 更新市场健康度
            self._update_market_health(trade)
            
            # 加入待同步队列
            self._pending_trades.append(trade)
        
        # 通知 WebSocket
        if notify:
            self._notify_ws('trade', self._trade_to_response(trade))
    
    def add_alert(self, alert: MemoryAlert, notify: bool = True):
        """添加警报"""
        with self._lock:
            self._alerts.append(alert)
            self._pending_alerts.append(alert)
        
        if notify:
            self._notify_ws('alert', self._alert_to_response(alert))
    
    def _update_market_health(self, trade: MemoryTrade):
        """更新市场健康度"""
        token_id = trade.token_id
        
        if token_id not in self._market_health:
            self._market_health[token_id] = MarketHealthData(token_id=token_id)
        
        health = self._market_health[token_id]
        health.total_trades += 1
        volume = Decimal(str(trade.volume))
        health.total_volume += volume
        health.unique_traders.add(trade.maker.lower())
        health.unique_traders.add(trade.taker.lower())
        
        if trade.is_wash:
            health.wash_trades += 1
            health.wash_volume += volume
            health.suspicious_addresses.add(trade.maker.lower())
            health.suspicious_addresses.add(trade.taker.lower())
        else:
            health.organic_volume += volume
    
    def mark_wash_trade(self, tx_hash: str, log_index: int, 
                        wash_type: str, confidence: float):
        """标记交易为刷量"""
        with self._lock:
            for trade in self._trades_by_hash.get(tx_hash, []):
                if trade.log_index == log_index and not trade.is_wash:
                    trade.is_wash = True
                    trade.wash_type = wash_type
                    trade.wash_confidence = confidence
                    
                    self._total_wash += 1
                    self._wash_volume += trade.volume
                    
                    # 更新健康度
                    if trade.token_id in self._market_health:
                        health = self._market_health[trade.token_id]
                        health.wash_trades += 1
                        volume = Decimal(str(trade.volume))
                        health.wash_volume += volume
                        health.organic_volume -= volume
                        health.suspicious_addresses.add(trade.maker.lower())
                        health.suspicious_addresses.add(trade.taker.lower())
    
    def cache_market(self, token_id: str, info: Dict):
        """缓存市场信息（单个token）"""
        with self._lock:
            self._market_cache[token_id] = info
        
        # 异步保存到数据库
        try:
            db = SessionLocal()
            try:
                cache = MarketCacheDB(
                    token_id=token_id,
                    question=info.get('question', ''),
                    slug=info.get('slug', ''),
                    outcome=info.get('outcome', ''),
                    condition_id=info.get('condition_id', ''),
                    market_id=info.get('market_id', ''),
                    updated_at=datetime.utcnow(),
                )
                db.merge(cache)
                db.commit()
            finally:
                db.close()
        except:
            pass
    
    def cache_market_event(self, slug: str, info: Dict):
        """缓存事件级别的市场信息（包含所有token_ids）"""
        with self._lock:
            self._event_cache[slug] = info
    
    def get_event_by_slug(self, slug: str) -> Optional[Dict]:
        """根据 slug 获取事件信息"""
        with self._lock:
            return self._event_cache.get(slug)
    
    def get_slug_by_token_id(self, token_id: str) -> Optional[str]:
        """根据 token_id 反查 slug"""
        with self._lock:
            market_info = self._market_cache.get(token_id)
            if market_info:
                return market_info.get('slug')
            return None
    
    # ========================================================================
    # 查询接口
    # ========================================================================
    
    def get_trades(self, limit: int = 100, offset: int = 0,
                   token_id: Optional[str] = None,
                   address: Optional[str] = None,
                   is_wash: Optional[bool] = None,
                   side: Optional[str] = None,
                   start_time: Optional[datetime] = None,
                   end_time: Optional[datetime] = None) -> List[TradeResponse]:
        """获取交易列表"""
        with self._lock:
            trades = list(self._trades)
        
        # 过滤
        if token_id:
            trades = [t for t in trades if t.token_id == token_id]
        if address:
            address = address.lower()
            trades = [t for t in trades if t.maker.lower() == address or t.taker.lower() == address]
        if is_wash is not None:
            trades = [t for t in trades if t.is_wash == is_wash]
        if side:
            trades = [t for t in trades if t.side == side]
        if start_time:
            trades = [t for t in trades if t.timestamp >= start_time]
        if end_time:
            trades = [t for t in trades if t.timestamp <= end_time]
        
        # 按时间倒序
        trades.sort(key=lambda t: t.timestamp, reverse=True)
        
        # 分页
        trades = trades[offset:offset + limit]
        
        return [self._trade_to_response(t) for t in trades]
    
    def get_trade_by_hash(self, tx_hash: str) -> List[TradeResponse]:
        """根据交易哈希获取"""
        with self._lock:
            trades = self._trades_by_hash.get(tx_hash.lower(), [])
        return [self._trade_to_response(t) for t in trades]
    
    def get_alerts(self, limit: int = 50, 
                   offset: int = 0,
                   alert_type: Optional[str] = None,
                   severity: Optional[str] = None,
                   start_time: Optional[datetime] = None,
                   end_time: Optional[datetime] = None) -> List[AlertResponse]:
        """获取警报列表"""
        with self._lock:
            alerts = list(self._alerts)
        
        # 过滤
        if alert_type:
            alerts = [a for a in alerts if a.alert_type == alert_type]
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        if start_time:
            alerts = [a for a in alerts if a.timestamp >= start_time]
        if end_time:
            alerts = [a for a in alerts if a.timestamp <= end_time]
        
        # 按时间倒序
        alerts.sort(key=lambda a: a.timestamp, reverse=True)
        
        # 分页
        alerts = alerts[offset:offset + limit]
        
        return [self._alert_to_response(a) for a in alerts]
    
    def get_market_summary(self, limit: int = 20) -> List[MarketSummary]:
        """获取市场汇总"""
        with self._lock:
            # 按 question 聚合
            question_stats = defaultdict(lambda: {
                'question': '',
                'token_ids': [],
                'trade_count': 0,
                'volume': 0.0,
                'wash_count': 0,
                'unique_traders': set(),
                'outcomes': [],
            })
            
            for token_id, health in self._market_health.items():
                market_info = self._market_cache.get(token_id, {})
                question = market_info.get('question', f"Token {token_id[:16]}...")
                outcome = market_info.get('outcome', '')
                
                stats = question_stats[question]
                stats['question'] = question
                stats['token_ids'].append(token_id)
                stats['trade_count'] += health.total_trades
                stats['volume'] += float(health.total_volume)
                stats['wash_count'] += health.wash_trades
                stats['unique_traders'].update(health.unique_traders)
                if outcome:
                    stats['outcomes'].append(outcome)
            
            # 转换为列表
            result = []
            for question, stats in question_stats.items():
                result.append(MarketSummary(
                    question=question,
                    token_ids=stats['token_ids'],
                    trade_count=stats['trade_count'],
                    volume=stats['volume'],
                    wash_count=stats['wash_count'],
                    wash_ratio=stats['wash_count'] / stats['trade_count'] if stats['trade_count'] > 0 else 0,
                    unique_traders=len(stats['unique_traders']),
                    outcomes=list(set(stats['outcomes'])),
                ))
            
            # 按交易量排序
            result.sort(key=lambda x: x.volume, reverse=True)
            return result[:limit]
    
    def get_market_health(self, token_id: Optional[str] = None) -> List[MarketHealth]:
        """获取市场健康度"""
        with self._lock:
            if token_id:
                health = self._market_health.get(token_id)
                if health:
                    return [self._health_to_response(health)]
                return []
            
            result = []
            for health in self._market_health.values():
                if health.total_trades > 0:
                    result.append(self._health_to_response(health))
            
            result.sort(key=lambda x: x.health_score)
            return result
    
    def get_stats(self) -> SystemStats:
        """获取系统统计"""
        with self._lock:
            return SystemStats(
                total_trades=self._total_trades,
                total_alerts=len(self._alerts),
                wash_trade_count=self._total_wash,
                total_volume=self._total_volume,
                wash_volume=self._wash_volume,
                unique_markets=len(self._market_health),
                unique_traders=len(self._trades_by_address),
            )
    
    def get_market_name(self, token_id: str) -> str:
        """获取市场名称"""
        info = self._market_cache.get(token_id, {})
        question = info.get('question', '')
        outcome = info.get('outcome', '')

        if not question:
            self._schedule_market_fetch(token_id)
        
        if question:
            display = question[:50] + '...' if len(question) > 50 else question
            return f"{display} ({outcome})" if outcome else display
        
        return f"Token {token_id[:16]}..."
    
    def get_market_info(self, token_id: str, fetch_if_missing: bool = False) -> dict:
        """
        获取完整市场信息
        
        Args:
            token_id: Token ID
            fetch_if_missing: 如果缓存中没有，是否发起网络请求获取（默认 False 避免阻塞）
        """
        info = self._market_cache.get(token_id, {})
        question = info.get('question', '')
        outcome = info.get('outcome', '')
        slug = info.get('slug', '')  # 现在存储的是 event slug，无需规范化

        if not question and not fetch_if_missing:
            self._schedule_market_fetch(token_id)
        
        # 只在明确需要时才按需查询API（避免在循环中意外触发大量请求）
        if not question and fetch_if_missing:
            try:
                import requests
                resp = requests.get(
                    f"https://gamma-api.polymarket.com/markets/{token_id}",
                    timeout=2
                )
                if resp.status_code == 200:
                    market_data = resp.json()
                    if market_data and isinstance(market_data, dict):
                        question = market_data.get('question', '')
                        outcome = market_data.get('outcome', '')
                        # 重要：从 events[0].slug 获取 event slug（Polymarket URL 需要）
                        events = market_data.get('events', [])
                        event_slug = events[0].get('slug', '') if events else ''
                        slug = event_slug or market_data.get('slug', '')
                        market_id = str(market_data.get('market_id', ''))
                        
                        # 缓存到内存和数据库
                        if question:
                            self.cache_market(
                                token_id=token_id,
                                question=question,
                                outcome=outcome,
                                slug=slug,
                                market_id=market_id
                            )
                            logger.info(f"✅ 按需加载市场: {question[:30]}...")
            except Exception as e:
                logger.debug(f"按需查询失败: {token_id[:16]}... - {e}")
        
        display_name = question
        if question:
            display_name = question[:50] + '...' if len(question) > 50 else question
            if outcome:
                display_name = f"{display_name} ({outcome})"
        else:
            display_name = f"Token {token_id[:16]}..."
        
        polymarket_url = None
        if slug:
            polymarket_url = f"https://polymarket.com/event/{slug}"
        
        return {
            'name': display_name,
            'slug': slug,
            'polymarket_url': polymarket_url,
            'question': question,
            'outcome': outcome
        }
    
    # ========================================================================
    # WebSocket 通知
    # ========================================================================
    
    def register_ws_callback(self, callback: Callable):
        """注册 WebSocket 回调"""
        self._ws_callbacks.append(callback)
    
    def unregister_ws_callback(self, callback: Callable):
        """注销 WebSocket 回调"""
        if callback in self._ws_callbacks:
            self._ws_callbacks.remove(callback)
    
    def _notify_ws(self, msg_type: str, data: Any):
        """通知所有 WebSocket 客户端"""
        message = {'type': msg_type, 'data': data.dict() if hasattr(data, 'dict') else data}
        for callback in self._ws_callbacks:
            try:
                callback(message)
            except Exception as e:
                logger.error(f"WebSocket 回调失败: {e}")
    
    # ========================================================================
    # 转换函数
    # ========================================================================
    
    def _trade_to_response(self, trade: MemoryTrade) -> TradeResponse:
        return TradeResponse(
            tx_hash=trade.tx_hash,
            log_index=trade.log_index,
            block_number=trade.block_number,
            timestamp=trade.timestamp,
            contract=trade.contract,
            maker=trade.maker,
            taker=trade.taker,
            token_id=trade.token_id,
            side=trade.side,
            price=trade.price,
            size=trade.size,
            volume=trade.volume,
            is_wash=trade.is_wash,
            wash_type=trade.wash_type,
            wash_confidence=trade.wash_confidence,
            market_name=self.get_market_name(trade.token_id),
        )
    
    def _alert_to_response(self, alert: MemoryAlert) -> AlertResponse:
        return AlertResponse(
            alert_id=alert.alert_id,
            timestamp=alert.timestamp,
            alert_type=alert.alert_type,
            severity=alert.severity,
            tx_hash=alert.tx_hash,
            token_id=alert.token_id,
            trade_count=alert.trade_count,
            volume=alert.volume,
            confidence=alert.confidence,
            addresses=alert.addresses,
            market_name=self.get_market_name(alert.token_id) if alert.token_id else None,
        )
    
    def _health_to_response(self, health: MarketHealthData) -> MarketHealth:
        return MarketHealth(
            token_id=health.token_id,
            market_name=self.get_market_name(health.token_id),
            health_score=health.health_score,
            wash_ratio=health.wash_ratio,
            total_volume=float(health.total_volume),
            organic_volume=float(health.organic_volume),
            total_trades=health.total_trades,
            unique_traders=len(health.unique_traders),
            suspicious_count=len(health.suspicious_addresses),
        )


# ============================================================================
# 全局实例
# ============================================================================

_data_store: Optional[DataStore] = None


def get_data_store() -> DataStore:
    """获取数据存储实例（单例）"""
    global _data_store
    if _data_store is None:
        _data_store = DataStore()
        _data_store.start_sync()
    return _data_store
