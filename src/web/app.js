/* Self-Claw 前端逻辑 */

const API = '/api/v1';

// ── 导航切换 ──────────────────────────────────────────

document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', e => {
        e.preventDefault();
        const view = item.dataset.view;
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        item.classList.add('active');
        document.getElementById(`view-${view}`).classList.add('active');
        // 切换到对应视图时刷新数据
        refreshView(view);
    });
});

// ── 健康检查 ──────────────────────────────────────────

async function checkHealth() {
    const dot = document.getElementById('health-dot');
    const text = document.getElementById('health-text');
    try {
        const resp = await fetch(`${API}/health`);
        const data = await resp.json();
        if (data.status === 'ok') {
            dot.className = 'status-dot ok';
            text.textContent = '服务正常';
        } else {
            dot.className = 'status-dot err';
            text.textContent = '异常';
        }
    } catch {
        dot.className = 'status-dot err';
        text.textContent = '无法连接';
    }
}

// ── 数据加载 ──────────────────────────────────────────

async function fetchJSON(path) {
    const resp = await fetch(`${API}${path}`);
    if (!resp.ok) return null;
    return resp.json();
}

async function loadDashboard() {
    const [skills, tools, sessions] = await Promise.all([
        fetchJSON('/skills'),
        fetchJSON('/tools'),
        fetchJSON('/sessions'),
    ]);

    document.getElementById('stat-skills').textContent = skills ? skills.length : 0;
    document.getElementById('stat-tools').textContent = tools ? tools.length : 0;
    document.getElementById('stat-sessions').textContent = sessions ? sessions.filter(s => s.status === 'active').length : 0;
    document.getElementById('stat-tasks').textContent = '0'; // 任务 API 待接入

    // 最近会话
    const tbody = document.getElementById('recent-sessions');
    if (sessions && sessions.length > 0) {
        tbody.innerHTML = sessions.slice(0, 10).map(s => `
            <tr>
                <td><code>${shortId(s.id)}</code></td>
                <td>${esc(s.user_id || '-')}</td>
                <td>${esc(s.channel_type || 'web')}</td>
                <td><span class="badge badge-${s.status}">${s.status}</span></td>
                <td>${formatTime(s.last_active_at)}</td>
            </tr>
        `).join('');
    } else {
        tbody.innerHTML = '<tr><td colspan="5" class="empty">暂无数据</td></tr>';
    }
}

async function loadSkills() {
    const filter = document.getElementById('skill-filter').value;
    const params = filter ? `?status=${filter}` : '';
    const skills = await fetchJSON(`/skills${params}`);
    const tbody = document.getElementById('skills-list');
    if (skills && skills.length > 0) {
        tbody.innerHTML = skills.map(s => `
            <tr>
                <td><strong>${esc(s.name)}</strong></td>
                <td>${esc(s.version || 'v1')}</td>
                <td><span class="badge badge-${s.status}">${s.status}</span></td>
                <td>${formatTime(s.created_at)}</td>
                <td>
                    ${s.status === 'enabled'
                        ? `<button class="btn btn-sm btn-danger" onclick="toggleSkill('${s.skill_id || s.id}','disabled')">停用</button>`
                        : `<button class="btn btn-sm btn-success" onclick="toggleSkill('${s.skill_id || s.id}','enabled')">启用</button>`
                    }
                </td>
            </tr>
        `).join('');
    } else {
        tbody.innerHTML = '<tr><td colspan="5" class="empty">暂无 Skill</td></tr>';
    }
}

async function loadSessions() {
    const filter = document.getElementById('session-filter').value;
    const params = filter ? `?status=${filter}` : '';
    const sessions = await fetchJSON(`/sessions${params}`);
    const tbody = document.getElementById('sessions-list');
    if (sessions && sessions.length > 0) {
        tbody.innerHTML = sessions.map(s => `
            <tr>
                <td><code>${shortId(s.id)}</code></td>
                <td>${esc(s.user_id || '-')}</td>
                <td>${esc(s.channel_type || 'web')}</td>
                <td><span class="badge badge-${s.status}">${s.status}</span></td>
                <td>${formatTime(s.created_at)}</td>
                <td>${formatTime(s.last_active_at)}</td>
                <td>
                    ${s.status === 'active'
                        ? `<button class="btn btn-sm" onclick="closeSession('${s.id}')">关闭</button>`
                        : '-'
                    }
                </td>
            </tr>
        `).join('');
    } else {
        tbody.innerHTML = '<tr><td colspan="7" class="empty">暂无会话</td></tr>';
    }
}

