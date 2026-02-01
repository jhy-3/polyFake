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
    currentParams: {},
    currentFilterToken: null,
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

function showLoading() {
    let loader = document.getElementById('global-loader');
    if (!loader) {
        loader = document.createElement('div');
        loader.id = 'global-loader';
        loader.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: rgba(0,0,0,0.8);
            color: #00d4ff;
            padding: 20px 40px;
            border-radius: 8px;
            z-index: 10000;
            font-size: 14px;
        `;
        loader.textContent = '分析中...';
        document.body.appendChild(loader);
    }
    loader.style.display = 'block';
}

function hideLoading() {
    const loader = document.getElementById('global-loader');
    if (loader) loader.style.display = 'none';
}

function showNotification(message, type = 'info') {
    let container = document.getElementById('notification-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'notification-container';
        container.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 10001;
        `;
        document.body.appendChild(container);
    }
    
    const notification = document.createElement('div');
    const colors = {
        'info': '#00d4ff',
        'success': '#00ff88',
        'warning': '#ffaa00',
        'error': '#ff4444'
    };
    notification.style.cssText = `
        background: ${colors[type] || colors.info};
        color: #000;
        padding: 12px 20px;
        border-radius: 6px;
        margin-bottom: 10px;
        font-weight: 500;
        animation: slideIn 0.3s ease;
    `;
    notification.textContent = message;
    container.appendChild(notification);
    
    setTimeout(() => {
        notification.style.opacity = '0';
        setTimeout(() => notification.remove(), 300);
    }, 4000);
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
// Security Alert Center
// ============================================================================

// 告警中心状态
let alertCenterCount = 0;
const MAX_ALERTS = 20;

/**
 * 添加安全告警到告警中心
 */
function addSecurityAlert(alertData) {
    const container = document.getElementById('alert-center-content');
    const badge = document.getElementById('alert-center-badge');
    if (!container) return;
    
    // 移除空状态提示
    const emptyEl = container.querySelector('.alert-center-empty');
    if (emptyEl) emptyEl.remove();
    
    // 告警类型映射
    const typeInfo = {
        'SELF_TRADE': { emoji: '🔄', name: '自交易', severity: 'high' },
        'CIRCULAR_TRADE': { emoji: '🔗', name: '循环交易', severity: 'medium' },
        'NEW_WALLET_INSIDER': { emoji: '🆕', name: '新钱包内幕', severity: 'high' },
        'ATOMIC_WASH': { emoji: '⚛️', name: '原子刷量', severity: 'medium' },
        'SYBIL_CLUSTER': { emoji: '👥', name: '女巫集群', severity: 'high' },
        'VOLUME_SPIKE': { emoji: '📈', name: '交易量异常', severity: 'low' },
        'HIGH_WINRATE': { emoji: '🎯', name: '高胜率异常', severity: 'medium' },
        'GAS_ANOMALY': { emoji: '⛽', name: 'Gas异常', severity: 'low' },
    };
    
    const info = typeInfo[alertData.type] || { emoji: '⚠️', name: alertData.type, severity: 'medium' };
    const txHash = alertData.tx_hash || '';
    const shortTx = txHash ? shortenHash(txHash, 8) : 'N/A';
    const polygonscanUrl = txHash ? `https://polygonscan.com/tx/${txHash}` : '#';
    const volume = alertData.volume ? formatUSD(alertData.volume) : '';
    const time = formatTimeAgo(alertData.timestamp || new Date().toISOString());
    
    // 创建告警卡片
    const card = document.createElement('div');
    card.className = `alert-card ${info.severity}`;
    card.innerHTML = `
        <div class="alert-card-header">
            <span class="alert-card-type">${info.emoji} ${info.name}</span>
            <span class="alert-card-time">${time}</span>
        </div>
        <div class="alert-card-detail">
            ${volume ? `<span>金额: ${volume}</span> · ` : ''}
            <a href="${polygonscanUrl}" target="_blank" rel="noopener">${shortTx}</a>
        </div>
    `;
    
    // 插入到顶部
    container.insertBefore(card, container.firstChild);
    
    // 更新计数
    alertCenterCount++;
    updateAlertBadge();
    
    // 限制最大数量
    while (container.children.length > MAX_ALERTS) {
        container.lastChild.remove();
    }
}

/**
 * 更新告警徽章数字
 */
function updateAlertBadge() {
    const badge = document.getElementById('alert-center-badge');
    if (badge) {
        const count = Math.min(alertCenterCount, 99);
        badge.textContent = count > 99 ? '99+' : count;
        badge.classList.toggle('zero', count === 0);
    }
}

/**
 * 设置告警中心折叠/展开
 */
function setupAlertCenterToggle() {
    const toggle = document.getElementById('alert-center-toggle');
    const center = document.getElementById('alert-center');
    
    if (toggle && center) {
        toggle.addEventListener('click', () => {
            center.classList.toggle('collapsed');
            toggle.textContent = center.classList.contains('collapsed') ? '+' : '−';
        });
    }
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

// ============================================================================
// Forensic Analysis Functions (取证分析)
// ============================================================================

/**
 * 运行单项取证分析（基础）
 * @param {string} analysisType - 分析类型: insider, high_winrate, gas_anomaly
 */
async function runForensicAnalysis(analysisType) {
    const typeNames = {
        'insider': '新钱包内幕',
        'high_winrate': '高胜率交易',
        'gas_anomaly': 'Gas异常(抢跑)'
    };
    
    const apiPaths = {
        'insider': '/trades/analysis/insider',
        'high_winrate': '/trades/analysis/high-winrate',
        'gas_anomaly': '/trades/analysis/gas-anomaly'
    };
    
    const typeName = typeNames[analysisType] || analysisType;
    const apiPath = apiPaths[analysisType];
    
    if (!apiPath) {
        showNotification(`未知的分析类型: ${analysisType}`, 'error');
        return;
    }
    
    showLoading();
    
    try {
        const data = await fetchAPI(apiPath);
        hideLoading();
        
        // 更新对应的统计卡片
        updateForensicStats(analysisType, { flagged_trades: data.flagged || [] });
        
        if (data.flagged && data.flagged.length > 0) {
            showNotification(`${typeName}检测完成: 发现 ${data.count} 笔可疑交易`, 'warning');
            
            // 可选：跳转到交易页面并显示结果
            if (confirm(`发现 ${data.count} 笔${typeName}可疑交易，是否查看详情？`)) {
                switchPage('trades');
                displayFlaggedTrades(data.flagged, typeName);
            }
        } else {
            showNotification(`${typeName}检测完成: 未发现可疑交易`, 'success');
        }
    } catch (error) {
        hideLoading();
        console.error(`${typeName}分析失败:`, error);
        showNotification(`${typeName}分析失败: ${error.message}`, 'error');
    }
}

/**
 * 运行全部取证分析（带超时处理）
 */
async function runFullForensicAnalysis() {
    showLoading();
    
    // 设置超时（30秒）
    const controller = new AbortController();
    const timeoutId = setTimeout(() => {
        controller.abort();
    }, 30000);
    
    try {
        const response = await fetch(API_BASE + '/trades/analysis/full', {
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        hideLoading();
        
        // 更新所有统计卡片
        if (data.new_wallet_insider) {
            updateForensicStats('insider', { flagged_trades: data.new_wallet_insider.flagged || [] });
        }
        if (data.high_win_rate) {
            updateForensicStats('high_winrate', { flagged_trades: data.high_win_rate.flagged || [] });
        }
        if (data.gas_anomaly) {
            updateForensicStats('gas_anomaly', { flagged_trades: data.gas_anomaly.flagged || [] });
        }
        
        const totalFlagged = (data.new_wallet_insider?.count || 0) + 
                            (data.high_win_rate?.count || 0) + 
                            (data.gas_anomaly?.count || 0);
        
        showNotification(`全部分析完成: 共发现 ${totalFlagged} 笔可疑交易`, 
                        totalFlagged > 0 ? 'warning' : 'success');
        
    } catch (error) {
        clearTimeout(timeoutId);
        hideLoading();
        
        if (error.name === 'AbortError') {
            console.error('分析超时');
            showNotification('分析超时，请稍后重试或减少数据量', 'error');
        } else {
            console.error('全部分析失败:', error);
            showNotification(`分析失败: ${error.message}`, 'error');
        }
    }
}

// ============================================================================
// Advanced Forensic Analysis (高级取证分析)
// ============================================================================

/**
 * 运行高级取证分析
 * @param {string} analysisType - 分析类型: self_trade, circular, atomic, volume_spike, sybil
 */
async function runAdvancedAnalysis(analysisType) {
    const typeNames = {
        'self_trade': '自交易(刷量)',
        'circular': '循环交易',
        'atomic': '原子刷量',
        'volume_spike': '交易量异常',
        'sybil': '女巫集群'
    };
    
    const apiPaths = {
        'self_trade': '/trades/analysis/advanced/self-trades',
        'circular': '/trades/analysis/advanced/circular-trades',
        'atomic': '/trades/analysis/advanced/atomic-wash',
        'volume_spike': '/trades/analysis/advanced/volume-spikes',
        'sybil': '/trades/analysis/advanced/sybil-clusters'
    };
    
    const typeName = typeNames[analysisType] || analysisType;
    const apiPath = apiPaths[analysisType];
    
    if (!apiPath) {
        showNotification(`未知的分析类型: ${analysisType}`, 'error');
        return;
    }
    
    showLoading();
    
    try {
        // 设置60秒超时（高级分析可能需要更长时间）
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 60000);
        
        const response = await fetch(API_BASE + apiPath, { signal: controller.signal });
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        hideLoading();
        
        // 更新对应的统计卡片
        updateAdvancedStats(analysisType, data);
        
        const count = data.count || 0;
        if (count > 0) {
            showNotification(`${typeName}检测完成: 发现 ${count} 条可疑证据`, 'warning');
            
            // 保存结果供后续筛选使用
            state.advancedResults = state.advancedResults || {};
            state.advancedResults[analysisType] = data;
            
            if (confirm(`发现 ${count} 条${typeName}可疑证据，是否查看详情？`)) {
                switchPage('trades');
                displayAdvancedEvidence(data.evidence || [], typeName, analysisType);
            }
        } else {
            showNotification(`${typeName}检测完成: 未发现可疑交易`, 'success');
        }
    } catch (error) {
        hideLoading();
        
        if (error.name === 'AbortError') {
            showNotification(`${typeName}分析超时，请稍后重试`, 'error');
        } else {
            console.error(`${typeName}分析失败:`, error);
            showNotification(`${typeName}分析失败: ${error.message}`, 'error');
        }
    }
}

/**
 * 运行市场健康评估
 */
async function runMarketHealthReport() {
    showLoading();
    
    try {
        // 设置120秒超时（完整分析需要更长时间）
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 120000);
        
        const response = await fetch(API_BASE + '/trades/analysis/advanced/market-health', {
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        hideLoading();
        
        // 保存结果
        state.healthReport = data;
        
        // 更新所有统计卡片
        updateHealthReportUI(data);
        
        // 显示结果摘要
        const riskEmoji = {
            'LOW': '✅',
            'MEDIUM': '⚠️',
            'HIGH': '🔶',
            'CRITICAL': '🚨'
        };
        
        showNotification(
            `市场健康评估完成 ${riskEmoji[data.risk_level] || ''}\n` +
            `健康评分: ${data.health_score?.toFixed(1)}/100\n` +
            `风险等级: ${data.risk_level}\n` +
            `证据总数: ${data.evidence_count}`,
            data.risk_level === 'LOW' ? 'success' : 'warning'
        );
        
        // 如果有证据，询问是否查看
        if (data.evidence_count > 0 && data.top_evidence?.length > 0) {
            if (confirm(`发现 ${data.evidence_count} 条证据，是否查看详情？`)) {
                showEvidenceModal(data);
            }
        }
        
    } catch (error) {
        hideLoading();
        
        if (error.name === 'AbortError') {
            showNotification('市场健康评估超时，请稍后重试', 'error');
        } else {
            console.error('市场健康评估失败:', error);
            showNotification(`评估失败: ${error.message}`, 'error');
        }
    }
}

/**
 * 运行全面扫描（8项检测）
 */
async function runFullSecurityScan() {
    if (!confirm('全面扫描将运行8项检测，可能需要2-3分钟，是否继续？')) {
        return;
    }
    
    showLoading();
    
    try {
        // 先运行基础分析
        const basicResponse = await fetch(API_BASE + '/trades/analysis/full');
        const basicData = await basicResponse.json();
        
        // 更新基础分析卡片
        if (basicData.new_wallet_insider) {
            updateForensicStats('insider', { flagged_trades: basicData.new_wallet_insider.flagged || [] });
        }
        if (basicData.high_win_rate) {
            updateForensicStats('high_winrate', { flagged_trades: basicData.high_win_rate.flagged || [] });
        }
        if (basicData.gas_anomaly) {
            updateForensicStats('gas_anomaly', { flagged_trades: basicData.gas_anomaly.flagged || [] });
        }
        
        // 再运行市场健康报告（包含高级分析）
        const healthResponse = await fetch(API_BASE + '/trades/analysis/advanced/market-health');
        const healthData = await healthResponse.json();
        
        hideLoading();
        
        // 保存结果
        state.healthReport = healthData;
        state.fullScanResults = {
            basic: basicData,
            health: healthData
        };
        
        // 更新UI
        updateHealthReportUI(healthData);
        
        const totalIssues = (basicData.new_wallet_insider?.count || 0) +
                          (basicData.high_win_rate?.count || 0) +
                          (basicData.gas_anomaly?.count || 0) +
                          (healthData.evidence_count || 0);
        
        showNotification(
            `🛡️ 全面扫描完成\n` +
            `健康评分: ${healthData.health_score?.toFixed(1)}/100\n` +
            `发现问题: ${totalIssues} 条`,
            totalIssues > 10 ? 'warning' : 'success'
        );
        
    } catch (error) {
        hideLoading();
        console.error('全面扫描失败:', error);
        showNotification(`扫描失败: ${error.message}`, 'error');
    }
}

/**
 * 更新高级分析统计卡片
 */
function updateAdvancedStats(analysisType, data) {
    const count = data.count || 0;
    
    const elementMap = {
        'self_trade': { count: 'stat-selftrade-count', trend: 'stat-selftrade-trend' },
        'circular': { count: 'stat-circular-count', trend: 'stat-circular-trend' },
        'atomic': { count: 'stat-atomic-count', trend: 'stat-atomic-trend' },
        'sybil': { count: 'stat-sybil-count', trend: 'stat-sybil-trend' },
        'volume_spike': { count: 'stat-spike-count', trend: 'stat-spike-trend' }
    };
    
    const elements = elementMap[analysisType];
    if (elements) {
        const countEl = document.getElementById(elements.count);
        const trendEl = document.getElementById(elements.trend);
        
        if (countEl) {
            countEl.textContent = formatNumber(count);
            countEl.style.animation = 'none';
            countEl.offsetHeight;
            countEl.style.animation = 'pulse 0.5s ease';
        }
        
        if (trendEl) {
            if (count > 0) {
                const volume = data.total_volume || data.total_spike_volume || 0;
                trendEl.textContent = volume > 0 ? `⚠️ $${formatNumber(volume)}` : `⚠️ ${count}条`;
                trendEl.style.color = '#ff6b6b';
            } else {
                trendEl.textContent = '✓ 正常';
                trendEl.style.color = '#00ff88';
            }
        }
    }
}

/**
 * 更新健康报告UI
 */
function updateHealthReportUI(data) {
    // 更新健康评分
    const scoreEl = document.getElementById('stat-health-score');
    const levelEl = document.getElementById('stat-health-level');
    const evidenceCountEl = document.getElementById('stat-evidence-count');
    const evidenceTrendEl = document.getElementById('stat-evidence-trend');
    
    if (scoreEl) {
        scoreEl.textContent = data.health_score?.toFixed(0) || '--';
    }
    
    if (levelEl) {
        const levelText = {
            'LOW': '✅ 健康',
            'MEDIUM': '⚠️ 中等风险',
            'HIGH': '🔶 高风险',
            'CRITICAL': '🚨 严重风险'
        };
        levelEl.textContent = levelText[data.risk_level] || data.risk_level;
        levelEl.style.color = {
            'LOW': '#4caf50',
            'MEDIUM': '#ff9800',
            'HIGH': '#ff5722',
            'CRITICAL': '#f44336'
        }[data.risk_level] || '#888';
    }
    
    // 更新健康卡片样式
    const healthCard = document.getElementById('btn-health-report')?.closest('.stat-card');
    if (healthCard) {
        healthCard.classList.remove('risk-low', 'risk-medium', 'risk-high', 'risk-critical');
        healthCard.classList.add(`risk-${data.risk_level?.toLowerCase()}`);
    }
    
    if (evidenceCountEl) {
        evidenceCountEl.textContent = formatNumber(data.evidence_count || 0);
    }
    
    if (evidenceTrendEl) {
        evidenceTrendEl.textContent = data.evidence_count > 0 ? '点击查看' : '无证据';
        evidenceTrendEl.style.color = data.evidence_count > 0 ? '#00bcd4' : '#888';
    }
    
    // 更新高级分析卡片
    if (data.detector_results) {
        const dr = data.detector_results;
        
        if (dr.self_trades) {
            updateAdvancedStats('self_trade', { count: dr.self_trades.count, total_volume: dr.self_trades.volume });
        }
        if (dr.circular_trades) {
            updateAdvancedStats('circular', { count: dr.circular_trades.count, total_volume: dr.circular_trades.volume });
        }
        if (dr.atomic_wash) {
            updateAdvancedStats('atomic', { count: dr.atomic_wash.count, total_volume: dr.atomic_wash.volume });
        }
        if (dr.sybil_clusters) {
            updateAdvancedStats('sybil', { count: dr.sybil_clusters.count, total_volume: dr.sybil_clusters.volume });
        }
        if (dr.volume_spikes) {
            updateAdvancedStats('volume_spike', { count: dr.volume_spikes.count, total_spike_volume: dr.volume_spikes.volume });
        }
    }
}

/**
 * 显示证据详情弹窗
 */
function showEvidenceModal(data) {
    // 创建弹窗
    const modal = document.createElement('div');
    modal.className = 'evidence-modal';
    modal.innerHTML = `
        <div class="evidence-modal-content glass">
            <div class="evidence-modal-header">
                <h2>📋 证据详情</h2>
                <button class="btn-close" onclick="this.closest('.evidence-modal').remove()">✕</button>
            </div>
            <div class="evidence-modal-body">
                <div class="evidence-summary">
                    <div class="summary-item">
                        <span class="label">健康评分</span>
                        <span class="value" style="color: ${getHealthColor(data.health_score)}">${data.health_score?.toFixed(1)}/100</span>
                    </div>
                    <div class="summary-item">
                        <span class="label">风险等级</span>
                        <span class="value">${data.risk_level}</span>
                    </div>
                    <div class="summary-item">
                        <span class="label">总交易数</span>
                        <span class="value">${formatNumber(data.total_trades)}</span>
                    </div>
                    <div class="summary-item">
                        <span class="label">证据数量</span>
                        <span class="value">${data.evidence_count}</span>
                    </div>
                </div>
                
                <h3>证据类型分布</h3>
                <div class="evidence-types">
                    ${Object.entries(data.evidence_by_type || {}).map(([type, count]) => `
                        <div class="type-item">
                            <span class="type-name">${getEvidenceTypeName(type)}</span>
                            <span class="type-count">${count}</span>
                        </div>
                    `).join('')}
                </div>
                
                <h3>高置信度证据 (Top 20)</h3>
                <div class="evidence-list">
                    ${(data.top_evidence || []).map(e => `
                        <div class="evidence-item">
                            <div class="evidence-type">${getEvidenceTypeName(e.type)}</div>
                            <div class="evidence-confidence" style="color: ${getConfidenceColor(e.confidence)}">
                                置信度: ${(e.confidence * 100).toFixed(0)}%
                            </div>
                            <div class="evidence-details">
                                ${e.tx_hash ? `<a href="https://polygonscan.com/tx/${e.tx_hash}" target="_blank">查看交易</a>` : ''}
                                ${e.volume > 0 ? `<span>交易量: $${formatNumber(e.volume)}</span>` : ''}
                            </div>
                            ${e.addresses?.length > 0 ? `
                                <div class="evidence-addresses">
                                    涉及地址: ${e.addresses.slice(0, 3).map(a => `<code>${a.slice(0, 10)}...</code>`).join(', ')}
                                </div>
                            ` : ''}
                        </div>
                    `).join('')}
                </div>
                
                ${Object.keys(data.suspicious_addresses || {}).length > 0 ? `
                    <h3>可疑地址排名</h3>
                    <div class="suspicious-addresses">
                        ${Object.entries(data.suspicious_addresses || {}).slice(0, 10).map(([addr, info]) => `
                            <div class="address-item">
                                <a href="https://polygonscan.com/address/${addr}" target="_blank" class="address">
                                    ${addr.slice(0, 20)}...
                                </a>
                                <span class="risk-score" style="color: ${getRiskScoreColor(info.risk_score)}">
                                    风险分: ${info.risk_score?.toFixed(0)}
                                </span>
                                <span class="evidence-count">${info.evidence_count} 条证据</span>
                            </div>
                        `).join('')}
                    </div>
                ` : ''}
            </div>
        </div>
    `;
    
    document.body.appendChild(modal);
    
    // 点击背景关闭
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.remove();
        }
    });
}

/**
 * 显示高级证据
 */
function displayAdvancedEvidence(evidence, typeName, analysisType) {
    const trades = evidence.map(e => ({
        tx_hash: e.tx_hash || 'N/A',
        maker: e.addresses?.[0] || 'Unknown',
        taker: e.addresses?.[1] || e.addresses?.[0] || 'Unknown',
        timestamp: e.timestamp || new Date().toISOString(),
        token_id: e.details?.market_id || '',
        side: e.details?.side || '-',
        price: e.details?.price || 0,
        size: e.details?.size || e.details?.trade_size || 0,
        volume: e.volume || 0,
        is_wash: true,
        wash_type: e.type,
        wash_confidence: e.confidence,
        market_name: typeName,
        _analysis_type: analysisType,
        _evidence_details: e.details
    }));
    
    state.trades = trades;
    renderTradesTable();
}

// 辅助函数
function getHealthColor(score) {
    if (score >= 80) return '#4caf50';
    if (score >= 60) return '#ff9800';
    if (score >= 40) return '#ff5722';
    return '#f44336';
}

function getConfidenceColor(confidence) {
    if (confidence >= 0.9) return '#f44336';
    if (confidence >= 0.7) return '#ff5722';
    if (confidence >= 0.5) return '#ff9800';
    return '#4caf50';
}

function getRiskScoreColor(score) {
    if (score >= 80) return '#f44336';
    if (score >= 60) return '#ff5722';
    if (score >= 40) return '#ff9800';
    return '#4caf50';
}

function getEvidenceTypeName(type) {
    const names = {
        'SELF_TRADE_DIRECT': '🔄 直接自交易',
        'SELF_TRADE_COORDINATED': '🔄 协调自交易',
        'CIRCULAR_TRADE': '🔗 循环交易',
        'ATOMIC_WASH': '⚛️ 原子刷量',
        'VOLUME_SPIKE': '📈 交易量异常',
        'SYBIL_CLUSTER': '👥 女巫集群',
        'NEW_WALLET_INSIDER': '🆕 新钱包内幕',
        'HIGH_WIN_RATE': '🎯 高胜率',
        'GAS_ANOMALY': '⛽ Gas异常'
    };
    return names[type] || type;
}

/**
 * 更新取证统计卡片
 */
function updateForensicStats(analysisType, data) {
    const count = data.flagged_trades?.length || 0;
    
    const elementMap = {
        'insider': { count: 'stat-insider-count', trend: 'stat-insider-trend' },
        'high_winrate': { count: 'stat-highwin-count', trend: 'stat-highwin-trend' },
        'gas_anomaly': { count: 'stat-gas-count', trend: 'stat-gas-trend' }
    };
    
    const elements = elementMap[analysisType];
    if (elements) {
        const countEl = document.getElementById(elements.count);
        const trendEl = document.getElementById(elements.trend);
        
        if (countEl) {
            countEl.textContent = formatNumber(count);
            // 添加动画效果
            countEl.style.animation = 'none';
            countEl.offsetHeight; // 触发重绘
            countEl.style.animation = 'pulse 0.5s ease';
        }
        
        if (trendEl) {
            if (count > 0) {
                trendEl.textContent = `⚠️ ${count}笔`;
                trendEl.style.color = '#ff6b6b';
            } else {
                trendEl.textContent = '✓ 正常';
                trendEl.style.color = '#00ff88';
            }
        }
    }
}

/**
 * 显示被标记的交易
 */
function displayFlaggedTrades(flaggedTrades, typeName) {
    // 将标记的交易转换为表格格式
    const trades = flaggedTrades.map(ft => ({
        tx_hash: ft.tx_hash,
        maker: ft.wallet_address,
        taker: ft.wallet_address,
        timestamp: ft.details?.trade_time || new Date().toISOString(),
        token_id: ft.details?.token_id || '',
        side: ft.details?.side || '-',
        price: ft.details?.price || 0,
        size: ft.details?.trade_size || ft.details?.size || 0,
        volume: (ft.details?.trade_size || ft.details?.size || 0) * (ft.details?.price || 1),
        is_wash: true,
        wash_type: ft.flag_type,
        wash_confidence: ft.confidence,
        market_name: typeName + ' 可疑',
        _flagDetails: ft.details
    }));
    
    state.trades = trades;
    renderTradesTable();
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
        // interval=10 表示每10秒一个数据点，让图表更精细
        const data = await fetchAPI('/trades/timeline?hours=1&interval=10');
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
        
        case 'analysis_stats':
            // 实时更新分析统计
            handleAnalysisStatsUpdate(msg.data);
            break;
        
        case 'suspicious_trade':
            // 实时发现可疑交易
            handleSuspiciousTrade(msg.data);
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
    
    // 添加到安全告警中心
    addSecurityAlert({
        type: alert.alert_type,
        tx_hash: alert.tx_hash,
        volume: alert.volume,
        timestamp: alert.timestamp,
    });
}

/**
 * 处理实时分析统计更新
 */
function handleAnalysisStatsUpdate(data) {
    const stats = data.stats || {};
    
    // 更新各检测类型的统计卡片
    const statMapping = {
        'insider': { count: 'stat-insider-count', trend: 'stat-insider-trend' },
        'high_winrate': { count: 'stat-highwin-count', trend: 'stat-highwin-trend' },
        'gas_anomaly': { count: 'stat-gas-count', trend: 'stat-gas-trend' },
        'self_trade': { count: 'stat-selftrade-count', trend: 'stat-selftrade-trend' },
        'circular': { count: 'stat-circular-count', trend: 'stat-circular-trend' },
        'atomic': { count: 'stat-atomic-count', trend: 'stat-atomic-trend' },
        'sybil': { count: 'stat-sybil-count', trend: 'stat-sybil-trend' },
        'volume_spike': { count: 'stat-spike-count', trend: 'stat-spike-trend' },
    };
    
    for (const [key, elements] of Object.entries(statMapping)) {
        const count = stats[key] || 0;
        const countEl = document.getElementById(elements.count);
        const trendEl = document.getElementById(elements.trend);
        
        if (countEl) {
            const oldValue = parseInt(countEl.textContent) || 0;
            countEl.textContent = formatNumber(count);
            
            // 如果数值增加，添加动画
            if (count > oldValue) {
                countEl.style.animation = 'none';
                countEl.offsetHeight;
                countEl.style.animation = 'pulse 0.5s ease';
            }
        }
        
        if (trendEl) {
            if (count > 0) {
                trendEl.textContent = `⚠️ 检测到 ${count}`;
                trendEl.style.color = '#ff6b6b';
            } else {
                trendEl.textContent = '✓ 正常';
                trendEl.style.color = '#00ff88';
            }
        }
    }
    
    // 更新健康评分
    if (data.health_score !== undefined) {
        const scoreEl = document.getElementById('stat-health-score');
        const levelEl = document.getElementById('stat-health-level');
        
        if (scoreEl) {
            scoreEl.textContent = data.health_score.toFixed(0);
        }
        
        if (levelEl) {
            const levelText = {
                'LOW': '✅ 健康',
                'MEDIUM': '⚠️ 中等风险',
                'HIGH': '🔶 高风险',
                'CRITICAL': '🚨 严重风险'
            };
            levelEl.textContent = levelText[data.risk_level] || data.risk_level;
            levelEl.style.color = {
                'LOW': '#4caf50',
                'MEDIUM': '#ff9800',
                'HIGH': '#ff5722',
                'CRITICAL': '#f44336'
            }[data.risk_level] || '#888';
        }
        
        // 更新健康卡片样式
        const healthCard = document.getElementById('btn-health-report');
        if (healthCard) {
            healthCard.classList.remove('risk-low', 'risk-medium', 'risk-high', 'risk-critical');
            healthCard.classList.add(`risk-${data.risk_level?.toLowerCase()}`);
        }
    }
    
    // 更新证据总数
    if (data.total_evidence !== undefined) {
        const evidenceCountEl = document.getElementById('stat-evidence-count');
        if (evidenceCountEl) {
            evidenceCountEl.textContent = formatNumber(data.total_evidence);
        }
    }
    
    // 保存到 state
    state.analysisStats = data;
    
    // 更新饼图
    renderWashChart();
}

/**
 * 处理实时发现的可疑交易
 */
function handleSuspiciousTrade(data) {
    const trade = data.trade;
    const detections = data.detections || [];
    
    // 添加到实时 Feed
    const container = document.getElementById('live-content');
    if (container) {
        const item = document.createElement('div');
        item.className = 'live-item wash';
        
        const typeEmojis = {
            'SELF_TRADE': '🔄',
            'CIRCULAR_TRADE': '🔗',
            'NEW_WALLET_INSIDER': '🆕',
            'ATOMIC_WASH': '⚛️',
            'SYBIL_CLUSTER': '👥',
            'VOLUME_SPIKE': '📈'
        };
        
        const emoji = typeEmojis[detections[0]] || '⚠️';
        const volume = trade.volume ? formatUSD(trade.volume) : '';
        const tx = trade.tx_hash ? shortenHash(trade.tx_hash) : '';
        
        item.innerHTML = `
            <span>${emoji}</span>
            <span>${volume}</span>
            <span style="color: var(--text-muted)">${tx}</span>
        `;
        
        container.insertBefore(item, container.firstChild);
        
        while (container.children.length > 20) {
            container.lastChild.remove();
        }
    }
    
    // 添加到安全告警中心（所有检测到的类型）
    for (const detection of detections) {
        addSecurityAlert({
            type: detection,
            tx_hash: trade.tx_hash,
            volume: trade.volume,
            timestamp: trade.timestamp || new Date().toISOString(),
        });
    }
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
        showLoading();
        const trades = await fetchAPI(`/trades?token_id=${tokenId}&limit=500`);
        state.trades = trades;
        state.currentFilterToken = tokenId;
        
        // 显示筛选提示
        showFilterInfo(tokenId, trades.length);
        
        renderTradesTable();
        hideLoading();
    } catch (error) {
        console.error('筛选交易失败:', error);
        showToast('加载交易失败', 'error');
        hideLoading();
    }
}

