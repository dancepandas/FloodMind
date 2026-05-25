/**
 * FloodMind Web 前端交互脚本
 */

// ============================================
// 全局状态
// ============================================
const state = {
    messages: [],
    isStreaming: false,
    isPaused: false,
    currentMessageId: null,
    sessionId: localStorage.getItem('floodmind_session_id') || generateSessionId(),
    uploadedFiles: [],
    pendingUploadedFiles: [],
    chatHistory: [],
    enableSearch: false,
    enableRag: true,
    enableReasoning: true,
    currentReader: null,
    pendingProcessCards: [],
    reasoningContent: '',
    rawReasoningContent: '',
    messageFlowsByMessage: {},
    toolResultsByMessage: {},
    subagentPlansByMessage: {},
    workflowPlansByMessage: {},
    toolActivityFeed: [],
    restorePollTimer: null,
};

const TOOL_ACTIVITY_LIMIT = 28;

// ============================================
// DOM 元素
// ============================================
const elements = {
    chatInput: document.getElementById('chat-input'),
    sendBtn: document.getElementById('send-btn'),
    messagesList: document.getElementById('messages-list'),
    welcomeMessage: document.getElementById('welcome-message'),
    chatContainer: document.getElementById('chat-container'),
    loadingOverlay: document.getElementById('loading-overlay'),
    sidebar: document.getElementById('sidebar'),
    fileInput: document.getElementById('file-input'),
    uploadModal: document.getElementById('upload-modal'),
    progressFill: document.getElementById('progress-fill'),
    progressText: document.getElementById('progress-text'),
    historyList: document.getElementById('history-list'),
    attachBtn: document.getElementById('attach-btn'),
    uploadedFilesPanel: document.getElementById('uploaded-files-panel'),
    uploadedFileMeta: document.getElementById('uploaded-file-meta'),
    toolActivityFeed: document.getElementById('tool-activity-feed'),
    workflowSidebarCard: document.getElementById('workflow-sidebar-card'),
    workflowSidebarBody: document.getElementById('workflow-sidebar-body'),
    workflowPanelMeta: document.getElementById('workflow-panel-meta'),
};

// ============================================
// 初始化
// ============================================
document.addEventListener('DOMContentLoaded', () => {
    initializeEventListeners();
    refreshWorkspacePanels();
    initializeAgent();
    ensureSnapshotPolling();
});

function refreshWorkspacePanels() {
    renderUploadedFilesPanel();
    renderToolActivityFeed();
    renderWorkflowSidebar();
}

function renderWorkspaceSummary() {
    renderWorkflowSidebar();
}

function renderUploadedFilesPanel() {
    if (!elements.uploadedFilesPanel) return;
    if (elements.uploadedFileMeta) {
        elements.uploadedFileMeta.textContent = state.uploadedFiles.length > 0 ? `${state.uploadedFiles.length} 个文件` : '等待上传';
    }
    if (state.uploadedFiles.length === 0) {
        elements.uploadedFilesPanel.innerHTML = '<div class="empty-panel-state">暂无上传文件。可上传 `tests/测试1.xlsx` 或其他水文数据文件开始任务。</div>';
        return;
    }
    elements.uploadedFilesPanel.innerHTML = state.uploadedFiles.map(file => `
        <div class="uploaded-file-item ${state.pendingUploadedFiles.some(pending => pending.id === file.id) ? 'is-pending' : ''}">
            <div class="uploaded-file-icon">${escapeHtml(file.name.split('.').pop().toUpperCase())}</div>
            <div class="uploaded-file-meta">
                <div class="uploaded-file-name">${escapeHtml(file.name)}</div>
                <div class="uploaded-file-size">${formatFileSize(file.size || 0)}${state.pendingUploadedFiles.some(pending => pending.id === file.id) ? ' · 待附带' : ''}</div>
            </div>
        </div>
    `).join('');
}

function shouldAttachAllUploadedFiles(message) {
    const text = String(message || '').trim();
    if (!text) return false;
    return /已上传的文件|上传的文件|上次上传的文件|之前上传的文件/.test(text);
}

function pushToolActivity(toolName, content, status = 'done') {
    const normalizedContent = summarizeToolEventContent(toolName, content, status);
    const entry = {
        toolName: toolName || 'unknown',
        content: normalizedContent,
        status,
        timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    };
    state.toolActivityFeed.unshift(entry);
    if (state.toolActivityFeed.length > TOOL_ACTIVITY_LIMIT) {
        state.toolActivityFeed = state.toolActivityFeed.slice(0, TOOL_ACTIVITY_LIMIT);
    }
    renderToolActivityFeed();
    renderWorkspaceSummary();
}

function renderToolActivityFeed() {
    if (!elements.toolActivityFeed) return;
    if (state.toolActivityFeed.length === 0) {
        elements.toolActivityFeed.innerHTML = '<div class="empty-panel-state">工具过程会显示在这里，适合回看最近几轮脚本、搜索和导出结果。</div>';
        return;
    }
    elements.toolActivityFeed.innerHTML = state.toolActivityFeed.map((entry, index) => `
        <div class="tool-activity-item ${index === 0 ? 'is-latest' : ''}">
            <div class="tool-activity-topline">
                <span class="tool-activity-name">${escapeHtml(getFriendlyToolName(entry.toolName))}</span>
                <span class="tool-activity-time">${entry.timestamp}</span>
            </div>
            <div class="tool-activity-status ${entry.status === 'error' ? 'is-error' : entry.status === 'running' ? 'is-running' : 'is-done'}">${entry.status === 'error' ? '失败' : entry.status === 'running' ? '进行中' : '完成'}</div>
            <div class="tool-activity-text">${escapeHtml((entry.content || '无详细输出').slice(0, 220))}</div>
        </div>
    `).join('');
}

function normalizeDisplayText(text) {
    return String(text || '').replace(/\r\n?/g, '\n').trim();
}

function toCompactLine(text, limit = 120) {
    const normalized = normalizeDisplayText(text).replace(/\s+/g, ' ');
    if (!normalized) return '';
    return normalized.length > limit ? `${normalized.slice(0, limit).trim()}...` : normalized;
}

function pickMeaningfulLines(text, limit = 2) {
    return normalizeDisplayText(text)
        .split('\n')
        .map(line => line.trim())
        .filter(line => line && !/^\[.*\]$/.test(line))
        .slice(0, limit);
}

function extractJsonSummary(toolName, raw) {
    try {
        const payload = JSON.parse(raw);
        if (!payload || typeof payload !== 'object') return '';
        const preferredKeys = ['task', 'user_goal', 'summary', 'script_name', 'skill_name', 'query', 'path', 'filename'];
        for (const key of preferredKeys) {
            const value = payload[key];
            if (typeof value === 'string' && value.trim()) {
                return `${getFriendlyToolName(toolName)}: ${toCompactLine(value, 120)}`;
            }
        }
        if (Array.isArray(payload.args) && payload.args.length > 0) {
            return `${getFriendlyToolName(toolName)}: ${toCompactLine(payload.args.join(' '), 120)}`;
        }
    } catch (error) {
        return '';
    }
    return '';
}

function summarizeDelegatedTask(raw) {
    const normalized = normalizeDisplayText(raw);
    if (!normalized) return '';
    const primary = normalized.split(/\n\s*\n|\[原始用户需求\]|\[已有中间结果\]|\[case 约束摘要\]/)[0] || '';
    return toCompactLine(primary, 120);
}

function summarizeToolInput(toolName, raw) {
    const normalized = normalizeDisplayText(raw);
    if (!normalized) return '';

    const jsonSummary = extractJsonSummary(toolName, normalized);
    if (jsonSummary) return jsonSummary;

    if ((toolName || '').includes('delegate_')) {
        const delegated = summarizeDelegatedTask(normalized);
        return delegated ? `子任务: ${delegated}` : '';
    }

    const lines = pickMeaningfulLines(normalized, 2);
    if (lines.length === 0) return '';
    return lines.length === 1 ? `输入: ${toCompactLine(lines[0], 140)}` : `输入: ${toCompactLine(lines.join(' | '), 140)}`;
}

function summarizeToolResult(toolName, raw) {
    const normalized = normalizeDisplayText(raw);
    if (!normalized) return '';

    const lines = pickMeaningfulLines(normalized, 3);
    if (lines.length === 0) return '';

    if ((toolName || '').includes('delegate_')) {
        return `结果: ${toCompactLine(lines.join(' | '), 160)}`;
    }

    return `结果: ${toCompactLine(lines.join(' | '), 160)}`;
}

function summarizeToolEventContent(toolName, content, status) {
    if (status === 'running') {
        return summarizeToolInput(toolName, content) || '已发起执行，等待工具返回。';
    }
    if (status === 'error') {
        return toCompactLine(content || '工具执行失败', 160);
    }
    return summarizeToolResult(toolName, content) || '工具已完成，未返回可展示摘要。';
}

function getLatestWorkflowMessageId(preferredMessageId = '') {
    if (preferredMessageId && state.workflowPlansByMessage[preferredMessageId]) return preferredMessageId;
    if (state.currentMessageId) {
        return state.workflowPlansByMessage[state.currentMessageId] ? state.currentMessageId : '';
    }
    const messageIds = Object.keys(state.workflowPlansByMessage);
    return messageIds.length > 0 ? messageIds[messageIds.length - 1] : '';
}

function renderWorkflowSidebar(preferredMessageId = '') {
    if (!elements.workflowSidebarCard || !elements.workflowSidebarBody || !elements.workflowPanelMeta) return;

    const messageId = getLatestWorkflowMessageId(preferredMessageId);
    const workflow = messageId ? state.workflowPlansByMessage[messageId] : null;
    if (!workflow || !Array.isArray(workflow.steps) || workflow.steps.length === 0) {
        elements.workflowSidebarCard.classList.add('hidden');
        elements.workflowPanelMeta.textContent = '等待触发';
        elements.workflowSidebarBody.innerHTML = '';
        return;
    }

    const activeStep = workflow.steps.find(step => step.status === 'running') || workflow.steps[workflow.steps.length - 1];
    const statusText = activeStep ? `${activeStep.label || ''} ${activeStep.title || ''}`.trim() : '等待执行';
    elements.workflowPanelMeta.textContent = statusText || '执行中';

    const stepsHtml = workflow.steps.map(step => {
        const stepSymbol = getWorkflowStepSymbol(step.status);
        const statusClass = `is-${step.status || 'pending'}`;
        const detailHtml = `
            <div class="workflow-step-detail ${step.detail ? '' : 'hidden'}">${escapeHtml(step.detail || '')}</div>
            <div class="workflow-step-outcome ${step.outcome ? '' : 'hidden'}">${escapeHtml(step.outcome || '')}</div>
        `;
        return `
            <div class="workflow-step ${statusClass}">
                <div class="workflow-step-main">
                    <span class="workflow-step-symbol">${escapeHtml(stepSymbol)}</span>
                    <span class="workflow-step-label">${escapeHtml(step.label || '')}</span>
                    <span class="workflow-step-title">${escapeHtml(step.title || '待分析')}</span>
                </div>
                ${detailHtml}
            </div>
        `;
    }).join('');

    elements.workflowSidebarBody.innerHTML = `
        <div class="workflow-task-title">${escapeHtml(workflow.title || 'Workflow')}</div>
        <div class="workflow-steps compact">${stepsHtml}</div>
    `;
    elements.workflowSidebarCard.classList.remove('hidden');
}

