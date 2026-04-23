const API = '/api/v1';
const USER_ID = 'web-user';
const MAX_ACTIVE_SESSIONS = 5;

const state = {
    currentView: 'entry',
    currentSessionId: null,
    currentSession: null,
    entrySessions: [],
    statusSessionId: null,
    liveRun: null,
    pendingTitleRequest: null,
    skillSearchTimer: null,
};

const el = {
    sessionBubbles: document.getElementById('session-bubbles'),
    entrySummary: document.getElementById('entry-summary'),
    chatMessages: document.getElementById('chat-messages'),
    chatInput: document.getElementById('chat-input'),
    chatSend: document.getElementById('chat-send'),
    chatTitle: document.getElementById('chat-title'),
    chatMeta: document.getElementById('chat-meta'),
    chatSummary: document.getElementById('chat-summary'),
    taskMode: document.getElementById('task-mode'),
    selectedSessionText: document.getElementById('selected-session-text'),
    statusOverviewList: document.getElementById('status-overview-list'),
    statusRunTree: document.getElementById('status-run-tree'),
    skillsList: document.getElementById('skills-list'),
    skillsSummary: document.getElementById('skills-summary'),
    skillSearch: document.getElementById('skill-search'),
    tasksList: document.getElementById('tasks-list'),
    tasksSummary: document.getElementById('tasks-summary'),
    taskSkill: document.getElementById('task-skill'),
    toolsList: document.getElementById('tools-list'),
    toolsSummary: document.getElementById('tools-summary'),
    toolApprovals: document.getElementById('tool-approvals'),
    skillFilter: document.getElementById('skill-filter'),
    healthDot: document.getElementById('health-dot'),
    healthText: document.getElementById('health-text'),
    chatNav: document.querySelector('.nav-chat'),
    toastStack: document.getElementById('toast-stack'),
    sessionModal: document.getElementById('modal-session'),
    sessionModalTitle: document.getElementById('session-modal-title'),
    sessionModalHint: document.getElementById('session-modal-hint'),
    sessionTitleInput: document.getElementById('session-title-input'),
    sessionSubmit: document.getElementById('btn-session-submit'),
    sessionCancel: document.getElementById('btn-session-cancel'),
    sessionClose: document.getElementById('btn-session-close'),
    sessionForm: document.getElementById('form-session'),
};

document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', event => {
        event.preventDefault();
        const view = item.dataset.view;
        if (view === 'chat' && !state.currentSessionId) {
            return;
        }
        setView(view);
    });
});

document.getElementById('btn-back-entry').addEventListener('click', () => setView('entry'));
document.getElementById('btn-refresh-chat').addEventListener('click', () => refreshCurrentSession());
document.getElementById('btn-refresh-status').addEventListener('click', () => loadStatusOverview(true));
document.getElementById('btn-refresh-skills').addEventListener('click', () => loadSkills());
document.getElementById('btn-refresh-tasks').addEventListener('click', () => loadTasks());
document.getElementById('btn-refresh-tools').addEventListener('click', () => loadTools());
document.getElementById('btn-close-session').addEventListener('click', () => closeCurrentSession());
document.getElementById('form-task').addEventListener('submit', createTask);
el.sessionForm.addEventListener('submit', submitTitleModal);
el.sessionCancel.addEventListener('click', () => resolveTitleModal({ cancelled: true, value: '' }));
el.sessionClose.addEventListener('click', () => resolveTitleModal({ cancelled: true, value: '' }));

document.querySelectorAll('[data-close]').forEach(button => {
    button.addEventListener('click', () => {
        document.getElementById(button.dataset.close).classList.add('hidden');
    });
});

el.skillFilter.addEventListener('change', () => loadSkills());
el.skillSearch.addEventListener('input', () => {
    if (state.skillSearchTimer) {
        window.clearTimeout(state.skillSearchTimer);
    }
    state.skillSearchTimer = window.setTimeout(() => loadSkills(), 180);
});
el.chatSend.addEventListener('click', () => sendMessage());
el.chatInput.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
});

document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !el.sessionModal.classList.contains('hidden')) {
        resolveTitleModal({ cancelled: true, value: '' });
    }
});

el.sessionBubbles.addEventListener('click', event => {
    const deleteTarget = findClosestFromEvent(event, '[data-action="delete-session"]');
    if (deleteTarget) {
        deleteSession(deleteTarget.dataset.sessionId);
        return;
    }

    const createTarget = findClosestFromEvent(event, '[data-action="create-session"]');
    if (createTarget) {
        createSession();
        return;
    }

    const bubble = findClosestFromEvent(event, '[data-session-id]');
    if (bubble) {
        openSession(bubble.dataset.sessionId);
    }
});

el.statusOverviewList.addEventListener('click', event => {
    const item = findClosestFromEvent(event, '[data-status-session-id]');
    if (item) {
        loadStatusSession(item.dataset.statusSessionId);
    }
});

