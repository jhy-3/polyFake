/**
 * PolySleuth - Frontend Application
 * Polymarket 刷量取证分析系统
 */

// ============================================================================
// API & WebSocket Configuration
// ============================================================================

const API_BASE = '/api';
const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;

// ============================================================================
// State Management
// ============================================================================

const state = {
    currentPage: 'dashboard',
    isStreaming: false,
    isConnected: false,
    ws: null,
    trades: [],
    markets: [],
    alerts: [],
    stats: {
        total_trades: 0,
        total_volume: 0,
        wash_trade_count: 0,
        total_alerts: 0,
    },
    pagination: {
        trades: { page: 1, limit: 50 }
    }
};

// 统计历史（用于1分钟趋势）
const statsHistory = [];

function recordStatsSnapshot() {
    const now = Date.now();
    statsHistory.push({
        ts: now,
        total_trades: state.stats.total_trades || 0,
        total_volume: state.stats.total_volume || 0,
    });

    // 只保留最近10分钟
    const cutoff = now - 10 * 60 * 1000;
    while (statsHistory.length && statsHistory[0].ts < cutoff) {
        statsHistory.shift();
    }
}

function computeOneMinuteDelta(current, key) {
    const now = Date.now();
    const target = now - 60 * 1000;
    let baseline = null;

    for (let i = statsHistory.length - 1; i >= 0; i--) {
        if (statsHistory[i].ts <= target) {
            baseline = statsHistory[i][key];
            break;
        }
    }

    if (baseline === null || baseline === 0) return 0;
    return ((current - baseline) / baseline) * 100;
}

// 图表刷新节流
let lastTimelineRefresh = 0;
let lastAlertStatsRefresh = 0;
let timelineRefreshScheduled = false;
let alertStatsRefreshScheduled = false;

// ============================================================================
// Utility Functions
// ============================================================================

function formatNumber(num, decimals = 0) {
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return num.toFixed(decimals);
}

function formatUSD(amount) {
    return '$' + formatNumber(amount, 2);
}

function formatTime(timestamp) {
    const date = new Date(timestamp);
    return date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

function formatTimeAgo(timestamp) {
    const now = new Date();
    const date = new Date(timestamp);
    const diff = Math.floor((now - date) / 1000);
    
    if (diff < 60) return `${diff}秒前`;
    if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
    return `${Math.floor(diff / 86400)}天前`;
}

function shortenAddress(addr) {
    if (!addr) return '';
    return addr.slice(0, 6) + '...' + addr.slice(-4);
}

function shortenHash(hash) {
    if (!hash) return '';
    return hash.slice(0, 10) + '...';
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.remove();
    }, 3000);
}

// ============================================================================
// API Functions
// ============================================================================