function initializeEventListeners() {
    elements.chatInput.addEventListener('input', handleInput);
    elements.chatInput.addEventListener('keydown', handleKeyDown);
    elements.sendBtn.addEventListener('click', handleSendButtonClick);
    elements.attachBtn.addEventListener('click', () => elements.fileInput.click());
    elements.fileInput.addEventListener('change', handleFileSelect);

    initBubbleButtons();
    initSidebarButtons();
    initHeaderButtons();
}

// ============================================
// 发送/暂停按钮处理
// ============================================
function handleSendButtonClick() {
    if (state.isStreaming) {
        pauseSession();
    } else {
        sendMessage();
    }
}

function updateSendButton() {
    const sendIcon = elements.sendBtn.querySelector('.send-icon');
    const pauseIcon = elements.sendBtn.querySelector('.pause-icon');
    
    if (state.isStreaming) {
        sendIcon.style.display = 'none';
        pauseIcon.style.display = 'block';
        elements.sendBtn.disabled = false;
        elements.sendBtn.title = '暂停';
        elements.sendBtn.classList.add('streaming');
    } else {
        sendIcon.style.display = 'block';
        pauseIcon.style.display = 'none';
        elements.sendBtn.disabled = elements.chatInput.value.trim().length === 0;
        elements.sendBtn.title = '发送';
        elements.sendBtn.classList.remove('streaming');
    }
}

// ============================================
// 快捷气泡按钮
// ============================================
function initBubbleButtons() {
    const bubbleBtns = document.querySelectorAll('.bubble-btn');
    bubbleBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const example = btn.dataset.example;
            if (example) {
                setInputValue(example);
                sendMessage();
            }
        });
    });
}

// ============================================
// 侧边栏按钮
// ============================================
function initSidebarButtons() {
    const newChatNav = document.getElementById('new-chat-nav');
    if (newChatNav) {
        newChatNav.addEventListener('click', () => {
            startNewChat();
        });
    }

    const aboutBtn = document.getElementById('about-btn');
    if (aboutBtn) {
        aboutBtn.addEventListener('click', () => {
            showAboutDialog();
        });
    }
}

// ============================================
// 顶部标题栏按钮
// ============================================
function initHeaderButtons() {
    const uploadBtn = document.getElementById('upload-btn');
    const settingsBtn = document.getElementById('settings-btn');

    if (uploadBtn) {
        uploadBtn.addEventListener('click', () => {
            elements.fileInput.click();
        });
    }

    if (settingsBtn) {
        settingsBtn.addEventListener('click', () => {
            showSettingsDialog();
        });
    }
}

// ============================================
// 对话框
// ============================================
function showAboutDialog() {
    const dialog = document.createElement('div');
    dialog.className = 'dialog-overlay';
    dialog.innerHTML = `
        <div class="dialog-content">
            <div class="dialog-header">
                <h3>关于 FloodMind</h3>
                <button class="dialog-close" onclick="this.closest('.dialog-overlay').remove()">✕</button>
            </div>
            <div class="dialog-body">
                <p><strong>FloodMind</strong> 是一个洪水预报智能助手</p>
                <p>基于时序大模型，提供零样本水文预测和多工具协同分析服务。</p>
                <br>
                <p><strong>主要功能：</strong></p>
                <ul>
                    <li>流量预测：预测未来时段流量</li>
                    <li>数据读取：支持 CSV、Excel 等格式</li>
                    <li>模型验证：评估预测精度</li>
                    <li>报告生成：导出 Word 预报报告</li>
                </ul>
            </div>
        </div>
    `;
    dialog.addEventListener('click', (e) => {
        if (e.target === dialog) dialog.remove();
    });
    document.body.appendChild(dialog);
}

function showSettingsDialog() {
    const dialog = document.createElement('div');
    dialog.className = 'dialog-overlay';
    dialog.innerHTML = `
        <div class="dialog-content">
            <div class="dialog-header">
                <h3>设置</h3>
                <button class="dialog-close" onclick="this.closest('.dialog-overlay').remove()">✕</button>
            </div>
            <div class="dialog-body">
                <div class="setting-item">
                    <div class="setting-info">
                        <span class="setting-title">联网搜索</span>
                        <span class="setting-desc">启用模型联网搜索能力</span>
                    </div>
                    <label class="toggle-switch">
                        <input type="checkbox" id="setting-search" ${state.enableSearch ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
                <div class="setting-item">
                    <div class="setting-info">
                        <span class="setting-title">深度推理</span>
                        <span class="setting-desc">显示主 Agent 的思考与任务分解过程</span>
                    </div>
                    <label class="toggle-switch">
                        <input type="checkbox" id="setting-reasoning" ${state.enableReasoning ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
                <div class="setting-item">
                    <div class="setting-info">
                        <span class="setting-title">知识库检索</span>
                        <span class="setting-desc">启用 RAG 知识检索功能</span>
                    </div>
                    <label class="toggle-switch">
                        <input type="checkbox" id="setting-rag" ${state.enableRag ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>
        </div>
    `;
    dialog.addEventListener('click', (e) => {
        if (e.target === dialog) dialog.remove();
    });
    document.body.appendChild(dialog);

    document.getElementById('setting-search').addEventListener('change', (e) => {
        state.enableSearch = e.target.checked;
        updateSessionConfig();
    });
    document.getElementById('setting-reasoning').addEventListener('change', (e) => {
        state.enableReasoning = e.target.checked;
        updateSessionConfig();
    });
    document.getElementById('setting-rag').addEventListener('change', (e) => {
        state.enableRag = e.target.checked;
        updateSessionConfig();
    });
}

function showInfo(message) {
    const infoDiv = document.createElement('div');
    infoDiv.className = 'info-toast';
    infoDiv.innerHTML = `
        <span>💡</span>
        <span>${escapeHtml(message)}</span>
    `;
    
    infoDiv.style.cssText = `
        position: fixed;
        top: 70px;
        left: 50%;
        transform: translateX(-50%);
        background: #3b82f6;
        color: white;
        padding: 12px 24px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        gap: 10px;
        z-index: 10000;
        animation: slideDown 0.3s ease;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    `;
    
    document.body.appendChild(infoDiv);
    
    setTimeout(() => {
        infoDiv.style.animation = 'slideUp 0.3s ease';
        setTimeout(() => infoDiv.remove(), 300);
    }, 2000);
}

// ============================================
// 文件上传处理
// ============================================
function handleFileSelect(e) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    
    Array.from(files).forEach(file => uploadFile(file));
    e.target.value = '';
}

async function uploadFile(file) {
    const allowedTypes = ['.csv', '.xlsx', '.xls', '.txt', '.json'];
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    
    if (!allowedTypes.includes(ext)) {
        showError(`不支持的文件类型: ${ext}。支持的类型: ${allowedTypes.join(', ')}`);
        return;
    }

    showUploadModal();
    updateProgress(0, '准备上传...');

    const formData = new FormData();
    formData.append('file', file);
    formData.append('session_id', state.sessionId);

    try {
        const xhr = new XMLHttpRequest();
        
        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                updateProgress(percent, `上传中... ${percent}%`);
            }
        };

        xhr.onload = () => {
            if (xhr.status === 200) {
                const response = JSON.parse(xhr.responseText);
                if (response.status === 'success') {
                    updateProgress(100, '上传成功！');

                    const uploadedFile = {
                        id: response.file_id,
                        name: file.name,
                        size: file.size,
                        path: response.file_path
                    };
                    state.uploadedFiles.push(uploadedFile);
                    state.pendingUploadedFiles.push({ ...uploadedFile });
                    refreshWorkspacePanels();
                    
                    setTimeout(() => {
                        hideUploadModal();
                        showSuccess(`文件 "${file.name}" 上传成功！`);
                    }, 500);
                } else {
                    throw new Error(response.message || '上传失败');
                }
            } else {
                throw new Error('服务器错误');
            }
        };

        xhr.onerror = () => {
            hideUploadModal();
            showError('上传失败，请检查网络连接');
        };

        xhr.open('POST', '/api/upload');
        xhr.send(formData);

    } catch (error) {
        hideUploadModal();
        showError(`上传失败: ${error.message}`);
    }
}

function showUploadModal() {
    elements.uploadModal.classList.add('active');
}

function hideUploadModal() {
    elements.uploadModal.classList.remove('active');
    elements.progressFill.style.width = '0%';
}

function updateProgress(percent, text) {
    elements.progressFill.style.width = percent + '%';
    elements.progressText.textContent = text;
}

// ============================================
// 智能体初始化
// ============================================
async function initializeAgent() {
    showLoading('正在初始化...');
    
    try {
        localStorage.setItem('floodmind_session_id', state.sessionId);
        
        const response = await fetch('/api/init', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                session_id: state.sessionId,
                enable_search: state.enableSearch,
                enable_rag: state.enableRag,
                enable_reasoning: state.enableReasoning,
            })
        });
        
        if (!response.ok) {
            throw new Error('初始化失败');
        }
        
        const data = await response.json();
        console.log('智能体初始化成功:', data);
        
        if (data.enable_search !== undefined) {
            state.enableSearch = data.enable_search;
        }
        
        if (data.enable_rag !== undefined) {
            state.enableRag = data.enable_rag;
        }

        if (data.enable_reasoning !== undefined) {
            state.enableReasoning = data.enable_reasoning;
        }
        
        await loadSessionHistory();
        await loadServerSessions();
        
    } catch (error) {
        console.error('初始化失败:', error);
        showError('智能体初始化失败，请检查后端服务是否运行');
    } finally {
        hideLoading();
    }
}

async function loadSessionHistory() {
    try {
        const response = await fetch(`/api/sessions/${state.sessionId}`);
        if (response.ok) {
            const data = await response.json();
            if (data.status === 'success' && data.messages && data.messages.length > 0) {
                elements.welcomeMessage.classList.add('hidden');
                elements.messagesList.innerHTML = '';
                state.messages = [];
                state.toolResultsByMessage = {};
                state.subagentPlansByMessage = {};
                state.workflowPlansByMessage = {};
                let lastAssistantMessageId = '';
                
                data.messages.forEach(msg => {
                    const messageId = addMessage(msg.role, msg.content, false);
                    if (msg.role === 'assistant') {
                        lastAssistantMessageId = messageId;
                        restoreAssistantProcessCards(messageId, msg);
                    }
                });

                if (lastAssistantMessageId && Array.isArray(data.artifacts) && data.artifacts.length > 0) {
                    restoreGeneratedArtifacts(lastAssistantMessageId, data.artifacts);
                }

                if (data.in_progress && data.in_progress.message_id) {
                    restoreInProgressSnapshot(data.in_progress);
                }
                
                state.messages = data.messages.map((msg, idx) => ({
                    role: msg.role,
                    content: msg.content,
                    id: `restored-${idx}`
                }));
                
                console.log(`恢复会话历史: ${data.messages.length} 条消息`);
            } else if (data.status === 'success' && data.in_progress && data.in_progress.message_id) {
                elements.welcomeMessage.classList.add('hidden');
                elements.messagesList.innerHTML = '';
                state.messages = [];
                state.toolResultsByMessage = {};
                state.subagentPlansByMessage = {};
                state.workflowPlansByMessage = {};
                restoreInProgressSnapshot(data.in_progress);
            }
        }
    } catch (error) {
        console.error('加载会话历史失败:', error);
    }
}