// 显示筛选提示信息
function showFilterInfo(tokenId, count) {
    // 检查是否已有筛选提示
    let filterInfo = document.getElementById('filter-info-bar');
    if (!filterInfo) {
        filterInfo = document.createElement('div');
        filterInfo.id = 'filter-info-bar';
        filterInfo.style.cssText = 'background: var(--surface); padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; border: 1px solid var(--primary);';
        
        const tradesContainer = document.getElementById('trades-container');
        if (tradesContainer) {
            tradesContainer.parentNode.insertBefore(filterInfo, tradesContainer);
        }
    }
    
    filterInfo.innerHTML = `
        <div style="display: flex; align-items: center; gap: 12px;">
            <span style="color: var(--primary);">🔍</span>
            <span>筛选市场: <strong style="color: var(--primary);">${shortenHash(tokenId, 8)}</strong></span>
            <span style="color: var(--text-secondary);">共 ${count} 笔交易</span>
        </div>
        <button onclick="clearTradeFilter()" style="padding: 6px 12px; background: var(--error); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">清除筛选</button>
    `;
    filterInfo.style.display = 'flex';
}

// 清除交易筛选
function clearTradeFilter() {
    state.currentFilterToken = null;
    
    // 隐藏筛选提示
    const filterInfo = document.getElementById('filter-info-bar');
    if (filterInfo) {
        filterInfo.style.display = 'none';
    }
    
    // 更新 URL
    navigateToPage('trades');
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
                    <button onclick="event.stopPropagation(); navigateToPage('trades', {token_id: '${tokenId}'});" style="flex: 1; padding: 6px; background: var(--primary); color: white; border: none; border-radius: 4px; cursor: pointer;">查看交易</button>
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
        
        // 确定交易类型显示
        let typeClass = 'tag-normal';
        let typeName = '正常';
        
        if (trade._analysis_type) {
            // 有分析类型标记（包括基础和高级分析）
            const typeMap = {
                // 基础分析类型
                'NEW_WALLET_INSIDER': { class: 'tag-insider', name: '🆕 新钱包内幕' },
                'HIGH_WIN_RATE': { class: 'tag-highwin', name: '🎯 高胜率' },
                'GAS_ANOMALY': { class: 'tag-gas', name: '⛽ Gas异常' },
                // 高级分析类型
                'SELF_TRADE': { class: 'tag-selftrade', name: '🔄 自交易' },
                'SELF_TRADE_DIRECT': { class: 'tag-selftrade', name: '🔄 直接自交易' },
                'SELF_TRADE_COORDINATED': { class: 'tag-selftrade', name: '🔄 协调自交易' },
                'CIRCULAR_TRADE': { class: 'tag-circular', name: '🔗 循环交易' },
                'ATOMIC_WASH': { class: 'tag-atomic', name: '⚛️ 原子刷量' },
                'SYBIL_CLUSTER': { class: 'tag-sybil', name: '👥 女巫集群' },
                'VOLUME_SPIKE': { class: 'tag-spike', name: '📈 交易量异常' }
            };
            
            const typeInfo = typeMap[trade._analysis_type] || { class: 'tag-suspicious', name: '可疑' };
            typeClass = typeInfo.class;
            typeName = typeInfo.name;
        } else if (trade.is_wash) {
            typeClass = 'tag-wash';
            typeName = '刷量';
        }
        
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
                <td><span class="tag ${typeClass}">${typeName}</span></td>
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
        const tokenId = market.token_id || '';
        
        return `
            <div class="market-card">
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
                <div style="display: flex; gap: 8px; margin-top: 12px; font-size: 12px;">
                    <button onclick="event.stopPropagation(); navigateToPage('trades', {token_id: '${tokenId}'});" style="flex: 1; padding: 8px; background: var(--primary); color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: 500;">📊 查看交易</button>
                    ${hasUrl ? `<button onclick="event.stopPropagation(); window.open('${marketUrl}', '_blank');" style="flex: 1; padding: 8px; background: var(--surface); color: var(--primary); border: 1px solid var(--primary); border-radius: 4px; cursor: pointer; font-weight: 500;">🔗 Polymarket</button>` : ''}
                </div>
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

// 保存时间线数据供多个图表使用
let timelineData = [];

function renderVolumeChart(data) {
    if (!data || data.length === 0) {
        return;
    }
    
    // 保存数据供其他图表使用
    timelineData = data;
    
    const timestamps = data.map(d => d.timestamp);
    const totalVolume = data.map(d => d.total_volume);
    
    const traces = [
        {
            x: timestamps,
            y: totalVolume,
            name: '总交易量',
            type: 'scatter',
            mode: 'lines',
            fill: 'tozeroy',
            line: { color: '#00f5d4', width: 2 },
            fillcolor: 'rgba(0, 245, 212, 0.3)',
        }
    ];
    
    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: '#a0a0a0', size: 10 },
        margin: { t: 10, r: 15, b: 35, l: 50 },
        xaxis: {
            gridcolor: 'rgba(255,255,255,0.05)',
            tickformat: '%H:%M',
            dtick: 60000,  // 每60秒(1分钟)显示一个刻度
            tickmode: 'linear',
        },
        yaxis: {
            gridcolor: 'rgba(255,255,255,0.05)',
            tickprefix: '$',
        },
        showlegend: false,
    };
    
    Plotly.newPlot('chart-volume', traces, layout, { responsive: true, displayModeBar: false });
    
    // 同时渲染可疑行为趋势图
    renderSuspiciousChart(data);
}

// 可疑行为趋势图（折线图）
function renderSuspiciousChart(data) {
    if (!data || data.length === 0) {
        return;
    }
    
    const timestamps = data.map(d => d.timestamp);
    const selfTradeCount = data.map(d => d.self_trade_count || 0);
    const circularCount = data.map(d => d.circular_count || 0);
    const atomicCount = data.map(d => d.atomic_count || 0);
    const sybilCount = data.map(d => d.sybil_count || 0);
    const insiderCount = data.map(d => d.insider_count || 0);
    
    const traces = [
        {
            x: timestamps,
            y: selfTradeCount,
            name: '自交易',
            type: 'scatter',
            mode: 'lines',
            line: { color: '#f72585', width: 2 },
            fill: 'tozeroy',
            fillcolor: 'rgba(247, 37, 133, 0.1)',
        },
        {
            x: timestamps,
            y: circularCount,
            name: '循环交易',
            type: 'scatter',
            mode: 'lines',
            line: { color: '#ff6b35', width: 2 },
        },
        {
            x: timestamps,
            y: atomicCount,
            name: '原子刷量',
            type: 'scatter',
            mode: 'lines',
            line: { color: '#ffd60a', width: 2 },
        },
        {
            x: timestamps,
            y: sybilCount,
            name: '女巫集群',
            type: 'scatter',
            mode: 'lines',
            line: { color: '#9b5de5', width: 2 },
        },
        {
            x: timestamps,
            y: insiderCount,
            name: '内幕交易',
            type: 'scatter',
            mode: 'lines',
            line: { color: '#00b4d8', width: 2 },
        }
    ];
    
    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { color: '#a0a0a0', size: 9 },
        margin: { t: 10, r: 15, b: 35, l: 50 },
        xaxis: {
            gridcolor: 'rgba(255,255,255,0.05)',
            tickformat: '%H:%M',
            dtick: 60000,  // 每60秒(1分钟)显示一个刻度
            tickmode: 'linear',
        },
        yaxis: {
            gridcolor: 'rgba(255,255,255,0.05)',
            title: { text: '交易数量', font: { size: 10 } },
        },
        legend: {
            orientation: 'h',
            y: 1.2,
            x: 0.5,
            xanchor: 'center',
            font: { size: 8 },
        },
        showlegend: true,
    };
    
    Plotly.newPlot('chart-suspicious', traces, layout, { responsive: true, displayModeBar: false });
}

function renderWashChart() {
    if (state.stats.total_trades === 0) return;
    
    // 从 analysisStats 获取各类可疑交易统计
    const stats = state.analysisStats?.stats || {};
    
    const selfTrade = stats.self_trade || 0;
    const circular = stats.circular || 0;
    const atomic = stats.atomic || 0;
    const sybil = stats.sybil || 0;
    const insider = stats.insider || 0;
    const highWinrate = stats.high_winrate || 0;
    const gasAnomaly = stats.gas_anomaly || 0;
    const volumeSpike = stats.volume_spike || 0;
    
    const totalSuspicious = selfTrade + circular + atomic + sybil + insider + highWinrate + gasAnomaly + volumeSpike;
    const normalCount = Math.max(0, state.stats.total_trades - totalSuspicious);
    
    // 只显示有数据的类别
    const values = [];
    const labels = [];
    const colors = [];
    
    if (normalCount > 0) {
        values.push(normalCount);
        labels.push('正常交易');
        colors.push('#00f5d4');
    }
    if (selfTrade > 0) {
        values.push(selfTrade);
        labels.push('🔄 自交易');
        colors.push('#f72585');
    }
    if (circular > 0) {
        values.push(circular);
        labels.push('🔁 循环交易');
        colors.push('#ff6b35');
    }
    if (atomic > 0) {
        values.push(atomic);
        labels.push('⚡ 原子刷量');
        colors.push('#ffd60a');
    }
    if (sybil > 0) {
        values.push(sybil);
        labels.push('👥 女巫集群');
        colors.push('#9b5de5');
    }
    if (insider > 0) {
        values.push(insider);
        labels.push('🆕 内幕交易');
        colors.push('#00b4d8');
    }
    if (highWinrate > 0) {
        values.push(highWinrate);
        labels.push('📈 高胜率');
        colors.push('#06d6a0');
    }
    if (gasAnomaly > 0) {
        values.push(gasAnomaly);
        labels.push('⛽ Gas异常');
        colors.push('#ef476f');
    }
    if (volumeSpike > 0) {
        values.push(volumeSpike);
        labels.push('📊 异常放量');
        colors.push('#118ab2');
    }
    
    // 如果没有任何数据，显示全部正常
    if (values.length === 0) {
        values.push(state.stats.total_trades || 1);
        labels.push('正常交易');
        colors.push('#00f5d4');
    }

    const data = [{
        values: values,
        labels: labels,
        type: 'pie',
        hole: 0.6,
        marker: {
            colors: colors
        },
        textinfo: 'percent',
        textfont: { color: '#fff', size: 10 },
        textposition: 'inside',
    }];
    
    const suspiciousRate = state.stats.total_trades > 0 
        ? ((totalSuspicious / state.stats.total_trades) * 100).toFixed(1)
        : '0.0';
    
    const layout = {
        paper_bgcolor: 'transparent',
        font: { color: '#a0a0a0', size: 9 },
        margin: { t: 10, r: 10, b: 30, l: 10 },
        showlegend: true,
        legend: {
            orientation: 'h',
            y: -0.15,
            x: 0.5,
            xanchor: 'center',
            font: { size: 8 },
        },
        annotations: [{
            text: `<b>${suspiciousRate}%</b><br>可疑`,
            font: { size: 18, color: totalSuspicious > 0 ? '#f72585' : '#00f5d4' },
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
// Page Navigation & Routing
// ============================================================================

// 路由映射
const routes = {
    '': 'dashboard',
    'dashboard': 'dashboard',
    'trades': 'trades',
    'markets': 'markets',
    'alerts': 'alerts',
};

// 根据 URL 获取当前页面
function getPageFromURL() {
    const path = window.location.pathname.replace(/^\//, '').replace(/\/$/, '');
    return routes[path] || 'dashboard';
}

// 导航到指定页面（更新 URL 并切换页面）
function navigateToPage(pageName, params = {}) {
    const url = pageName === 'dashboard' ? '/' : `/${pageName}`;
    
    // 构建查询参数
    const searchParams = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
        if (value) searchParams.set(key, value);
    }
    const queryString = searchParams.toString();
    const fullUrl = queryString ? `${url}?${queryString}` : url;
    
    // 更新浏览器历史
    window.history.pushState({ page: pageName, params }, '', fullUrl);
    
    // 切换页面
    switchPage(pageName, params);
}

// 切换页面（不更新 URL）
function switchPage(pageName, params = {}) {
    state.currentPage = pageName;
    state.currentParams = params;
    
    // 更新导航按钮
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.page === pageName);
    });
    
    // 更新页面显示
    document.querySelectorAll('.page').forEach(page => {
        page.classList.toggle('active', page.id === `page-${pageName}`);
    });
    
    // 加载页面数据
    loadPageData(pageName, params);
}

// 处理浏览器前进/后退
window.addEventListener('popstate', (event) => {
    if (event.state && event.state.page) {
        switchPage(event.state.page, event.state.params || {});
    } else {
        switchPage(getPageFromURL());
    }
});

async function loadPageData(pageName, params = {}) {
    switch (pageName) {
        case 'dashboard':
            await refreshDashboard();
            break;
        case 'trades':
            if (params.token_id) {
                await filterTradesByToken(params.token_id);
            } else {
                await fetchTrades();
            }
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
    // 导航 - 使用路由导航
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => navigateToPage(btn.dataset.page));
    });
    
    // 控制按钮
    document.getElementById('btn-fetch').addEventListener('click', handleFetchData);
    document.getElementById('btn-stream-start').addEventListener('click', handleStartStreaming);
    document.getElementById('btn-stream-stop').addEventListener('click', handleStopStreaming);
    
    // 交易筛选
    document.getElementById('btn-filter-trades').addEventListener('click', async () => {
        const params = {};
        
        const wash = document.getElementById('filter-wash').value;
        const side = document.getElementById('filter-side').value;
        const address = document.getElementById('filter-address').value;
        
        // 检查是否选择了基础分析类型
        const basicAnalysisTypes = ['insider', 'high_winrate', 'gas_anomaly'];
        
        // 检查是否选择了高级分析类型
        const advancedAnalysisTypes = ['self_trade', 'circular', 'atomic', 'sybil', 'volume_spike'];
        
        if (basicAnalysisTypes.includes(wash)) {
            // 使用基础分析 API 获取被标记的交易
            try {
                showLoading();
                const flaggedData = await fetchAPI(`/trades/analysis/flagged-tx?analysis_type=${wash}`);
                
                if (flaggedData.tx_hashes && flaggedData.tx_hashes.length > 0) {
                    // 获取这些交易的详情
                    const allTrades = await fetchAPI('/trades?limit=5000');
                    const flaggedSet = new Set(flaggedData.tx_hashes);
                    
                    // 筛选出被标记的交易
                    const filteredTrades = allTrades.filter(t => flaggedSet.has(t.tx_hash));
                    
                    // 为筛选出的交易添加分析类型标记
                    const typeLabels = {
                        'insider': 'NEW_WALLET_INSIDER',
                        'high_winrate': 'HIGH_WIN_RATE',
                        'gas_anomaly': 'GAS_ANOMALY'
                    };
                    filteredTrades.forEach(t => {
                        t._analysis_type = typeLabels[wash] || wash;
                    });
                    
                    // 应用其他筛选条件
                    let result = filteredTrades;
                    if (side) result = result.filter(t => t.side === side);
                    if (address) result = result.filter(t => 
                        t.maker.toLowerCase().includes(address.toLowerCase()) || 
                        t.taker.toLowerCase().includes(address.toLowerCase())
                    );
                    
                    state.trades = result;
                    renderTradesTable();
                    
                    // 显示统计
                    const typeNames = {
                        'insider': '新钱包内幕',
                        'high_winrate': '高胜率交易',
                        'gas_anomaly': 'Gas异常(抢跑)'
                    };
                    showNotification(`发现 ${flaggedData.count} 笔 ${typeNames[wash]} 可疑交易`, 'info');
                } else {
                    state.trades = [];
                    renderTradesTable();
                    showNotification('未发现此类型的可疑交易', 'info');
                }
                hideLoading();
            } catch (err) {
                hideLoading();
                showNotification('分析失败: ' + err.message, 'error');
            }
        } else if (advancedAnalysisTypes.includes(wash)) {
            // 使用高级分析 API 获取被标记的交易
            try {
                showLoading();
                const flaggedData = await fetchAPI(`/trades/analysis/advanced/flagged-tx?analysis_type=${wash}`);
                
                if ((flaggedData.tx_hashes && flaggedData.tx_hashes.length > 0) || 
                    (flaggedData.wallet_addresses && flaggedData.wallet_addresses.length > 0)) {
                    
                    // 获取所有交易
                    const allTrades = await fetchAPI('/trades?limit=5000');
                    const flaggedTxSet = new Set(flaggedData.tx_hashes || []);
                    const flaggedAddrSet = new Set((flaggedData.wallet_addresses || []).map(a => a.toLowerCase()));
                    
                    // 筛选出被标记的交易
                    let filteredTrades = allTrades.filter(t => 
                        flaggedTxSet.has(t.tx_hash) || 
                        flaggedAddrSet.has(t.maker?.toLowerCase()) ||
                        flaggedAddrSet.has(t.taker?.toLowerCase())
                    );
                    
                    // 为筛选出的交易添加分析类型标记
                    const typeLabels = {
                        'self_trade': 'SELF_TRADE',
                        'circular': 'CIRCULAR_TRADE',
                        'atomic': 'ATOMIC_WASH',
                        'sybil': 'SYBIL_CLUSTER',
                        'volume_spike': 'VOLUME_SPIKE'
                    };
                    filteredTrades.forEach(t => {
                        t._analysis_type = typeLabels[wash] || wash;
                    });
                    
                    // 应用其他筛选条件
                    if (side) filteredTrades = filteredTrades.filter(t => t.side === side);
                    if (address) filteredTrades = filteredTrades.filter(t => 
                        t.maker.toLowerCase().includes(address.toLowerCase()) || 
                        t.taker.toLowerCase().includes(address.toLowerCase())
                    );
                    
                    state.trades = filteredTrades;
                    renderTradesTable();
                    
                    // 显示统计
                    const typeNames = {
                        'self_trade': '自交易(刷量)',
                        'circular': '循环交易',
                        'atomic': '原子刷量',
                        'sybil': '女巫集群',
                        'volume_spike': '交易量异常'
                    };
                    showNotification(`发现 ${filteredTrades.length} 笔 ${typeNames[wash]} 相关交易`, 'info');
                } else {
                    state.trades = [];
                    renderTradesTable();
                    showNotification('未发现此类型的可疑交易', 'info');
                }
                hideLoading();
            } catch (err) {
                hideLoading();
                showNotification('高级分析失败: ' + err.message, 'error');
            }
        } else {
            // 原有的刷量交易筛选逻辑
            if (wash) params.is_wash = wash;
            if (side) params.side = side;
            if (address) params.address = address;
            
            fetchTrades(params);
        }
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
    
    // 统计卡片点击事件（浮动效果 + 筛选功能）
    document.querySelectorAll('.stat-card.clickable').forEach(card => {
        card.addEventListener('click', (e) => {
            // 添加浮动效果
            card.classList.add('floating');
            setTimeout(() => card.classList.remove('floating'), 300);
            
            // 处理不同类型的卡片
            const filterType = card.dataset.filter;
            
            // 只有部分卡片有跳转功能，其他只有浮动效果
            if (filterType === 'all') {
                // 跳转到交易记录页，显示全部
                switchPage('trades');
                document.getElementById('filter-wash').value = '';
                fetchTrades({});
            } else if (filterType === 'volume') {
                // 显示交易量统计（跳转到市场分析）
                switchPage('markets');
            } else if (filterType === 'alerts') {
                // 跳转到警报中心
                switchPage('alerts');
            }
            // 刷量交易、新钱包内幕、高胜率交易、Gas异常 只有浮动效果，不跳转
            // filterType: 'wash', 'insider', 'highwin', 'gas' 不做任何跳转操作
        });
    });
    
    // 高级分析卡片点击事件（现在只用于查看详情，分析在实时流中自动进行）
    const advancedCardMappings = {
        'self_trade': () => runAdvancedAnalysis('self_trade'),
        'circular': () => runAdvancedAnalysis('circular'),
        'atomic': () => runAdvancedAnalysis('atomic'),
        'sybil': () => runAdvancedAnalysis('sybil'),
        'volume_spike': () => runAdvancedAnalysis('volume_spike')
    };
    
    document.querySelectorAll('.advanced-grid .stat-card[data-filter]').forEach(card => {
        card.addEventListener('click', () => {
            const filterType = card.dataset.filter;
            card.classList.add('floating');
            setTimeout(() => card.classList.remove('floating'), 300);
            
            if (advancedCardMappings[filterType]) {
                advancedCardMappings[filterType]();
            }
        });
    });
    
    // 市场健康评估按钮
    const healthReportBtn = document.getElementById('btn-health-report');
    if (healthReportBtn) {
        healthReportBtn.addEventListener('click', () => {
            healthReportBtn.classList.add('floating');
            setTimeout(() => healthReportBtn.classList.remove('floating'), 300);
            runMarketHealthReport();
        });
    }
    
    // 查看证据按钮
    const viewEvidenceBtn = document.getElementById('btn-view-evidence');
    if (viewEvidenceBtn) {
        viewEvidenceBtn.addEventListener('click', () => {
            viewEvidenceBtn.classList.add('floating');
            setTimeout(() => viewEvidenceBtn.classList.remove('floating'), 300);
            
            if (state.healthReport && state.healthReport.evidence_count > 0) {
                showEvidenceModal(state.healthReport);
            } else {
                showNotification('请先运行市场健康评估', 'info');
            }
        });
    }
    
    // 健康网格卡片点击事件
    document.querySelectorAll('.health-grid .stat-card[data-filter]').forEach(card => {
        card.addEventListener('click', () => {
            const filterType = card.dataset.filter;
            card.classList.add('floating');
            setTimeout(() => card.classList.remove('floating'), 300);
            
            if (filterType === 'volume_spike') {
                runAdvancedAnalysis('volume_spike');
            }
        });
    });
}

// ============================================================================
// Initialization
// ============================================================================

async function init() {
    console.log('🚀 PolySleuth Frontend 初始化...');
    
    // 设置事件监听
    setupEventListeners();
    
    // 设置告警中心折叠功能
    setupAlertCenterToggle();
    
    // 连接 WebSocket
    connectWebSocket();
    
    // 根据 URL 初始化页面
    const initialPage = getPageFromURL();
    const urlParams = new URLSearchParams(window.location.search);
    const params = {};
    for (const [key, value] of urlParams.entries()) {
        params[key] = value;
    }
    
    // 设置初始历史状态
    window.history.replaceState({ page: initialPage, params }, '', window.location.href);
    
    // 加载对应页面
    switchPage(initialPage, params);
    
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