async function fetchAPI(endpoint, options = {}) {
    try {
        const response = await fetch(API_BASE + endpoint, {
            headers: { 'Content-Type': 'application/json' },
            ...options
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error(`API Error (${endpoint}):`, error);
        throw error;
    }
}

async function fetchStats() {
    try {
        const data = await fetchAPI('/system/stats');
        state.stats = data;
        updateStatsUI();
    } catch (error) {
        console.error('获取统计失败:', error);
    }
}

async function fetchTrades(params = {}) {
    try {
        const queryParams = new URLSearchParams({
            limit: params.limit || 50,
            offset: params.offset || 0,
            ...params
        });
        
        const data = await fetchAPI(`/trades?${queryParams}`);
        state.trades = data;
        renderTradesTable();
    } catch (error) {
        console.error('获取交易失败:', error);
    }
}

async function fetchMarkets(sortBy = 'volume') {
    try {
        const data = await fetchAPI(`/markets?limit=500&sort_by=${sortBy}`);
        state.markets = data;
        renderMarkets();
    } catch (error) {
        console.error('获取市场失败:', error);
    }
}

async function fetchHotMarkets() {
    const container = document.getElementById('hot-markets-container');
    try {
        // 设置加载超时 (15秒)
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 15000);
        
        const response = await fetch(API_BASE + '/markets/hot?limit=20', {
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        if (data && data.length > 0) {
            renderHotMarkets(data);
        } else {
            container.innerHTML = '<div class="loading">暂无数据</div>';
        }
    } catch (error) {
        console.error('获取热门市场失败:', error);
        if (error.name === 'AbortError') {
            container.innerHTML = '<div class="loading" style="color: var(--warning);">请求超时，请稍后重试</div>';
        } else {
            container.innerHTML = '<div class="loading" style="color: var(--error);">加载失败: ' + error.message + '</div>';
        }
    }
}

async function fetchAlerts() {
    try {
        const data = await fetchAPI('/alerts?limit=100');
        state.alerts = data;
        renderAlerts();
    } catch (error) {
        console.error('获取警报失败:', error);
    }
}

async function fetchRecentAlerts() {
    const container = document.getElementById('recent-alerts-container');
    try {
        // 设置加载超时 (10秒)
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10000);
        
        const response = await fetch(API_BASE + '/alerts/recent?limit=5', {
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        renderRecentAlerts(data);
    } catch (error) {
        console.error('获取最近警报失败:', error);
        if (error.name === 'AbortError') {
            container.innerHTML = '<div class="loading" style="color: var(--warning);">请求超时</div>';
        } else {
            container.innerHTML = '<div class="loading" style="color: var(--error);">加载失败</div>';
        }
    }
}

async function fetchTradeTimeline() {
    try {
        const data = await fetchAPI('/trades/timeline?hours=24&interval=1');
        renderVolumeChart(data);
    } catch (error) {
        console.error('获取时间线失败:', error);
    }
}

async function fetchAlertStats() {
    try {
        const data = await fetchAPI('/alerts/stats?hours=24');
        renderAlertChart(data);
        updateAlertStats(data);
    } catch (error) {
        console.error('获取警报统计失败:', error);
    }
}

// ============================================================================
// Control Actions
// ============================================================================

async function handleFetchData() {
    const blocks = parseInt(document.getElementById('fetch-blocks').value) || 100;
    
    try {
        showToast('正在获取链上数据...', 'info');
        const result = await fetchAPI(`/system/fetch?blocks=${blocks}`, { method: 'POST' });
        showToast(`成功获取 ${result.fetched_trades} 笔交易`, 'success');
        
        // 刷新数据
        await refreshDashboard();
    } catch (error) {
        showToast('获取数据失败: ' + error.message, 'error');
    }
}

async function handleStartStreaming() {
    const interval = parseFloat(document.getElementById('poll-interval').value) || 5;
    
    try {
        const result = await fetchAPI(
            `/system/stream/start?poll_interval=${interval}&blocks_per_poll=10`,
            { method: 'POST' }
        );
        
        if (result.status === 'started' || result.status === 'already_streaming') {
            state.isStreaming = true;
            updateStreamingUI();
            showToast('流式监控已启动', 'success');
        }
    } catch (error) {
        showToast('启动监控失败: ' + error.message, 'error');
    }
}

async function handleStopStreaming() {
    try {
        await fetchAPI('/system/stream/stop', { method: 'POST' });
        state.isStreaming = false;
        updateStreamingUI();
        showToast('流式监控已停止', 'warning');
    } catch (error) {
        showToast('停止监控失败: ' + error.message, 'error');
    }
}

// ============================================================================
// WebSocket
// ============================================================================

function connectWebSocket() {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        return;
    }
    
    state.ws = new WebSocket(WS_URL);
    
    state.ws.onopen = () => {
        console.log('WebSocket 已连接');
        state.isConnected = true;
        updateConnectionUI();
    };
    
    state.ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleWSMessage(msg);
        } catch (error) {
            console.error('解析 WebSocket 消息失败:', error);
        }
    };
    
    state.ws.onclose = () => {
        console.log('WebSocket 已断开');
        state.isConnected = false;
        updateConnectionUI();
        
        // 自动重连
        setTimeout(connectWebSocket, 3000);
    };
    
    state.ws.onerror = (error) => {
        console.error('WebSocket 错误:', error);
    };
}

function handleWSMessage(msg) {
    switch (msg.type) {
        case 'connected':
            if (msg.data.stats) {
                state.stats = msg.data.stats;
                updateStatsUI();
            }
            break;
        
        case 'new_trade':
        case 'trade':
            handleNewTrade(msg.data);
            break;
        
        case 'new_alert':
        case 'alert':
            handleNewAlert(msg.data);
            break;
        
        case 'stats':
            state.stats = msg.data;
            state.isStreaming = msg.data.is_streaming;
            updateStatsUI();
            updateStreamingUI();
            break;
        
        case 'pong':
            // Heartbeat response
            break;
    }
}

function scheduleTimelineRefresh() {
    if (timelineRefreshScheduled) return;
    const now = Date.now();
    if (now - lastTimelineRefresh < 10000) return;
    timelineRefreshScheduled = true;
    setTimeout(async () => {
        timelineRefreshScheduled = false;
        lastTimelineRefresh = Date.now();
        if (state.currentPage === 'dashboard') {
            await fetchTradeTimeline();
        }
    }, 1000);
}

function scheduleAlertStatsRefresh() {
    if (alertStatsRefreshScheduled) return;
    const now = Date.now();
    if (now - lastAlertStatsRefresh < 15000) return;
    alertStatsRefreshScheduled = true;
    setTimeout(async () => {
        alertStatsRefreshScheduled = false;
        lastAlertStatsRefresh = Date.now();
        if (state.currentPage === 'dashboard') {
            await fetchAlertStats();
        }
    }, 1000);
}

function handleNewTrade(trade) {
    // 添加到实时 Feed
    addToLiveFeed(trade);
    
    // 更新统计
    state.stats.total_trades++;
    state.stats.total_volume = (state.stats.total_volume || 0) + (trade.volume || 0);
    if (trade.is_wash) {
        state.stats.wash_trade_count++;
        state.stats.wash_volume = (state.stats.wash_volume || 0) + (trade.volume || 0);
    }
    updateStatsUI();

    // 刷新趋势图（节流）
    scheduleTimelineRefresh();
}

function handleNewAlert(alert) {
    state.stats.total_alerts++;
    updateStatsUI();

    // 刷新警报统计图（节流）
    scheduleAlertStatsRefresh();

    // 添加到实时 Feed（警报）
    addAlertToLiveFeed(alert);
    
    // 显示通知
    showToast(`🚨 新警报: ${alert.alert_type}`, 'warning');
}

function addAlertToLiveFeed(alert) {
    const container = document.getElementById('live-content');
    if (!container) return;

    const item = document.createElement('div');
    item.className = 'live-item wash';

    const typeEmoji = alert.alert_type === 'CIRCULAR_TRADE' ? '🟠' : '🔴';
    const volume = alert.volume ? formatUSD(alert.volume) : '';
    const tx = alert.tx_hash ? shortenHash(alert.tx_hash) : '';

    item.innerHTML = `
        <span>${typeEmoji}</span>
        <span>${volume}</span>
        <span style="color: var(--text-muted)">${tx}</span>
    `;

    container.insertBefore(item, container.firstChild);

    while (container.children.length > 20) {
        container.lastChild.remove();
    }
}

function addToLiveFeed(trade) {
    const container = document.getElementById('live-content');
    if (!container) return;

    // 仅显示可疑交易
    if (!trade.is_wash) return;
    
    const item = document.createElement('div');
    item.className = `live-item ${trade.is_wash ? 'wash' : 'normal'}`;
    
    const sideEmoji = trade.side === 'BUY' ? '🟢' : '🔴';
    const washEmoji = trade.is_wash ? '⚠️' : '';
    
    item.innerHTML = `
        <span>${sideEmoji}</span>
        <span>${formatUSD(trade.volume)}</span>
        <span style="color: var(--text-muted)">${shortenHash(trade.tx_hash)}</span>
        ${washEmoji}
    `;
    
    // 插入到顶部
    container.insertBefore(item, container.firstChild);
    
    // 限制数量
    while (container.children.length > 20) {
        container.lastChild.remove();
    }
}

// ============================================================================
// UI Rendering
// ============================================================================

// 按 token 筛选交易
async function filterTradesByToken(tokenId) {
    try {
        const trades = await fetchTrades({ token_id: tokenId, limit: 100 });
        state.trades = trades;
        renderTradesTable();
    } catch (error) {
        console.error('筛选交易失败:', error);
        showToast('加载交易失败', 'error');
    }
}

function updateStatsUI() {
    recordStatsSnapshot();

    document.getElementById('stat-total-trades').textContent = formatNumber(state.stats.total_trades);
    document.getElementById('stat-total-volume').textContent = formatUSD(state.stats.total_volume || 0);
    document.getElementById('stat-wash-count').textContent = formatNumber(state.stats.wash_trade_count);
    document.getElementById('stat-alerts').textContent = formatNumber(state.stats.total_alerts);
    
    const washRatio = state.stats.total_trades > 0 
        ? (state.stats.wash_trade_count / state.stats.total_trades * 100).toFixed(1)
        : 0;
    document.getElementById('stat-wash-ratio').textContent = washRatio + '%';

    // 1分钟趋势
    const tradesDelta = computeOneMinuteDelta(state.stats.total_trades || 0, 'total_trades');
    const volumeDelta = computeOneMinuteDelta(state.stats.total_volume || 0, 'total_volume');

    const tradesTrend = document.getElementById('stat-total-trades-trend');
    const volumeTrend = document.getElementById('stat-total-volume-trend');

    if (tradesTrend) {
        const sign = tradesDelta >= 0 ? '+' : '';
        tradesTrend.textContent = `${sign}${tradesDelta.toFixed(1)}%`;
        tradesTrend.classList.toggle('up', tradesDelta >= 0);
        tradesTrend.classList.toggle('down', tradesDelta < 0);
    }

    if (volumeTrend) {
        const sign = volumeDelta >= 0 ? '+' : '';
        volumeTrend.textContent = `${sign}${volumeDelta.toFixed(1)}%`;
        volumeTrend.classList.toggle('up', volumeDelta >= 0);
        volumeTrend.classList.toggle('down', volumeDelta < 0);
    }

    if (window.Plotly) {
        renderWashChart();
    }
}

function updateConnectionUI() {
    const statusDot = document.querySelector('#chain-status .status-dot');
    const statusText = document.querySelector('#chain-status .status-text');
    
    if (state.isConnected) {
        statusDot.classList.add('online');
        statusDot.classList.remove('offline');
        statusText.textContent = '已连接';
    } else {
        statusDot.classList.remove('online');
        statusDot.classList.add('offline');
        statusText.textContent = '断开连接';
    }
}

function updateStreamingUI() {
    const startBtn = document.getElementById('btn-stream-start');
    const stopBtn = document.getElementById('btn-stream-stop');
    const streamStatus = document.querySelector('#stream-status .status-text');
    
    if (state.isStreaming) {
        startBtn.disabled = true;
        stopBtn.disabled = false;
        streamStatus.textContent = '监控中';
        streamStatus.style.color = 'var(--success)';
    } else {
        startBtn.disabled = false;
        stopBtn.disabled = true;
        streamStatus.textContent = '离线';
        streamStatus.style.color = 'var(--text-muted)';
    }
}

function renderHotMarkets(markets) {
    const container = document.getElementById('hot-markets-container');
    if (!markets || markets.length === 0) {
        container.innerHTML = '<div class="loading">暂无数据</div>';
        return;
    }
    
    container.innerHTML = markets.map(market => {
        const washRatio = market.wash_ratio || 0;
        let badgeClass = 'healthy';
        let badgeText = '健康';
        
        if (washRatio > 30) {
            badgeClass = 'danger';
            badgeText = '高风险';
        } else if (washRatio > 10) {
            badgeClass = 'suspicious';
            badgeText = '可疑';
        }
        
        // 构建市场链接
        const marketUrl = market.polymarket_url || '#';
        const hasUrl = market.polymarket_url ? true : false;
        const marketName = market.question || 'Unknown Market';
        const tokenId = market.token_id;
        
        return `
            <div class="market-card" style="position: relative;">
                <div class="market-card-header">
                    <div class="market-name" title="${marketName}">${marketName}</div>
                    <span class="market-badge ${badgeClass}">${badgeText}</span>
                </div>
                <div class="market-stats">
                    <div class="market-stat">
                        <div class="market-stat-value">${formatNumber(market.total_trades)}</div>
                        <div class="market-stat-label">交易数</div>
                    </div>
                    <div class="market-stat">
                        <div class="market-stat-value">${formatUSD(market.total_volume)}</div>
                        <div class="market-stat-label">交易量</div>
                    </div>
                    <div class="market-stat">
                        <div class="market-stat-value" style="color: ${washRatio > 10 ? 'var(--warning)' : 'var(--success)'}">${washRatio.toFixed(1)}%</div>
                        <div class="market-stat-label">刷量率</div>
                    </div>
                </div>
                <div style="display: flex; gap: 8px; margin-top: 8px; font-size: 12px;">
                    <button onclick="event.stopPropagation(); navigateToPage('trades'); setTimeout(() => filterTradesByToken('${tokenId}'), 100);" style="flex: 1; padding: 6px; background: var(--primary); color: white; border: none; border-radius: 4px; cursor: pointer;">查看交易</button>
                    ${hasUrl ? `<button onclick="event.stopPropagation(); window.open('${marketUrl}', '_blank');" style="flex: 1; padding: 6px; background: var(--surface); color: var(--primary); border: 1px solid var(--primary); border-radius: 4px; cursor: pointer;">Polymarket</button>` : ''}
                </div>
            </div>
        `;
    }).join('');
}

function renderRecentAlerts(alerts) {
    const container = document.getElementById('recent-alerts-container');
    if (!alerts || alerts.length === 0) {
        container.innerHTML = '<div class="loading">暂无警报</div>';
        return;
    }
    
    container.innerHTML = alerts.map(alert => {
        const typeNames = {
            'SELF_TRADE': '自成交',
            'CIRCULAR_TRADE': '环形交易',
        };
        const typeIcons = {
            'SELF_TRADE': '🔴',
            'CIRCULAR_TRADE': '🟠',
        };
        
        return `
            <div class="alert-item ${alert.severity.toLowerCase()}">
                <div class="alert-icon">${typeIcons[alert.alert_type] || '⚠️'}</div>
                <div class="alert-content">
                    <div class="alert-type">${typeNames[alert.alert_type] || alert.alert_type}</div>
                    <div class="alert-detail">
                        ${alert.market_name || shortenHash(alert.token_id)} · 
                        ${formatUSD(alert.volume)} · 
                        置信度 ${(alert.confidence * 100).toFixed(0)}%
                    </div>
                </div>
                <div class="alert-time">${formatTimeAgo(alert.timestamp)}</div>
            </div>
        `;
    }).join('');
}

function renderTradesTable() {
    const tbody = document.getElementById('trades-table-body');
    if (!state.trades || state.trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center">暂无数据</td></tr>';
        return;
    }
    
    tbody.innerHTML = state.trades.map(trade => {
        // 构建市场链接
        const marketUrl = trade.polymarket_url || '#';
        const hasMarketUrl = trade.polymarket_url ? true : false;
        const marketName = trade.market_name || 'Unknown';
        const displayName = marketName.length > 30 ? marketName.slice(0, 30) + '...' : marketName;
        
        // 构建交易哈希链接
        const txUrl = trade.polyscan_url || `https://polygonscan.com/tx/${trade.tx_hash}`;
        
        return `
            <tr>
                <td>${formatTime(trade.timestamp)}</td>
                <td title="${marketName}">
                    ${hasMarketUrl ? `<a href="${marketUrl}" target="_blank" style="color: var(--primary);">${displayName}</a>` : displayName}
                </td>
                <td><span class="tag tag-${trade.side.toLowerCase()}">${trade.side}</span></td>
                <td>${trade.price.toFixed(4)}</td>
                <td>${formatNumber(trade.size, 2)}</td>
                <td>${formatUSD(trade.volume)}</td>
                <td><span class="tag ${trade.is_wash ? 'tag-wash' : 'tag-normal'}">${trade.is_wash ? '刷量' : '正常'}</span></td>
                <td class="tx-hash">
                    <a href="${txUrl}" target="_blank" style="color: var(--accent);">${shortenHash(trade.tx_hash)}</a>
                </td>
            </tr>
        `;
    }).join('');
}

function renderMarkets() {
    const container = document.getElementById('markets-container');
    if (!state.markets || state.markets.length === 0) {
        container.innerHTML = '<div class="loading">暂无数据</div>';
        return;
    }
    
    document.getElementById('markets-count').textContent = state.markets.length;
    document.getElementById('suspicious-count').textContent = state.markets.filter(m => m.wash_ratio > 10).length;
    
    container.innerHTML = state.markets.map(market => {
        const washRatio = market.wash_ratio || 0;
        let badgeClass = 'healthy';
        let badgeText = '健康';
        
        if (washRatio > 30) {
            badgeClass = 'danger';
            badgeText = '高风险';
        } else if (washRatio > 10) {
            badgeClass = 'suspicious';
            badgeText = '可疑';
        }
        
        // 构建市场链接
        const marketUrl = market.polymarket_url || '#';
        const hasUrl = market.polymarket_url ? true : false;
        const marketName = market.question || 'Unknown Market';
        
        return `
            <div class="market-card" ${hasUrl ? `style="cursor: pointer;" onclick="window.open('${marketUrl}', '_blank')"` : ''}>
                <div class="market-card-header">
                    <div class="market-name" title="${marketName}">${marketName}</div>
                    <span class="market-badge ${badgeClass}">${badgeText}</span>
                </div>
                <div class="market-stats">
                    <div class="market-stat">
                        <div class="market-stat-value">${formatNumber(market.total_trades)}</div>
                        <div class="market-stat-label">交易数</div>
                    </div>
                    <div class="market-stat">
                        <div class="market-stat-value">${formatUSD(market.total_volume)}</div>
                        <div class="market-stat-label">交易量</div>
                    </div>
                    <div class="market-stat">
                        <div class="market-stat-value" style="color: ${washRatio > 10 ? 'var(--warning)' : 'var(--success)'}">${washRatio.toFixed(1)}%</div>
                        <div class="market-stat-label">刷量率</div>
                    </div>
                </div>
                ${hasUrl ? '<div style="text-align: center; margin-top: 8px; font-size: 12px; color: var(--primary); opacity: 0.7;">点击查看 Polymarket →</div>' : ''}
            </div>
        `;
    }).join('');
}

function renderAlerts() {
    const container = document.getElementById('all-alerts-container');
    if (!state.alerts || state.alerts.length === 0) {
        container.innerHTML = '<div class="loading">暂无警报</div>';
        return;
    }
    
    const typeNames = {
        'SELF_TRADE': '自成交',
        'CIRCULAR_TRADE': '环形交易',
    };
    const typeIcons = {
        'SELF_TRADE': '🔴',
        'CIRCULAR_TRADE': '🟠',
    };
    
    container.innerHTML = state.alerts.map(alert => `
        <div class="alert-item ${alert.severity.toLowerCase()}">
            <div class="alert-icon">${typeIcons[alert.alert_type] || '⚠️'}</div>
            <div class="alert-content">
                <div class="alert-type">${typeNames[alert.alert_type] || alert.alert_type}</div>
                <div class="alert-detail">
                    ${alert.market_name || shortenHash(alert.token_id)} · 
                    ${formatUSD(alert.volume)} · 
                    涉及 ${alert.trade_count} 笔交易 · 
                    置信度 ${(alert.confidence * 100).toFixed(0)}%
                </div>
            </div>
            <div class="alert-time">${formatTime(alert.timestamp)}</div>
        </div>
    `).join('');
}

function updateAlertStats(data) {
    const bySeverity = data.by_severity || {};
    document.getElementById('alerts-high').textContent = (bySeverity.HIGH || {}).count || 0;
    document.getElementById('alerts-medium').textContent = (bySeverity.MEDIUM || {}).count || 0;
    document.getElementById('alerts-low').textContent = (bySeverity.LOW || {}).count || 0;
}

// ============================================================================
// Charts
// ============================================================================

function renderVolumeChart(data) {
    if (!data || data.length === 0) {
        return;
    }
    
    const timestamps = data.map(d => d.timestamp);
    const totalVolume = data.map(d => d.total_volume);
    const washVolume = data.map(d => d.wash_volume);
    
    const traces = [
        {
            x: timestamps,
            y: totalVolume,
            name: '总交易量',
            type: 'scatter',
            mode: 'lines',
            fill: 'tozeroy',
            line: { color: '#00f5d4', width: 2 },
            fillcolor: 'rgba(0, 245, 212, 0.2)',
        },
        {
            x: timestamps,
            y: washVolume,
            name: '刷量交易',
            type: 'scatter',
            mode: 'lines',
            fill: 'tozeroy',
            line: { color: '#f72585', width: 2 },
            fillcolor: 'rgba(247, 37, 133, 0.2)',
        }
    ];
    
    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: '#a0a0a0' },
        margin: { t: 20, r: 20, b: 40, l: 60 },
        xaxis: {
            gridcolor: 'rgba(255,255,255,0.05)',
            tickformat: '%H:%M',
        },
        yaxis: {
            gridcolor: 'rgba(255,255,255,0.05)',
            tickprefix: '$',
        },
        legend: {
            orientation: 'h',
            y: 1.1,
        },
        showlegend: true,
    };
    
    Plotly.newPlot('chart-volume', traces, layout, { responsive: true, displayModeBar: false });
}