async function loadServerSessions() {
    try {
        const response = await fetch('/api/sessions');
        if (response.ok) {
            const data = await response.json();
            if (data.status === 'success' && data.sessions) {
                state.chatHistory = data.sessions
                    .filter(s => s.session_id !== state.sessionId)
                    .map(s => ({
                        id: s.session_id,
                        title: s.title || `会话 ${s.session_id.substring(0, 8)}`,
                        time: formatTime(s.last_active),
                        messageCount: s.message_count || 0,
                        timestamp: new Date(s.last_active).getTime()
                    }))
                    .sort((a, b) => b.timestamp - a.timestamp)
                    .slice(0, 20);
                
                renderHistoryList();
            }
        }
    } catch (error) {
        console.error('加载服务器会话列表失败:', error);
    }
}

function formatTime(isoString) {
    const date = new Date(isoString);
    return date.toLocaleString('zh-CN', { 
        month: '2-digit', 
        day: '2-digit', 
        hour: '2-digit', 
        minute: '2-digit' 
    });
}

async function saveSessionToServer() {
    try {
        await fetch('/api/sessions/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: state.sessionId })
        });
    } catch (error) {
        console.error('保存会话失败:', error);
    }
}

// ============================================
// 事件处理
// ============================================
function handleInput() {
    autoResizeTextarea();
    if (!state.isStreaming) {
        updateSendButton();
    }
}

function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!state.isStreaming && elements.chatInput.value.trim()) {
            sendMessage();
        }
    }
}

function autoResizeTextarea() {
    const textarea = elements.chatInput;
    textarea.style.height = 'auto';
    const newHeight = Math.min(textarea.scrollHeight, 150);
    textarea.style.height = newHeight + 'px';
}

function setInputValue(value) {
    elements.chatInput.value = value;
    handleInput();
    elements.chatInput.focus();
}

// ============================================
// 消息发送与接收
// ============================================
async function sendMessage() {
    const message = elements.chatInput.value.trim();
    if (!message || state.isStreaming) return;
    const shouldAttachAll = shouldAttachAllUploadedFiles(message);
    const attachedFilesSnapshot = (shouldAttachAll ? state.uploadedFiles : state.pendingUploadedFiles).map(file => ({ ...file }));
    const attachedFileIds = attachedFilesSnapshot.map(file => file.id);
    
    elements.welcomeMessage.classList.add('hidden');
    
    addMessage('user', message, false, attachedFilesSnapshot);
    
    elements.chatInput.value = '';
    elements.chatInput.style.height = 'auto';
    
    const assistantMessageId = addMessage('assistant', '', true);
    state.currentMessageId = assistantMessageId;
    state.isStreaming = true;
    state.isPaused = false;
    state.reasoningContent = '';
    state.rawReasoningContent = '';
    state.toolResultsByMessage[assistantMessageId] = [];
    state.subagentPlansByMessage[assistantMessageId] = null;
    state.workflowPlansByMessage[assistantMessageId] = null;
    refreshWorkspacePanels();
    updateSendButton();
    
    try {
        console.log('[Frontend Debug] 发送消息');
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId,
                message: message,
                uploaded_files: attachedFileIds,
                assistant_message_id: assistantMessageId,
            })
        });

        state.pendingUploadedFiles = [];
        refreshWorkspacePanels();
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        state.currentReader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullContent = '';
        let streamBuffer = '';
        
        while (true) {
            const { done, value } = await state.currentReader.read();
            if (done) break;
            
            if (state.isPaused) {
                break;
            }
            
            streamBuffer += decoder.decode(value, { stream: true });
            const lines = streamBuffer.split('\n');
            streamBuffer = lines.pop() || '';

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed) continue;

                try {
                    const parsed = JSON.parse(trimmed);
                    await handleStreamChunk(parsed, assistantMessageId);

                    if (parsed.type === 'token' && parsed.content) {
                        fullContent += parsed.content;
                    }
                } catch (e) {
                    console.error('解析流数据失败:', e, trimmed);
                }
            }
        }

        const trailing = streamBuffer.trim();
        if (trailing) {
            try {
                const parsed = JSON.parse(trailing);
                await handleStreamChunk(parsed, assistantMessageId);
                if (parsed.type === 'token' && parsed.content) {
                    fullContent += parsed.content;
                }
            } catch (e) {
                console.error('解析流尾部数据失败:', e, trailing);
            }
        }
        
        const finalDisplayedContent = getRenderedMessageText(assistantMessageId) || fullContent;

        state.messages.push({
            role: 'assistant',
            content: finalDisplayedContent,
            id: assistantMessageId
        });
        
        saveSessionToServer();
        await loadServerSessions();
        scheduleSessionListRefresh();
          
    } catch (error) {
        console.error('发送消息失败:', error);
        updateMessageContent(assistantMessageId, '抱歉，连接失败，请检查网络或后端服务。');
    } finally {
        state.isStreaming = false;
        state.currentReader = null;
        state.pendingProcessCards = [];
        state.reasoningContent = '';
        state.rawReasoningContent = '';
        trimRetainedProcessState();
        refreshWorkspacePanels();
        updateSendButton();
        removeStreamingCursor(assistantMessageId);
    }
}

function scheduleSessionListRefresh(delay = 2500) {
    window.setTimeout(() => {
        loadServerSessions().catch(error => {
            console.error('延迟刷新历史会话失败:', error);
        });
    }, delay);
}

function startSessionListAutoRefresh() {
    if (window.__sessionListRefreshTimer) {
        window.clearInterval(window.__sessionListRefreshTimer);
    }
    window.__sessionListRefreshTimer = window.setInterval(() => {
        if (!document.hidden) {
            loadServerSessions().catch(error => {
                console.error('自动刷新历史会话失败:', error);
            });
        }
    }, 12000);

    window.addEventListener('focus', () => {
        loadServerSessions().catch(error => {
            console.error('窗口聚焦刷新历史会话失败:', error);
        });
    });

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            loadServerSessions().catch(error => {
                console.error('页面可见时刷新历史会话失败:', error);
            });
        }
    });
}

function handleStreamChunk(data, messageId) {
    return new Promise(resolve => {
        console.log('[Frontend Debug] 接收到数据:', JSON.stringify(data).substring(0, 200));
        console.log('[Frontend Debug] data.type:', data.type, '| data.content:', data.content ? data.content.substring(0, 100) : 'null');
        
        if (data.type === 'reasoning') {
            appendRawReasoningContent(data.content || '');
            appendAssistantDialogueSegment(messageId, 'thought', data.content || '', true);
        } else if (data.type === 'thought_summary') {
            appendAssistantDialogueSegment(messageId, 'thought', data.content || '', false);
        } else if (data.type === 'workflow_plan') {
            state.workflowPlansByMessage[messageId] = {
                title: data.title || 'Workflow',
                steps: Array.isArray(data.steps) ? data.steps : [],
                isWorkflow: true,
            };
            updateWorkflowTaskCard(messageId);
        } else if (data.type === 'workflow_step') {
            applyWorkflowStepUpdate(messageId, data);
            updateWorkflowTaskCard(messageId);
        } else if (data.type === 'tool_status') {
            syncSubagentCardFromToolStatus(messageId, data);
            pushToolActivity(data.tool_name || '', data.content || '', data.status === 'error' ? 'error' : 'running');
            const toolNarration = buildToolStatusNarration(data);
            if (toolNarration) {
                appendAssistantDialogueSegment(messageId, 'answer', toolNarration, false);
            }
        } else if (data.type === 'tool_result') {
            syncSubagentCardFromToolResult(messageId, data);
            pushToolActivity(data.tool_name || '', data.content || '', 'done');
        } else if (data.type === 'search_result') {
            pushToolActivity('search_result', data.content || '搜索结果', 'done');
            addSearchResultCard(messageId, data.content);
        } else if (data.type === 'error') {
            pushToolActivity('system', data.content || '处理请求时出错', 'error');
            finalizeReasoningCard(messageId);
            collapseAllProcessCards(messageId);
            appendAssistantDialogueSegment(messageId, 'answer', data.content || '处理请求时出错', false);
        } else if (data.type === 'image_generated') {
            pushToolActivity('image_generated', data.filename || '生成图片', 'done');
            finalizeReasoningCard(messageId);
            collapseAllProcessCards(messageId);
            addImageCard(messageId, data.filename, data.filepath, data.size, data.image_url, data.image_data);
        } else if (data.type === 'file_generated') {
            pushToolActivity('file_generated', data.filename || '生成文件', 'done');
            finalizeReasoningCard(messageId);
            collapseAllProcessCards(messageId);
            addDownloadCard(messageId, data.filename, data.filepath, data.size, data.download_url);
        } else if (data.type === 'final_override') {
            finalizeReasoningCard(messageId);
            collapseAllProcessCards(messageId);
            updateAssistantAnswerContent(messageId, data.content || '');
        } else if (data.type === 'stream_end') {
            finalizeReasoningCard(messageId);
            collapseAllProcessCards(messageId);
        } else if (data.content) {
            finalizeReasoningCard(messageId);
            collapseAllProcessCards(messageId);
            appendAssistantDialogueSegment(messageId, 'answer', data.content, data.type === 'token');
        }
        
        renderWorkspaceSummary();
        scrollToBottom();
        requestAnimationFrame(resolve);
    });
}

function buildToolStatusNarration(data) {
    const toolName = getFriendlyToolName(data.tool_name || '');
    const detail = summarizeToolInput(data.tool_name || '', data.content || '');
    if (!toolName) return '';
    if (data.status === 'error') {
        return `这一步执行出错，我需要调整后重试。\n\n${toolName}${detail ? `：${detail}` : ''}`;
    }
    return `我需要调用 ${toolName}${detail ? `，${detail}` : ''}`;
}

// ============================================
// 暂停功能
// ============================================
async function pauseSession() {
    if (!state.isStreaming) return;
    
    try {
        const response = await fetch('/api/session/pause', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: state.sessionId })
        });
        
        if (response.ok) {
            state.isPaused = true;
            state.isStreaming = false;
            refreshWorkspacePanels();
            updateSendButton();
            showInfo('已暂停生成');
            
            if (state.currentReader) {
                state.currentReader.cancel();
            }
        }
    } catch (error) {
        console.error('暂停会话失败:', error);
        showError('暂停失败');
    }
}