async function loadTools() {
    const tools = await fetchJSON('/tools');
    const container = document.getElementById('tools-list');
    if (tools && tools.length > 0) {
        container.innerHTML = tools.map(t => `
            <div class="tool-card">
                <h4>${esc(t.display_name || t.name)}</h4>
                <div class="tool-name">${esc(t.name)}</div>
                <p>${esc(t.description || '无描述')}</p>
                <div class="tool-meta">
                    <span class="badge badge-${t.requires_approval ? 'expired' : 'enabled'}">
                        ${t.requires_approval ? '需审批' : '自动执行'}
                    </span>
                    <span class="badge badge-active">${esc(t.category || 'custom')}</span>
                </div>
            </div>
        `).join('');
    } else {
        container.innerHTML = '<div class="empty-card">暂无已注册工具</div>';
    }
}

// ── 操作 ─────────────────────────────────────────────

async function toggleSkill(skillId, newStatus) {
    await fetch(`${API}/skills/${skillId}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
    });
    loadSkills();
}

async function closeSession(sessionId) {
    await fetch(`${API}/sessions/${sessionId}/close`, { method: 'POST' });
    loadSessions();
}

// ── Agent 对话 ───────────────────────────────────────

let currentSessionId = null;

const chatInput = document.getElementById('chat-input');
const chatSend = document.getElementById('chat-send');
const chatMessages = document.getElementById('chat-messages');

chatSend.addEventListener('click', sendMessage);
chatInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text) return;

    appendMsg('user', text);
    chatInput.value = '';
    chatSend.disabled = true;

    // 显示加载
    const loadingEl = appendMsg('assistant', '<span class="loading"></span> 思考中...');

    try {
        const resp = await fetch(`${API}/agent/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: currentSessionId,
                message: text,
                stream: false,
            }),
        });
        const data = await resp.json();
        currentSessionId = data.session_id;

        // 构建回复内容
        let reply = data.reply || '(无回复)';
        let stepsHtml = '';
        if (data.steps && data.steps.length > 0) {
            const stepsContent = data.steps.map((s, i) => {
                let parts = [];
                if (s.thinking) parts.push(`💭 ${s.thinking}`);
                if (s.action) parts.push(`🔧 ${s.action}(${JSON.stringify(s.action_input || {})})`);
                if (s.observation) parts.push(`👁️ ${s.observation}`);
                return `步骤 ${s.step || i + 1}:\n${parts.join('\n')}`;
            }).join('\n\n');
            stepsHtml = `
                <div class="steps-toggle" onclick="this.nextElementSibling.classList.toggle('show')">
                    ▶ 查看推理过程（${data.steps.length} 步）
                </div>
                <div class="steps-detail">${esc(stepsContent)}</div>
            `;
        }

        loadingEl.innerHTML = esc(reply) + stepsHtml;
    } catch (err) {
        loadingEl.innerHTML = `⚠️ 请求失败: ${esc(err.message)}`;
    }

    chatSend.disabled = false;
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function appendMsg(role, html) {
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    div.innerHTML = html;
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return div;
}

// ── Skill 新建 ──────────────────────────────────────

document.getElementById('btn-new-skill').addEventListener('click', () => {
    document.getElementById('modal-skill').style.display = 'flex';
});

document.querySelectorAll('[data-close]').forEach(btn => {
    btn.addEventListener('click', () => {
        document.getElementById(btn.dataset.close).style.display = 'none';
    });
});

document.getElementById('form-skill').addEventListener('submit', async e => {
    e.preventDefault();
    const form = e.target;
    const body = {
        name: form.name.value,
        display_name: form.display_name.value,
        scenario: form.scenario.value,
        content: form.content.value,
    };
    await fetch(`${API}/skills`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    document.getElementById('modal-skill').style.display = 'none';
    form.reset();
    loadSkills();
});

// ── 筛选 ─────────────────────────────────────────────

document.getElementById('skill-filter').addEventListener('change', loadSkills);
document.getElementById('session-filter').addEventListener('change', loadSessions);

// ── 视图刷新路由 ─────────────────────────────────────

function refreshView(view) {
    switch (view) {
        case 'dashboard': loadDashboard(); break;
        case 'skills': loadSkills(); break;
        case 'sessions': loadSessions(); break;
        case 'tools': loadTools(); break;
    }
}

// ── 工具函数 ─────────────────────────────────────────

function esc(str) {
    if (str == null) return '';
    const d = document.createElement('div');
    d.textContent = String(str);
    return d.innerHTML;
}

function shortId(id) {
    if (!id) return '-';
    return id.length > 12 ? id.slice(0, 8) + '…' : id;
}

function formatTime(iso) {
    if (!iso) return '-';
    try {
        const d = new Date(iso);
        return d.toLocaleString('zh-CN', { hour12: false });
    } catch {
        return iso;
    }
}

// ── 初始化 ───────────────────────────────────────────

checkHealth();
loadDashboard();
setInterval(checkHealth, 30000);