el.skillsList.addEventListener('click', async event => {
    const button = findClosestFromEvent(event, '[data-skill-name]');
    if (!button) {
        return;
    }
    try {
        await requestJSON(`/skills/${button.dataset.skillName}/actions`, {
            method: 'POST',
            body: { action: button.dataset.nextAction, operator: USER_ID },
        });
        await Promise.all([loadSkills(), loadTaskSkills()]);
        showToast(button.dataset.nextAction === 'enable' ? 'Skill 已启用' : 'Skill 已停用', 'success');
    } catch (error) {
        showToast(error.message || 'Skill 状态更新失败', 'error');
    }
});

el.toolApprovals.addEventListener('click', handleApprovalAction);
el.chatMessages.addEventListener('click', handleApprovalAction);

el.tasksList.addEventListener('click', async event => {
    const openButton = findClosestFromEvent(event, '[data-task-open-session]');
    if (openButton) {
        openSession(openButton.dataset.taskOpenSession);
        return;
    }

    const button = findClosestFromEvent(event, '[data-task-action]');
    if (!button) {
        return;
    }
    const taskId = button.dataset.taskId;
    const action = button.dataset.taskAction;
    try {
        await requestJSON(`/tasks/${taskId}/${action}`, { method: 'POST', body: {} });
        await Promise.all([loadTasks(), loadStatusOverview(false)]);
        showToast(`任务操作已提交：${action}`, 'success');
    } catch (error) {
        showToast(error.message || '任务操作失败', 'error');
    }
});

async function handleApprovalAction(event) {
    const button = findClosestFromEvent(event, '[data-approval-id]');
    if (!button) {
        return;
    }
    const approvalId = button.dataset.approvalId;
    const decision = button.dataset.decision;
    button.disabled = true;
    try {
        await requestJSON(`/tools/approvals/${approvalId}`, {
            method: 'POST',
            body: { decision, operator: USER_ID },
        });
        await Promise.all([loadTools(), loadStatusOverview(false)]);
        if (state.currentSessionId) {
            await refreshCurrentSession();
        }
        showToast(decision === 'approved' ? '审批已通过' : '审批已拒绝', 'success');
    } catch (error) {
        showToast(error.message || '审批操作失败', 'error');
        button.disabled = false;
    }
}

async function fetchJSON(path, options = {}) {
    const response = await fetch(`${API}${path}`, options);
    if (!response.ok) {
        let detail = '请求失败';
        try {
            const payload = await response.json();
            detail = payload.detail || detail;
        } catch {
            detail = response.statusText || detail;
        }
        throw new Error(detail);
    }
    return response.json();
}

async function requestJSON(path, { method = 'GET', body = null } = {}) {
    return fetchJSON(path, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
    });
}

function findClosestFromEvent(event, selector) {
    const target = event.target instanceof Element ? event.target : event.target?.parentElement;
    if (!target) {
        return null;
    }
    return target.closest(selector);
}

