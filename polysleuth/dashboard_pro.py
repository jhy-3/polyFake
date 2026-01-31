"""
PolySleuth Pro - 专业级链上取证仪表板
=====================================

全新 Cyberpunk/Fintech 风格设计
- 深色主题 + Glassmorphism 卡片
- AgGrid 专业表格
- Plotly 高级可视化
- 网络关系图展示刷量环

所有数据均为真实链上数据
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import networkx as nx
import time
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import logging

from dotenv import load_dotenv
load_dotenv()

# 条件导入 AgGrid
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
    AGGRID_AVAILABLE = True
except ImportError:
    AGGRID_AVAILABLE = False
    print("⚠️ st_aggrid not available, using standard dataframe")

# 导入取证模块
try:
    from polysleuth.real_forensics import (
        OnChainForensics, StreamingMonitor,
        POLYGON_RPC_URL, CTF_EXCHANGE, NEG_RISK_EXCHANGE
    )
    from polysleuth.data_fetcher import PolymarketDataFetcher, GammaAPIClient
except ImportError:
    from real_forensics import (
        OnChainForensics, StreamingMonitor,
        POLYGON_RPC_URL, CTF_EXCHANGE, NEG_RISK_EXCHANGE
    )
    from data_fetcher import PolymarketDataFetcher, GammaAPIClient

logger = logging.getLogger(__name__)


# ============================================================================
# 🎨 CYBERPUNK THEME - CSS 注入
# ============================================================================

CYBERPUNK_CSS = """
<style>
    /* ========== 全局深色主题 ========== */
    .stApp {
        background: linear-gradient(135deg, #0a0a0f 0%, #1a1a2e 50%, #16213e 100%);
    }
    
    /* 隐藏默认 Streamlit 元素 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    /* ========== Glassmorphism 卡片 ========== */
    .glass-card {
        background: rgba(26, 26, 46, 0.7);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border-radius: 16px;
        border: 1px solid rgba(255, 255, 255, 0.1);
        padding: 24px;
        margin: 10px 0;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        transition: transform 0.3s ease, box-shadow 0.3s ease;
    }
    
    .glass-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 12px 40px rgba(102, 126, 234, 0.2);
    }
    
    /* ========== 霓虹 KPI 卡片 ========== */
    .neon-metric {
        background: linear-gradient(145deg, rgba(26, 26, 46, 0.9), rgba(22, 33, 62, 0.9));
        border-radius: 16px;
        padding: 20px;
        border: 1px solid transparent;
        background-clip: padding-box;
        position: relative;
    }
    
    .neon-metric::before {
        content: '';
        position: absolute;
        top: 0; right: 0; bottom: 0; left: 0;
        z-index: -1;
        margin: -2px;
        border-radius: inherit;
        background: linear-gradient(135deg, #667eea, #764ba2, #f093fb);
    }
    
    .neon-metric-green::before {
        background: linear-gradient(135deg, #00c853, #00e676, #69f0ae);
    }
    
    .neon-metric-red::before {
        background: linear-gradient(135deg, #ff1744, #ff5252, #ff8a80);
    }
    
    .neon-metric-orange::before {
        background: linear-gradient(135deg, #ff9100, #ffab40, #ffd180);
    }
    
    .metric-value {
        font-size: 2.5rem;
        font-weight: 800;
        background: linear-gradient(90deg, #fff 0%, #a0a0a0 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 0;
        line-height: 1.2;
    }
    
    .metric-label {
        font-size: 0.85rem;
        color: #888;
        text-transform: uppercase;
        letter-spacing: 1.5px;
        margin-top: 8px;
    }
    
    .metric-delta {
        font-size: 0.9rem;
        margin-top: 4px;
    }
    
    .metric-delta.positive { color: #00e676; }
    .metric-delta.negative { color: #ff5252; }
    
    /* ========== 标题样式 ========== */
    .cyber-title {
        font-size: 3rem;
        font-weight: 900;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 50%, #f093fb 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        text-shadow: 0 0 40px rgba(102, 126, 234, 0.5);
        margin-bottom: 0;
        letter-spacing: -1px;
    }
    
    .cyber-subtitle {
        color: #666;
        font-size: 1.1rem;
        margin-top: 8px;
    }
    
    /* ========== 实时数据徽章 ========== */
    .live-badge {
        display: inline-flex;
        align-items: center;
        background: linear-gradient(90deg, rgba(0, 200, 83, 0.2), rgba(0, 230, 118, 0.2));
        border: 1px solid #00c853;
        color: #00e676;
        padding: 6px 16px;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 600;
        margin-left: 16px;
    }
    
    .live-badge::before {
        content: '';
        width: 8px;
        height: 8px;
        background: #00e676;
        border-radius: 50%;
        margin-right: 8px;
        animation: pulse 2s infinite;
    }
    
    @keyframes pulse {
        0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(0, 230, 118, 0.7); }
        50% { opacity: 0.7; box-shadow: 0 0 0 10px rgba(0, 230, 118, 0); }
    }
    
    /* ========== 警报卡片 ========== */
    .alert-card {
        background: linear-gradient(145deg, rgba(255, 23, 68, 0.1), rgba(255, 82, 82, 0.05));
        border: 1px solid rgba(255, 82, 82, 0.3);
        border-radius: 12px;
        padding: 16px;
        margin: 8px 0;
    }
    
    .alert-card.warning {
        background: linear-gradient(145deg, rgba(255, 145, 0, 0.1), rgba(255, 171, 64, 0.05));
        border: 1px solid rgba(255, 171, 64, 0.3);
    }
    
    /* ========== 表格样式 ========== */
    .dataframe {
        background: rgba(26, 26, 46, 0.5) !important;
        border-radius: 12px !important;
    }
    
    .stDataFrame {
        border-radius: 12px;
        overflow: hidden;
    }
    
    /* ========== 侧边栏美化 ========== */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0a0a0f 0%, #1a1a2e 100%);
        border-right: 1px solid rgba(102, 126, 234, 0.2);
    }
    
    [data-testid="stSidebar"] .stButton > button {
        background: linear-gradient(135deg, #667eea, #764ba2);
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    
    [data-testid="stSidebar"] .stButton > button:hover {
        transform: scale(1.02);
        box-shadow: 0 4px 20px rgba(102, 126, 234, 0.4);
    }
    
    /* ========== 选项卡样式 ========== */
    .stTabs [data-baseweb="tab-list"] {
        background: rgba(26, 26, 46, 0.5);
        border-radius: 12px;
        padding: 4px;
    }
    
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        color: #888;
        font-weight: 500;
    }
    
    .stTabs [data-baseweb="tab"]:hover {
        color: #fff;
    }
    
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #667eea, #764ba2) !important;
        color: white !important;
    }
    
    /* ========== 进度条 ========== */
    .stProgress > div > div {
        background: linear-gradient(90deg, #667eea, #764ba2, #f093fb);
    }
    
    /* ========== 分隔线 ========== */
    hr {
        border: none;
        height: 1px;
        background: linear-gradient(90deg, transparent, rgba(102, 126, 234, 0.3), transparent);
        margin: 24px 0;
    }
    
    /* ========== 网络图容器 ========== */
    .network-container {
        background: rgba(10, 10, 15, 0.8);
        border-radius: 16px;
        border: 1px solid rgba(102, 126, 234, 0.2);
        padding: 20px;
    }
    
    /* ========== 市场卡片悬浮效果 ========== */
    .market-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 12px 40px rgba(102, 126, 234, 0.3);
    }
</style>
"""


# ============================================================================
# 📊 市场名称缓存系统
# ============================================================================

class MarketNameCache:
    """
    智能市场名称缓存
    - 预加载活跃市场
    - 动态获取未知 token
    - 本地缓存减少 API 调用
    """
    
    def __init__(self):
        self._cache: Dict[str, Dict] = {}
        self._api = GammaAPIClient()
        self._preloaded = False
    
    def preload(self, limit: int = 1000):
        """预加载活跃市场的 token 映射"""
        if self._preloaded:
            return
        
        try:
            token_map = self._api.build_token_to_market_map(limit=limit)
            self._cache.update(token_map)
            self._preloaded = True
            logger.info(f"✅ 预加载 {len(self._cache)} 个市场映射")
        except Exception as e:
            logger.error(f"预加载失败: {e}")
    
    def get_market_name(self, token_id: str) -> str:
        """获取市场显示名称"""
        if not token_id:
            return "Unknown"
        
        # 先查缓存
        if token_id in self._cache:
            info = self._cache[token_id]
            question = info.get('question', 'Unknown')[:60]
            outcome = info.get('outcome', '')
            return f"{question}{'...' if len(info.get('question', '')) > 60 else ''} ({outcome})" if outcome else question
        
        # 动态获取
        try:
            market = self._api.get_market_by_token_id(token_id)
            if market:
                question = market.get('question', 'Unknown')
                # 确定 outcome
                tokens = market.get('tokens', [])
                outcome = ''
                for t in tokens:
                    if str(t.get('token_id', '')) == token_id:
                        outcome = t.get('outcome', '').upper()
                        break
                
                self._cache[token_id] = {
                    'question': question,
                    'outcome': outcome,
                    'slug': market.get('slug', ''),
                }
                
                return f"{question[:60]}{'...' if len(question) > 60 else ''} ({outcome})" if outcome else question[:60]
        except:
            pass
        
        # 返回缩略 token ID
        return f"Token {token_id[:12]}..."
    
    def get_full_info(self, token_id: str) -> Optional[Dict]:
        """获取完整市场信息"""
        self.get_market_name(token_id)  # 确保缓存
        return self._cache.get(token_id)


# 全局缓存实例
market_cache = MarketNameCache()


# ============================================================================
# 📈 高级可视化组件
# ============================================================================

def create_stacked_area_chart(trades: List, title: str = "交易量时序分析") -> go.Figure:
    """
    创建 Wash Volume vs Organic Volume 堆叠面积图
    红色层叠加在绿色层上，直观展示刷量占比
    """
    if not trades:
        return go.Figure()
    
    # 按小时聚合数据
    hourly_data = defaultdict(lambda: {'organic': 0, 'wash': 0})
    
    for t in trades:
        hour = t.timestamp.replace(minute=0, second=0, microsecond=0)
        volume = t.size * t.price
        
        if t.is_wash:
            hourly_data[hour]['wash'] += volume
        else:
            hourly_data[hour]['organic'] += volume
    
    # 排序并转为列表
    sorted_hours = sorted(hourly_data.keys())
    organic_values = [hourly_data[h]['organic'] for h in sorted_hours]
    wash_values = [hourly_data[h]['wash'] for h in sorted_hours]
    
    fig = go.Figure()
    
    # 有机交易层 (绿色)
    fig.add_trace(go.Scatter(
        x=sorted_hours,
        y=organic_values,
        name='🟢 Organic Volume',
        fill='tozeroy',
        fillcolor='rgba(0, 230, 118, 0.4)',
        line=dict(color='#00e676', width=2),
        hovertemplate='<b>%{x}</b><br>Organic: $%{y:,.0f}<extra></extra>'
    ))
    
    # 刷量层 (红色，叠加在有机层上)
    fig.add_trace(go.Scatter(
        x=sorted_hours,
        y=[o + w for o, w in zip(organic_values, wash_values)],
        name='🔴 Wash Volume',
        fill='tonexty',
        fillcolor='rgba(255, 82, 82, 0.6)',
        line=dict(color='#ff5252', width=2),
        hovertemplate='<b>%{x}</b><br>Total: $%{y:,.0f}<extra></extra>'
    ))
    
    fig.update_layout(
        title=dict(
            text=f"<b>{title}</b>",
            font=dict(size=18, color='#fff'),
            x=0
        ),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#888'),
        xaxis=dict(
            title='时间',
            gridcolor='rgba(255,255,255,0.05)',
            showgrid=True,
        ),
        yaxis=dict(
            title='交易量 (USD)',
            gridcolor='rgba(255,255,255,0.05)',
            showgrid=True,
            tickformat='$,.0f',
        ),
        legend=dict(
            orientation='h',
            yanchor='bottom',
            y=1.02,
            xanchor='right',
            x=1,
            bgcolor='rgba(0,0,0,0)'
        ),
        hovermode='x unified',
        height=400,
        margin=dict(l=60, r=20, t=60, b=40),
    )
    
    return fig


def create_sunburst_chart(trades: List) -> go.Figure:
    """
    创建 Sunburst 图 - 刷量类型分布
    内环: 刷量类型 (SELF_TRADE, CIRCULAR, ATOMIC)
    外环: 具体市场
    """
    if not trades:
        return go.Figure()
    
    wash_trades = [t for t in trades if t.is_wash]
    if not wash_trades:
        return go.Figure()
    
    # 构建层级数据
    data = defaultdict(lambda: defaultdict(float))
    
    for t in wash_trades:
        wash_type = t.wash_type or 'UNKNOWN'
        market = market_cache.get_market_name(t.token_id)[:30]
        volume = t.size * t.price
        data[wash_type][market] += volume
    
    # 转换为 Sunburst 格式
    ids = ['Total']
    labels = ['All Wash Trades']
    parents = ['']
    values = [sum(sum(m.values()) for m in data.values())]
    colors = ['#667eea']
    
    type_colors = {
        'SELF_TRADE': '#ff5252',
        'CIRCULAR': '#ff9100', 
        'ATOMIC': '#ffea00',
        'UNKNOWN': '#888888',
    }
    
    for wash_type, markets in data.items():
        type_total = sum(markets.values())
        ids.append(wash_type)
        labels.append(wash_type)
        parents.append('Total')
        values.append(type_total)
        colors.append(type_colors.get(wash_type, '#888'))
        
        for market, volume in markets.items():
            ids.append(f"{wash_type}_{market}")
            labels.append(market)
            parents.append(wash_type)
            values.append(volume)
            colors.append(type_colors.get(wash_type, '#888'))
    
    fig = go.Figure(go.Sunburst(
        ids=ids,
        labels=labels,
        parents=parents,
        values=values,
        marker=dict(colors=colors),
        branchvalues='total',
        hovertemplate='<b>%{label}</b><br>Volume: $%{value:,.0f}<extra></extra>',
        textfont=dict(color='white', size=11),
    ))
    
    fig.update_layout(
        title=dict(
            text='<b>🎯 刷量类型分布 (Sunburst)</b>',
            font=dict(size=18, color='#fff'),
        ),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#888'),
        height=450,
        margin=dict(l=20, r=20, t=60, b=20),
    )
    
    return fig


def create_network_graph(trades: List, limit: int = 50) -> go.Figure:
    """
    创建钱包关系网络图 - 展示刷量环
    节点: 钱包地址 (红色=可疑, 绿色=正常)
    边: 交易关系 (粗细=交易量)
    """
    if not trades:
        return go.Figure()
    
    # 构建网络图
    G = nx.DiGraph()
    
    # 统计地址的可疑交易
    address_wash_count = defaultdict(int)
    address_total_count = defaultdict(int)
    
    for t in trades:
        address_total_count[t.maker] += 1
        address_total_count[t.taker] += 1
        if t.is_wash:
            address_wash_count[t.maker] += 1
            address_wash_count[t.taker] += 1
    
    # 筛选活跃地址和可疑交易
    wash_trades = [t for t in trades if t.is_wash][:limit]
    
    # 添加边
    edge_weights = defaultdict(float)
    for t in wash_trades:
        maker = t.maker[:10] + '...' + t.maker[-4:]
        taker = t.taker[:10] + '...' + t.taker[-4:]
        volume = t.size * t.price
        
        G.add_edge(maker, taker, weight=volume)
        edge_weights[(maker, taker)] += volume
    
    if len(G.nodes()) == 0:
        return go.Figure()
    
    # 计算布局
    try:
        pos = nx.spring_layout(G, k=2, iterations=50, seed=42)
    except:
        pos = nx.circular_layout(G)
    
    # 创建边的轨迹
    edge_x = []
    edge_y = []
    edge_colors = []
    
    for edge in G.edges():
        x0, y0 = pos[edge[0]]
        x1, y1 = pos[edge[1]]
        edge_x.extend([x0, x1, None])
        edge_y.extend([y0, y1, None])
    
    edge_trace = go.Scatter(
        x=edge_x, y=edge_y,
        line=dict(width=1.5, color='rgba(102, 126, 234, 0.5)'),
        hoverinfo='none',
        mode='lines'
    )
    
    # 创建节点的轨迹
    node_x = []
    node_y = []
    node_colors = []
    node_sizes = []
    node_texts = []
    
    for node in G.nodes():
        x, y = pos[node]
        node_x.append(x)
        node_y.append(y)
        
        # 根据连接数确定大小
        degree = G.degree(node)
        node_sizes.append(15 + degree * 5)
        
        # 根据可疑程度确定颜色
        full_addr = next((t.maker for t in trades if t.maker.startswith(node[:10])), 
                        next((t.taker for t in trades if t.taker.startswith(node[:10])), ''))
        wash_ratio = address_wash_count[full_addr] / max(address_total_count[full_addr], 1)
        
        if wash_ratio > 0.5:
            node_colors.append('#ff5252')  # 红色 - 高度可疑
        elif wash_ratio > 0.2:
            node_colors.append('#ff9100')  # 橙色 - 中度可疑
        else:
            node_colors.append('#00e676')  # 绿色 - 低风险
        
        node_texts.append(f"{node}<br>连接数: {degree}<br>可疑比例: {wash_ratio:.0%}")
    
    node_trace = go.Scatter(
        x=node_x, y=node_y,
        mode='markers+text',
        hoverinfo='text',
        text=[n[:8] for n in G.nodes()],
        textposition='top center',
        textfont=dict(size=9, color='#888'),
        hovertext=node_texts,
        marker=dict(
            size=node_sizes,
            color=node_colors,
            line=dict(width=2, color='rgba(255,255,255,0.3)'),
        )
    )
    
    fig = go.Figure(data=[edge_trace, node_trace])
    
    fig.update_layout(
        title=dict(
            text='<b>🕸️ 可疑交易网络图 (Wash Trading Ring)</b>',
            font=dict(size=18, color='#fff'),
        ),
        showlegend=False,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        height=500,
        margin=dict(l=20, r=20, t=60, b=20),
        annotations=[
            dict(
                text="🔴 高风险 | 🟠 中风险 | 🟢 低风险",
                xref="paper", yref="paper",
                x=0, y=-0.05,
                showarrow=False,
                font=dict(size=11, color='#666'),
            )
        ]
    )
    
    return fig


def create_treemap_chart(trades: List) -> go.Figure:
    """
    创建 TreeMap 图 - 市场交易量分布
    """
    if not trades:
        return go.Figure()
    
    # 按市场聚合
    market_data = defaultdict(lambda: {'volume': 0, 'wash': 0, 'count': 0})
    
    for t in trades:
        market = market_cache.get_market_name(t.token_id)
        volume = t.size * t.price
        market_data[market]['volume'] += volume
        market_data[market]['count'] += 1
        if t.is_wash:
            market_data[market]['wash'] += volume
    
    # 转换为 DataFrame
    df = pd.DataFrame([
        {
            'market': market[:40] + '...' if len(market) > 40 else market,
            'volume': data['volume'],
            'wash_ratio': data['wash'] / data['volume'] if data['volume'] > 0 else 0,
            'trade_count': data['count'],
        }
        for market, data in market_data.items()
    ])
    
    df = df.nlargest(20, 'volume')  # Top 20
    
    fig = px.treemap(
        df,
        path=['market'],
        values='volume',
        color='wash_ratio',
        color_continuous_scale=['#00e676', '#ffea00', '#ff5252'],
        range_color=[0, 0.5],
        hover_data={'volume': ':$,.0f', 'wash_ratio': ':.1%', 'trade_count': True},
    )
    
    fig.update_layout(
        title=dict(
            text='<b>📊 市场交易量 TreeMap</b>',
            font=dict(size=18, color='#fff'),
        ),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#888'),
        height=400,
        margin=dict(l=20, r=20, t=60, b=20),
        coloraxis_colorbar=dict(
            title='刷量比例',
            tickformat='.0%',
        )
    )
    
    return fig


# ============================================================================
# 🎛️ UI 组件
# ============================================================================

def render_neon_metric(label: str, value: str, delta: str = None, delta_type: str = "neutral", icon: str = ""):
    """渲染霓虹风格的 KPI 卡片"""
    
    color_class = {
        "positive": "neon-metric-green",
        "negative": "neon-metric-red",
        "warning": "neon-metric-orange",
        "neutral": "",
    }.get(delta_type, "")
    
    delta_html = ""
    if delta:
        delta_class = "positive" if delta_type == "positive" else "negative" if delta_type == "negative" else ""
        delta_html = f'<div class="metric-delta {delta_class}">{delta}</div>'
    
    st.markdown(f"""
    <div class="neon-metric {color_class}">
        <div class="metric-value">{icon} {value}</div>
        <div class="metric-label">{label}</div>
        {delta_html}
    </div>
    """, unsafe_allow_html=True)


def render_aggrid_table(df: pd.DataFrame, height: int = 400, selection: bool = False):
    """渲染专业 AgGrid 表格"""
    
    if not AGGRID_AVAILABLE:
        st.dataframe(df, use_container_width=True, hide_index=True, height=height)
        return None
    
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        filterable=True,
        sortable=True,
        resizable=True,
        wrapText=True,
        autoHeight=True,
    )
    
    if selection:
        gb.configure_selection(selection_mode='single', use_checkbox=True)
    
    # 特殊列配置
    if '交易哈希' in df.columns:
        cell_renderer = JsCode("""
        function(params) {
            if (params.value) {
                return '<a href="https://polygonscan.com/tx/' + params.value + '" target="_blank" style="color: #667eea;">' + params.value.substring(0,16) + '...</a>';
            }
            return params.value;
        }
        """)
        gb.configure_column('交易哈希', cellRenderer=cell_renderer)
    
    if '风险等级' in df.columns or '级别' in df.columns:
        gb.configure_column(df.columns[0], pinned='left', width=100)
    
    grid_options = gb.build()
    
    # 自定义主题
    custom_css = {
        ".ag-theme-streamlit": {
            "background-color": "rgba(26, 26, 46, 0.7) !important",
            "color": "#fff !important",
        },
        ".ag-header": {
            "background-color": "rgba(102, 126, 234, 0.2) !important",
        },
        ".ag-row-even": {
            "background-color": "rgba(26, 26, 46, 0.5) !important",
        },
        ".ag-row-odd": {
            "background-color": "rgba(22, 33, 62, 0.5) !important",
        },
        ".ag-row:hover": {
            "background-color": "rgba(102, 126, 234, 0.15) !important",
        },
    }
    
    return AgGrid(
        df,
        gridOptions=grid_options,
        height=height,
        theme='streamlit',
        custom_css=custom_css,
        update_mode=GridUpdateMode.SELECTION_CHANGED if selection else GridUpdateMode.NO_UPDATE,
        allow_unsafe_jscode=True,
    )


# ============================================================================
# 📱 页面布局
# ============================================================================

def init_state():
    """初始化会话状态"""
    if 'forensics' not in st.session_state:
        st.session_state.forensics = None
    if 'monitor' not in st.session_state:
        st.session_state.monitor = None
    if 'initialized' not in st.session_state:
        st.session_state.initialized = False
    if 'streaming' not in st.session_state:
        st.session_state.streaming = False
    if 'last_update' not in st.session_state:
        st.session_state.last_update = None


def render_sidebar():
    """渲染侧边栏"""
    with st.sidebar:
        # Logo 区域
        st.markdown("""
        <div style="text-align: center; padding: 20px 0;">
            <span style="font-size: 2.5rem;">🔍</span>
            <h1 style="margin: 0; font-size: 1.8rem; background: linear-gradient(90deg, #667eea, #764ba2); 
                       -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
                PolySleuth
            </h1>
            <p style="color: #666; font-size: 0.85rem; margin-top: 4px;">Polymarket Forensics</p>
        </div>
        """, unsafe_allow_html=True)
        
        # 连接状态
        st.markdown("---")
        
        if st.session_state.forensics and st.session_state.forensics.w3:
            if st.session_state.forensics.w3.is_connected():
                block = st.session_state.forensics.w3.eth.block_number
                st.markdown(f"""
                <div style="background: rgba(0, 200, 83, 0.1); border: 1px solid #00c853; 
                            border-radius: 8px; padding: 12px; text-align: center;">
                    <span style="color: #00e676; font-weight: 600;">🟢 已连接 Polygon</span>
                    <p style="color: #888; font-size: 0.8rem; margin: 4px 0 0 0;">区块 #{block:,}</p>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.error("❌ 连接断开")
        else:
            st.info("⏳ 等待初始化...")
        
        st.markdown("---")
        
        # 数据控制
        st.markdown("##### 📡 数据获取")
        
        num_blocks = st.slider("扫描区块数", 50, 500, 100, 50)
        
        if st.button("🚀 获取链上数据", use_container_width=True, type="primary"):
            with st.spinner(f"正在扫描 {num_blocks} 个区块..."):
                if not st.session_state.forensics:
                    st.session_state.forensics = OnChainForensics()
                
                forensics = st.session_state.forensics
                
                if forensics.w3 and forensics.w3.is_connected():
                    # 预加载市场名称
                    market_cache.preload()
                    
                    trades = forensics.fetch_recent_trades(num_blocks=num_blocks)
                    
                    if trades:
                        forensics.detect_self_trades()
                        forensics.detect_circular_trades()
                        st.session_state.initialized = True
                        st.session_state.last_update = datetime.now()
                        st.toast(f"✅ 获取 {len(trades)} 笔真实交易!", icon="🎉")
                    else:
                        st.warning("未获取到交易数据")
                else:
                    st.error("节点连接失败")
            
            st.rerun()
        
        # 流式监控
        st.markdown("---")
        st.markdown("##### 📺 实时监控")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("▶️ 启动", use_container_width=True, disabled=st.session_state.streaming):
                if not st.session_state.forensics:
                    st.session_state.forensics = OnChainForensics()
                if not st.session_state.monitor:
                    st.session_state.monitor = StreamingMonitor(st.session_state.forensics)
                
                market_cache.preload()
                st.session_state.monitor.start(poll_interval=15.0, blocks_per_poll=20)
                st.session_state.streaming = True
                st.session_state.initialized = True
                st.rerun()
        
        with col2:
            if st.button("⏹️ 停止", use_container_width=True, disabled=not st.session_state.streaming):
                if st.session_state.monitor:
                    st.session_state.monitor.stop()
                st.session_state.streaming = False
                st.rerun()
        
        if st.session_state.streaming:
            st.markdown("""
            <div style="background: rgba(0, 200, 83, 0.1); border: 1px solid #00c853; 
                        border-radius: 8px; padding: 8px; text-align: center; margin-top: 8px;">
                <span class="live-badge" style="font-size: 0.8rem;">🔴 LIVE</span>
            </div>
            """, unsafe_allow_html=True)
        
        # 统计摘要
        if st.session_state.initialized and st.session_state.forensics:
            st.markdown("---")
            summary = st.session_state.forensics.get_summary()
            
            st.markdown(f"""
            <div style="background: rgba(26, 26, 46, 0.5); border-radius: 8px; padding: 12px;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                    <span style="color: #888;">交易数</span>
                    <span style="color: #fff; font-weight: 600;">{summary['total_trades']:,}</span>
                </div>
                <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                    <span style="color: #888;">可疑交易</span>
                    <span style="color: #ff5252; font-weight: 600;">{summary['wash_trades']:,}</span>
                </div>
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: #888;">总交易量</span>
                    <span style="color: #00e676; font-weight: 600;">${summary['organic_volume']:,.0f}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            if st.session_state.last_update:
                st.caption(f"更新于 {st.session_state.last_update.strftime('%H:%M:%S')}")
        
        return None


def render_header():
    """渲染页面头部"""
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.markdown("""
        <h1 class="cyber-title">PolySleuth Pro</h1>
        <p class="cyber-subtitle">Polymarket 链上取证分析 · 实时刷量检测 · 市场健康监控</p>
        """, unsafe_allow_html=True)
    
    with col2:
        if st.session_state.initialized:
            st.markdown("""
            <div style="text-align: right; padding-top: 20px;">
                <span class="live-badge">✓ 真实链上数据</span>
            </div>
            """, unsafe_allow_html=True)


def render_kpi_row(summary: Dict):
    """渲染 KPI 指标行"""
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        organic_pct = summary['organic_ratio'] * 100
        delta = f"{'↑' if organic_pct > 85 else '↓'} {abs(organic_pct - 85):.1f}% vs 基准"
        render_neon_metric(
            "有机交易率",
            f"{organic_pct:.1f}%",
            delta,
            "positive" if organic_pct > 85 else "negative",
            "🧹"
        )
    
    with col2:
        render_neon_metric(
            "真实交易数",
            f"{summary['total_trades']:,}",
            f"区块 #{summary['last_block']:,}",
            "neutral",
            "📊"
        )
    
    with col3:
        wash_pct = summary['wash_ratio'] * 100
        render_neon_metric(
            "可疑交易",
            f"{summary['wash_trades']:,}",
            f"{wash_pct:.1f}% 刷量率",
            "negative" if wash_pct > 5 else "positive",
            "🚨"
        )
    
    with col4:
        render_neon_metric(
            "有机交易量",
            f"${summary['organic_volume']:,.0f}",
            f"${summary['wash_volume']:,.0f} 刷量",
            "warning" if summary['wash_volume'] > 1000 else "positive",
            "💰"
        )


def render_overview_page():
    """渲染总览页面"""
    if not st.session_state.initialized:
        st.markdown("""
        <div class="glass-card" style="text-align: center; padding: 60px;">
            <h2 style="color: #667eea;">👋 欢迎使用 PolySleuth Pro</h2>
            <p style="color: #888; font-size: 1.1rem; margin: 20px 0;">
                请点击左侧边栏的「获取链上数据」开始分析
            </p>
            <div style="display: flex; justify-content: center; gap: 40px; margin-top: 40px;">
                <div>
                    <span style="font-size: 2rem;">🔍</span>
                    <p style="color: #666;">自成交检测</p>
                </div>
                <div>
                    <span style="font-size: 2rem;">🔄</span>
                    <p style="color: #666;">环形交易</p>
                </div>
                <div>
                    <span style="font-size: 2rem;">⚡</span>
                    <p style="color: #666;">原子刷量</p>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        return
    
    forensics = st.session_state.forensics
    summary = forensics.get_summary()
    
    # KPI 行
    render_kpi_row(summary)
    
    st.markdown("---")
    
    # 主图表区
    col_left, col_right = st.columns([2, 1])
    
    with col_left:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        fig = create_stacked_area_chart(forensics.trades, "📈 交易量时序分析 (Organic vs Wash)")
        st.plotly_chart(fig, use_container_width=True, key="overview_stacked_area")
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col_right:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.markdown("### 🔔 实时警报")
        
        alerts = forensics.get_alerts(limit=5)
        if alerts:
            for alert in alerts:
                severity = "🔴" if alert['confidence'] > 0.9 else "🟠" if alert['confidence'] > 0.8 else "🟡"
                st.markdown(f"""
                <div class="alert-card {'warning' if alert['confidence'] < 0.9 else ''}">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span>{severity} <b>{alert['type']}</b></span>
                        <span style="color: #888; font-size: 0.8rem;">{alert['confidence']:.0%}</span>
                    </div>
                    <div style="color: #888; font-size: 0.85rem; margin-top: 4px;">
                        💰 ${alert['volume']:,.2f} · 📊 {alert['trade_count']} 笔
                    </div>
                    <div style="color: #667eea; font-size: 0.75rem; margin-top: 4px;">
                        {alert['tx_hash'][:20]}...
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.success("✨ 暂无警报")
        st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 热门市场 - Polymarket 风格卡片设计
    st.markdown("### 🏆 热门交易市场")
    
    if not forensics._market_map_loaded:
        market_cache.preload()
        forensics.load_market_map()
    
    with st.spinner("🔄 加载市场数据..."):
        markets_summary = forensics.get_markets_summary()[:12]  # Top 12 for 3x4 grid
    
    if markets_summary:
        # 3列卡片布局 (更宽敞)
        cols_per_row = 3
        for row_start in range(0, len(markets_summary), cols_per_row):
            cols = st.columns(cols_per_row, gap="medium")
            for i, col in enumerate(cols):
                idx = row_start + i
                if idx >= len(markets_summary):
                    break
                
                m = markets_summary[idx]
                question = m['question']
                is_token_id = question.startswith("Token ")
                
                # 截断显示
                display_question = question[:55] + '...' if len(question) > 55 else question
                
                # 计算风险等级和颜色
                wash_ratio = m['wash_ratio']
                if wash_ratio > 0.2:
                    risk_color = "#ff5252"
                    risk_bg = "rgba(255, 82, 82, 0.1)"
                    risk_text = "High Risk"
                elif wash_ratio > 0.05:
                    risk_color = "#ff9100"
                    risk_bg = "rgba(255, 145, 0, 0.1)"
                    risk_text = "Medium"
                else:
                    risk_color = "#00e676"
                    risk_bg = "rgba(0, 230, 118, 0.1)"
                    risk_text = "Low Risk"
                
                # 热度标签
                if idx == 0:
                    rank_badge = '<span style="background: linear-gradient(135deg, #FFD700, #FFA500); color: #000; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 700;">🥇 TOP 1</span>'
                elif idx == 1:
                    rank_badge = '<span style="background: linear-gradient(135deg, #C0C0C0, #A0A0A0); color: #000; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 700;">🥈 TOP 2</span>'
                elif idx == 2:
                    rank_badge = '<span style="background: linear-gradient(135deg, #CD7F32, #B87333); color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 700;">🥉 TOP 3</span>'
                elif idx < 6:
                    rank_badge = '<span style="background: rgba(255, 100, 50, 0.2); color: #ff6432; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 600;">🔥 Hot</span>'
                else:
                    rank_badge = ''
                
                with col:
                    st.markdown(f"""
                    <div style="
                        background: linear-gradient(145deg, #1a1a2e, #16213e);
                        border-radius: 12px;
                        padding: 16px 18px;
                        border: 1px solid rgba(255, 255, 255, 0.08);
                        margin-bottom: 16px;
                        transition: all 0.3s ease;
                        cursor: pointer;
                    " class="market-card" onmouseover="this.style.transform='translateY(-4px)';this.style.boxShadow='0 12px 40px rgba(102, 126, 234, 0.2)';" onmouseout="this.style.transform='translateY(0)';this.style.boxShadow='none';">
                        
                        <!-- 头部: 排名标签 -->
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                            {rank_badge}
                            <span style="background: {risk_bg}; color: {risk_color}; padding: 3px 10px; border-radius: 12px; font-size: 0.7rem; font-weight: 600;">
                                {risk_text}
                            </span>
                        </div>
                        
                        <!-- 市场问题 -->
                        <div style="
                            font-size: 0.95rem;
                            color: {'#666' if is_token_id else '#fff'};
                            font-weight: 600;
                            line-height: 1.4;
                            margin-bottom: 16px;
                            min-height: 45px;
                        ">
                            {display_question}
                        </div>
                        
                        <!-- 统计数据网格 -->
                        <div style="
                            display: grid;
                            grid-template-columns: 1fr 1fr;
                            gap: 12px;
                            padding: 12px 0;
                            border-top: 1px solid rgba(255,255,255,0.06);
                            border-bottom: 1px solid rgba(255,255,255,0.06);
                        ">
                            <div>
                                <div style="color: #666; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px;">交易量</div>
                                <div style="color: #00e676; font-weight: 700; font-size: 1.1rem;">${m['volume']:,.0f}</div>
                            </div>
                            <div>
                                <div style="color: #666; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px;">交易数</div>
                                <div style="color: #fff; font-weight: 600; font-size: 1rem;">{m['trade_count']:,}</div>
                            </div>
                            <div>
                                <div style="color: #666; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px;">活跃用户</div>
                                <div style="color: #667eea; font-weight: 600; font-size: 1rem;">{m['unique_traders']}</div>
                            </div>
                            <div>
                                <div style="color: #666; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px;">可疑交易</div>
                                <div style="color: {risk_color}; font-weight: 600; font-size: 1rem;">{m['wash_count']} <span style="font-size: 0.75rem; opacity: 0.8;">({m['wash_ratio']:.1%})</span></div>
                            </div>
                        </div>
                        
                        <!-- 底部状态 -->
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 12px;">
                            <span style="color: #888; font-size: 0.75rem;">
                                📊 {len(m.get('outcomes', []))} outcomes
                            </span>
                            <span style="color: #667eea; font-size: 0.75rem; font-weight: 500;">
                                View Details →
                            </span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
    else:
        st.info("暂无市场数据")


def render_detection_page():
    """渲染刷量检测页面"""
    if not st.session_state.initialized:
        st.warning("⚠️ 请先获取数据")
        return
    
    forensics = st.session_state.forensics
    summary = forensics.get_summary()
    
    # 顶部统计
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_neon_metric("分析交易数", f"{summary['total_trades']:,}", icon="🔍")
    with col2:
        render_neon_metric("检测刷量", f"{summary['wash_trades']:,}", delta_type="negative", icon="🚨")
    with col3:
        render_neon_metric("刷量比例", f"{summary['wash_ratio']:.1%}", delta_type="warning", icon="📈")
    with col4:
        render_neon_metric("刷量金额", f"${summary['wash_volume']:,.0f}", delta_type="negative", icon="💰")
    
    st.markdown("---")
    
    # 两列布局：Sunburst + 网络图
    col_left, col_right = st.columns(2)
    
    with col_left:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        fig = create_sunburst_chart(forensics.trades)
        st.plotly_chart(fig, use_container_width=True, key="detection_sunburst")
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col_right:
        st.markdown('<div class="glass-card network-container">', unsafe_allow_html=True)
        fig = create_network_graph(forensics.trades, limit=30)
        st.plotly_chart(fig, use_container_width=True, key="detection_network")
        st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 可疑交易表格
    st.markdown("### 🚨 可疑交易列表")
    
    wash_trades = forensics.get_wash_trades(limit=100)
    
    if wash_trades:
        df = pd.DataFrame(wash_trades)
        df['级别'] = df['confidence'].apply(
            lambda x: '🔴 高危' if x >= 0.9 else '🟠 中危' if x >= 0.8 else '🟡 低危'
        )
        df['类型'] = df['type']  # 重命名 type -> 类型
        df['市场'] = df['token_id'].apply(lambda x: market_cache.get_market_name(x)[:40])
        df['金额'] = df['volume'].apply(lambda x: f"${x:,.2f}")
        df['置信度'] = df['confidence'].apply(lambda x: f"{x:.0%}")
        df['Maker'] = df['maker'].apply(lambda x: f"{x[:10]}...{x[-4:]}")
        df['Taker'] = df['taker'].apply(lambda x: f"{x[:10]}...{x[-4:]}")
        df['交易哈希'] = df['tx_hash']
        
        display_df = df[['级别', '类型', '市场', '金额', '置信度', 'Maker', 'Taker', '交易哈希']]
        
        render_aggrid_table(display_df, height=400)
        
        # 下载按钮
        csv = df.to_csv(index=False)
        st.download_button(
            "📥 导出完整报告 (CSV)",
            csv,
            f"polysleuth_wash_trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "text/csv",
        )
    else:
        st.success("✨ 未检测到刷量交易")


def render_market_health_page():
    """渲染市场健康度页面"""
    if not st.session_state.initialized:
        st.warning("⚠️ 请先获取数据")
        return
    
    forensics = st.session_state.forensics
    
    # 确保市场名称已加载
    if not forensics._market_map_loaded:
        market_cache.preload()
        forensics.load_market_map()
    
    health_data = forensics.get_all_health()
    
    if not health_data:
        st.info("暂无市场数据")
        return
    
    df = pd.DataFrame(health_data)
    df['market_name'] = df['token_id'].apply(lambda x: market_cache.get_market_name(x))
    
    # 风险分布
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        low = len(df[df['health_score'] >= 80])
        render_neon_metric("🟢 低风险", f"{low} 个", delta_type="positive")
    with col2:
        med = len(df[(df['health_score'] >= 60) & (df['health_score'] < 80)])
        render_neon_metric("🟡 中风险", f"{med} 个", delta_type="neutral")
    with col3:
        high = len(df[(df['health_score'] >= 40) & (df['health_score'] < 60)])
        render_neon_metric("🟠 高风险", f"{high} 个", delta_type="warning")
    with col4:
        critical = len(df[df['health_score'] < 40])
        render_neon_metric("🔴 极高风险", f"{critical} 个", delta_type="negative")
    
    st.markdown("---")
    
    # 可视化
    col_left, col_right = st.columns(2)
    
    with col_left:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        fig = px.histogram(
            df, x='health_score', nbins=10,
            title='<b>健康度分布</b>',
            color_discrete_sequence=['#667eea'],
        )
        fig.update_layout(
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#888'),
            xaxis=dict(title='健康度评分', gridcolor='rgba(255,255,255,0.05)'),
            yaxis=dict(title='市场数量', gridcolor='rgba(255,255,255,0.05)'),
            height=350,
        )
        st.plotly_chart(fig, use_container_width=True, key="health_histogram")
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col_right:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        fig = create_treemap_chart(forensics.trades)
        st.plotly_chart(fig, use_container_width=True, key="health_treemap")
        st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 市场列表
    st.markdown("### 📋 市场健康度排名")
    
    df['风险等级'] = df['health_score'].apply(
        lambda x: "🟢 低风险" if x >= 80 else "🟡 中风险" if x >= 60 else "🟠 高风险" if x >= 40 else "🔴 极高风险"
    )
    df['刷量比例'] = df['wash_ratio'].apply(lambda x: f"{x:.1%}")
    df['交易量'] = df['total_volume'].apply(lambda x: f"${x:,.0f}")
    
    display_df = df[['风险等级', 'market_name', 'health_score', '刷量比例', '交易量', 'total_trades', 'unique_traders']].rename(
        columns={
            'market_name': '市场名称',
            'health_score': '健康度',
            'total_trades': '交易数',
            'unique_traders': '活跃用户',
        }
    )
    
    render_aggrid_table(display_df, height=400)


def render_trade_details_page():
    """渲染交易详情页面"""
    if not st.session_state.initialized:
        st.warning("⚠️ 请先获取数据")
        return
    
    forensics = st.session_state.forensics
    
    if not forensics._market_map_loaded:
        market_cache.preload()
        forensics.load_market_map()
    
    # 搜索选项卡
    tab1, tab2, tab3 = st.tabs(["🔍 交易哈希", "👛 钱包地址", "🏷️ 按市场"])
    
    with tab1:
        tx_hash = st.text_input("输入交易哈希", placeholder="0x...", key="tx_search")
        
        if tx_hash:
            tx_hash = tx_hash.lower()
            related = [t for t in forensics.trades if t.tx_hash.lower() == tx_hash]
            
            if related:
                st.success(f"找到 {len(related)} 笔交易")
                
                for t in related:
                    market_name = market_cache.get_market_name(t.token_id)
                    
                    with st.expander(f"📌 {market_name}", expanded=True):
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            st.markdown(f"**区块**: #{t.block_number:,}")
                            st.markdown(f"**时间**: {t.timestamp}")
                            st.markdown(f"**方向**: {t.side}")
                            st.markdown(f"**价格**: ${t.price:.4f}")
                            st.markdown(f"**规模**: {t.size:,.2f}")
                        
                        with col2:
                            st.markdown(f"**Maker**: `{t.maker}`")
                            st.markdown(f"**Taker**: `{t.taker}`")
                            
                            if t.is_wash:
                                st.error(f"⚠️ 可疑: {t.wash_type} ({t.wash_confidence:.0%})")
                            else:
                                st.success("✅ 正常交易")
                        
                        st.markdown(f"[🔗 在 Polygonscan 查看](https://polygonscan.com/tx/{t.tx_hash})")
            else:
                st.warning("未找到该交易")
    
    with tab2:
        address = st.text_input("输入钱包地址", placeholder="0x...", key="addr_search")
        
        if address:
            address = address.lower()
            related = [t for t in forensics.trades if t.maker.lower() == address or t.taker.lower() == address]
            
            if related:
                # 统计
                total_volume = sum(t.size * t.price for t in related)
                wash_count = sum(1 for t in related if t.is_wash)
                as_maker = sum(1 for t in related if t.maker.lower() == address)
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    render_neon_metric("交易次数", f"{len(related)}", icon="📊")
                with col2:
                    render_neon_metric("总交易量", f"${total_volume:,.0f}", icon="💰")
                with col3:
                    render_neon_metric("可疑交易", f"{wash_count}", delta_type="negative" if wash_count > 0 else "positive", icon="🚨")
                with col4:
                    render_neon_metric("Maker/Taker", f"{as_maker}/{len(related)-as_maker}", icon="🔄")
                
                # 交易列表
                df = pd.DataFrame([{
                    '时间': t.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    '市场': market_cache.get_market_name(t.token_id)[:35],
                    '方向': t.side,
                    '金额': f"${t.size * t.price:,.2f}",
                    '角色': 'Maker' if t.maker.lower() == address else 'Taker',
                    '状态': '🚨' if t.is_wash else '✅',
                    '交易哈希': t.tx_hash,
                } for t in related])
                
                render_aggrid_table(df, height=400)
            else:
                st.warning("未找到该地址的交易")
    
    with tab3:
        markets_summary = forensics.get_markets_summary()
        
        if markets_summary:
            market_options = [market_cache.get_market_name(m['token_id']) for m in markets_summary[:50]]
            selected_idx = st.selectbox("选择市场", range(len(market_options)), format_func=lambda i: market_options[i])
            
            if selected_idx is not None:
                selected = markets_summary[selected_idx]
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    render_neon_metric("交易次数", f"{selected['trade_count']}", icon="📊")
                with col2:
                    render_neon_metric("总交易量", f"${selected['volume']:,.0f}", icon="💰")
                with col3:
                    render_neon_metric("可疑交易", f"{selected['wash_count']}", delta_type="negative" if selected['wash_count'] > 0 else "positive", icon="🚨")
                with col4:
                    render_neon_metric("活跃用户", f"{selected['unique_traders']}", icon="👥")
                
                # 该市场的交易 (使用 token_ids 列表匹配)
                token_ids = selected.get('token_ids', [])
                market_trades = [t for t in forensics.trades if t.token_id in token_ids][-100:]
                
                df = pd.DataFrame([{
                    '时间': t.timestamp.strftime('%H:%M:%S'),
                    '方向': t.side,
                    '价格': f"${t.price:.4f}",
                    '数量': f"{t.size:,.2f}",
                    '金额': f"${t.size * t.price:,.2f}",
                    'Maker': f"{t.maker[:10]}...",
                    'Taker': f"{t.taker[:10]}...",
                    '状态': '🚨' if t.is_wash else '✅',
                } for t in market_trades])
                
                render_aggrid_table(df, height=400)
    
    st.markdown("---")
    
    # 高频地址
    st.markdown("### 🔥 高频交易地址")
    
    address_stats = defaultdict(lambda: {'count': 0, 'volume': 0, 'wash': 0})
    for t in forensics.trades:
        for addr in [t.maker, t.taker]:
            address_stats[addr]['count'] += 1
            address_stats[addr]['volume'] += t.size * t.price
            if t.is_wash:
                address_stats[addr]['wash'] += 1
    
    top_addresses = sorted(address_stats.items(), key=lambda x: x[1]['count'], reverse=True)[:20]
    
    df = pd.DataFrame([{
        '地址': f"{addr[:10]}...{addr[-6:]}",
        '交易次数': stats['count'],
        '总交易量': f"${stats['volume']:,.0f}",
        '可疑交易': stats['wash'],
        '可疑比例': f"{stats['wash']/stats['count']*100:.1f}%" if stats['count'] > 0 else "0%",
        '风险': '🔴' if stats['wash']/stats['count'] > 0.3 else '🟡' if stats['wash']/stats['count'] > 0.1 else '🟢',
    } for addr, stats in top_addresses])
    
    render_aggrid_table(df, height=350)


# ============================================================================
# 🚀 主程序
# ============================================================================

def main():
    # 页面配置
    st.set_page_config(
        page_title="PolySleuth Pro - Polymarket Forensics",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    
    # 注入 CSS
    st.markdown(CYBERPUNK_CSS, unsafe_allow_html=True)
    
    # 初始化状态
    init_state()
    
    # 渲染侧边栏
    render_sidebar()
    
    # 主内容区
    render_header()
    
    # 选项卡导航
    tab1, tab2, tab3, tab4 = st.tabs([
        "🏠 总览",
        "🔬 刷量检测", 
        "💊 市场健康",
        "🕵️ 交易详情"
    ])
    
    with tab1:
        render_overview_page()
    
    with tab2:
        render_detection_page()
    
    with tab3:
        render_market_health_page()
    
    with tab4:
        render_trade_details_page()
    
    # 流式监控自动刷新
    if st.session_state.streaming:
        time.sleep(10)
        st.rerun()


if __name__ == "__main__":
    main()