// ============================================
// 消息渲染
// ============================================
function addMessage(role, content, isStreaming = false, attachments = [], forcedId = '') {
    const messageId = forcedId || 'msg-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
    const existing = document.getElementById(messageId);
    if (existing) {
        updateMessageContent(messageId, content);
        return messageId;
    }
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;
    messageDiv.id = messageId;
    
    const avatar = role === 'user' ? '我' : 'AI';
    const time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    
    messageDiv.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-content">
            <div class="message-bubble" id="bubble-${messageId}">
                ${role === 'assistant'
                    ? `<div class="assistant-dialogue-flow" id="flow-${messageId}"></div>${isStreaming ? '<span class="streaming-cursor"></span>' : ''}`
                    : escapeHtml(content)}
            </div>
            <span class="message-time">${time}</span>
        </div>
    `;

    if (role === 'user' && attachments.length > 0) {
        const messageContent = messageDiv.querySelector('.message-content');
        const timeEl = messageDiv.querySelector('.message-time');
        const strip = buildUserAttachmentStrip(attachments);
        if (messageContent && timeEl && strip) {
            messageContent.insertBefore(strip, timeEl);
        }
    }
    
    elements.messagesList.appendChild(messageDiv);

    if (role === 'assistant') {
        state.messageFlowsByMessage[messageId] = [];
        if (content) {
            updateAssistantAnswerContent(messageId, content);
        } else {
            renderAssistantDialogueFlow(messageId, isStreaming);
        }
    }
    
    if (role === 'user') {
        state.messages.push({ role, content, id: messageId });
    }
    
    scrollToBottom();
    return messageId;
}

function buildUserAttachmentStrip(attachments) {
    if (!attachments || attachments.length === 0) return null;
    const strip = document.createElement('div');
    strip.className = 'user-attachment-strip';

    attachments.forEach(file => {
        const chip = document.createElement('button');
        chip.className = 'user-attachment-chip';
        chip.type = 'button';
        chip.title = `查看 ${file.name} 内容`;
        chip.innerHTML = `
            <span class="user-attachment-name">${escapeHtml(file.name)}</span>
            <span class="user-attachment-size">${formatFileSize(file.size || 0)}</span>
        `;
        chip.addEventListener('click', () => previewUploadedFile(file.id));
        strip.appendChild(chip);
    });

    return strip;
}

function appendMessageContent(messageId, content) {
    const bubble = document.getElementById(`bubble-${messageId}`);
    if (!bubble) return;

    if (bubble.querySelector('.assistant-dialogue-flow')) {
        appendAssistantDialogueSegment(messageId, 'answer', content, true);
        return;
    }

    bubble.dataset.rawText = (bubble.dataset.rawText || '') + content;
    const cursor = bubble.querySelector('.streaming-cursor');
    const textNode = document.createTextNode(content);
    if (cursor) {
        bubble.insertBefore(textNode, cursor);
    } else {
        bubble.appendChild(textNode);
    }
    bubble.innerHTML = renderMarkdown(bubble.dataset.rawText);
    if (cursor) {
        bubble.appendChild(cursor);
    }
}

function updateMessageContent(messageId, content) {
    const bubble = document.getElementById(`bubble-${messageId}`);
    if (bubble) {
        if (bubble.querySelector('.assistant-dialogue-flow')) {
            updateAssistantAnswerContent(messageId, content);
            return;
        }
        bubble.dataset.rawText = content;
        bubble.innerHTML = renderMarkdown(content);
    }
}

function getRenderedMessageText(messageId) {
    const bubble = document.getElementById(`bubble-${messageId}`);
    if (!bubble) return '';
    if (bubble.querySelector('.assistant-dialogue-flow')) {
        const flow = state.messageFlowsByMessage[messageId] || [];
        return flow
            .filter(segment => segment.type === 'answer')
            .map(segment => segment.rawText || '')
            .join('\n\n')
            .trim();
    }
    if (bubble.dataset.rawText !== undefined) {
        return bubble.dataset.rawText || '';
    }
    return bubble.textContent || '';
}

function removeStreamingCursor(messageId) {
    const bubble = document.getElementById(`bubble-${messageId}`);
    if (!bubble) return;
    if (bubble.querySelector('.assistant-dialogue-flow')) {
        renderAssistantDialogueFlow(messageId, false);
        return;
    }
    if (bubble.dataset.rawText !== undefined) {
        bubble.innerHTML = renderMarkdown(bubble.dataset.rawText);
    } else {
        const cursor = bubble.querySelector('.streaming-cursor');
        if (cursor) cursor.remove();
    }
}

function getOrCreateProcessContainer(messageDiv) {
    let processContainer = messageDiv.querySelector('.process-container');
    if (!processContainer) {
        processContainer = document.createElement('div');
        processContainer.className = 'process-container';
        const messageContent = messageDiv.querySelector('.message-content');
        const messageBubble = messageDiv.querySelector('.message-bubble');
        messageContent.insertBefore(processContainer, messageBubble);
    }
    return processContainer;
}

function updateReasoningCard(messageId, content, rawContent = '', isStreaming = false) {
    const messageDiv = document.getElementById(messageId);
    if (!messageDiv) return;
    
    const processContainer = getOrCreateProcessContainer(messageDiv);
    let reasoningCard = processContainer.querySelector('.reasoning-card');
    
    if (!reasoningCard) {
        reasoningCard = document.createElement('div');
        reasoningCard.className = 'reasoning-card' + (isStreaming ? ' streaming' : '');
        reasoningCard.innerHTML = `
            <div class="card-header" onclick="toggleReasoningCard(this)">
                <div class="card-label">
                    <svg class="thinking-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"/>
                        <path d="M12 6v6l4 2"/>
                    </svg>
                    <span>深度思考</span>
                    ${isStreaming ? '<span class="thinking-status">思考中...</span>' : ''}
                </div>
                <svg class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
            </div>
            <div class="reasoning-content-wrapper">
                <div class="reasoning-summary">${escapeHtml(sanitizeReasoningContent(content || '正在生成原始思考内容...'))}</div>
                <details class="reasoning-raw-panel ${rawContent ? '' : 'hidden'}">
                    <summary>查看原始思考</summary>
                    <pre class="reasoning-content">${escapeHtml(sanitizeReasoningContent(rawContent))}</pre>
                </details>
            </div>
        `;
        processContainer.insertBefore(reasoningCard, processContainer.firstChild);
        state.pendingProcessCards.push(reasoningCard);
    } else {
        const summaryDiv = reasoningCard.querySelector('.reasoning-summary');
        const contentDiv = reasoningCard.querySelector('.reasoning-content');
        const rawPanel = reasoningCard.querySelector('.reasoning-raw-panel');
        if (summaryDiv) {
            summaryDiv.textContent = sanitizeReasoningContent(content || '正在生成原始思考内容...');
        }
        if (contentDiv) {
            contentDiv.textContent = sanitizeReasoningContent(rawContent);
        }
        if (rawPanel) {
            rawPanel.classList.toggle('hidden', !sanitizeReasoningContent(rawContent));
        }
        if (isStreaming) {
            reasoningCard.classList.add('streaming');
        }
    }
}

function renderThoughtMarkdown(text) {
    const normalized = normalizeLooseMarkdown(sanitizeReasoningContent(text || '').replace(/\n{3,}/g, '\n\n'));
    return escapeHtml(normalized).replace(/\n/g, '<br>');
}

function toggleThoughtSegment(button) {
    const segment = button.closest('.assistant-segment.is-thought');
    if (!segment) return;
    segment.classList.toggle('collapsed');
}

function renderAssistantDialogueFlow(messageId, isStreaming = false) {
    const bubble = document.getElementById(`bubble-${messageId}`);
    if (!bubble) return;

    const flow = state.messageFlowsByMessage[messageId] || [];
    const segmentsHtml = flow.map(segment => {
        const kindClass = segment.type === 'thought' ? 'is-thought' : 'is-answer';
        const contentHtml = segment.type === 'thought'
            ? renderThoughtMarkdown(segment.rawText || '')
            : renderMarkdown(segment.rawText || '');
        if (segment.type === 'thought') {
            const isComplete = !!segment.isComplete;
            return `
                <div class="assistant-segment ${kindClass}${isComplete ? ' collapsed' : ''}">
                    <button class="assistant-thought-toggle" onclick="toggleThoughtSegment(this)">
                        <span class="assistant-thought-chevron">${isComplete ? '&#9656;' : '&#9662;'}</span>
                        <span class="assistant-thought-dot"></span>
                        <span class="assistant-segment-label">${escapeHtml(isComplete ? '思考过程' : '思考中...')}</span>
                    </button>
                    <div class="assistant-thought-panel">
                        <div class="assistant-segment-body">${contentHtml || '正在整理思路...'}</div>
                    </div>
                </div>
            `;
        }
        return `
            <div class="assistant-segment ${kindClass}">
                <div class="assistant-segment-label">${escapeHtml('回答')}</div>
                <div class="assistant-segment-body markdown-body">${contentHtml || (segment.type === 'thought' ? '正在整理思路...' : '正在组织回答...')}</div>
                <div class="assistant-artifact-stack"></div>
            </div>
        `;
    }).join('');

    bubble.innerHTML = `
        <div class="assistant-dialogue-flow" id="flow-${messageId}">${segmentsHtml}</div>
        ${isStreaming ? '<span class="streaming-cursor"></span>' : ''}
    `;
}

function appendAssistantDialogueSegment(messageId, type, content, append = true) {
    const normalized = String(content || '').replace(/\r\n?/g, '\n');
    if (!normalized.trim()) return;

    const flow = state.messageFlowsByMessage[messageId] || [];
    const lastSegment = flow[flow.length - 1];
    if (type === 'answer' && lastSegment && lastSegment.type === 'thought') {
        lastSegment.isComplete = true;
    }
    if (type === 'thought' && lastSegment && lastSegment.type === 'answer' && String(lastSegment.rawText || '').trim()) {
        append = false;
    }
    if (append && lastSegment && lastSegment.type === type) {
        lastSegment.rawText = (lastSegment.rawText || '') + normalized;
    } else {
        flow.push({ type, rawText: normalized, isComplete: type !== 'thought' });
    }
    state.messageFlowsByMessage[messageId] = flow;
    renderAssistantDialogueFlow(messageId, state.isStreaming && state.currentMessageId === messageId);
}

function updateAssistantAnswerContent(messageId, content) {
    const normalized = String(content || '').replace(/\r\n?/g, '\n');
    const flow = state.messageFlowsByMessage[messageId] || [];
    let lastAnswerSegment = null;
    for (let index = flow.length - 1; index >= 0; index -= 1) {
        if (flow[index].type === 'answer') {
            lastAnswerSegment = flow[index];
            break;
        }
    }
    if (lastAnswerSegment) {
        lastAnswerSegment.rawText = normalized;
    } else if (normalized.trim()) {
        if (flow.length > 0 && flow[flow.length - 1].type === 'thought') {
            flow[flow.length - 1].isComplete = true;
        }
        flow.push({ type: 'answer', rawText: normalized, isComplete: true });
    }
    state.messageFlowsByMessage[messageId] = flow;
    renderAssistantDialogueFlow(messageId, state.isStreaming && state.currentMessageId === messageId);
}

function getLastAnswerArtifactHost(messageId) {
    const messageDiv = document.getElementById(messageId);
    if (!messageDiv) return null;
    const answerSegments = messageDiv.querySelectorAll('.assistant-segment.is-answer');
    if (!answerSegments.length) return null;
    const lastSegment = answerSegments[answerSegments.length - 1];
    return lastSegment.querySelector('.assistant-artifact-stack') || lastSegment;
}

function isWorkflowMessage(messageId) {
    return !!(state.workflowPlansByMessage[messageId] && state.workflowPlansByMessage[messageId].isWorkflow);
}

function applyWorkflowStepUpdate(messageId, data) {
    const current = state.workflowPlansByMessage[messageId] || { title: 'Workflow', steps: [], isWorkflow: true };
    const steps = Array.isArray(current.steps) ? current.steps.map(step => ({ ...step })) : [];
    const stepKey = data.step_key || '';
    const stepIndex = steps.findIndex(step => step.key === stepKey);
    const nextStep = {
        key: stepKey,
        label: data.label || data.step_label || stepKey,
        title: data.title || '待分析',
        status: data.status || 'pending',
        detail: data.detail || '',
        outcome: data.outcome || '',
    };

    if (stepIndex >= 0) {
        steps[stepIndex] = { ...steps[stepIndex], ...nextStep };
    } else if (stepKey) {
        steps.push(nextStep);
    }

    state.workflowPlansByMessage[messageId] = {
        ...current,
        steps,
        isWorkflow: true,
    };
}

function getWorkflowStepSymbol(status) {
    if (status === 'completed') return '[√]';
    if (status === 'running') return '[>]';
    if (status === 'error') return '[!]';
    return '[ ]';
}

function updateWorkflowTaskCard(messageId) {
    renderWorkflowSidebar(messageId);
}

function appendReasoningContent(content, asBlock = false) {
    const normalized = String(content || '').replace(/\r\n?/g, '\n').trim();
    if (!normalized) return;

    const existingBlocks = String(state.reasoningContent || '')
        .split(/\n\n+/)
        .map(block => block.trim())
        .filter(Boolean);
    if (existingBlocks.length > 0 && existingBlocks[existingBlocks.length - 1] === normalized) {
        return;
    }

    if (!state.reasoningContent) {
        state.reasoningContent = normalized;
        return;
    }

    state.reasoningContent += asBlock ? `\n\n${normalized}` : normalized;
}

function appendRawReasoningContent(content) {
    const normalized = String(content || '').replace(/\r\n?/g, '\n');
    if (!normalized.trim()) return;
    state.rawReasoningContent += normalized;
}

function appendProcessNote(messageId, note, isStreaming = false) {
    if (!note) return;
    appendReasoningContent(note, true);
    appendAssistantDialogueSegment(messageId, 'thought', note, false);
}

function finalizeReasoningCard(messageId) {
    const flow = state.messageFlowsByMessage[messageId] || [];
    let changed = false;
    flow.forEach(segment => {
        if (segment.type === 'thought' && !segment.isComplete) {
            segment.isComplete = true;
            changed = true;
        }
    });
    if (changed) {
        renderAssistantDialogueFlow(messageId, false);
    }
}

function sanitizeReasoningContent(content) {
    return String(content || '').replace(/\r\n?/g, '\n');
}

function getSubagentLabel(stage) {
    const labels = {
        orchestrator: '主调度',
        execution_specialist: '执行专项',
    };
    return labels[stage] || stage || '处理中';
}

function getDelegatedStageFromToolName(toolName) {
    const name = (toolName || '').toLowerCase();
    if (name.includes('subagent')) return 'execution_specialist';
    return '';
}

function syncSubagentCardFromToolStatus(messageId, data) {
    return;
}

function syncSubagentCardFromToolResult(messageId, data) {
    return;
}

function buildToolStatusText(data) {
    const toolName = getFriendlyToolName(data.tool_name || '');
    const detail = summarizeToolInput(data.tool_name || '', data.content || '');
    if (data.status === 'error') {
        return `工具执行失败: ${toolName}${detail ? `\n${detail}` : ''}`;
    }
    return `正在执行工具: ${toolName}${detail ? `\n${detail}` : ''}`;
}

function buildToolResultText(data) {
    const toolName = getFriendlyToolName(data.tool_name || '');
    const content = summarizeToolResult(data.tool_name || '', data.content || '');
    if (!content) return '';
    return `工具返回: ${toolName}\n${content}`;
}

function addToolResultEntry(messageId, toolName, content) {
    const messageDiv = document.getElementById(messageId);
    if (!messageDiv || !content) return;

    const processContainer = getOrCreateProcessContainer(messageDiv);
    if (!state.toolResultsByMessage[messageId]) {
        state.toolResultsByMessage[messageId] = [];
    }
    state.toolResultsByMessage[messageId].push({ toolName, content });

    let card = processContainer.querySelector('.tool-result-card');
    const entriesHtml = state.toolResultsByMessage[messageId].map((entry, index) => `
        <div class="tool-result-entry ${index > 0 ? 'with-divider' : ''}">
            <div class="tool-result-entry-header">
                <span class="tool-result-index">#${index + 1}</span>
                <span class="tool-result-toolname">${escapeHtml(getFriendlyToolName(entry.toolName || ''))}</span>
            </div>
            <div class="tool-result-content markdown-body">${renderMarkdown(entry.content)}</div>
        </div>
    `).join('');

    if (!card) {
        card = document.createElement('div');
        card.className = 'search-result-card tool-result-card';
        card.innerHTML = `
            <div class="card-header" onclick="toggleSearchCard(this)">
                <div class="card-label">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M9 12h6M9 16h6M9 8h6"/>
                        <path d="M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>
                    </svg>
                    <span>工具执行结果</span>
                    <span class="search-count tool-result-count">${state.toolResultsByMessage[messageId].length} 条</span>
                </div>
                <svg class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
            </div>
            <div class="search-content-wrapper tool-result-wrapper">
                <div class="tool-result-list">${entriesHtml}</div>
            </div>
        `;

        processContainer.appendChild(card);
        state.pendingProcessCards.push(card);
    } else {
        const list = card.querySelector('.tool-result-list');
        const count = card.querySelector('.tool-result-count');
        if (list) list.innerHTML = entriesHtml;
        if (count) count.textContent = `${state.toolResultsByMessage[messageId].length} 条`;
    }
}

function restoreAssistantProcessCards(messageId, message) {
    if (!messageId || !message) return;

    const reasoning = sanitizeReasoningContent(message.reasoning || '');
    if (reasoning) {
        appendAssistantDialogueSegment(messageId, 'thought', reasoning, false);
    }

    if (message.content) {
        updateAssistantAnswerContent(messageId, message.content);
    }
}

function restoreGeneratedArtifacts(messageId, artifacts) {
    if (!messageId || !Array.isArray(artifacts)) return;

    artifacts.forEach(artifact => {
        if (artifact.type === 'image_generated') {
            addImageCard(
                messageId,
                artifact.filename || '生成图片',
                artifact.filepath || '',
                artifact.size || 0,
                artifact.image_url || '',
                artifact.image_data || ''
            );
        } else if (artifact.type === 'file_generated') {
            addDownloadCard(
                messageId,
                artifact.filename || '生成文件',
                artifact.filepath || '',
                artifact.size || 0,
                artifact.download_url || ''
            );
        }
    });
}

function restoreInProgressSnapshot(snapshot) {
    if (!snapshot || !snapshot.message_id) return;

    const existed = !!document.getElementById(snapshot.message_id);
    const messageId = addMessage('assistant', snapshot.content || '', true, [], snapshot.message_id);
    state.currentMessageId = messageId;
    state.isStreaming = !!snapshot.is_streaming;
    state.isPaused = false;
    state.messageFlowsByMessage[messageId] = [];

    if (snapshot.reasoning) {
        state.reasoningContent = snapshot.reasoning;
        state.rawReasoningContent = snapshot.raw_reasoning || '';
        appendAssistantDialogueSegment(messageId, 'thought', snapshot.reasoning, false);
    }

    if (snapshot.content) {
        updateAssistantAnswerContent(messageId, snapshot.content);
    }

    if (snapshot.workflow && Array.isArray(snapshot.workflow.steps)) {
        state.workflowPlansByMessage[messageId] = {
            title: snapshot.workflow.title || 'Workflow',
            steps: snapshot.workflow.steps,
            isWorkflow: true,
        };
        updateWorkflowTaskCard(messageId);
    }

    state.toolResultsByMessage[messageId] = [];

    restoreGeneratedArtifacts(messageId, Array.isArray(snapshot.artifacts) ? snapshot.artifacts : []);

    if (!snapshot.is_streaming && !existed) {
        finalizeReasoningCard(messageId);
        collapseAllProcessCards(messageId);
    }

    refreshWorkspacePanels();
    return messageId;
}

async function refreshCurrentSessionSnapshot() {
    if (!state.sessionId) return;
    try {
        const response = await fetch(`/api/session/status?session_id=${encodeURIComponent(state.sessionId)}`);
        if (!response.ok) return;
        const data = await response.json();
        if (data.status !== 'success') return;

        const snapshot = data.in_progress;
        if (snapshot && snapshot.message_id) {
            restoreInProgressSnapshot(snapshot);
            if (!snapshot.is_streaming) {
                state.isStreaming = false;
                refreshWorkspacePanels();
                updateSendButton();
                if (state.restorePollTimer) {
                    window.clearInterval(state.restorePollTimer);
                    state.restorePollTimer = null;
                }
            }
        }
    } catch (error) {
        console.error('刷新进行中会话快照失败:', error);
    }
}

function ensureSnapshotPolling() {
    if (state.restorePollTimer) {
        window.clearInterval(state.restorePollTimer);
    }
    state.restorePollTimer = window.setInterval(() => {
        if (!document.hidden) {
            refreshCurrentSessionSnapshot();
        }
    }, 3000);
}

function addSearchResultCard(messageId, content) {
    const messageDiv = document.getElementById(messageId);
    if (!messageDiv) return;
    
    const processContainer = getOrCreateProcessContainer(messageDiv);
    
    // 解析搜索结果
    let searchResults = [];
    try {
        searchResults = JSON.parse(content);
        if (!Array.isArray(searchResults)) {
            searchResults = [];
        }
    } catch (e) {
        console.error('解析搜索结果失败:', e);
        return;
    }
    
    if (searchResults.length === 0) return;
    
    // 创建搜索结果卡片
    const searchCard = document.createElement('div');
    searchCard.className = 'search-result-card';
    
    // 生成搜索结果HTML
    const resultsHtml = searchResults.slice(0, 6).map((item, index) => `
        <a href="${escapeHtml(item.url || '#')}" target="_blank" class="search-result-item">
            <div class="search-result-number">${index + 1}</div>
            <div class="search-result-content">
                <div class="search-result-title">${escapeHtml(item.title || '无标题')}</div>
                <div class="search-result-url">${escapeHtml(item.url || '')}</div>
            </div>
        </a>
    `).join('');
    
    searchCard.innerHTML = `
        <div class="card-header" onclick="toggleSearchCard(this)">
            <div class="card-label">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="11" cy="11" r="8"/>
                    <path d="m21 21-4.35-4.35"/>
                </svg>
                <span>搜索结果</span>
                <span class="search-count">${searchResults.length} 条</span>
            </div>
            <svg class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
        </div>
        <div class="search-content-wrapper">
            <div class="search-results-list">
                ${resultsHtml}
            </div>
        </div>
    `;
    
    // 插入到思考卡片之后（如果存在）
    const reasoningCard = processContainer.querySelector('.reasoning-card');
    if (reasoningCard && reasoningCard.nextSibling) {
        processContainer.insertBefore(searchCard, reasoningCard.nextSibling);
    } else {
        processContainer.appendChild(searchCard);
    }
    
    state.pendingProcessCards.push(searchCard);
}

function toggleSearchCard(header) {
    const card = header.closest('.search-result-card');
    if (!card) return;
    
    const wrapper = card.querySelector('.search-content-wrapper');
    const toggleIcon = card.querySelector('.toggle-icon');
    
    if (card.classList.contains('collapsed')) {
        if (wrapper) {
            wrapper.style.maxHeight = '400px';
            wrapper.style.opacity = '1';
        }
        if (toggleIcon) toggleIcon.style.transform = 'rotate(0deg)';
        card.classList.remove('collapsed');
    } else {
        if (wrapper) {
            wrapper.style.maxHeight = '0';
            wrapper.style.opacity = '0';
        }
        if (toggleIcon) toggleIcon.style.transform = 'rotate(180deg)';
        card.classList.add('collapsed');
    }
}

function toggleWorkflowTaskCard(header) {
    const card = header.closest('.workflow-task-card');
    if (!card) return;

    const wrapper = card.querySelector('.workflow-task-wrapper');
    const toggleIcon = card.querySelector('.toggle-icon');

    if (card.classList.contains('collapsed')) {
        if (wrapper) {
            wrapper.style.maxHeight = '520px';
            wrapper.style.opacity = '1';
        }
        if (toggleIcon) toggleIcon.style.transform = 'rotate(0deg)';
        card.classList.remove('collapsed');
    } else {
        if (wrapper) {
            wrapper.style.maxHeight = '0';
            wrapper.style.opacity = '0';
        }
        if (toggleIcon) toggleIcon.style.transform = 'rotate(180deg)';
        card.classList.add('collapsed');
    }
}

function getFriendlyToolName(toolName) {
    const friendlyNames = {
        'Bash': 'Bash',
        'Glob': 'Glob',
        'Grep': 'Grep',
        'Read': 'Read',
        'Write': 'Write',
        'Edit': 'Edit',
        'GetSkill': 'GetSkill',
        'KnowledgeSearch': 'KnowledgeSearch',
        'KnowledgeAdd': 'KnowledgeAdd',
        'WebSearch': 'WebSearch',
        'WebFetch': 'WebFetch',
        'MemorySearch': 'MemorySearch',
        'MemoryAdd': 'MemoryAdd',
        'CreatePlan': 'CreatePlan',
        'UpdateProjectInstructions': 'UpdateProjectInstructions',
        'CreateScheduledTask': 'CreateScheduledTask',
        'ListScheduledTasks': 'ListScheduledTasks',
        'CancelScheduledTask': 'CancelScheduledTask',
        'SubAgent': 'SubAgent',
        'ParallelSubAgent': 'ParallelSubAgent',
        'context_compress': 'ContextCompress',
    };
    
    if (!toolName) return '处理中...';
    
    const name = toolName.toLowerCase().replace(/['"]/g, '').trim();
    
    for (const [key, value] of Object.entries(friendlyNames)) {
        if (name.includes(key.toLowerCase())) {
            return value;
        }
    }
    
    return '处理中...';
}

// 搜索结果和思考卡片在最终回答后会自动折叠
function collapseAllProcessCards(messageId) {
    const messageDiv = document.getElementById(messageId);
    if (!messageDiv) return;
    if (isWorkflowMessage(messageId)) return;
    
    // 折叠思考卡片
    const reasoningCard = messageDiv.querySelector('.reasoning-card');
    if (reasoningCard && !reasoningCard.classList.contains('collapsed')) {
        const wrapper = reasoningCard.querySelector('.reasoning-content-wrapper');
        const toggleIcon = reasoningCard.querySelector('.toggle-icon');
        if (wrapper) {
            wrapper.style.maxHeight = '0';
            wrapper.style.opacity = '0';
        }
        if (toggleIcon) {
            toggleIcon.style.transform = 'rotate(180deg)';
        }
        reasoningCard.classList.add('collapsed');
    }
    
    // 折叠搜索结果卡片
    const searchCard = messageDiv.querySelector('.search-result-card');
    if (searchCard && !searchCard.classList.contains('collapsed')) {
        const wrapper = searchCard.querySelector('.search-content-wrapper');
        const toggleIcon = searchCard.querySelector('.toggle-icon');
        if (wrapper) {
            wrapper.style.maxHeight = '0';
            wrapper.style.opacity = '0';
        }
        if (toggleIcon) {
            toggleIcon.style.transform = 'rotate(180deg)';
        }
        searchCard.classList.add('collapsed');
    }

}

function toggleReasoningCard(header) {
    const card = header.closest('.reasoning-card');
    if (!card) return;
    
    const wrapper = card.querySelector('.reasoning-content-wrapper');
    const toggleIcon = card.querySelector('.toggle-icon');
    
    if (card.classList.contains('collapsed')) {
        if (wrapper) {
            wrapper.style.maxHeight = '300px';
            wrapper.style.opacity = '1';
        }
        if (toggleIcon) toggleIcon.style.transform = 'rotate(0deg)';
        card.classList.remove('collapsed');
    } else {
        if (wrapper) {
            wrapper.style.maxHeight = '0';
            wrapper.style.opacity = '0';
        }
        if (toggleIcon) toggleIcon.style.transform = 'rotate(180deg)';
        card.classList.add('collapsed');
    }
}

function addDownloadCard(messageId, filename, filepath, size, downloadUrl) {
    const messageDiv = document.getElementById(messageId);
    if (!messageDiv) return;
    if (isBlockedInternalFileLink(filepath, downloadUrl)) return;
    const artifactKey = downloadUrl || filepath || filename;
    if (artifactKey && messageDiv.querySelector(`.download-card[data-artifact-key="${CSS.escape(artifactKey)}"]`)) return;
    
    const sizeStr = formatFileSize(size || 0);
    const href = downloadUrl || `/api/download/${encodeURIComponent(filepath)}`;
    
    const card = document.createElement('div');
    card.className = 'download-card';
    if (artifactKey) card.dataset.artifactKey = artifactKey;
    card.innerHTML = `
        <div class="download-icon-wrapper">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                <polyline points="14,2 14,8 20,8"/>
                <line x1="12" y1="18" x2="12" y2="12"/>
                <polyline points="9,15 12,18 15,15"/>
            </svg>
        </div>
        <div class="download-info">
            <span class="download-filename">${escapeHtml(filename)}</span>
            <span class="download-size">${sizeStr}</span>
        </div>
        <a class="download-btn" href="${href}" download="${filename}">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/>
            </svg>
            点击下载
        </a>
    `;
    
    const artifactHost = getLastAnswerArtifactHost(messageId);
    if (artifactHost) {
        artifactHost.appendChild(card);
        return;
    }

    const messageContent = messageDiv.querySelector('.message-content');
    const messageBubble = messageDiv.querySelector('.message-bubble');
    messageContent.insertBefore(card, messageBubble);
}

function addImageCard(messageId, filename, filepath, size, imageUrl, imageBase64) {
    const messageDiv = document.getElementById(messageId);
    if (!messageDiv) return;
    if (isBlockedInternalFileLink(filepath, imageUrl)) return;
    const artifactKey = imageUrl || filepath || filename;
    if (artifactKey && messageDiv.querySelector(`.image-card[data-artifact-key="${CSS.escape(artifactKey)}"]`)) return;
    
    const sizeStr = formatFileSize(size || 0);
    const imageSrc = imageBase64 ? `data:image/png;base64,${imageBase64}` : imageUrl;
    const downloadHref = imageUrl || `/api/download/${encodeURIComponent(filepath)}`;
    if (!imageSrc) return;
    
    const card = document.createElement('div');
    card.className = 'image-card';
    if (artifactKey) card.dataset.artifactKey = artifactKey;
    card.innerHTML = `
        <div class="image-preview-container">
            <img src="${imageSrc}" alt="${escapeHtml(filename)}" class="generated-image" onclick="openImageModal(this.src)">
        </div>
        <div class="image-info-bar">
            <div class="image-info">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                    <circle cx="8.5" cy="8.5" r="1.5"/>
                    <polyline points="21 15 16 10 5 21"/>
                </svg>
                <span class="image-filename">${escapeHtml(filename)}</span>
                <span class="image-size">${sizeStr}</span>
            </div>
            <a class="image-download-btn" href="${downloadHref}" download="${filename}">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/>
                </svg>
                下载图片
            </a>
        </div>
    `;
    
    const artifactHost = getLastAnswerArtifactHost(messageId);
    if (artifactHost) {
        artifactHost.appendChild(card);
        return;
    }

    const messageContent = messageDiv.querySelector('.message-content');
    const messageBubble = messageDiv.querySelector('.message-bubble');
    messageContent.insertBefore(card, messageBubble);
}

function openImageModal(src) {
    const modal = document.createElement('div');
    modal.className = 'image-modal';
    modal.innerHTML = `
        <div class="image-modal-content">
            <img src="${src}" alt="放大图片">
            <button class="image-modal-close" onclick="this.closest('.image-modal').remove()">✕</button>
        </div>
    `;
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });
    document.body.appendChild(modal);
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// ============================================
// Markdown 渲染
// ============================================
function renderMarkdown(text) {
    if (!text) return '';

    const markdown = normalizeLooseMarkdown(filterInternalUrls(filterSystemPaths(text)).replace(/\r\n?/g, '\n'));
    if (typeof marked === 'undefined') {
        return escapeHtml(markdown).replace(/\n/g, '<br>');
    }

    return marked.parse(markdown, {
        gfm: true,
        breaks: true,
        async: false,
    });
}

function filterSystemPaths(text) {
    if (!text) return text;
    
    let filtered = text;
    
    const pathPatterns = [
        /[A-Z]:\\[^\s<>\n]+\.(?:csv|xlsx|xls|json|txt|docx|doc|pdf|png|jpg|jpeg|gif)/gi,
        /\/(?:home|usr|var|tmp|data|app|root)\/[^\s<>\n]+\.(?:csv|xlsx|xls|json|txt|docx|doc|pdf|png|jpg|jpeg|gif)/gi,
    ];
    
    pathPatterns.forEach(pattern => {
        filtered = filtered.replace(pattern, (match) => {
            const filename = match.split(/[/\\]/).pop();
            return `[${filename}]`;
        });
    });
    
    filtered = filtered.replace(/已成功生成[^，。！？\n]*[，：]\s*文件保存于[^\n]*/gi, '已生成报告文件。');
    
    return filtered;
}

function isBlockedInternalFileLink(filepath, url) {
    const candidates = [filepath, url]
        .filter(Boolean)
        .map(value => String(value).toLowerCase());
    return candidates.some(value => value.includes('/uploads/') || value.includes('\\uploads\\') || value.includes('/api/download/') && value.includes('uploads'));
}

function filterInternalUrls(text) {
    if (!text) return text;

    let filtered = text;
    const blockedUrlPatterns = [
        /https?:\/\/[^\s)\]]*uploads[^\s)\]]*/gi,
        /\/api\/download\/[^\s)\]]*uploads[^\s)\]]*/gi,
        /\[[^\]]+\]\(([^)]*uploads[^)]*)\)/gi,
    ];

    blockedUrlPatterns.forEach(pattern => {
        filtered = filtered.replace(pattern, (match, markdownHref) => {
            if (markdownHref) {
                const labelMatch = match.match(/^\[([^\]]+)\]\(/);
                const label = labelMatch ? labelMatch[1] : '内部上传文件';
                return `\`${label}\``;
            }
            return '`内部上传文件`';
        });
    });

    return filtered;
}