function setView(view) {
    state.currentView = view;
    document.querySelectorAll('.view').forEach(node => node.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(node => node.classList.remove('active'));

    const section = document.getElementById(`view-${view}`);
    if (section) {
        section.classList.add('active');
    }

    const nav = document.querySelector(`.nav-item[data-view="${view}"]`);
    if (nav) {
        nav.classList.add('active');
    }

    if (view === 'entry') {
        loadEntry();
    }
    if (view === 'status') {
        loadStatusOverview(true);
    }
    if (view === 'skills') {
        loadSkills();
    }
    if (view === 'tasks') {
        loadTasks();
    }
    if (view === 'tools') {
        loadTools();
    }
    if (view === 'chat' && state.currentSessionId) {
        refreshCurrentSession();
    }
}

async function checkHealth() {
    try {
        const data = await fetchJSON('/health');
        if (data.status === 'ok') {
            el.healthDot.className = 'status-dot ok';
            el.healthText.textContent = '服务正常';
            return;
        }
    } catch {
    }
    el.healthDot.className = 'status-dot err';
    el.healthText.textContent = '服务异常';
}

function showToast(message, kind = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${kind}`;
    toast.textContent = message;
    el.toastStack.appendChild(toast);
    window.requestAnimationFrame(() => toast.classList.add('visible'));
    window.setTimeout(() => {
        toast.classList.remove('visible');
        window.setTimeout(() => toast.remove(), 220);
    }, 2600);
}

function requestTitleModal({ title, hint, submitLabel }) {
    if (state.pendingTitleRequest) {
        state.pendingTitleRequest({ cancelled: true, value: '' });
    }
    return new Promise(resolve => {
        state.pendingTitleRequest = resolve;
        el.sessionModalTitle.textContent = title;
        el.sessionModalHint.textContent = hint;
        el.sessionSubmit.textContent = submitLabel;
        el.sessionTitleInput.value = '';
        el.sessionModal.classList.remove('hidden');
        window.requestAnimationFrame(() => el.sessionTitleInput.focus());
    });
}

function submitTitleModal(event) {
    event.preventDefault();
    resolveTitleModal({
        cancelled: false,
        value: el.sessionTitleInput.value.trim(),
    });
}

function resolveTitleModal(result) {
    const resolver = state.pendingTitleRequest;
    state.pendingTitleRequest = null;
    el.sessionModal.classList.add('hidden');
    el.sessionTitleInput.value = '';
    if (resolver) {
        resolver(result);
    }
}

async function loadEntry() {
    try {
        const sessions = await fetchJSON(`/status/entry?user_id=${encodeURIComponent(USER_ID)}`);
        state.entrySessions = sessions;
        renderEntrySummary(sessions);
        const canCreate = sessions.length < MAX_ACTIVE_SESSIONS;
        if (!sessions.length) {
            el.sessionBubbles.innerHTML = renderCreateSessionCard(canCreate);
            return;
        }

        const cards = sessions.map(session => `
            <article class="bubble-card session-card ${session.session_id === state.currentSessionId ? 'active' : ''}" data-session-id="${escapeHTML(session.session_id)}">
                <button type="button" class="bubble-delete" data-action="delete-session" data-session-id="${escapeHTML(session.session_id)}" aria-label="删除会话">×</button>
                <div class="bubble-head solo">
                    <h3>${escapeHTML(getSessionDisplayName(session))}</h3>
                </div>
                <div class="bubble-meta">
                    <span>${escapeHTML(formatTime(session.last_active_at))}</span>
                </div>
            </article>
        `).join('');

        el.sessionBubbles.innerHTML = cards + renderCreateSessionCard(canCreate);
    } catch (error) {
        state.entrySessions = [];
        el.sessionBubbles.innerHTML = '<div class="empty-state">会话入口加载失败</div>';
        showToast(error.message || '会话入口加载失败', 'error');
    }
}

function renderEntrySummary(sessions) {
    const running = sessions.filter(item => item.current_run_status === 'running').length;
    const remaining = Math.max(MAX_ACTIVE_SESSIONS - sessions.length, 0);
    el.entrySummary.innerHTML = [
        `会话 ${sessions.length}/${MAX_ACTIVE_SESSIONS}`,
        `运行中 ${running}`,
        remaining ? `可新建 ${remaining}` : '已达上限',
    ].map(renderSummaryPill).join('');
}

function renderCreateSessionCard(canCreate) {
    return `
        <button type="button" class="bubble-card create ${canCreate ? '' : 'is-disabled'}" data-action="create-session" ${canCreate ? '' : 'disabled aria-disabled="true"'}>
            <div class="bubble-plus">+</div>
            <h3>${canCreate ? '新建' : '已满'}</h3>
            <div class="bubble-meta bubble-note">
                <span>${canCreate ? '最多 5 个会话' : '请先删除一个会话'}</span>
            </div>
        </button>
    `;
}

function getSessionDisplayName(session) {
    if (!session) {
        return '未命名会话';
    }
    return session.display_label || session.title || '未命名会话';
}

async function createSession() {
    if (state.entrySessions.length >= MAX_ACTIVE_SESSIONS) {
        showToast(`最多只能保留 ${MAX_ACTIVE_SESSIONS} 个会话，请先删除一个现有会话`, 'warning');
        return;
    }
    const modalResult = await requestTitleModal({
        title: '新建会话',
        hint: '输入名称，可留空。',
        submitLabel: '创建',
    });
    if (modalResult.cancelled) {
        return;
    }
    try {
        const created = await requestJSON('/sessions', {
            method: 'POST',
            body: {
                user_id: USER_ID,
                title: modalResult.value || null,
                channel_type: 'web',
            },
        });
        await Promise.all([loadEntry(), loadStatusOverview(false)]);
        showToast('会话已创建', 'success');
        await openSession(created.session_id);
    } catch (error) {
        showToast(error.message || '创建会话失败', 'error');
    }
}

async function openSession(sessionId) {
    try {
        const session = await fetchJSON(`/sessions/${sessionId}`);
        state.currentSessionId = sessionId;
        state.currentSession = session;
        el.taskMode.value = 'continue';
        el.chatNav.classList.remove('hidden');
        setView('chat');
        renderChat(session);
    } catch (error) {
        showToast(error.message || '打开会话失败', 'error');
    }
}

async function refreshCurrentSession() {
    if (!state.currentSessionId) {
        return;
    }
    try {
        const session = await fetchJSON(`/sessions/${state.currentSessionId}`);
        state.currentSession = session;
        renderChat(session);
    } catch (error) {
        showToast(error.message || '刷新会话失败', 'error');
    }
}

function renderChat(session) {
    const messageCount = Array.isArray(session.messages) ? session.messages.length : 0;
    const sessionTitle = session.title || '当前会话';
    const statusText = session.status === 'active' ? '活跃' : session.status;

    el.chatTitle.textContent = sessionTitle;
    el.chatMeta.textContent = `最近活跃 ${formatTime(session.last_active_at)}`;
    el.selectedSessionText.textContent = sessionTitle || shortId(session.id);
    el.chatSummary.innerHTML = [
        `状态 ${statusText}`,
        `消息 ${messageCount}`,
        `会话 ${sessionTitle}`,
    ].map(renderSummaryPill).join('');

    const messages = session.messages || [];
    const liveHtml = state.liveRun ? renderLiveMessage(state.liveRun) : '';

    if (!messages.length && !liveHtml) {
        el.chatMessages.innerHTML = '<div class="msg system">开始对话。</div>';
        return;
    }

    el.chatMessages.innerHTML = messages.map(renderMessage).join('') + liveHtml;
    el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
}

function renderLiveMessage(liveRun) {
    const steps = Array.isArray(liveRun.steps) ? liveRun.steps : [];
    const approval = liveRun.pendingApproval;
    const usage = liveRun.usage;
    const approvalHtml = approval ? `
        <div class="approval-card inline-approval">
            <div class="stack-item-head">
                <strong>工具审批</strong>
                ${renderBadge(approval.status || 'pending')}
            </div>
            <div class="stack-item-meta">
                <span>${escapeHTML(approval.tool_name || '-')}</span>
                <span>${escapeHTML(JSON.stringify(approval.arguments || {}))}</span>
            </div>
            ${approval.approval_id ? `
                <div class="approval-actions">
                    <button class="btn btn-secondary" data-approval-id="${escapeHTML(approval.approval_id)}" data-decision="rejected">拒绝</button>
                    <button class="btn btn-primary" data-approval-id="${escapeHTML(approval.approval_id)}" data-decision="approved">批准</button>
                </div>
            ` : ''}
        </div>
    ` : '';
    const usageHtml = usage ? `
        <div class="table-muted live-usage">输入 ${escapeHTML(usage.input_tokens || 0)} | 输出 ${escapeHTML(usage.output_tokens || 0)} | 费用 ${escapeHTML(usage.estimated_cost || 0)}</div>
    ` : '';

    return `
        <div class="msg assistant live">
            <div>${escapeHTML(liveRun.reply || 'Agent 正在处理中...')}</div>
            ${usageHtml}
            ${steps.length ? `
                <div class="msg-steps">
                    ${steps.map(step => `
                        <div class="step-card">
                            <div>${escapeHTML(step.thinking || step.action || '')}</div>
                            ${step.action ? `<div class="table-muted">工具 ${escapeHTML(step.action)} ${escapeHTML(JSON.stringify(step.action_input || {}))}</div>` : ''}
                            ${step.observation ? `<div class="table-muted">${escapeHTML(step.observation)}</div>` : ''}
                        </div>
                    `).join('')}
                </div>
            ` : ''}
            ${approvalHtml}
        </div>
    `;
}

function renderMessage(message) {
    const steps = Array.isArray(message.metadata?.steps) ? message.metadata.steps : [];
    const stepsHtml = steps.length ? `
        <div class="msg-steps">
            ${steps.map(step => `
                <div class="step-card">
                    <div>${escapeHTML(step.thinking || step.action || '')}</div>
                    ${step.observation ? `<div class="table-muted">${escapeHTML(step.observation)}</div>` : ''}
                </div>
            `).join('')}
        </div>
    ` : '';

    return `
        <div class="msg ${escapeHTML(message.role)}">
            <div>${escapeHTML(message.content)}</div>
            ${stepsHtml}
        </div>
    `;
}

async function sendMessage() {
    const message = el.chatInput.value.trim();
    if (!message) {
        return;
    }

    const taskMode = el.taskMode.value;
    if (!state.currentSessionId && taskMode !== 'new_task') {
        showToast('请先选择一个会话，或切换到“新建会话”。', 'warning');
        return;
    }

    let newTaskTitle = null;
    if (taskMode === 'new_task') {
        const modalResult = await requestTitleModal({
            title: '新建会话',
            hint: '输入名称，可留空。',
            submitLabel: '开始',
        });
        if (modalResult.cancelled) {
            return;
        }
        newTaskTitle = modalResult.value || null;
    }

    el.chatSend.disabled = true;

    try {
        startLiveRun({
            message,
            sessionTitle: newTaskTitle || null,
            taskMode,
        });
        const result = await streamJSON('/agent/chat', {
            method: 'POST',
            body: {
                session_id: state.currentSessionId,
                message,
                task_mode: taskMode,
                session_title: newTaskTitle || null,
                stream: true,
                user_id: USER_ID,
            },
            onEvent: handleChatStreamEvent,
        });
        el.chatInput.value = '';
        state.liveRun = null;
        if (result.session_id) {
            state.currentSessionId = result.session_id;
        }
        await Promise.all([
            state.currentSessionId ? openSession(state.currentSessionId) : Promise.resolve(),
            loadEntry(),
            loadStatusOverview(false),
            loadTools(),
        ]);
        showToast('Agent 运行完成，界面已刷新', 'success');
    } catch (error) {
        state.liveRun = null;
        if (state.currentSession) {
            renderChat(state.currentSession);
        }
        showToast(error.message || '发送失败', 'error');
    } finally {
        el.chatSend.disabled = false;
    }
}

function startLiveRun({ message, sessionTitle, taskMode }) {
    state.liveRun = {
        userMessage: message,
        reply: '',
        steps: [],
        latestThinkingByStep: {},
        pendingApproval: null,
        usage: null,
        sessionTitle: sessionTitle || (state.currentSession ? state.currentSession.title : '新会话'),
        taskMode,
    };
    el.chatNav.classList.remove('hidden');
    if (!state.currentSession) {
        renderLiveOnly();
    } else {
        renderChat(state.currentSession);
    }
}

function renderLiveOnly() {
    const title = state.liveRun?.sessionTitle || '新会话';
    el.chatTitle.textContent = title;
    el.chatMeta.textContent = '处理中';
    el.selectedSessionText.textContent = title;
    el.chatSummary.innerHTML = [
        '状态 处理中',
        `会话 ${title}`,
    ].map(renderSummaryPill).join('');
    el.chatMessages.innerHTML = `
        <div class="msg user live"><div>${escapeHTML(state.liveRun.userMessage || '')}</div></div>
    ` + renderLiveMessage(state.liveRun);
    el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
}

function handleChatStreamEvent(event, payload) {
    if (!state.liveRun) {
        return;
    }

    if (event === 'thinking') {
        const stepKey = String(payload.step || '0');
        state.liveRun.latestThinkingByStep[stepKey] = payload.content || '';
    }

    if (event === 'action') {
        const stepKey = String(payload.step || '0');
        state.liveRun.steps.push({
            step: payload.step,
            thinking: state.liveRun.latestThinkingByStep[stepKey] || '',
            action: payload.name,
            action_input: payload.input || {},
            observation: '',
        });
    }

    if (event === 'observation') {
        const step = [...state.liveRun.steps].reverse().find(item => !item.observation);
        if (step) {
            step.observation = payload.content || '';
        }
    }

    if (event === 'reply') {
        state.liveRun.reply = payload.content || '';
    }

    if (event === 'approval_pending') {
        state.liveRun.pendingApproval = payload;
        if (!state.liveRun.reply) {
            state.liveRun.reply = `工具 ${payload.tool_name || ''} 正在等待审批。`;
        }
    }

    if (event === 'usage') {
        state.liveRun.usage = payload;
    }

    if (state.currentSession) {
        renderChat(state.currentSession);
    } else {
        renderLiveOnly();
    }
}

async function streamJSON(path, { method = 'POST', body = null, onEvent }) {
    const response = await fetch(`${API}${path}`, {
        method,
        headers: body ? { 'Content-Type': 'application/json', Accept: 'text/event-stream' } : { Accept: 'text/event-stream' },
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
        let detail = '请求失败';
        try {
            const payload = await response.json();
            detail = payload.detail || detail;
        } catch {
            detail = response.statusText || detail;
        }
        throw new Error(detail);
    }
    if (!response.body) {
        throw new Error('浏览器不支持流式响应');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    const finalPayload = {};

    while (true) {
        const { value, done } = await reader.read();
        if (done) {
            break;
        }
        buffer += decoder.decode(value, { stream: true });
        buffer = buffer.replace(/\r\n/g, '\n');

        let boundary = buffer.indexOf('\n\n');
        while (boundary !== -1) {
            const chunk = buffer.slice(0, boundary).trim();
            buffer = buffer.slice(boundary + 2);
            if (chunk && !chunk.startsWith(':')) {
                const parsed = parseSSEChunk(chunk);
                if (parsed) {
                    if (parsed.event === 'done') {
                        Object.assign(finalPayload, parsed.payload || {});
                    }
                    if (parsed.event === 'usage') {
                        finalPayload.usage = parsed.payload || {};
                    }
                    onEvent(parsed.event, parsed.payload || {});
                }
            }
            boundary = buffer.indexOf('\n\n');
        }
    }

    return finalPayload;
}

function parseSSEChunk(chunk) {
    let eventName = 'message';
    const dataLines = [];
    chunk.split('\n').forEach(line => {
        if (line.startsWith('event:')) {
            eventName = line.slice(6).trim();
            return;
        }
        if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim());
        }
    });
    if (!dataLines.length) {
        return null;
    }
    try {
        return {
            event: eventName,
            payload: JSON.parse(dataLines.join('\n')),
        };
    } catch {
        return {
            event: eventName,
            payload: { content: dataLines.join('\n') },
        };
    }
}

function resetCurrentSessionState() {
    state.currentSessionId = null;
    state.currentSession = null;
    state.liveRun = null;
    el.chatNav.classList.add('hidden');
    el.selectedSessionText.textContent = '当前未进入任何会话';
    el.chatTitle.textContent = '当前会话';
    el.chatMeta.textContent = '尚未选择会话。';
    el.chatSummary.innerHTML = '';
    el.chatMessages.innerHTML = '<div class="msg system">请先选择一个会话。</div>';
}

async function deleteSession(sessionId) {
    if (!sessionId) {
        return;
    }
    const targetSession = state.entrySessions.find(item => item.session_id === sessionId)
        || (state.currentSession && state.currentSession.id === sessionId ? state.currentSession : null);
    const sessionName = getSessionDisplayName(targetSession);
    if (!window.confirm(`确认删除“${sessionName}”？`)) {
        return;
    }
    try {
        await requestJSON(`/sessions/${sessionId}/close`, { method: 'POST', body: {} });
        if (state.currentSessionId === sessionId) {
            resetCurrentSessionState();
        }
        if (state.statusSessionId === sessionId) {
            state.statusSessionId = null;
        }
        await Promise.all([loadEntry(), loadStatusOverview(false)]);
        showToast('会话已删除', 'success');
        if (state.currentView === 'chat' && state.currentSessionId === null) {
            setView('entry');
        }
    } catch (error) {
        showToast(error.message || '删除会话失败', 'error');
    }
}

async function closeCurrentSession() {
    if (!state.currentSessionId) {
        return;
    }
    await deleteSession(state.currentSessionId);
}

async function loadStatusOverview(forceTreeRefresh) {
    try {
        const payload = await fetchJSON(`/status/overview?user_id=${encodeURIComponent(USER_ID)}`);
        const sessions = payload.sessions || [];
        if (!sessions.length) {
            state.statusSessionId = null;
            el.statusOverviewList.innerHTML = '<div class="empty-state">暂无会话。</div>';
            if (forceTreeRefresh) {
                el.statusRunTree.innerHTML = '<div class="empty-state">选择左侧会话后查看详情。</div>';
            }
            return;
        }

        el.statusOverviewList.innerHTML = sessions.map(session => `
            <button class="stack-item ${session.session_id === state.statusSessionId ? 'active' : ''}" data-status-session-id="${escapeHTML(session.session_id)}">
                <div class="stack-item-head">
                    <strong>${escapeHTML(session.title || '未命名会话')}</strong>
                    ${renderBadge((session.current_main_run || {}).status || session.status)}
                </div>
                <div class="stack-item-meta">
                    <span>${escapeHTML(formatTime(session.last_event_at))}</span>
                </div>
            </button>
        `).join('');

        const targetSessionId = forceTreeRefresh
            ? (state.statusSessionId || sessions[0].session_id)
            : (state.statusSessionId || sessions[0].session_id);
        await loadStatusSession(targetSessionId);
    } catch (error) {
        el.statusOverviewList.innerHTML = '<div class="empty-state">状态总览加载失败</div>';
        showToast(error.message || '状态总览加载失败', 'error');
    }
}

async function loadStatusSession(sessionId) {
    state.statusSessionId = sessionId;
    try {
        const payload = await fetchJSON(`/status/sessions/${sessionId}`);
        const currentTitle = payload.title || '未命名会话';
        const runs = payload.runs || [];
        if (!runs.length) {
            el.statusRunTree.innerHTML = `<div class="empty-state">${escapeHTML(currentTitle)} 暂无记录。</div>`;
            return;
        }

        const childMap = new Map();
        runs.filter(run => run.parent_run_id).forEach(run => {
            const siblings = childMap.get(run.parent_run_id) || [];
            siblings.push(run);
            childMap.set(run.parent_run_id, siblings);
        });
        const mainRuns = runs.filter(run => !run.parent_run_id);

        el.statusRunTree.innerHTML = `
            <div class="stack-item">
                <div class="stack-item-head">
                    <strong>${escapeHTML(currentTitle)}</strong>
                    ${renderBadge((payload.current_main_run || {}).status || 'idle')}
                </div>
                <div class="stack-item-meta">
                    <span>记录 ${runs.length}</span>
                    <span>${escapeHTML(formatTime((payload.current_main_run || {}).started_at || ''))}</span>
                </div>
            </div>
            ${mainRuns.map((run, index) => renderRunNode(run, childMap.get(run.run_id) || [], index + 1)).join('')}
        `;

        document.querySelectorAll('[data-status-session-id]').forEach(item => {
            item.classList.toggle('active', item.dataset.statusSessionId === sessionId);
        });
    } catch (error) {
        el.statusRunTree.innerHTML = '<div class="empty-state">详情加载失败</div>';
        showToast(error.message || '详情加载失败', 'error');
    }
}

function renderRunNode(run, childRuns = [], index = 1) {
    return `
        <div class="run-node">
            <div class="run-head">
                <div>
                    <strong>记录 ${escapeHTML(index)}</strong>
                </div>
                ${renderBadge(run.status)}
            </div>
            <div class="run-meta">
                <span>${escapeHTML(run.progress_text || '')}</span>
                <span>开始 ${escapeHTML(formatTime(run.started_at))}</span>
                <span>${escapeHTML(run.latest_event || '')}</span>
                ${run.latest_error ? `<span>错误 ${escapeHTML(run.latest_error)}</span>` : ''}
            </div>
            ${childRuns.map((child, childIndex) => `
                <div class="run-node sub">
                    <div class="run-head">
                        <div>
                            <strong>子项 ${escapeHTML(childIndex + 1)}</strong>
                        </div>
                        ${renderBadge(child.status)}
                    </div>
                    <div class="run-meta">
                        <span>${escapeHTML(child.progress_text || '')}</span>
                        <span>开始 ${escapeHTML(formatTime(child.started_at))}</span>
                        <span>${escapeHTML(child.latest_event || '')}</span>
                        ${child.latest_error ? `<span>错误 ${escapeHTML(child.latest_error)}</span>` : ''}
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}

async function loadSkills() {
    try {
        const filter = el.skillFilter.value;
        const keyword = el.skillSearch.value.trim();
        const params = new URLSearchParams();
        if (filter) {
            params.set('status', filter);
        }
        if (keyword) {
            params.set('keyword', keyword);
        }
        const suffix = params.size ? `?${params.toString()}` : '';
        const [skills] = await Promise.all([
            fetchJSON(`/skills${suffix}`),
        ]);
        renderSkillsSummary(skills);

        if (!skills.length) {
            el.skillsList.innerHTML = '<tr><td colspan="5" class="empty">暂无 Skill</td></tr>';
        } else {
            el.skillsList.innerHTML = skills.map(skill => `
                <tr>
                    <td><strong>${escapeHTML(skill.skill_name || skill.name)}</strong><div class="table-muted">${escapeHTML(skill.description || '')}</div></td>
                    <td>${renderBadge(skill.status)}</td>
                    <td>${escapeHTML(skill.source || 'project')}</td>
                    <td>${escapeHTML(formatTime(skill.last_indexed_at || skill.indexed_at))}</td>
                    <td>
                        <button class="btn btn-secondary" data-skill-name="${escapeHTML(skill.skill_name || skill.name)}" data-next-action="${skill.status === 'enabled' ? 'disable' : 'enable'}">
                            ${skill.status === 'enabled' ? '停用' : '启用'}
                        </button>
                    </td>
                </tr>
            `).join('');
        }

    } catch (error) {
        el.skillsList.innerHTML = '<tr><td colspan="5" class="empty">Skill 目录加载失败</td></tr>';
        showToast(error.message || 'Skill 目录加载失败', 'error');
    }
}

function renderSkillsSummary(skills) {
    const enabled = skills.filter(skill => skill.status === 'enabled').length;
    const disabled = skills.filter(skill => skill.status === 'disabled').length;
    el.skillsSummary.innerHTML = [
        `目录总数 ${skills.length}`,
        `启用 ${enabled}`,
        `停用 ${disabled}`,
    ].map(renderSummaryPill).join('');
}

async function loadTaskSkills() {
    try {
        const skills = await fetchJSON('/skills?status=enabled');
        const taskSkillValue = el.taskSkill.value;
        const options = ['<option value="">默认</option>']
            .concat(skills.map(skill => `<option value="${escapeHTML(skill.skill_name || skill.name)}">${escapeHTML(skill.skill_name || skill.name)}${skill.description ? ` · ${escapeHTML(skill.description)}` : ''}</option>`))
            .join('');
        el.taskSkill.innerHTML = options;
        if (taskSkillValue) {
            el.taskSkill.value = taskSkillValue;
        }
    } catch (error) {
        showToast(error.message || 'Skill 选项加载失败', 'error');
    }
}

async function createTask(event) {
    event.preventDefault();
    const form = event.target;
    try {
        await requestJSON('/tasks', {
            method: 'POST',
            body: {
                title: form.title.value,
                prompt: form.prompt.value,
                schedule_text: form.schedule_text.value,
                requested_skill_name: form.requested_skill_name.value || null,
            },
        });
        form.reset();
        await loadTasks();
        showToast('任务已创建', 'success');
    } catch (error) {
        showToast(error.message || '创建任务失败', 'error');
    }
}

async function loadTasks() {
    try {
        const tasks = await fetchJSON('/tasks');
        renderTasksSummary(tasks);
        if (!tasks.length) {
            el.tasksList.innerHTML = '<div class="empty-state">暂无任务</div>';
            return;
        }

        el.tasksList.innerHTML = tasks.map(task => `
            <div class="stack-item task-card">
                <div class="stack-item-head">
                    <strong>${escapeHTML(task.title || '未命名任务')}</strong>
                    ${renderBadge(task.status || 'idle')}
                </div>
                <div class="stack-item-meta">
                    <span>${escapeHTML(task.status_text || task.status || '')}</span>
                    <span>${escapeHTML(task.human_schedule || '')}</span>
                </div>
                <div>${escapeHTML(task.prompt || '')}</div>
                <div class="stack-item-meta">
                    <span>下次执行 ${escapeHTML(formatTime(task.next_run_at))}</span>
                    <span>最近执行 ${escapeHTML(formatTime(task.last_run_at))}</span>
                    <span>累计成本 ${escapeHTML((task.cost?.total_cost || 0).toFixed ? (task.cost.total_cost || 0).toFixed(6) : String(task.cost?.total_cost || 0))}</span>
                </div>
                <div class="table-muted">最近结果 ${escapeHTML(task.last_result?.reply || task.last_result?.error || (task.last_result?.pending_approval ? '等待审批' : '-'))}</div>
                <div class="approval-actions">
                    <button class="btn btn-secondary" data-task-id="${escapeHTML(task.id)}" data-task-action="run-now">立即执行</button>
                    ${task.status === 'active'
                        ? `<button class="btn btn-secondary" data-task-id="${escapeHTML(task.id)}" data-task-action="pause">暂停</button>`
                        : ''}
                    ${task.status === 'paused'
                        ? `<button class="btn btn-secondary" data-task-id="${escapeHTML(task.id)}" data-task-action="resume">恢复</button>`
                        : ''}
                    ${task.status !== 'cancelled' && task.status !== 'completed'
                        ? `<button class="btn btn-primary" data-task-id="${escapeHTML(task.id)}" data-task-action="cancel">取消</button>`
                        : ''}
                    ${task.session_id
                        ? `<button class="btn btn-secondary" data-task-open-session="${escapeHTML(task.session_id)}">打开会话</button>`
                        : ''}
                </div>
            </div>
        `).join('');
    } catch (error) {
        el.tasksList.innerHTML = '<div class="empty-state">任务列表加载失败</div>';
        showToast(error.message || '任务列表加载失败', 'error');
    }
}

function renderTasksSummary(tasks) {
    const active = tasks.filter(task => task.status === 'active').length;
    const paused = tasks.filter(task => task.status === 'paused').length;
    const waiting = tasks.filter(task => task.last_result?.pending_approval).length;
    el.tasksSummary.innerHTML = [
        `任务总数 ${tasks.length}`,
        `活跃 ${active}`,
        `暂停 ${paused}`,
        `待审批 ${waiting}`,
    ].map(renderSummaryPill).join('');
}

async function loadTools() {
    try {
        const [tools, approvals] = await Promise.all([
            fetchJSON('/tools'),
            fetchJSON('/tools/approvals?status=pending'),
        ]);
        renderToolsSummary(tools, approvals);

        if (!tools.length) {
            el.toolsList.innerHTML = '<div class="empty-state">暂无已注册工具</div>';
        } else {
            el.toolsList.innerHTML = tools.map(tool => `
                <article class="tool-card">
                    <h4>${escapeHTML(tool.display_name || tool.name)}</h4>
                    <div class="tool-name">${escapeHTML(tool.name)}</div>
                    <p>${escapeHTML(tool.description || '无描述')}</p>
                    <div class="tool-meta">
                        ${renderBadge(tool.category || 'custom')}
                        ${renderBadge(tool.requires_approval ? 'approval_required' : 'enabled')}
                    </div>
                </article>
            `).join('');
        }

        if (!approvals.length) {
            el.toolApprovals.innerHTML = '<div class="empty-state">暂无待审批调用</div>';
            return;
        }

        el.toolApprovals.innerHTML = approvals.map(approval => `
            <div class="stack-item approval-card">
                <div class="stack-item-head">
                    <strong>${escapeHTML(approval.tool_name || '-')}</strong>
                    ${renderBadge(approval.status || 'pending')}
                </div>
                <div class="stack-item-meta">
                    <span>会话 ${escapeHTML(shortId(approval.session_id))}</span>
                    <span>运行 ${escapeHTML(shortId(approval.run_id))}</span>
                </div>
                <div class="table-muted">${escapeHTML(JSON.stringify(approval.arguments || {}))}</div>
                <div class="approval-actions">
                    <button class="btn btn-secondary" data-approval-id="${escapeHTML(approval.approval_id)}" data-decision="rejected">拒绝</button>
                    <button class="btn btn-primary" data-approval-id="${escapeHTML(approval.approval_id)}" data-decision="approved">批准</button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        el.toolsList.innerHTML = '<div class="empty-state">工具列表加载失败</div>';
        el.toolApprovals.innerHTML = '<div class="empty-state">审批列表加载失败</div>';
        showToast(error.message || '工具列表加载失败', 'error');
    }
}

function renderToolsSummary(tools, approvals) {
    const approvalRequired = tools.filter(tool => tool.requires_approval).length;
    el.toolsSummary.innerHTML = [
        `工具总数 ${tools.length}`,
        `需审批 ${approvalRequired}`,
        `待处理审批 ${approvals.length}`,
    ].map(renderSummaryPill).join('');
}

function renderBadge(status) {
    const normalized = String(status || 'idle').toLowerCase();
    const labelMap = {
        active: '活跃',
        archived: '已归档',
        expired: '已过期',
        running: '运行中',
        paused: '已暂停',
        queued: '排队中',
        success: '已完成',
        completed: '已完成',
        failed: '失败',
        timeout: '超时',
        cancelled: '已取消',
        pending: '待审批',
        approval_required: '需审批',
        enabled: '已启用',
        disabled: '已停用',
        draft: '待审核',
        approved: '已批准',
        rejected: '已拒绝',
        custom: '自定义',
        builtin: '内置',
        idle: '空闲',
    };
    return `<span class="badge badge-${escapeHTML(normalized)}">${escapeHTML(labelMap[normalized] || normalized)}</span>`;
}

function renderSummaryPill(text) {
    return `<span class="summary-pill">${escapeHTML(text)}</span>`;
}

function escapeHTML(value) {
    if (value == null) {
        return '';
    }
    const div = document.createElement('div');
    div.textContent = String(value);
    return div.innerHTML;
}

function shortId(value) {
    if (!value) {
        return '-';
    }
    return value.length > 10 ? `${value.slice(0, 8)}..` : value;
}

function formatTime(value) {
    if (!value) {
        return '-';
    }
    try {
        return new Date(value).toLocaleString('zh-CN', { hour12: false });
    } catch {
        return value;
    }
}

async function bootstrap() {
    await checkHealth();
    await Promise.all([loadEntry(), loadStatusOverview(false), loadTaskSkills()]);
    window.setInterval(checkHealth, 30000);
}

bootstrap();