"""
PolySleuth - Polymarket 数据获取器

直接从 Gamma API 和 Polygon 链上获取真实的 Polymarket 数据
无需数据库，实时分析
"""

import json
import time
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from web3 import Web3
from web3.exceptions import TransactionNotFound

# ============================================================================
# 常量配置
# ============================================================================

import os
from dotenv import load_dotenv

load_dotenv()

# Polymarket 合约地址（从环境变量读取，提供默认值）
CTF_EXCHANGE_ADDRESS = os.getenv("CTF_EXCHANGE_ADDRESS", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
NEG_RISK_EXCHANGE_ADDRESS = os.getenv("NEG_RISK_EXCHANGE_ADDRESS", "0xC5d563A36AE78145C45a50134d48A1215220f80a")
CONDITIONAL_TOKENS_ADDRESS = os.getenv("CONDITIONAL_TOKENS_ADDRESS", "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# API URLs
GAMMA_API_BASE = os.getenv("GAMMA_API_URL", "https://gamma-api.polymarket.com")
CLOB_API_BASE = "https://clob.polymarket.com"
DEFAULT_RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")

# 事件签名
ORDER_FILLED_SIGNATURE = "OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)"
POSITION_SPLIT_SIGNATURE = "PositionSplit(address,address,bytes32,bytes32,uint256[],uint256)"
POSITIONS_MERGE_SIGNATURE = "PositionsMerge(address,address,bytes32,bytes32,uint256[],uint256)"

# USDC 精度
USDC_DECIMALS = 6


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class MarketInfo:
    """市场信息"""
    condition_id: str
    question_id: str
    slug: str
    question: str
    description: str
    oracle: str
    yes_token_id: str
    no_token_id: str
    active: bool
    closed: bool
    volume: float
    liquidity: float
    end_date: Optional[datetime] = None
    outcome_prices: Dict[str, float] = field(default_factory=dict)


@dataclass
class TradeInfo:
    """交易信息"""
    tx_hash: str
    log_index: int
    block_number: int
    timestamp: datetime
    exchange: str
    maker: str
    taker: str
    token_id: str
    side: str  # BUY / SELL
    price: Decimal
    size: Decimal
    fee: int


@dataclass
class MarketTrades:
    """市场交易汇总"""
    market: MarketInfo
    trades: List[TradeInfo]
    total_volume: Decimal
    unique_makers: set
    unique_takers: set
    
    @property
    def unique_traders(self) -> int:
        return len(self.unique_makers | self.unique_takers)


# ============================================================================
# Gamma API 客户端
# ============================================================================

class GammaAPIClient:
    """Polymarket Gamma API 客户端"""
    
    def __init__(self, base_url: str = GAMMA_API_BASE):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'PolySleuth/1.0'
        })
    
    def get_markets(
        self,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        """获取市场列表"""
        params = {
            'limit': limit,
            'offset': offset,
            'active': str(active).lower(),
            'closed': str(closed).lower(),
        }
        
        try:
            resp = self.session.get(f"{self.base_url}/markets", params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"Error fetching markets: {e}")
            return []
    
    def get_market_by_slug(self, slug: str) -> Optional[Dict]:
        """通过 slug 获取市场"""
        try:
            resp = self.session.get(f"{self.base_url}/markets", params={'slug': slug}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            return data if isinstance(data, dict) and 'conditionId' in data else None
        except Exception as e:
            print(f"Error fetching market {slug}: {e}")
            return None
    
    def get_market_by_condition_id(self, condition_id: str) -> Optional[Dict]:
        """通过 conditionId 获取市场"""
        try:
            resp = self.session.get(
                f"{self.base_url}/markets",
                params={'condition_id': condition_id},
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            return None
        except Exception as e:
            print(f"Error fetching market by condition_id: {e}")
            return None
    
    def get_events(self, limit: int = 50, active: bool = True) -> List[Dict]:
        """获取事件列表"""
        params = {'limit': limit, 'active': str(active).lower()}
        try:
            resp = self.session.get(f"{self.base_url}/events", params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"Error fetching events: {e}")
            return []
    
    def get_market_by_token_id(self, token_id: str) -> Optional[Dict]:
        """通过 token_id 获取市场信息"""
        if not token_id or token_id in ['', '0', '[', '"']:
            return None
        try:
            # Gamma API 支持通过 clob_token_ids 搜索
            resp = self.session.get(
                f"{self.base_url}/markets",
                params={'clob_token_ids': token_id, 'limit': 1},
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            return None
        except Exception as e:
            # 静默失败
            return None
    
    def build_token_to_market_map(self, limit: int = 500) -> Dict[str, Dict]:
        """
        构建 token_id -> 市场信息的映射表
        一次性获取大量市场，减少后续 API 调用
        
        Returns:
            Dict[token_id, {'question': str, 'slug': str, 'outcome': 'YES'|'NO'}]
        """
        token_map = {}
        
        try:
            # 获取活跃市场
            markets = self.get_markets(active=True, limit=limit)
            
            for market in markets:
                question = market.get('question', 'Unknown Market')
                slug = market.get('slug', '')
                
                # 解析 token IDs
                tokens = market.get('tokens', [])
                for token in tokens:
                    tid = str(token.get('token_id', ''))
                    outcome = token.get('outcome', '').upper()
                    if tid:
                        token_map[tid] = {
                            'question': question,
                            'slug': slug,
                            'outcome': outcome,
                            'condition_id': market.get('conditionId', ''),
                        }
                
                # 备用：从 clobTokenIds 获取
                if 'clobTokenIds' in market:
                    import json as json_module
                    clob_ids = market['clobTokenIds']
                    if isinstance(clob_ids, str):
                        try:
                            clob_ids = json_module.loads(clob_ids)
                        except:
                            clob_ids = []
                    
                    if isinstance(clob_ids, list):
                        outcomes = ['YES', 'NO']
                        for i, tid in enumerate(clob_ids):
                            tid = str(tid)
                            if tid and tid not in token_map:
                                token_map[tid] = {
                                    'question': question,
                                    'slug': slug,
                                    'outcome': outcomes[i] if i < len(outcomes) else 'UNKNOWN',
                                    'condition_id': market.get('conditionId', ''),
                                }
            
            print(f"📊 已构建 {len(token_map)} 个 token 的市场映射")
            return token_map
            
        except Exception as e:
            print(f"Error building token map: {e}")
            return {}
    
    def search_markets(self, query: str, limit: int = 20) -> List[Dict]:
        """搜索市场"""
        try:
            # 尝试使用搜索端点
            resp = self.session.get(
                f"{self.base_url}/markets",
                params={'_q': query, 'limit': limit},
                timeout=30
            )
            resp.raise_for_status()
            return resp.json()
        except:
            # 回退：获取所有市场然后过滤
            markets = self.get_markets(limit=200)
            query_lower = query.lower()
            return [
                m for m in markets
                if query_lower in m.get('question', '').lower()
                or query_lower in m.get('slug', '').lower()
            ][:limit]
    
    def parse_market_info(self, data: Dict) -> Optional[MarketInfo]:
        """解析市场数据为 MarketInfo"""
        try:
            import json as json_module
            
            # 获取 token IDs
            tokens = data.get('tokens', [])
            yes_token_id = ""
            no_token_id = ""
            outcome_prices = {}
            
            for token in tokens:
                outcome = token.get('outcome', '').upper()
                token_id = str(token.get('token_id', ''))
                price = float(token.get('price', 0))
                
                if outcome == 'YES':
                    yes_token_id = token_id
                    outcome_prices['YES'] = price
                elif outcome == 'NO':
                    no_token_id = token_id
                    outcome_prices['NO'] = price
            
            # 或者从 clobTokenIds 获取
            if not yes_token_id and 'clobTokenIds' in data:
                clob_ids = data['clobTokenIds']
                # clobTokenIds 可能是 JSON 字符串，需要解析
                if isinstance(clob_ids, str):
                    try:
                        clob_ids = json_module.loads(clob_ids)
                    except:
                        clob_ids = []
                if isinstance(clob_ids, list) and len(clob_ids) >= 2:
                    yes_token_id = str(clob_ids[0])
                    no_token_id = str(clob_ids[1])
            
            # 从 outcomePrices 获取价格（也可能是 JSON 字符串）
            if not outcome_prices and 'outcomePrices' in data:
                prices = data['outcomePrices']
                if isinstance(prices, str):
                    try:
                        prices = json_module.loads(prices)
                    except:
                        prices = []
                if isinstance(prices, list) and len(prices) >= 2:
                    try:
                        outcome_prices['YES'] = float(prices[0])
                        outcome_prices['NO'] = float(prices[1])
                    except:
                        pass
            
            # 解析结束日期
            end_date = None
            if data.get('endDate'):
                try:
                    end_date = datetime.fromisoformat(data['endDate'].replace('Z', '+00:00'))
                except:
                    pass
            
            return MarketInfo(
                condition_id=data.get('conditionId', ''),
                question_id=data.get('questionID', '') or data.get('questionId', ''),
                slug=data.get('slug', ''),
                question=data.get('question', ''),
                description=data.get('description', ''),
                oracle=data.get('oracle', ''),
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                active=data.get('active', False),
                closed=data.get('closed', False),
                volume=float(data.get('volume', 0) or 0),
                liquidity=float(data.get('liquidity', 0) or 0),
                end_date=end_date,
                outcome_prices=outcome_prices,
            )
        except Exception as e:
            print(f"Error parsing market info: {e}")
            return None


# ============================================================================
# CLOB API 客户端（用于获取订单簿和最近交易）
# ============================================================================

class CLOBAPIClient:
    """
    Polymarket CLOB API 客户端
    
    注意：CLOB API 的部分端点需要认证，我们改用公开的 Gamma API 来获取交易数据
    """
    
    def __init__(self, base_url: str = CLOB_API_BASE):
        self.base_url = base_url
        self.gamma_base = GAMMA_API_BASE
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'PolySleuth/1.0'
        })
    
    def get_orderbook(self, token_id: str) -> Optional[Dict]:
        """获取订单簿（公开端点）"""
        if not token_id or token_id in ['', '[', '"']:
            return None
        try:
            resp = self.session.get(
                f"{self.base_url}/book",
                params={'token_id': token_id},
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            # 静默失败，不打印错误
            return None
    
    def get_trades_from_gamma(self, condition_id: str, limit: int = 100) -> List[Dict]:
        """
        从 Gamma API 获取市场活动/交易
        这是公开 API，不需要认证
        """
        if not condition_id:
            return []
        try:
            # 使用 Gamma API 的 market activity 端点
            resp = self.session.get(
                f"{self.gamma_base}/activity",
                params={'market': condition_id, 'limit': limit},
                timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except:
            # 尝试备用端点
            try:
                resp = self.session.get(
                    f"{self.gamma_base}/markets/{condition_id}/activity",
                    params={'limit': limit},
                    timeout=15
                )
                resp.raise_for_status()
                return resp.json()
            except:
                return []
    
    def get_trades(self, token_id: str, limit: int = 100) -> List[Dict]:
        """
        获取 token 的交易记录
        优先使用 Gamma API（公开），CLOB 需要认证
        """
        if not token_id or token_id in ['', '[', '"']:
            return []
        
        # 尝试 Gamma API 的 trades 端点
        try:
            resp = self.session.get(
                f"{self.gamma_base}/trades",
                params={'asset_id': token_id, 'limit': limit},
                timeout=15
            )
            if resp.status_code == 200:
                return resp.json()
        except:
            pass
        
        return []
    
    def get_market_trades(
        self,
        condition_id: str,
        limit: int = 100,
    ) -> List[Dict]:
        """获取市场的所有交易"""
        return self.get_trades_from_gamma(condition_id, limit)


# ============================================================================
# 链上数据获取器
# ============================================================================

class OnChainDataFetcher:
    """从 Polygon 链上获取数据"""
    
    def __init__(self, rpc_url: str = DEFAULT_RPC_URL):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.order_filled_topic = self.w3.keccak(text=ORDER_FILLED_SIGNATURE)
        self.position_split_topic = self.w3.keccak(text=POSITION_SPLIT_SIGNATURE)
        self.positions_merge_topic = self.w3.keccak(text=POSITIONS_MERGE_SIGNATURE)
        
        self.exchange_addresses = [
            CTF_EXCHANGE_ADDRESS.lower(),
            NEG_RISK_EXCHANGE_ADDRESS.lower()
        ]
    
    def is_connected(self) -> bool:
        return self.w3.is_connected()
    
    def get_latest_block(self) -> int:
        return self.w3.eth.block_number
    
    def decode_order_filled(self, log: Dict) -> Optional[TradeInfo]:
        """解码 OrderFilled 事件"""
        try:
            event_abi = {
                "anonymous": False,
                "inputs": [
                    {"indexed": True, "name": "orderHash", "type": "bytes32"},
                    {"indexed": True, "name": "maker", "type": "address"},
                    {"indexed": True, "name": "taker", "type": "address"},
                    {"indexed": False, "name": "makerAssetId", "type": "uint256"},
                    {"indexed": False, "name": "takerAssetId", "type": "uint256"},
                    {"indexed": False, "name": "makerAmountFilled", "type": "uint256"},
                    {"indexed": False, "name": "takerAmountFilled", "type": "uint256"},
                    {"indexed": False, "name": "fee", "type": "uint256"}
                ],
                "name": "OrderFilled",
                "type": "event"
            }
            
            event = self.w3.eth.contract(abi=[event_abi]).events.OrderFilled()
            decoded = event.process_log(log)
            args = decoded['args']
            
            maker_asset_id = str(args['makerAssetId'])
            taker_asset_id = str(args['takerAssetId'])
            maker_amount = args['makerAmountFilled']
            taker_amount = args['takerAmountFilled']
            
            # 确定交易方向
            if maker_asset_id == "0" or maker_asset_id == "0x0":
                token_id = taker_asset_id
                usdc_amount = maker_amount
                token_amount = taker_amount
                side = "BUY"
            else:
                token_id = maker_asset_id
                usdc_amount = taker_amount
                token_amount = maker_amount
                side = "SELL"
            
            # 计算价格
            if token_amount > 0:
                price = Decimal(usdc_amount) / Decimal(token_amount)
                price = price.quantize(Decimal('0.000001'), rounding=ROUND_DOWN)
            else:
                price = Decimal('0')
            
            # 获取区块时间戳
            block = self.w3.eth.get_block(log['blockNumber'])
            timestamp = datetime.fromtimestamp(block['timestamp'])
            
            return TradeInfo(
                tx_hash=log['transactionHash'].hex(),
                log_index=log['logIndex'],
                block_number=log['blockNumber'],
                timestamp=timestamp,
                exchange=log['address'],
                maker=args['maker'],
                taker=args['taker'],
                token_id=token_id,
                side=side,
                price=price,
                size=Decimal(token_amount) / Decimal(10 ** USDC_DECIMALS),
                fee=args['fee'],
            )
        except Exception as e:
            print(f"Error decoding OrderFilled: {e}")
            return None
    
    def get_trades_by_token_id(
        self,
        token_id: str,
        from_block: int,
        to_block: int = None,
    ) -> List[TradeInfo]:
        """获取指定 token 的交易记录"""
        if to_block is None:
            to_block = self.get_latest_block()
        
        trades = []
        
        for exchange_addr in [CTF_EXCHANGE_ADDRESS, NEG_RISK_EXCHANGE_ADDRESS]:
            try:
                logs = self.w3.eth.get_logs({
                    'address': exchange_addr,
                    'topics': [self.order_filled_topic.hex()],
                    'fromBlock': from_block,
                    'toBlock': to_block,
                })
                
                for log in logs:
                    trade = self.decode_order_filled(log)
                    if trade and trade.token_id == token_id:
                        trades.append(trade)
            except Exception as e:
                print(f"Error fetching logs from {exchange_addr}: {e}")
        
        return sorted(trades, key=lambda t: (t.block_number, t.log_index))
    
    def get_trades_in_blocks(
        self,
        from_block: int,
        to_block: int = None,
        max_blocks: int = 1000,
    ) -> List[TradeInfo]:
        """获取区块范围内的所有交易"""
        if to_block is None:
            to_block = self.get_latest_block()
        
        # 限制区块范围
        if to_block - from_block > max_blocks:
            from_block = to_block - max_blocks
        
        trades = []
        
        for exchange_addr in [CTF_EXCHANGE_ADDRESS, NEG_RISK_EXCHANGE_ADDRESS]:
            try:
                logs = self.w3.eth.get_logs({
                    'address': exchange_addr,
                    'topics': [self.order_filled_topic.hex()],
                    'fromBlock': from_block,
                    'toBlock': to_block,
                })
                
                for log in logs:
                    trade = self.decode_order_filled(log)
                    if trade:
                        trades.append(trade)
            except Exception as e:
                print(f"Error fetching logs: {e}")
        
        return sorted(trades, key=lambda t: (t.block_number, t.log_index))
    
    def get_transaction_events(self, tx_hash: str) -> Dict[str, List]:
        """获取交易中的所有相关事件"""
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return {'order_filled': [], 'position_split': [], 'positions_merge': []}
        
        events = {
            'order_filled': [],
            'position_split': [],
            'positions_merge': [],
        }
        
        for log in receipt['logs']:
            if len(log['topics']) == 0:
                continue
            
            topic0 = log['topics'][0]
            
            if topic0 == self.order_filled_topic:
                trade = self.decode_order_filled(log)
                if trade:
                    events['order_filled'].append(trade)
            
            elif topic0 == self.position_split_topic:
                events['position_split'].append({
                    'log_index': log['logIndex'],
                    'stakeholder': self._topic_to_address(log['topics'][1]) if len(log['topics']) > 1 else None,
                })
            
            elif topic0 == self.positions_merge_topic:
                events['positions_merge'].append({
                    'log_index': log['logIndex'],
                    'stakeholder': self._topic_to_address(log['topics'][1]) if len(log['topics']) > 1 else None,
                })
        
        return events
    
    def _topic_to_address(self, topic) -> str:
        """将 topic 转换为地址"""
        if isinstance(topic, bytes):
            return Web3.to_checksum_address(topic[-20:])
        topic_hex = topic.hex() if hasattr(topic, 'hex') else topic
        return Web3.to_checksum_address('0x' + topic_hex[-40:])


# ============================================================================
# 综合数据获取器
# ============================================================================

class PolymarketDataFetcher:
    """Polymarket 综合数据获取器"""
    
    def __init__(self, rpc_url: str = DEFAULT_RPC_URL):
        self.gamma = GammaAPIClient()
        self.clob = CLOBAPIClient()
        self.chain = OnChainDataFetcher(rpc_url)
    
    def get_active_markets(self, limit: int = 50) -> List[MarketInfo]:
        """获取活跃市场列表"""
        markets_data = self.gamma.get_markets(active=True, limit=limit)
        markets = []
        
        for data in markets_data:
            market = self.gamma.parse_market_info(data)
            if market:
                markets.append(market)
        
        return markets
    
    def get_market(self, slug_or_id: str) -> Optional[MarketInfo]:
        """获取单个市场"""
        # 尝试作为 slug
        data = self.gamma.get_market_by_slug(slug_or_id)
        
        # 尝试作为 condition_id
        if not data and slug_or_id.startswith('0x'):
            data = self.gamma.get_market_by_condition_id(slug_or_id)
        
        if data:
            return self.gamma.parse_market_info(data)
        return None
    
    def search_markets(self, query: str) -> List[MarketInfo]:
        """搜索市场"""
        markets_data = self.gamma.search_markets(query)
        return [
            m for m in (self.gamma.parse_market_info(d) for d in markets_data)
            if m is not None
        ]
    
    def get_market_trades_from_api(
        self,
        market: MarketInfo,
        limit: int = 100,
    ) -> List[Dict]:
        """从 CLOB API 获取市场交易"""
        trades = []
        
        if market.yes_token_id:
            yes_trades = self.clob.get_trades(market.yes_token_id, limit=limit // 2)
            for t in yes_trades:
                t['outcome'] = 'YES'
            trades.extend(yes_trades)
        
        if market.no_token_id:
            no_trades = self.clob.get_trades(market.no_token_id, limit=limit // 2)
            for t in no_trades:
                t['outcome'] = 'NO'
            trades.extend(no_trades)
        
        return trades
    
    def get_market_trades_from_chain(
        self,
        market: MarketInfo,
        blocks_back: int = 5000,
    ) -> List[TradeInfo]:
        """从链上获取市场交易"""
        if not self.chain.is_connected():
            print("Warning: Not connected to RPC")
            return []
        
        latest = self.chain.get_latest_block()
        from_block = latest - blocks_back
        
        trades = []
        token_ids = [market.yes_token_id, market.no_token_id]
        
        for token_id in token_ids:
            if token_id:
                token_trades = self.chain.get_trades_by_token_id(
                    token_id, from_block, latest
                )
                trades.extend(token_trades)
        
        return sorted(trades, key=lambda t: (t.block_number, t.log_index))
    
    def analyze_market_volume(self, market: MarketInfo) -> Dict:
        """分析市场交易量"""
        trades = self.get_market_trades_from_api(market)
        
        total_volume = Decimal('0')
        makers = set()
        takers = set()
        
        for t in trades:
            total_volume += Decimal(str(t.get('size', 0)))
            if t.get('maker'):
                makers.add(t['maker'].lower())
            if t.get('taker'):
                takers.add(t['taker'].lower())
        
        return {
            'total_volume': float(total_volume),
            'trade_count': len(trades),
            'unique_makers': len(makers),
            'unique_takers': len(takers),
            'unique_traders': len(makers | takers),
        }


# ============================================================================
# 便捷函数
# ============================================================================

def get_top_markets(limit: int = 20) -> List[MarketInfo]:
    """获取热门市场"""
    fetcher = PolymarketDataFetcher()
    markets = fetcher.get_active_markets(limit=limit * 2)
    # 按交易量排序
    markets.sort(key=lambda m: m.volume, reverse=True)
    return markets[:limit]


def get_market_by_slug(slug: str) -> Optional[MarketInfo]:
    """通过 slug 获取市场"""
    fetcher = PolymarketDataFetcher()
    return fetcher.get_market(slug)


def search_markets(query: str) -> List[MarketInfo]:
    """搜索市场"""
    fetcher = PolymarketDataFetcher()
    return fetcher.search_markets(query)


# ============================================================================
# 测试
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("PolySleuth - Polymarket 数据获取器测试")
    print("=" * 60)
    
    fetcher = PolymarketDataFetcher()
    
    # 测试获取活跃市场
    print("\n📊 获取热门市场...")
    markets = fetcher.get_active_markets(limit=10)
    
    for i, m in enumerate(markets[:5], 1):
        print(f"\n{i}. {m.slug}")
        print(f"   问题: {m.question[:60]}...")
        print(f"   交易量: ${m.volume:,.2f}")
        print(f"   YES: {m.outcome_prices.get('YES', 0):.2%}")
        print(f"   NO: {m.outcome_prices.get('NO', 0):.2%}")
    
    # 测试搜索
    print("\n\n🔍 搜索 'trump'...")
    results = fetcher.search_markets("trump")
    for m in results[:3]:
        print(f"  - {m.slug}: {m.question[:50]}...")
    
    # 测试获取单个市场
    if markets:
        print(f"\n\n📈 获取市场详情: {markets[0].slug}")
        market = fetcher.get_market(markets[0].slug)
        if market:
            print(f"  Condition ID: {market.condition_id[:20]}...")
            print(f"  YES Token: {market.yes_token_id[:20]}..." if market.yes_token_id else "  YES Token: N/A")
            print(f"  NO Token: {market.no_token_id[:20]}..." if market.no_token_id else "  NO Token: N/A")
            
            # 获取交易
            print("\n  最近交易:")
            trades = fetcher.get_market_trades_from_api(market, limit=10)
            for t in trades[:5]:
                print(f"    {t.get('side', 'N/A')} {t.get('outcome', '')} @ {t.get('price', 0):.4f}")
    
    print("\n✅ 测试完成!")