function normalizeLooseMarkdown(text) {
    if (!text) return text;

    let normalized = text.replace(/\u00a0/g, ' ');
    normalized = normalizePredictionTables(normalized);

    const lines = normalized.split('\n');
    const normalizedLines = lines.map((line) => {
        let normalizedLine = line;
        normalizedLine = normalizedLine.replace(/\*([^*\n]+)：\*\*/g, '**$1：**');
        normalizedLine = normalizedLine.replace(/^(#{1,6})([^\s#])/g, '$1 $2');
        normalizedLine = normalizedLine.replace(/^(\d+)\.([^\s])/g, '$1. $2');
        normalizedLine = normalizedLine.replace(/^([-*])([^\s\-*])/g, '$1 $2');
        return normalizedLine;
    });

    return normalizedLines.join('\n').replace(/\n{3,}/g, '\n\n');
}

function normalizePredictionTables(text) {
    if (!text) return text;

    // 已经是标准 Markdown 表格时，直接交给 marked，避免二次修正把合法内容改坏。
    if (/^\|[-:| ]+\|$/m.test(text)) {
        return text;
    }

    let normalized = text;

    // 拆开被模型拼在同一行的 Markdown 表格行。
    normalized = normalized.replace(/\|\s*\|/g, '|\n|');
    normalized = normalized.replace(/(\|[-| ]+\|)(?=\|\s*\d+)/g, '$1\n');

    // 修正常见的日期时间粘连格式。
    normalized = normalized.replace(/(\d{4}-\d{2}-\d{2})(\d{2}:\d{2}:\d{2})/g, '$1 $2');

    const lines = normalized.split('\n');
    const output = [];

    for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index].trim();

        if (!line) {
            output.push('');
            continue;
        }

        // 制表符或多空格形式的两列表头，转换为标准 Markdown 表格。
        if (line === '项目\t值' || /^项目\s{2,}值$/.test(line)) {
            output.push('| 项目 | 值 |');
            output.push('|------|-----|');
            continue;
        }

        if (/^(预测模式|历史数据点数|预测步数|推断时间间隔)\t/.test(line)) {
            const [key, value] = line.split('\t');
            output.push(`| ${key.trim()} | ${(value || '').trim()} |`);
            continue;
        }

        if (/^(预测模式|历史数据点数|预测步数|推断时间间隔)\s{2,}/.test(line)) {
            const [key, value] = line.split(/\s{2,}/);
            output.push(`| ${key.trim()} | ${(value || '').trim()} |`);
            continue;
        }

        // 规范预测结果表头。
        if (/^\|?\s*序号\s*\|\s*时间\s*\|\s*流量\(m³\/s\)\s*\|\s*置信区间\s*\|?$/i.test(line)) {
            output.push('| 序号 | 时间 | 流量(m³/s) | 置信区间 |');
            output.push('|------|------|------------|----------|');
            continue;
        }

        // 规范预测结果数据行。
        if (line.startsWith('|')) {
            const cells = line
                .split('|')
                .map(cell => cell.trim())
                .filter(Boolean);

            if (cells.length === 4 && /^\d+$/.test(cells[0])) {
                const time = cells[1].replace(/(\d{4}-\d{2}-\d{2})(\d{2}:\d{2}:\d{2})/g, '$1 $2');
                let interval = cells[3]
                    .replace(/\[(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\]/g, '[$1 ~ $2]')
                    .replace(/\[(\d+(?:\.\d+)?)\s+(?=\d)/g, '[$1 ~ ')
                    .replace(/~\s*/g, '~ ');
                output.push(`| ${cells[0]} | ${time} | ${cells[2]} | ${interval} |`);
                continue;
            }
        }

        output.push(lines[index]);
    }

    return output.join('\n');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================
// 工具函数
// ============================================
function scrollToBottom() {
    elements.chatContainer.scrollTop = elements.chatContainer.scrollHeight;
}

function showLoading(text = '加载中...') {
    const loadingText = elements.loadingOverlay.querySelector('.loading-text');
    if (loadingText) loadingText.textContent = text;
    elements.loadingOverlay.classList.add('active');
}

function hideLoading() {
    elements.loadingOverlay.classList.remove('active');
}

function showError(message) {
    const errorDiv = document.createElement('div');
    errorDiv.className = 'error-toast';
    errorDiv.innerHTML = `
        <span>❌</span>
        <span>${escapeHtml(message)}</span>
    `;
    
    errorDiv.style.cssText = `
        position: fixed;
        top: 70px;
        left: 50%;
        transform: translateX(-50%);
        background: #ef4444;
        color: white;
        padding: 12px 24px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        gap: 10px;
        z-index: 10000;
        animation: slideDown 0.3s ease;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    `;
    
    document.body.appendChild(errorDiv);
    
    setTimeout(() => {
        errorDiv.style.animation = 'slideUp 0.3s ease';
        setTimeout(() => errorDiv.remove(), 300);
    }, 4000);
}

function showSuccess(message) {
    const successDiv = document.createElement('div');
    successDiv.className = 'success-toast';
    successDiv.innerHTML = `
        <span>✅</span>
        <span>${escapeHtml(message)}</span>
    `;
    
    successDiv.style.cssText = `
        position: fixed;
        top: 70px;
        left: 50%;
        transform: translateX(-50%);
        background: #10b981;
        color: white;
        padding: 12px 24px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        gap: 10px;
        z-index: 10000;
        animation: slideDown 0.3s ease;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    `;
    
    document.body.appendChild(successDiv);
    
    setTimeout(() => {
        successDiv.style.animation = 'slideUp 0.3s ease';
        setTimeout(() => successDiv.remove(), 300);
    }, 3000);
}

function generateSessionId() {
    return 'session-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
}

// ============================================
// 历史对话管理
// ============================================
function renderHistoryList() {
    if (state.chatHistory.length === 0) {
        elements.historyList.innerHTML = '<p class="no-history">暂无历史对话</p>';
        return;
    }
    
    elements.historyList.innerHTML = state.chatHistory.map(item => `
        <div class="history-item ${item.id === state.sessionId ? 'active' : ''}" data-id="${item.id}">
            <span class="history-icon">💬</span>
            <span class="history-title">${escapeHtml(item.title)}</span>
            <button class="history-delete-btn" data-id="${item.id}" title="删除会话">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M18 6L6 18M6 6l12 12"/>
                </svg>
            </button>
        </div>
    `).join('');
    
    elements.historyList.querySelectorAll('.history-item').forEach(item => {
        item.addEventListener('click', (e) => {
            if (e.target.closest('.history-delete-btn')) return;
            const historyId = item.dataset.id;
            if (historyId !== state.sessionId) {
                loadHistorySession(historyId);
            }
        });
    });
    
    elements.historyList.querySelectorAll('.history-delete-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const historyId = btn.dataset.id;
            deleteHistorySession(historyId);
        });
    });
}

