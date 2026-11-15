let currentTaskId = null;
let expandedNode = null;

function switchTab(tab) {
    document.getElementById('tasksView').style.display = tab === 'tasks' ? 'grid' : 'none';
    document.getElementById('memoryView').style.display = tab === 'memory' ? 'grid' : 'none';
    document.getElementById('tabTasksBtn').classList.toggle('active', tab === 'tasks');
    document.getElementById('tabMemoryBtn').classList.toggle('active', tab === 'memory');
    if (tab === 'memory') loadMemory();
}

async function fetchModels() {
    const data = await (await fetch('/api/models')).json();
    const sel = document.getElementById('modelSelect');
    const autoOpt = `<option value="AUTO" selected>Авто (Роутинг по ролям: R1 + DeepSeek-Coder + Qwen)</option>`;
    const modelsOpts = data.models.map(m => `<option value="${m}">Модель: ${m}</option>`).join('');
    sel.innerHTML = autoOpt + modelsOpts;
}

async function loadMemory() {
    const data = await (await fetch('/api/memory')).json();
    document.getElementById('skillsList').innerHTML = data.skills.length ? data.skills.map(s => `
    <div class="mem-item">
    <div style="display:flex; justify-content:space-between; align-items:center;">
    <strong>[${s.skill_id}] ${s.summary}</strong>
    <button class="btn-stop btn-sm" onclick="delMem('skills','${s.skill_id}')">Удалить</button>
    </div>
    <div style="font-size:12px; color:#94a3b8; margin:6px 0;">Теги: ${(s.tags || []).join(', ')}</div>
    <pre style="max-height:150px;">${s.recipe}</pre>
    </div>`).join('') : '<p style="color:#94a3b8;">Пока нет сохраненных навыков.</p>';

    document.getElementById('antiList').innerHTML = data.anti_patterns.length ? data.anti_patterns.map(a => `
    <div class="mem-item">
    <div style="display:flex; justify-content:space-between; align-items:center;">
    <strong>${a.id}</strong>
    <button class="btn-stop btn-sm" onclick="delMem('anti_patterns','${a.id}')">Удалить</button>
    </div>
    <pre style="max-height:150px; margin-top:8px;">${a.content}</pre>
    </div>`).join('') : '<p style="color:#94a3b8;">Ошибок не зафиксировано.</p>';
}

async function delMem(type, id) { await fetch(`/api/memory/${type}/${id}`, { method: 'DELETE' }); loadMemory(); }