function renderWashChart() {
    if (state.stats.total_trades === 0) return;
    
    const washCount = state.stats.wash_trade_count || 0;
    const normalCount = state.stats.total_trades - washCount;
    
    const data = [{
        values: [normalCount, washCount],
        labels: ['正常交易', '刷量交易'],
        type: 'pie',
        hole: 0.6,
        marker: {
            colors: ['#00f5d4', '#f72585']
        },
        textinfo: 'percent',
        textfont: { color: '#fff' },
    }];
    
    const layout = {
        paper_bgcolor: 'transparent',
        font: { color: '#a0a0a0' },
        margin: { t: 20, r: 20, b: 20, l: 20 },
        showlegend: true,
        legend: {
            orientation: 'h',
            y: -0.1,
        },
        annotations: [{
            text: `${((washCount / state.stats.total_trades) * 100).toFixed(1)}%`,
            font: { size: 24, color: '#f72585' },
            showarrow: false,
        }]
    };
    
    Plotly.newPlot('chart-wash', data, layout, { responsive: true, displayModeBar: false });
}

function renderAlertChart(data) {
    if (!data || !data.hourly_timeline || data.hourly_timeline.length === 0) {
        return;
    }
    
    const timestamps = data.hourly_timeline.map(d => d.timestamp);
    const counts = data.hourly_timeline.map(d => d.count);
    
    const trace = {
        x: timestamps,
        y: counts,
        type: 'bar',
        marker: { color: '#ffd60a' },
    };
    
    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: '#a0a0a0' },
        margin: { t: 20, r: 20, b: 40, l: 40 },
        xaxis: {
            gridcolor: 'rgba(255,255,255,0.05)',
            tickformat: '%H:%M',
        },
        yaxis: {
            gridcolor: 'rgba(255,255,255,0.05)',
        },
        showlegend: false,
    };
    
    Plotly.newPlot('chart-alerts', [trace], layout, { responsive: true, displayModeBar: false });
}