async function deleteHistorySession(historyId) {
    if (!confirm('确定要删除这个会话吗？')) return;
    
    try {
        const deletingCurrentSession = historyId === state.sessionId;
        if (deletingCurrentSession) {
            await switchToFreshSessionBeforeDeletion();
        }

        const response = await fetch(`/api/sessions/${historyId}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            await loadServerSessions();
            scheduleSessionListRefresh(1200);
            showSuccess('会话已删除');
        } else {
            throw new Error('删除失败');
        }
    } catch (error) {
        console.error('删除会话失败:', error);
        showError('删除会话失败');
    }
}

async function resetToFreshSession({ refreshDelay = 0 } = {}) {
    elements.messagesList.innerHTML = '';
    state.messages = [];
    state.sessionId = generateSessionId();
    state.uploadedFiles = [];
    state.pendingUploadedFiles = [];
    state.isPaused = false;
    state.isStreaming = false;
    state.enableSearch = false;
    state.enableRag = true;
    state.reasoningContent = '';
    state.rawReasoningContent = '';
    state.toolActivityFeed = [];
    state.toolResultsByMessage = {};
    state.subagentPlansByMessage = {};
    state.workflowPlansByMessage = {};

    localStorage.setItem('floodmind_session_id', state.sessionId);
    elements.welcomeMessage.classList.remove('hidden');
    refreshWorkspacePanels();
    updateSendButton();

    await loadServerSessions();
    if (refreshDelay > 0) {
        scheduleSessionListRefresh(refreshDelay);
    }
    fetch('/api/init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            session_id: state.sessionId,
            enable_search: false,
            enable_rag: true,
            enable_reasoning: state.enableReasoning,
        })
    }).catch(console.error);
}

async function switchToFreshSessionBeforeDeletion() {
    await resetToFreshSession({ refreshDelay: 1200 });
}

async function loadHistorySession(historyId) {
    showLoading('加载会话...');
    
    try {
        await saveSessionToServer();
        
        state.sessionId = historyId;
        localStorage.setItem('floodmind_session_id', historyId);
        
        elements.messagesList.innerHTML = '';
        state.messages = [];
        state.uploadedFiles = [];
        state.pendingUploadedFiles = [];
        state.toolActivityFeed = [];
        state.toolResultsByMessage = {};
        state.subagentPlansByMessage = {};
        state.workflowPlansByMessage = {};
        
        const response = await fetch(`/api/sessions/${historyId}`);
        if (response.ok) {
            const data = await response.json();
            if (data.status === 'success' && data.messages && data.messages.length > 0) {
                elements.welcomeMessage.classList.add('hidden');
                let lastAssistantMessageId = '';
                
                data.messages.forEach(msg => {
                    const messageId = addMessage(msg.role, msg.content, false);
                    if (msg.role === 'assistant') {
                        lastAssistantMessageId = messageId;
                        restoreAssistantProcessCards(messageId, msg);
                    }
                });

                if (lastAssistantMessageId && Array.isArray(data.artifacts) && data.artifacts.length > 0) {
                    restoreGeneratedArtifacts(lastAssistantMessageId, data.artifacts);
                }

                if (data.in_progress && data.in_progress.message_id) {
                    restoreInProgressSnapshot(data.in_progress);
                }
                
                state.messages = data.messages.map((msg, idx) => ({
                    role: msg.role,
                    content: msg.content,
                    id: `restored-${idx}`
                }));
            } else {
                if (data.status === 'success' && data.in_progress && data.in_progress.message_id) {
                    elements.welcomeMessage.classList.add('hidden');
                    restoreInProgressSnapshot(data.in_progress);
                } else {
                    elements.welcomeMessage.classList.remove('hidden');
                }
            }
        }
        
        renderHistoryList();
        refreshWorkspacePanels();
        showSuccess('已切换到历史会话');
        
    } catch (error) {
        console.error('加载历史会话失败:', error);
        showError('加载会话失败');
    } finally {
        hideLoading();
    }
}

async function startNewChat() {
    if (state.messages.length > 0) {
        await saveSessionToServer();
    }

    await resetToFreshSession({ refreshDelay: 2500 });
}

async function updateSessionConfig() {
    try {
        const response = await fetch('/api/session/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId,
                enable_search: state.enableSearch,
                enable_rag: state.enableRag,
                enable_reasoning: state.enableReasoning
            })
        });
        
        if (response.ok) {
            console.log('配置已更新');
            refreshWorkspacePanels();
        }
    } catch (error) {
        console.error('更新配置失败:', error);
    }
}

function trimRetainedProcessState() {
    const retainedIds = Object.keys(state.toolResultsByMessage).slice(-6);
    Object.keys(state.toolResultsByMessage).forEach(messageId => {
        if (!retainedIds.includes(messageId)) delete state.toolResultsByMessage[messageId];
    });
    Object.keys(state.subagentPlansByMessage).forEach(messageId => {
        if (!retainedIds.includes(messageId)) delete state.subagentPlansByMessage[messageId];
    });
    Object.keys(state.workflowPlansByMessage).forEach(messageId => {
        if (!retainedIds.includes(messageId)) delete state.workflowPlansByMessage[messageId];
    });
}

async function previewUploadedFile(fileId) {
    if (!fileId) return;
    try {
        showLoading('读取文件内容...');
        const response = await fetch(`/api/files/${encodeURIComponent(fileId)}/preview?session_id=${encodeURIComponent(state.sessionId)}`);
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            throw new Error(data.message || '预览失败');
        }
        showFilePreviewDialog(data.preview);
    } catch (error) {
        console.error('预览上传文件失败:', error);
        showError(`预览失败: ${error.message}`);
    } finally {
        hideLoading();
    }
}

function showFilePreviewDialog(preview) {
    const dialog = document.createElement('div');
    dialog.className = 'dialog-overlay file-preview-overlay';
    let bodyHtml = '<p class="file-preview-empty">暂无预览内容。</p>';

    if (preview.preview_type === 'text') {
        bodyHtml = `<pre class="file-preview-text">${escapeHtml(preview.content || '')}</pre>`;
    } else if (preview.preview_type === 'table') {
        bodyHtml = renderPreviewTable(preview.columns || [], preview.rows || []);
    } else if (preview.preview_type === 'excel') {
        bodyHtml = (preview.sheets || []).map(sheet => `
            <section class="file-preview-sheet">
                <h4>${escapeHtml(sheet.sheet_name || 'Sheet')}</h4>
                ${renderPreviewTable(sheet.columns || [], sheet.rows || [])}
            </section>
        `).join('');
    } else {
        bodyHtml = `<p class="file-preview-empty">${escapeHtml(preview.content || '该文件类型暂不支持在线预览。')}</p>`;
    }

    dialog.innerHTML = `
        <div class="dialog-content file-preview-dialog">
            <div class="dialog-header">
                <div>
                    <h3>${escapeHtml(preview.file_name || '文件预览')}</h3>
                    <div class="file-preview-meta">${formatFileSize(preview.size || 0)}</div>
                </div>
                <button class="dialog-close" onclick="this.closest('.dialog-overlay').remove()">✕</button>
            </div>
            <div class="dialog-body file-preview-body">
                ${bodyHtml}
            </div>
        </div>
    `;
    dialog.addEventListener('click', (e) => {
        if (e.target === dialog) dialog.remove();
    });
    document.body.appendChild(dialog);
}

function renderPreviewTable(columns, rows) {
    if (!columns.length) {
        return '<p class="file-preview-empty">未识别到可展示的表头。</p>';
    }
    const head = `<tr>${columns.map(col => `<th>${escapeHtml(String(col))}</th>`).join('')}</tr>`;
    const body = rows.length > 0
        ? rows.map(row => `<tr>${row.map(cell => `<td>${escapeHtml(String(cell ?? ''))}</td>`).join('')}</tr>`).join('')
        : `<tr><td colspan="${columns.length}">无数据</td></tr>`;
    return `
        <div class="file-preview-table-wrapper">
            <table class="file-preview-table">
                <thead>${head}</thead>
                <tbody>${body}</tbody>
            </table>
        </div>
    `;
}

// ============================================
// 添加动画样式和对话框样式
// ============================================
const style = document.createElement('style');
style.textContent = `
    @keyframes slideDown {
        from { transform: translateX(-50%) translateY(-20px); opacity: 0; }
        to { transform: translateX(-50%) translateY(0); opacity: 1; }
    }
    @keyframes slideUp {
        from { transform: translateX(-50%) translateY(0); opacity: 1; }
        to { transform: translateX(-50%) translateY(-20px); opacity: 0; }
    }
    
    .dialog-overlay {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.5);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
    }
    
    .dialog-content {
        background: white;
        border-radius: 12px;
        width: 90%;
        max-width: 400px;
        box-shadow: 0 20px 40px rgba(0, 0, 0, 0.2);
    }
    
    .dialog-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 16px 20px;
        border-bottom: 1px solid #e5e5e5;
    }
    
    .dialog-header h3 {
        font-size: 16px;
        font-weight: 600;
        color: #1a1a1a;
    }
    
    .dialog-close {
        width: 28px;
        height: 28px;
        border: none;
        background: transparent;
        border-radius: 6px;
        cursor: pointer;
        color: #666;
        font-size: 16px;
    }
    
    .dialog-close:hover {
        background: #f0f0f0;
    }
    
    .dialog-body {
        padding: 20px;
        font-size: 14px;
        color: #333;
        line-height: 1.6;
    }
    
    .dialog-body ul {
        margin: 10px 0;
        padding-left: 20px;
    }
    
    .dialog-body li {
        margin: 6px 0;
    }
    
    .setting-item {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 0;
        border-bottom: 1px solid #f0f0f0;
    }
    
    .setting-item:last-child {
        border-bottom: none;
    }

    .tool-status-card,
    .tool-result-card,
    .memory-status-card,
    .workflow-task-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        margin-bottom: 12px;
        overflow: hidden;
    }

    .tool-status-body,
    .memory-status-body {
        padding: 0 14px 14px;
        color: #475569;
        font-size: 13px;
        line-height: 1.6;
    }

    .tool-status-detail {
        margin-top: 6px;
        color: #64748b;
        font-size: 12px;
    }

    .tool-status-badge,
    .tool-result-badge,
    .memory-status-badge {
        display: inline-flex;
        align-items: center;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 700;
    }

    .tool-status-badge.is-running,
    .memory-status-badge.is-running {
        background: #dbeafe;
        color: #1d4ed8;
    }

    .tool-status-badge.is-error {
        background: #fee2e2;
        color: #b91c1c;
    }

    .tool-result-badge,
    .memory-status-badge.is-done {
        background: #dcfce7;
        color: #15803d;
    }

    .tool-result-wrapper {
        max-height: 360px;
        opacity: 1;
        transition: max-height 0.25s ease, opacity 0.2s ease;
    }

    .workflow-task-wrapper {
        max-height: 520px;
        opacity: 1;
        transition: max-height 0.25s ease, opacity 0.2s ease;
        padding: 0 14px 14px;
    }

    .workflow-task-card.collapsed .workflow-task-wrapper {
        max-height: 0;
        opacity: 0;
        padding-bottom: 0;
    }

    .workflow-task-title {
        padding-top: 4px;
        margin-bottom: 10px;
        color: #475569;
        font-size: 13px;
    }

    .workflow-steps {
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    .workflow-step {
        border-radius: 10px;
        padding: 10px 12px;
        background: #ffffff;
        border: 1px solid #e2e8f0;
    }

    .workflow-step.is-running {
        border-color: #93c5fd;
        background: #eff6ff;
    }

    .workflow-step.is-completed {
        border-color: #86efac;
        background: #f0fdf4;
    }

    .workflow-step.is-error {
        border-color: #fca5a5;
        background: #fef2f2;
    }

    .workflow-step-main {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        font-size: 13px;
    }

    .workflow-step-symbol {
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        color: #0f172a;
    }

    .workflow-step-label {
        color: #475569;
        font-weight: 700;
    }

    .workflow-step-title {
        color: #0f172a;
    }

    .workflow-step-detail {
        margin-top: 6px;
        padding-left: 40px;
        color: #64748b;
        font-size: 12px;
        line-height: 1.6;
    }

    .tool-result-card.collapsed .tool-result-wrapper {
        max-height: 0;
        opacity: 0;
    }

    .tool-result-content {
        padding: 0 14px 14px;
        font-size: 13px;
        line-height: 1.7;
        color: #0f172a;
        overflow: auto;
    }
    
    .setting-info {
        display: flex;
        flex-direction: column;
        gap: 2px;
    }
    
    .setting-title {
        font-size: 14px;
        font-weight: 500;
        color: #1a1a1a;
    }
    
    .setting-desc {
        font-size: 12px;
        color: #999;
    }
    
    .send-btn.streaming {
        background: #f59e0b !important;
    }
    
    .send-btn.streaming:hover {
        background: #d97706 !important;
    }
    
    .history-delete-btn {
        width: 24px;
        height: 24px;
        border: none;
        background: transparent;
        border-radius: 4px;
        cursor: pointer;
        color: #999;
        display: flex;
        align-items: center;
        justify-content: center;
        opacity: 0;
        transition: all 0.15s ease;
    }
    
    .history-item:hover .history-delete-btn {
        opacity: 1;
    }
    
    .history-delete-btn:hover {
        background: #fee2e2;
        color: #ef4444;
    }
    
    table.md-table {
        border-collapse: collapse;
        margin: 12px 0;
        font-size: 14px;
        width: 100%;
        overflow-x: auto;
    }
    
    table.md-table th,
    table.md-table td {
        border: 1px solid #e5e7eb;
        padding: 8px 12px;
        text-align: left;
    }
    
    table.md-table th {
        background-color: #f9fafb;
        font-weight: 600;
        color: #374151;
    }
    
    table.md-table tr:nth-child(even) {
        background-color: #f9fafb;
    }
    
    table.md-table tr:hover {
        background-color: #f3f4f6;
    }
    
    .image-card {
        background: white;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        overflow: hidden;
        margin: 12px 0;
        border: 1px solid #e5e7eb;
    }
    
    .image-preview-container {
        width: 100%;
        max-height: 400px;
        overflow: hidden;
        background: #f9fafb;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
    }
    
    .generated-image {
        max-width: 100%;
        max-height: 400px;
        object-fit: contain;
        transition: transform 0.2s ease;
    }
    
    .generated-image:hover {
        transform: scale(1.02);
    }
    
    .image-info-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 16px;
        background: #f9fafb;
        border-top: 1px solid #e5e7eb;
    }
    
    .image-info {
        display: flex;
        align-items: center;
        gap: 8px;
        color: #6b7280;
    }
    
    .image-filename {
        font-weight: 500;
        color: #374151;
    }
    
    .image-size {
        font-size: 12px;
        color: #9ca3af;
    }
    
    .image-download-btn {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 8px 16px;
        background: #3b82f6;
        color: white;
        border-radius: 8px;
        text-decoration: none;
        font-size: 14px;
        font-weight: 500;
        transition: all 0.2s ease;
    }
    
    .image-download-btn:hover {
        background: #2563eb;
        transform: translateY(-1px);
    }
    
    .image-modal {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.9);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
        cursor: pointer;
    }
    
    .image-modal-content {
        position: relative;
        max-width: 90vw;
        max-height: 90vh;
    }
    
    .image-modal-content img {
        max-width: 90vw;
        max-height: 90vh;
        object-fit: contain;
        border-radius: 8px;
        box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
    }
    
    .image-modal-close {
        position: absolute;
        top: -40px;
        right: 0;
        width: 32px;
        height: 32px;
        background: white;
        border: none;
        border-radius: 50%;
        cursor: pointer;
        font-size: 18px;
        color: #374151;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.2s ease;
    }
    
    .image-modal-close:hover {
        background: #f3f4f6;
        transform: scale(1.1);
    }
`;
document.head.appendChild(style);