function formatLinks(text) {
    let s = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    s = s.replace(/(^|[^"'>])(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
    return s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
}

function resetMainView() {
    currentTaskId = null;
    document.getElementById('currentTitle').innerText = 'Выберите или создайте задачу';
    document.getElementById('currentModelTag').innerText = '';
    document.getElementById('globalStatus').innerText = 'IDLE';
    document.getElementById('stopBtn').style.display = 'none';
    document.getElementById('finalResultBox').innerHTML = '';
    document.getElementById('nodesContainer').innerHTML = '';
    document.getElementById('logsContainer').innerText = 'Ожидание...';
}

async function clearOldTasks() {
    await fetch('/api/tasks', { method: 'DELETE' });
    resetMainView();
    fetchTasks();
}

async function deleteSingleTask(e, id) {
    e.stopPropagation();
    await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
    if (currentTaskId === id) resetMainView();
    fetchTasks();
}

async function stopCurrentTask() { if (currentTaskId) { await fetch(`/api/tasks/${currentTaskId}/stop`, { method: 'POST' }); updateTaskDetails(); } }

async function fetchTasks() {
    const data = await (await fetch('/api/tasks')).json();
    document.getElementById('taskList').innerHTML = data.tasks.map(t => {
        const canDel = !['IN_PROGRESS', 'STOPPING...'].includes(t.status);
        return `
        <div class="task-item ${t.id === currentTaskId ? 'active' : ''}" onclick="selectTask('${t.id}')">
        <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px; color:#94a3b8;">
        <span>${t.id}</span>
        <div style="display:flex; align-items:center; gap:6px;">
        <span class="badge">${t.status}</span>
        ${canDel ? `<button class="btn-stop btn-sm" style="padding:1px 6px;" title="Удалить задачу" onclick="deleteSingleTask(event,'${t.id}')">×</button>` : ''}
        </div>
        </div>
        <div class="task-goal">${t.goal}</div>
        </div>`;
    }).join('');
    if (!currentTaskId && data.tasks.length > 0) selectTask(data.tasks.id);
}

async function createTask() {
    const goal = document.getElementById('goalInput').value.trim();
    const model = document.getElementById('modelSelect').value;
    const filesInput = document.getElementById('fileInput');
    if (!goal) return;

    const formData = new FormData();
    formData.append('goal', goal);
    formData.append('model', model);
    for (const file of filesInput.files) {
        formData.append('files', file);
    }

    document.getElementById('goalInput').value = '';
    filesInput.value = '';
    const data = await (await fetch('/api/tasks', { method: 'POST', body: formData })).json();
    await fetchTasks();
    selectTask(data.task_id);
}

function selectTask(id) { currentTaskId = id; expandedNode = null; fetchTasks(); updateTaskDetails(); }
function toggleNode(id) { expandedNode = (expandedNode === id) ? null : id; updateTaskDetails(); }

async function updateTaskDetails() {
    if (!currentTaskId) return;
    const data = await (await fetch(`/api/tasks/${currentTaskId}`)).json();
    if (data.error) return;
    const tree = data.tree;
    document.getElementById('currentTitle').innerText = tree.goal || currentTaskId;
    const filesTag = tree.uploads?.length ? ` | Файлы: ${tree.uploads.join(', ')}` : '';
    document.getElementById('currentModelTag').innerText = (tree.model ? `Режим: ${tree.model}` : '') + filesTag;
    document.getElementById('globalStatus').innerText = tree.status;

    const stopBtn = document.getElementById('stopBtn');
    stopBtn.style.display = ['IN_PROGRESS', 'STOPPING...'].includes(tree.status) ? 'inline-block' : 'none';
    stopBtn.innerText = tree.status === 'STOPPING...' ? 'Остановка...' : 'СТОП';

    document.getElementById('finalResultBox').innerHTML = data.final_report ? `
    <div class="result-card"><h3 style="color:#6ee7b7; margin-bottom:10px;">Итоговый результат</h3><div class="result-body">${formatLinks(data.final_report)}</div></div>` : '';

    document.getElementById('nodesContainer').innerHTML = Object.entries(tree.nodes || {}).map(([id, n]) => {
        const det = (data.node_details || {})[id] || {};
        return `<div class="node ${n.status}" onclick="toggleNode('${id}')">
        <div style="display:flex; justify-content:space-between;"><strong>[${id}] ${n.title}</strong><span class="badge">${n.status} (Попыток: ${n.attempts || 0})</span></div>
        <div style="font-size:12px; color:#94a3b8; margin-top:6px;">Зависимости: ${n.dependencies?.length ? n.dependencies.join(', ') : 'нет'}</div>
        ${expandedNode === id ? `<div class="details-box" onclick="event.stopPropagation()"><div><h4>Результат Worker</h4><pre>${det.result || 'Нет данных'}</pre></div><div><h4>Вердикт Critic</h4><pre>${det.critique || 'Нет данных'}</pre></div></div>` : ''}
        </div>`;
    }).join('');

    const logsEl = document.getElementById('logsContainer');
    logsEl.innerText = data.logs || 'Лог пуст...';
    logsEl.scrollTop = logsEl.scrollHeight;
}

setInterval(() => { if (currentTaskId) { updateTaskDetails(); fetchTasks(); } }, 2500);
fetchModels();
fetchTasks();