// ============================================================================
// Page Navigation
// ============================================================================

function switchPage(pageName) {
    state.currentPage = pageName;
    
    // 更新导航按钮
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.page === pageName);
    });
    
    // 更新页面显示
    document.querySelectorAll('.page').forEach(page => {
        page.classList.toggle('active', page.id === `page-${pageName}`);
    });
    
    // 加载页面数据
    loadPageData(pageName);
}

async function loadPageData(pageName) {
    switch (pageName) {
        case 'dashboard':
            await refreshDashboard();
            break;
        case 'trades':
            await fetchTrades();
            break;
        case 'markets':
            await fetchMarkets();
            break;
        case 'alerts':
            await fetchAlerts();
            await fetchAlertStats();
            break;
    }
}

async function refreshDashboard() {
    await Promise.all([
        fetchStats(),
        fetchHotMarkets(),
        fetchRecentAlerts(),
        fetchTradeTimeline(),
    ]);
    renderWashChart();
}

// ============================================================================
// Event Listeners
// ============================================================================

function setupEventListeners() {
    // 导航
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => switchPage(btn.dataset.page));
    });
    
    // 控制按钮
    document.getElementById('btn-fetch').addEventListener('click', handleFetchData);
    document.getElementById('btn-stream-start').addEventListener('click', handleStartStreaming);
    document.getElementById('btn-stream-stop').addEventListener('click', handleStopStreaming);
    
    // 交易筛选
    document.getElementById('btn-filter-trades').addEventListener('click', () => {
        const params = {};
        
        const wash = document.getElementById('filter-wash').value;
        if (wash) params.is_wash = wash;
        
        const side = document.getElementById('filter-side').value;
        if (side) params.side = side;
        
        const address = document.getElementById('filter-address').value;
        if (address) params.address = address;
        
        fetchTrades(params);
    });
    
    // 分页
    document.getElementById('btn-prev-page').addEventListener('click', () => {
        if (state.pagination.trades.page > 1) {
            state.pagination.trades.page--;
            fetchTrades({ offset: (state.pagination.trades.page - 1) * state.pagination.trades.limit });
            document.getElementById('page-info').textContent = `第 ${state.pagination.trades.page} 页`;
        }
    });
    
    document.getElementById('btn-next-page').addEventListener('click', () => {
        state.pagination.trades.page++;
        fetchTrades({ offset: (state.pagination.trades.page - 1) * state.pagination.trades.limit });
        document.getElementById('page-info').textContent = `第 ${state.pagination.trades.page} 页`;
    });
    
    // 市场排序
    document.querySelectorAll('.sort-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            fetchMarkets(btn.dataset.sort);
        });
    });
    
    // 警报筛选
    document.getElementById('alert-severity-filter').addEventListener('change', async (e) => {
        const severity = e.target.value;
        const type = document.getElementById('alert-type-filter').value;
        
        let url = '/alerts?limit=100';
        if (severity) url += `&severity=${severity}`;
        if (type) url += `&alert_type=${type}`;
        
        const data = await fetchAPI(url);
        state.alerts = data;
        renderAlerts();
    });
    
    document.getElementById('alert-type-filter').addEventListener('change', async (e) => {
        const type = e.target.value;
        const severity = document.getElementById('alert-severity-filter').value;
        
        let url = '/alerts?limit=100';
        if (severity) url += `&severity=${severity}`;
        if (type) url += `&alert_type=${type}`;
        
        const data = await fetchAPI(url);
        state.alerts = data;
        renderAlerts();
    });
    
    // Live Feed 折叠
    document.getElementById('live-toggle').addEventListener('click', () => {
        const content = document.getElementById('live-content');
        const toggle = document.getElementById('live-toggle');
        
        if (content.style.display === 'none') {
            content.style.display = 'block';
            toggle.textContent = '−';
        } else {
            content.style.display = 'none';
            toggle.textContent = '+';
        }
    });
}

// ============================================================================
// Initialization
// ============================================================================

async function init() {
    console.log('🚀 PolySleuth Frontend 初始化...');
    
    // 设置事件监听
    setupEventListeners();
    
    // 连接 WebSocket
    connectWebSocket();
    
    // 加载初始数据
    await refreshDashboard();
    
    // 定期刷新（保证图表与摘要实时更新）
    setInterval(() => {
        if (state.currentPage === 'dashboard') {
            refreshDashboard();
        }
    }, 30000);
    
    // 定期刷新市场数据（每30秒）
    setInterval(async () => {
        if (state.currentPage === 'dashboard') {
            const markets = await fetchHotMarkets();
            if (markets) {
                renderHotMarkets(markets);
            }
        }
    }, 30000);
    
    console.log('✅ PolySleuth Frontend 初始化完成');
}

// 启动
document.addEventListener('DOMContentLoaded', init);
