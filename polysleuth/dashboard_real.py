"""
PolySleuth - 真实数据取证仪表板

使用 Chainstack Polygon 节点获取真实链上数据
所有分析结果均基于真实交易，无模拟数据
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import time
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()

# 导入取证模块
from real_forensics import (
    OnChainForensics, StreamingMonitor,
    get_forensics, get_monitor,
    POLYGON_RPC_URL, CTF_EXCHANGE, NEG_RISK_EXCHANGE
)
from data_fetcher import PolymarketDataFetcher

# ============================================================================
# 页面配置
# ============================================================================

st.set_page_config(
    page_title="PolySleuth - 真实链上取证",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .real-data-badge {
        background: linear-gradient(90deg, #00c853 0%, #00e676 100%);
        color: white;
        padding: 4px 12px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: bold;
    }
    .metric-card {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        padding: 20px;
        border-radius: 12px;
        border: 1px solid #0f3460;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# 状态管理
# ============================================================================

def init_state():
    if 'forensics' not in st.session_state:
        st.session_state.forensics = None
    if 'monitor' not in st.session_state:
        st.session_state.monitor = None
    if 'fetcher' not in st.session_state:
        st.session_state.fetcher = PolymarketDataFetcher()
    if 'initialized' not in st.session_state:
        st.session_state.initialized = False
    if 'streaming' not in st.session_state:
        st.session_state.streaming = False
    if 'last_update' not in st.session_state:
        st.session_state.last_update = None


# ============================================================================
# 侧边栏
# ============================================================================

def render_sidebar():
    with st.sidebar:
        st.markdown('<p class="main-header">🔍 PolySleuth</p>', unsafe_allow_html=True)
        st.markdown('<span class="real-data-badge">✓ 真实链上数据</span>', unsafe_allow_html=True)
        
        st.divider()
        
        # 连接状态
        st.subheader("🔗 节点连接")
        
        rpc_display = POLYGON_RPC_URL[:40] + "..." if len(POLYGON_RPC_URL) > 40 else POLYGON_RPC_URL
        st.caption(f"RPC: {rpc_display}")
        
        if st.session_state.forensics and st.session_state.forensics.w3:
            if st.session_state.forensics.w3.is_connected():
                block = st.session_state.forensics.w3.eth.block_number
                st.success(f"✅ 已连接 (区块: {block:,})")
            else:
                st.error("❌ 连接断开")
        else:
            st.info("⏳ 未初始化")
        
        st.divider()
        
        # 数据控制
        st.subheader("📡 数据控制")
        
        col1, col2 = st.columns(2)
        
        with col1:
            num_blocks = st.number_input("区块数", min_value=10, max_value=500, value=100, step=10)
        
        with col2:
            if st.button("🚀 获取数据", use_container_width=True):
                with st.spinner(f"获取最近 {num_blocks} 个区块..."):
                    if not st.session_state.forensics:
                        st.session_state.forensics = OnChainForensics()
                    
                    forensics = st.session_state.forensics
                    
                    if forensics.w3 and forensics.w3.is_connected():
                        trades = forensics.fetch_recent_trades(num_blocks=num_blocks)
                        
                        if trades:
                            forensics.detect_self_trades()
                            forensics.detect_circular_trades()
                            st.session_state.initialized = True
                            st.session_state.last_update = datetime.now()
                            st.success(f"✅ 获取 {len(trades)} 笔真实交易!")
                        else:
                            st.warning("未获取到交易数据")
                    else:
                        st.error("节点连接失败")
                
                st.rerun()
        
        # 流式监控
        st.divider()
        st.subheader("📺 流式监控")
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("▶️ 启动", use_container_width=True, disabled=st.session_state.streaming):
                if not st.session_state.forensics:
                    st.session_state.forensics = OnChainForensics()
                if not st.session_state.monitor:
                    st.session_state.monitor = StreamingMonitor(st.session_state.forensics)
                
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
            st.success("🟢 监控运行中")
        else:
            st.info("⚪ 监控未启动")
        
        # 状态显示
        if st.session_state.initialized and st.session_state.forensics:
            summary = st.session_state.forensics.get_summary()
            st.divider()
            st.subheader("📊 当前状态")
            st.metric("已分析交易", f"{summary['total_trades']:,}")
            st.metric("检测警报", f"{summary['alerts_count']}")
            st.metric("最新区块", f"{summary['last_block']:,}")
            
            if st.session_state.last_update:
                st.caption(f"更新: {st.session_state.last_update.strftime('%H:%M:%S')}")
        
        st.divider()
        
        # 导航
        st.subheader("📑 导航")
        page = st.radio(
            "选择页面",
            ["🏠 总览", "🔬 刷量检测", "💊 市场健康", "🕵️ 交易详情", "📊 市场数据"],
            label_visibility="collapsed"
        )
        
        # 手动刷新
        if st.session_state.streaming:
            if st.button("🔄 手动刷新", use_container_width=True):
                st.session_state.last_update = datetime.now()
                st.rerun()
        
        return page


# ============================================================================
# 总览页面
# ============================================================================

def render_overview():
    st.header("🏠 真实链上取证总览")
    
    if not st.session_state.initialized:
        st.warning("⚠️ 请先点击侧边栏的「获取数据」按钮")
        
        st.markdown("""
        ### 📖 关于 PolySleuth
        
        **PolySleuth** 是一个基于真实链上数据的 Polymarket 取证分析工具。
        
        #### 数据来源
        - **Polygon 链上日志**: 通过 Chainstack 节点直接获取
        - **合约事件**: `OrderFilled`, `PositionSplit`, `PositionsMerge`
        - **实时监控**: 支持流式数据获取
        
        #### 检测能力
        - 🔴 **自成交检测**: Maker == Taker (置信度 100%)
        - 🟠 **环形交易**: A→B→A 模式 (置信度 85%)
        - 🟡 **原子刷量**: Split→Trade→Merge (置信度 90%+)
        
        #### 使用方法
        1. 点击侧边栏「获取数据」获取历史数据
        2. 或点击「启动」开启流式监控
        3. 浏览各页面查看分析结果
        """)
        return
    
    forensics = st.session_state.forensics
    summary = forensics.get_summary()
    
    # 真实数据徽章
    st.markdown("""
    <div style="background: linear-gradient(90deg, #1a1a2e 0%, #16213e 100%); 
                padding: 10px 20px; border-radius: 8px; margin-bottom: 20px;
                border: 1px solid #00c853;">
        <span style="color: #00c853; font-weight: bold;">✓ 真实链上数据</span>
        <span style="color: #888; margin-left: 20px;">
            所有分析结果均来自 Polygon 链上真实交易日志
        </span>
    </div>
    """, unsafe_allow_html=True)
    
    # 核心指标
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        organic_pct = summary['organic_ratio'] * 100
        st.metric(
            "🧹 有机交易率",
            f"{organic_pct:.1f}%",
            delta=f"{organic_pct - 85:.1f}% vs 基准",
            delta_color="normal" if organic_pct > 85 else "inverse"
        )
    
    with col2:
        st.metric(
            "📊 真实交易数",
            f"{summary['total_trades']:,}",
            delta=f"区块 {summary['last_block']:,}"
        )
    
    with col3:
        st.metric(
            "🚨 可疑交易",
            f"{summary['wash_trades']:,}",
            delta=f"{summary['wash_ratio']:.1%}",
            delta_color="inverse" if summary['wash_ratio'] > 0.1 else "normal"
        )
    
    with col4:
        st.metric(
            "💰 有机交易量",
            f"${summary['organic_volume']:,.0f}",
        )
    
    st.divider()
    
    # 图表
    col_left, col_right = st.columns([2, 1])
    
    with col_left:
        st.subheader("📈 交易时序分布")
        
        trades = forensics.trades
        if trades:
            df = pd.DataFrame([
                {
                    'timestamp': t.timestamp,
                    'volume': t.size * t.price,
                    'type': '🚨 可疑' if t.is_wash else '✅ 正常',
                    'wash_type': t.wash_type if t.is_wash else 'Normal',
                }
                for t in trades
            ])
            
            df['hour'] = df['timestamp'].dt.floor('H')
            hourly = df.groupby(['hour', 'type']).agg({'volume': 'sum'}).reset_index()
            
            fig = px.bar(
                hourly,
                x='hour',
                y='volume',
                color='type',
                color_discrete_map={'✅ 正常': '#44ff44', '🚨 可疑': '#ff4444'},
                title='交易量时序分布 (真实数据)',
            )
            fig.update_layout(height=350, barmode='stack')
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("暂无交易数据")
    
    with col_right:
        st.subheader("🔔 最新警报")
        
        alerts = forensics.get_alerts(limit=5)
        if alerts:
            for alert in alerts:
                emoji = "🔴" if alert['confidence'] > 0.9 else "🟠" if alert['confidence'] > 0.8 else "🟡"
                with st.container():
                    st.markdown(f"""
                    {emoji} **{alert['type']}**  
                    📊 {alert['trade_count']} 笔交易  
                    💰 ${alert['volume']:,.2f}  
                    🎯 置信度: {alert['confidence']:.0%}  
                    `{alert['tx_hash'][:16]}...`
                    """)
                    st.divider()
        else:
            st.success("✨ 暂无警报")
    
    # 热门市场汇总
    st.divider()
    st.subheader("🏆 热门交易市场 (按交易量)")
    
    # 加载市场映射
    if not forensics._market_map_loaded:
        with st.spinner("加载市场名称..."):
            forensics.load_market_map()
    
    markets_summary = forensics.get_markets_summary()[:10]  # Top 10
    
    if markets_summary:
        market_data = [{
            '市场': m['question'][:45] + '...' if len(m['question']) > 45 else m['question'],
            '结果': m['outcome'],
            '交易数': m['trade_count'],
            '交易量': f"${m['volume']:,.0f}",
            '可疑': m['wash_count'],
            '刷量率': f"{m['wash_ratio']:.1%}",
            '用户数': m['unique_traders'],
        } for m in markets_summary]
        
        st.dataframe(pd.DataFrame(market_data), hide_index=True, use_container_width=True)
    else:
        st.info("暂无市场数据")
    
    # 刷量类型分布
    wash_trades = [t for t in trades if t.is_wash] if trades else []
    if wash_trades:
        st.subheader("📊 刷量类型分布")
        
        type_counts = defaultdict(int)
        type_volume = defaultdict(float)
        for t in wash_trades:
            type_counts[t.wash_type] += 1
            type_volume[t.wash_type] += t.size * t.price
        
        col1, col2 = st.columns(2)
        
        with col1:
            fig = px.pie(
                names=list(type_counts.keys()),
                values=list(type_counts.values()),
                title='按数量',
                color_discrete_sequence=px.colors.qualitative.Set3,
            )
            fig.update_layout(height=280)
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            fig = px.pie(
                names=list(type_volume.keys()),
                values=list(type_volume.values()),
                title='按金额',
                color_discrete_sequence=px.colors.qualitative.Pastel,
            )
            fig.update_layout(height=280)
            st.plotly_chart(fig, use_container_width=True)


# ============================================================================
# 刷量检测页面
# ============================================================================

def render_wash_detection():
    st.header("🔬 刷量检测 (真实数据)")
    
    if not st.session_state.initialized:
        st.warning("⚠️ 请先获取数据")
        return
    
    forensics = st.session_state.forensics
    summary = forensics.get_summary()
    
    # 检测原理
    with st.expander("📖 检测算法说明", expanded=False):
        st.markdown("""
        ### 基于真实链上事件的检测
        
        **1. 自成交检测 (SELF_TRADE)**
        ```
        检测条件: OrderFilled.maker == OrderFilled.taker
        置信度: 100%
        ```
        
        **2. 环形交易检测 (CIRCULAR)**
        ```
        检测条件: 60秒内出现 A→B 和 B→A 的反向交易
        置信度: 85%
        ```
        
        **3. 原子刷量检测 (ATOMIC)**
        ```
        检测条件: 同一 tx_hash 中包含:
          - PositionSplit (铸造代币)
          - OrderFilled (交易)
          - PositionsMerge (销毁代币)
        置信度: 90-98%
        ```
        """)
    
    # 统计
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("🔍 分析交易数", f"{summary['total_trades']:,}")
    with col2:
        st.metric("🚨 检测刷量", f"{summary['wash_trades']:,}")
    with col3:
        st.metric("📈 刷量比例", f"{summary['wash_ratio']:.1%}")
    with col4:
        st.metric("💰 刷量金额", f"${summary['wash_volume']:,.0f}")
    
    st.divider()
    
    # 可疑交易表
    st.subheader("🚨 可疑交易列表 (真实链上数据)")
    
    wash_trades = forensics.get_wash_trades(limit=100)
    
    if wash_trades:
        df = pd.DataFrame(wash_trades)
        df['confidence_fmt'] = df['confidence'].apply(lambda x: f"{x:.0%}")
        df['volume_fmt'] = df['volume'].apply(lambda x: f"${x:,.2f}")
        df['tx_link'] = df['tx_hash'].apply(
            lambda x: f"[{x[:16]}...](https://polygonscan.com/tx/{x})"
        )
        df['maker_short'] = df['maker'].apply(lambda x: f"{x[:10]}...{x[-6:]}")
        df['taker_short'] = df['taker'].apply(lambda x: f"{x[:10]}...{x[-6:]}")
        
        # 级别标记
        df['level'] = df['confidence'].apply(
            lambda x: '🔴' if x >= 0.9 else '🟠' if x >= 0.8 else '🟡'
        )
        
        st.dataframe(
            df[['level', 'type', 'volume_fmt', 'confidence_fmt', 'tx_link', 
                'maker_short', 'taker_short', 'timestamp', 'block']].rename(columns={
                'level': '级别',
                'type': '类型',
                'volume_fmt': '金额',
                'confidence_fmt': '置信度',
                'tx_link': '交易哈希',
                'maker_short': 'Maker',
                'taker_short': 'Taker',
                'timestamp': '时间',
                'block': '区块',
            }),
            use_container_width=True,
            hide_index=True,
            height=400,
        )
        
        # 下载
        csv = df.to_csv(index=False)
        st.download_button(
            "📥 下载完整报告 (CSV)",
            csv,
            f"polysleuth_wash_trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "text/csv",
        )
    else:
        st.success("✨ 未检测到刷量交易")


# ============================================================================
# 市场健康页面
# ============================================================================

def render_market_health():
    st.header("💊 市场健康度分析 (真实数据)")
    
    if not st.session_state.initialized:
        st.warning("⚠️ 请先获取数据")
        return
    
    forensics = st.session_state.forensics
    
    # 加载市场名称映射
    if not forensics._market_map_loaded:
        with st.spinner("🔄 加载市场名称映射..."):
            forensics.load_market_map()
    
    health_data = forensics.get_all_health()
    
    if not health_data:
        st.info("暂无市场数据")
        return
    
    # 添加市场名称
    for item in health_data:
        item['market_name'] = forensics.get_market_name(item['token_id'])
    
    df = pd.DataFrame(health_data)
    
    # 统计
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        low_risk = len(df[df['health_score'] >= 80])
        st.metric("🟢 低风险", f"{low_risk} 个")
    with col2:
        med_risk = len(df[(df['health_score'] >= 60) & (df['health_score'] < 80)])
        st.metric("🟡 中风险", f"{med_risk} 个")
    with col3:
        high_risk = len(df[(df['health_score'] >= 40) & (df['health_score'] < 60)])
        st.metric("🟠 高风险", f"{high_risk} 个")
    with col4:
        critical = len(df[df['health_score'] < 40])
        st.metric("🔴 极高风险", f"{critical} 个")
    
    st.divider()
    
    # 可视化
    col_left, col_right = st.columns(2)
    
    with col_left:
        fig = px.histogram(
            df,
            x='health_score',
            nbins=10,
            title='健康度分布',
            color_discrete_sequence=['#4ecdc4'],
        )
        fig.update_layout(xaxis_title='健康度评分', yaxis_title='市场数量', height=300)
        st.plotly_chart(fig, use_container_width=True)
    
    with col_right:
        fig = px.scatter(
            df,
            x='unique_traders',
            y='wash_ratio',
            color='health_score',
            size='total_volume',
            color_continuous_scale=['red', 'yellow', 'green'],
            title='交易者数量 vs 刷量比例',
            hover_data=['market_name'] if 'market_name' in df.columns else None,
        )
        fig.update_layout(height=300)
        st.plotly_chart(fig, use_container_width=True)
    
    # 市场列表
    st.subheader("📋 市场健康度排名")
    
    df['risk_level'] = df['health_score'].apply(
        lambda x: "🟢 低风险" if x >= 80 else "🟡 中风险" if x >= 60 else "🟠 高风险" if x >= 40 else "🔴 极高风险"
    )
    df['wash_pct'] = df['wash_ratio'].apply(lambda x: f"{x:.1%}")
    df['volume_fmt'] = df['total_volume'].apply(lambda x: f"${x:,.0f}")
    
    # 显示市场名称的列表
    display_cols = ['risk_level', 'market_name', 'health_score', 'wash_pct', 
                    'volume_fmt', 'total_trades', 'unique_traders']
    display_cols = [c for c in display_cols if c in df.columns]
    
    st.dataframe(
        df[display_cols].rename(columns={
            'risk_level': '风险等级',
            'market_name': '市场名称',
            'token_id': 'Token ID',
            'health_score': '健康度',
            'wash_pct': '刷量比例',
            'volume_fmt': '总交易量',
            'total_trades': '交易数',
            'unique_traders': '活跃用户',
        }),
        use_container_width=True,
        hide_index=True,
        height=400,
    )
    
    # 市场详情选择
    st.divider()
    st.subheader("🔍 市场详情")
    
    market_options = df['market_name'].tolist() if 'market_name' in df.columns else df['token_id'].tolist()
    selected_market = st.selectbox("选择市场查看详情", market_options)
    
    if selected_market:
        if 'market_name' in df.columns:
            market_row = df[df['market_name'] == selected_market].iloc[0]
        else:
            market_row = df[df['token_id'] == selected_market].iloc[0]
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("健康度评分", f"{market_row['health_score']}")
        with col2:
            st.metric("总交易量", f"${market_row['total_volume']:,.2f}")
        with col3:
            st.metric("刷量比例", f"{market_row['wash_ratio']:.1%}")
        
        # 显示该市场的最近交易
        token_id = market_row['token_id']
        market_trades = [t for t in forensics.trades if t.token_id == token_id][-20:]
        
        if market_trades:
            st.caption(f"最近 {len(market_trades)} 笔交易:")
            trade_data = [{
                '时间': t.timestamp.strftime('%H:%M:%S'),
                '方向': t.side,
                '价格': f"${t.price:.4f}",
                '数量': f"{t.size:,.2f}",
                '状态': '🚨 可疑' if t.is_wash else '✅ 正常',
            } for t in market_trades]
            st.dataframe(pd.DataFrame(trade_data), hide_index=True, use_container_width=True)


# ============================================================================
# 交易详情页面
# ============================================================================

def render_trade_details():
    st.header("🕵️ 交易详情查询")
    
    if not st.session_state.initialized:
        st.warning("⚠️ 请先获取数据")
        return
    
    forensics = st.session_state.forensics
    
    # 确保市场映射已加载
    if not forensics._market_map_loaded:
        forensics.load_market_map()
    
    # 搜索
    search_type = st.radio("搜索类型", ["交易哈希", "地址", "按市场筛选"], horizontal=True)
    
    if search_type == "交易哈希":
        tx_hash = st.text_input("输入交易哈希", placeholder="0x...")
        
        if tx_hash:
            tx_hash = tx_hash.lower()
            related = [t for t in forensics.trades if t.tx_hash.lower() == tx_hash]
            
            if related:
                st.success(f"找到 {len(related)} 笔交易")
                
                for t in related:
                    market_name = forensics.get_market_name(t.token_id)
                    with st.expander(f"交易 #{t.log_index} - {market_name}", expanded=True):
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            st.markdown(f"**市场**: {market_name}")
                            st.markdown(f"**区块**: {t.block_number}")
                            st.markdown(f"**时间**: {t.timestamp}")
                            st.markdown(f"**合约**: `{t.contract}`")
                            st.markdown(f"**方向**: {t.side}")
                            st.markdown(f"**价格**: {t.price:.4f}")
                            st.markdown(f"**规模**: {t.size:,.2f}")
                        
                        with col2:
                            st.markdown(f"**Maker**: `{t.maker}`")
                            st.markdown(f"**Taker**: `{t.taker}`")
                            st.markdown(f"**Token ID**: `{t.token_id[:30]}...`")
                            
                            if t.is_wash:
                                st.error(f"⚠️ 可疑交易: {t.wash_type} (置信度: {t.wash_confidence:.0%})")
                            else:
                                st.success("✅ 正常交易")
                        
                        st.markdown(f"[在 Polygonscan 查看](https://polygonscan.com/tx/{t.tx_hash})")
            else:
                st.warning("未找到该交易")
    
    elif search_type == "按市场筛选":
        # 获取市场汇总
        markets_summary = forensics.get_markets_summary()
        if markets_summary:
            market_options = [f"{m['question'][:50]}..." if len(m['question']) > 50 else m['question'] 
                            for m in markets_summary[:50]]
            selected_idx = st.selectbox("选择市场", range(len(market_options)), 
                                       format_func=lambda i: market_options[i])
            
            if selected_idx is not None:
                selected_market = markets_summary[selected_idx]
                token_id = selected_market['token_id']
                
                # 显示市场统计
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("交易次数", selected_market['trade_count'])
                with col2:
                    st.metric("总交易量", f"${selected_market['volume']:,.2f}")
                with col3:
                    st.metric("可疑交易", selected_market['wash_count'])
                with col4:
                    st.metric("活跃用户", selected_market['unique_traders'])
                
                # 显示该市场的交易
                market_trades = [t for t in forensics.trades if t.token_id == token_id]
                
                st.subheader(f"📋 {selected_market['question'][:60]}...")
                
                trade_data = [{
                    '时间': t.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    '方向': t.side,
                    '价格': f"${t.price:.4f}",
                    '数量': f"{t.size:,.2f}",
                    '金额': f"${t.size * t.price:,.2f}",
                    'Maker': f"{t.maker[:10]}...",
                    'Taker': f"{t.taker[:10]}...",
                    '状态': '🚨' if t.is_wash else '✅',
                    '交易哈希': t.tx_hash[:16] + '...',
                } for t in market_trades[-100:]]  # 最近100笔
                
                st.dataframe(pd.DataFrame(trade_data), hide_index=True, use_container_width=True, height=400)
        else:
            st.info("暂无市场数据")
    
    else:  # 地址搜索
        address = st.text_input("输入钱包地址", placeholder="0x...")
        
        if address:
            address = address.lower()
            related = [t for t in forensics.trades 
                      if t.maker.lower() == address or t.taker.lower() == address]
            
            if related:
                st.success(f"找到 {len(related)} 笔相关交易")
                
                # 统计
                total_volume = sum(t.size * t.price for t in related)
                wash_count = sum(1 for t in related if t.is_wash)
                as_maker = sum(1 for t in related if t.maker.lower() == address)
                as_taker = len(related) - as_maker
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("交易次数", len(related))
                with col2:
                    st.metric("总交易量", f"${total_volume:,.2f}")
                with col3:
                    st.metric("可疑交易", wash_count)
                with col4:
                    st.metric("Maker/Taker", f"{as_maker}/{as_taker}")
                
                # 交易列表
                df = pd.DataFrame([
                    {
                        'time': t.timestamp,
                        'tx_hash': t.tx_hash[:20] + '...',
                        'side': t.side,
                        'price': t.price,
                        'size': t.size,
                        'volume': t.size * t.price,
                        'role': 'Maker' if t.maker.lower() == address else 'Taker',
                        'status': '🚨' if t.is_wash else '✅',
                    }
                    for t in related
                ])
                
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.warning("未找到该地址的交易")
    
    st.divider()
    
    # 高频地址
    st.subheader("🔥 高频交易地址")
    
    address_stats = defaultdict(lambda: {'count': 0, 'volume': 0, 'wash': 0})
    for t in forensics.trades:
        address_stats[t.maker]['count'] += 1
        address_stats[t.maker]['volume'] += t.size * t.price
        if t.is_wash:
            address_stats[t.maker]['wash'] += 1
        
        address_stats[t.taker]['count'] += 1
        address_stats[t.taker]['volume'] += t.size * t.price
        if t.is_wash:
            address_stats[t.taker]['wash'] += 1
    
    top_addresses = sorted(address_stats.items(), key=lambda x: x[1]['count'], reverse=True)[:20]
    
    df = pd.DataFrame([
        {
            '地址': f"{addr[:10]}...{addr[-6:]}",
            '交易次数': stats['count'],
            '总交易量': f"${stats['volume']:,.0f}",
            '可疑交易': stats['wash'],
            '可疑比例': f"{stats['wash']/stats['count']*100:.1f}%" if stats['count'] > 0 else "0%",
        }
        for addr, stats in top_addresses
    ])
    
    st.dataframe(df, use_container_width=True, hide_index=True)


# ============================================================================
# 市场数据页面
# ============================================================================

def render_market_data():
    st.header("📊 实时市场数据")
    
    fetcher = st.session_state.fetcher
    
    query = st.text_input("🔍 搜索市场", placeholder="输入关键词...")
    
    if st.button("🔄 刷新市场"):
        st.rerun()
    
    with st.spinner("加载市场数据..."):
        if query:
            markets = fetcher.search_markets(query=query, limit=20)
        else:
            markets = fetcher.get_active_markets(limit=20)
    
    if markets:
        st.success(f"显示 {len(markets)} 个市场")
        
        for market in markets[:10]:
            question = market.get('question', 'Unknown')
            volume = float(market.get('volume', 0))
            liquidity = float(market.get('liquidity', 0))
            
            prices = market.get('outcomePrices', [])
            if prices and len(prices) >= 2:
                try:
                    yes_price = float(prices[0]) * 100
                    no_price = float(prices[1]) * 100
                except:
                    yes_price = no_price = 50
            else:
                yes_price = no_price = 50
            
            with st.expander(f"📌 {question[:80]}...", expanded=False):
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    st.markdown(f"**问题**: {question}")
                    st.markdown(f"**交易量**: ${volume:,.2f}")
                    st.markdown(f"**流动性**: ${liquidity:,.2f}")
                    st.markdown(f"**YES**: {yes_price:.1f}% | **NO**: {no_price:.1f}%")
                
                with col2:
                    fig = go.Figure(go.Indicator(
                        mode="gauge+number",
                        value=yes_price,
                        domain={'x': [0, 1], 'y': [0, 1]},
                        title={'text': "YES %"},
                        gauge={
                            'axis': {'range': [0, 100]},
                            'bar': {'color': "#44ff44" if yes_price > 50 else "#ff4444"},
                        }
                    ))
                    fig.update_layout(height=180, margin=dict(l=20, r=20, t=40, b=20))
                    st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("未找到市场数据")


# ============================================================================
# 主程序
# ============================================================================

def main():
    init_state()
    
    page = render_sidebar()
    
    if page == "🏠 总览":
        render_overview()
    elif page == "🔬 刷量检测":
        render_wash_detection()
    elif page == "💊 市场健康":
        render_market_health()
    elif page == "🕵️ 交易详情":
        render_trade_details()
    elif page == "📊 市场数据":
        render_market_data()
    
    # 自动刷新 (流式监控时)
    if st.session_state.streaming:
        time.sleep(5)
        st.rerun()


if __name__ == "__main__":
    main()
