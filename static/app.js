'use strict';

let currentStatus = {};
let allProjects = [];
let activeProject = {};
let allProviders = [];
let connected = false;
let mutationBusy = false;
let stopping = false;
let eventSource = null;
let statusPending = false;
let statusVersion = 0;
let projectVersion = 0;
let pollTimer = null;
const requestVersions = new Map();
const $ = id => document.getElementById(id);
const text = (id, value) => { $(id).textContent = String(value ?? ''); };
const number = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;

function element(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = String(value ?? '');
    return node;
}

async function api(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 60000);
    try {
        const response = await fetch(path, {cache: 'no-store', credentials: 'same-origin', ...options, signal: controller.signal});
        let data;
        try { data = await response.json(); }
        catch (_) { throw new Error(`پاسخ نامعتبر سرور (${response.status})`); }
        if (!response.ok || (data && data.error && data.ready !== false)) {
            throw new Error(data?.error || `خطای سرور (${response.status})`);
        }
        return data;
    } catch (error) {
        if (error.name === 'AbortError') throw new Error('مهلت پاسخ سرور تمام شد؛ پیش از تکرار عملیات، وضعیت را بررسی کنید.');
        throw error;
    } finally { clearTimeout(timer); }
}
const post = (path, body) => api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});

function reportError(error) {
    const message = error instanceof Error ? error.message : String(error);
    text('errorBanner', message);
    $('errorBanner').hidden = false;
    appendLog('error', message, new Date().toLocaleTimeString('fa-IR'));
}

function updateControls() {
    const busy = Boolean(currentStatus.is_running || stopping);
    $('btnStart').disabled = !connected || currentStatus.ready === false || busy || mutationBusy;
    $('btnStop').disabled = !connected || !currentStatus.is_running || stopping || mutationBusy;
    document.querySelectorAll('[data-idle-only]').forEach(node => { node.disabled = busy || mutationBusy; });
    $('projectSelect').disabled = busy || mutationBusy;
}

async function action(task) {
    if (mutationBusy) return;
    mutationBusy = true;
    statusVersion++;
    updateControls();
    try {
        $('errorBanner').hidden = true;
        await task();
    } catch (error) { reportError(error); }
    finally {
        mutationBusy = false;
        statusVersion++;
        updateControls();
        await fetchStatus();
    }
}

function switchTab(tabId) {
    const ids = ['dashboard', 'projects', 'providers', 'chunks', 'outputs'];
    if (!ids.includes(tabId)) return;
    document.querySelectorAll('.tab-content').forEach(node => node.classList.toggle('active', node.id === `tab-${tabId}`));
    document.querySelectorAll('.nav-item').forEach((node, i) => {
        node.classList.toggle('active', ids[i] === tabId);
        node.setAttribute('aria-current', ids[i] === tabId ? 'page' : 'false');
    });
    const loaders = {projects: () => { loadModelsList(); return loadProjects(); }, providers: loadProviders, chunks: loadChunksList, outputs: loadOutputsList};
    if (loaders[tabId]) loaders[tabId]().catch(reportError);
}

async function fetchStatus() {
    if (statusPending || mutationBusy) return;
    statusPending = true;
    const version = statusVersion;
    try {
        const data = await api('/api/status');
        if (version !== statusVersion) return;
        connected = true;
        updateDashboardUI(data);
    } catch (error) {
        if (version !== statusVersion) return;
        connected = false;
        text('headerStatusText', 'وضعیت نامشخص');
        $('headerStatusDot').className = 'pulse-dot';
        reportError(error);
        updateControls();
    } finally { statusPending = false; }
}

function updateDashboardUI(data) {
    if (!data || typeof data !== 'object' || Array.isArray(data)) return;
    const fullSnapshot = Object.prototype.hasOwnProperty.call(data, 'ready');
    currentStatus = {...currentStatus, ...data, stats: fullSnapshot ? (data.stats || {}) : {...currentStatus.stats, ...data.stats}};
    if (currentStatus.is_running === false) stopping = false;
    if (data.ready === false && data.error) { text('errorBanner', data.error); $('errorBanner').hidden = false; }
    else if (data.ready === true) { $('errorBanner').hidden = true; }
    const stats = currentStatus.stats || {};
    const total = number(stats.total), done = number(stats.done);
    text('headerStatusText', stopping || currentStatus.stop_requested ? 'در حال توقف' : currentStatus.is_running ? 'در حال کار' : currentStatus.ready === false ? 'نیازمند تنظیم منبع' : connected ? 'آماده' : 'وضعیت نامشخص');
    $('headerStatusDot').className = `pulse-dot${currentStatus.is_running ? ' running' : ''}`;
    text('topActiveProjectTitle', currentStatus.active_project?.title || 'موتور هوشمند Mix API');
    text('statDone', done);
    text('statWorking', number(stats.claimed));
    text('statPending', number(stats.pending));
    text('statFailed', number(stats.failed));
    const pct = total ? Math.min(100, Math.round(done * 100 / total)) : 0;
    text('txtPercent', `${pct}٪`);
    $('mainProgressBar').style.width = `${pct}%`;
    $('mainProgressBar').setAttribute('aria-valuenow', pct);
    text('txtProgressLabel', `قطعات: ${done} از ${total}`);
    text('txtCostToken', `${(number(stats.input_tokens) + number(stats.output_tokens)).toLocaleString('fa-IR')} توکن | $${number(stats.total_cost_usd).toFixed(4)}`);
    const streaming = Boolean(currentStatus.is_running && currentStatus.streaming_text);
    $('streamCard').style.display = streaming ? 'block' : 'none';
    text('streamTextContent', streaming ? currentStatus.streaming_text : '');
    text('streamChunkBadge', `قطعه ${number(currentStatus.current_idx) + 1}`);
    updateControls();
}

function startGeneration() { return action(async () => { await post('/api/start'); }); }
function stopGeneration() { return action(async () => { const result = await post('/api/stop'); stopping = result.status === 'stopping'; }); }
function retryFailedChunks() { return action(async () => { await post('/api/retry_failed'); }); }
function renderOutputs() {
    return action(async () => {
        const result = await post('/api/render');
        if (!result.paths || !Object.keys(result.paths).length) throw new Error('هیچ قطعهٔ تکمیل‌شده‌ای برای ساخت خروجی وجود ندارد.');
        appendLog('success', 'فایل‌های خروجی ساخته شدند.', new Date().toLocaleTimeString('fa-IR'));
        switchTab('outputs');
    });
}

async function latest(path) {
    const version = (requestVersions.get(path) || 0) + 1;
    requestVersions.set(path, version);
    const project = projectVersion;
    const data = await api(path);
    return requestVersions.get(path) === version && project === projectVersion ? data : null;
}

let availableModels = [];

async function loadModelsList(force = false) {
    try {
        const url = force ? '/api/models?force=1' : '/api/models';
        const data = await api(url);
        if (data && Array.isArray(data.models)) {
            availableModels = data.models;
            renderModelOptions();
            if (force) {
                appendLog('info', `✅ لیست مدل‌ها بر اساس کلیدها به‌روزرسانی شد (${availableModels.length} مدل).`, new Date().toLocaleTimeString('fa-IR'));
            }
        }
    } catch (e) {
        console.warn('Could not load models list:', e);
    }
}

function renderModelOptions() {
    const select = $('projModel');
    if (!select) return;
    const currentVal = select.value || (activeProject && activeProject.model) || 'Claude Sonnet 5.5';
    select.replaceChildren();
    
    availableModels.forEach(m => {
        const opt = element('option', '', m.name || m.id);
        opt.value = m.id;
        select.appendChild(opt);
    });

    if (currentVal && ![...select.options].some(o => o.value === currentVal)) {
        const opt = element('option', '', currentVal);
        opt.value = currentVal;
        select.appendChild(opt);
    }
    select.value = currentVal;
}

async function loadProjects() {
    const data = await latest('/api/projects');
    if (!data) return;
    allProjects = data.projects || [];
    const select = $('projectSelect');
    select.replaceChildren();
    allProjects.forEach(project => {
        const option = element('option', '', project.title);
        option.value = project.id;
        select.appendChild(option);
    });
    activeProject = allProjects.find(project => project.id === data.active_id) || allProjects[0] || {};
    select.value = activeProject.id || '';
    fillProjectForm(activeProject);
}
function fillProjectForm(project) {
    for (const [id, value] of Object.entries({projTitle: project.title, projDocTitle: project.doc_title, projInput: project.input,
        projChunkSize: project.chunk_size ?? 1800, projDelay: project.delay ?? 2,
        projMainPrompt: project.main_prompt, projContextPrompt: project.context_prompt})) $(id).value = value ?? '';
    const model = project.model || 'Claude Sonnet 5.5';
    if (![...$('projModel').options].some(option => option.value === model)) {
        const option = element('option', '', model); option.value = model; $('projModel').appendChild(option);
    }
    $('projModel').value = model;
    $('projEffort').value = project.effort || 'medium';
}
function switchProject(id) {
    return action(async () => {
        try {
            await post('/api/projects/switch', {id});
            projectVersion++;
            currentStatus = {};
            $('streamCard').style.display = 'none';
            await loadProjects();
        } catch (error) {
            $('projectSelect').value = activeProject.id || '';
            throw error;
        }
    });
}
function openNewProjectModal() {
    if (currentStatus.is_running || mutationBusy) return;
    // A draft is not persisted until Save; no hidden server side effects.
    const id = globalThis.crypto?.randomUUID ? crypto.randomUUID() : `project-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    activeProject = {id, title: 'پروژه جدید', output: `outputs/${id}`};
    fillProjectForm(activeProject);
    $('projTitle').focus();
}
function saveCurrentProject() {
    return action(async () => {
        if (!activeProject.id) throw new Error('ابتدا پروژه جدید بسازید.');
        const chunkSize = Number($('projChunkSize').value), delay = Number($('projDelay').value);
        if (!$('projTitle').value.trim() || !Number.isInteger(chunkSize) || chunkSize < 1 || !Number.isFinite(delay) || delay < 0) {
            throw new Error('نام پروژه، اندازهٔ مثبت قطعه و تاخیر نامنفی را بررسی کنید.');
        }
        const project = {...activeProject, title: $('projTitle').value.trim(), doc_title: $('projDocTitle').value,
            input: $('projInput').value, chunk_size: chunkSize, delay, model: $('projModel').value,
            effort: $('projEffort').value, main_prompt: $('projMainPrompt').value, context_prompt: $('projContextPrompt').value};
        const isNew = !allProjects.some(item => item.id === project.id);
        await post('/api/projects/save', {project});
        if (isNew) await post('/api/projects/switch', {id: project.id});
        projectVersion++;
        currentStatus = {};
        await loadProjects();
        appendLog('success', 'تنظیمات پروژه ذخیره شد.', new Date().toLocaleTimeString('fa-IR'));
    });
}
function readFile(file, binary) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(binary ? String(reader.result).split(',')[1] : reader.result);
        reader.onerror = () => reject(new Error('خواندن فایل ناموفق بود.'));
        reader.onabort = () => reject(new Error('خواندن فایل لغو شد.'));
        if (binary) reader.readAsDataURL(file); else reader.readAsText(file, 'UTF-8');
    });
}
function handleFileUpload(event) {
    const file = event.target.files[0];
    if (!file) return;
    return action(async () => {
        if (!activeProject.id || !allProjects.some(project => project.id === activeProject.id)) throw new Error('پیش از بارگذاری، پروژه جدید را ذخیره کنید.');
        if (!/\.(docx|txt|md)$/i.test(file.name)) throw new Error('فقط فایل DOCX، TXT و MD پذیرفته می‌شود.');
        if (file.size > 16 * 1024 * 1024) throw new Error('حداکثر اندازهٔ فایل ۱۶ مگابایت است.');
        const binary = /\.docx$/i.test(file.name);
        const content = await readFile(file, binary);
        const result = await post('/api/upload', {filename: file.name, content, encoding: binary ? 'base64' : 'utf-8'});
        if (typeof result.path !== 'string') throw new Error('مسیر فایل در پاسخ سرور وجود ندارد.');
        $('projInput').value = result.path;
        activeProject.input = result.path;
        projectVersion++;
        await loadProjects();
        event.target.value = '';
        appendLog('success', 'فایل بارگذاری شد.', new Date().toLocaleTimeString('fa-IR'));
    });
}

async function loadProviders() {
    loadSettingsForm().catch(() => {});
    const data = await latest('/api/providers');
    if (!data) return;
    allProviders = data.providers || [];
    const runtime = new Map((data.pool_snapshot || []).map(item => [`${item.provider}\u0000${item.name}\u0000${item.masked_key}`, item]));
    allProviders = allProviders.map(provider => ({...provider, keys: (provider.keys || []).map(key => {
        const snapshot = runtime.get(`${provider.name}\u0000${key.name}\u0000${key.key_masked || ''}`);
        return snapshot ? {...key, status: snapshot.state, request_count: snapshot.request_count, last_error: snapshot.last_error} : key;
    })}));
    const container = $('providersListContainer');
    container.replaceChildren();
    $('bulkProviderSelect').replaceChildren();
    allProviders.forEach((provider, index) => {
        const option = element('option', '', provider.name); option.value = provider.name; $('bulkProviderSelect').appendChild(option);
        const card = element('div', 'glass-card');
        const header = element('div', 'card-header');
        const info = element('div');
        info.append(element('div', 'card-title', provider.name), element('div', 'list-item-sub', provider.base_url));
        const label = element('label', 'switch');
        const checkbox = element('input'); checkbox.type = 'checkbox'; checkbox.checked = Boolean(provider.enabled);
        checkbox.setAttribute('aria-label', `فعال‌سازی ${provider.name}`);
        checkbox.setAttribute('data-idle-only', '');
        checkbox.onchange = () => toggleProvider(index);
        label.append(checkbox, element('span', 'slider'));
        header.append(info, label); card.append(header);
        (provider.keys || []).forEach(key => {
            const row = element('div', 'list-item');
            const state = ['active', 'cooldown', 'dead'].includes(key.status) ? key.status : 'dead';
            row.append(element('span', '', `${key.name || ''} (${key.key_masked || '••••'})`), element('span', `badge badge-${state}`, key.status));
            card.append(row);
        });
        container.append(card);
    });
    const newProvider = element('option', '', 'ارائه‌دهندهٔ جدید…');
    newProvider.value = '';
    $('bulkProviderSelect').appendChild(newProvider);
    if (!allProviders.length) container.append(element('p', '', 'ارائه‌دهنده‌ای تنظیم نشده است.'));
    updateControls();
}
function toggleProvider(index) {
    return action(async () => {
        const providers = allProviders.map((provider, i) => i === index ? {...provider, enabled: !provider.enabled} : provider);
        try { await post('/api/providers/save', {providers}); }
        finally { await loadProviders(); }
    });
}
function openBulkKeysModal() { loadProviders().then(() => $('bulkKeysModal').classList.add('active')).catch(reportError); }
function closeBulkKeysModal() { $('bulkKeysModal').classList.remove('active'); $('bulkKeysInput').value = ''; }
function submitBulkKeys() {
    return action(async () => {
        const selected = $('bulkProviderSelect').value;
        const name = selected || $('bulkProviderName').value.trim();
        const base_url = $('bulkProviderUrl').value.trim();
        const keys = $('bulkKeysInput').value;
        if (!name || !keys.trim()) throw new Error('نام ارائه‌دهنده و کلید را وارد کنید.');
        if (!selected && (!base_url || !/^https?:\/\//i.test(base_url))) throw new Error('نشانی API ارائه‌دهندهٔ جدید معتبر نیست.');
        await post('/api/keys/bulk', {provider: name, keys, ...(selected ? {} : {base_url})});
        closeBulkKeysModal();
        $('bulkProviderName').value = '';
        $('bulkProviderUrl').value = '';
        await loadProviders();
    });
}
async function loadChunksList() {
    const chunks = await latest('/api/chunks');
    if (!chunks) return;
    const container = $('chunksListContainer'); container.replaceChildren();
    if (!chunks.length) container.append(element('p', '', 'هنوز قطعه‌ای بارگذاری نشده است.'));
    chunks.forEach(chunk => {
        const item = element('button', 'list-item chunk-item'); item.type = 'button';
        item.onclick = () => showChunkModal(chunk.idx).catch(reportError);
        const info = element('div');
        info.append(element('div', 'list-item-title', `قطعه ${number(chunk.idx) + 1} ${chunk.heading || ''}`),
            element('div', 'list-item-sub', chunk.arabic_preview || chunk.arabic_text?.slice(0, 120) || ''));
        const badge = chunk.state === 'done' ? 'active' : chunk.state === 'failed' ? 'dead' : 'cooldown';
        item.append(info, element('span', `badge badge-${badge}`, chunk.state)); container.append(item);
    });
}
async function showChunkModal(idx) {
    if (!Number.isInteger(idx) || idx < 0) return;
    const chunk = await latest(`/api/chunks/${idx}`);
    if (!chunk) return;
    text('chunkModalTitle', `جزئیات قطعه ${number(chunk.idx) + 1}`);
    text('chunkModalArabic', chunk.arabic_text);
    text('chunkModalPersian', chunk.persian_text || (chunk.fail_reason ? `خطا: ${chunk.fail_reason}` : 'هنوز تولید نشده است.'));
    $('chunkModal').classList.add('active');
}
function closeChunkModal() { $('chunkModal').classList.remove('active'); }
async function loadOutputsList() {
    const files = await latest('/api/outputs');
    if (!files) return;
    const container = $('outputsListContainer'); container.replaceChildren();
    if (!files.length) container.append(element('p', '', 'هنوز خروجی ساخته نشده است.'));
    files.forEach(file => {
        const item = element('div', 'list-item'); const info = element('div');
        info.append(element('div', 'list-item-title', file.name), element('div', 'list-item-sub', `${(number(file.size) / 1024).toFixed(1)} کیلوبایت | ${file.modified || ''}`));
        const link = element('a', 'btn btn-primary', 'دانلود');
        link.href = `/api/download/${encodeURIComponent(file.name)}`; link.download = String(file.name);
        item.append(info, link); container.append(item);
    });
}
function clearLogs() { $('terminalLogs').replaceChildren(); }
function appendLog(level, message, time) {
    const box = $('terminalLogs');
    const safeLevel = ['info', 'success', 'warning', 'error'].includes(level) ? level : 'info';
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.append(element('div', `log-entry ${safeLevel}`, `[${time || ''}] ${message || ''}`));
    while (box.children.length > 200) box.firstElementChild.remove();
    if (atBottom) box.scrollTop = box.scrollHeight;
}
function setupSSE() {
    if (eventSource || !('EventSource' in window)) return;
    eventSource = new EventSource('/api/events');
    const listen = (name, callback) => eventSource.addEventListener(name, event => {
        try {
            const data = JSON.parse(event.data);
            if (!data || typeof data !== 'object' || Array.isArray(data)) return;
            const selectedProject = currentStatus.active_project?.id || activeProject.id;
            if (data.project_id && selectedProject && data.project_id !== selectedProject) return;
            callback(data);
        } catch (_) { appendLog('warning', 'رویداد نامعتبر سرور نادیده گرفته شد.', ''); }
    });
    listen('log', data => appendLog(data.level, data.message, data.time));
    listen('status', data => { statusVersion++; connected = true; updateDashboardUI(data); });
    listen('stream', data => {
        if (!currentStatus.is_running || !Number.isInteger(data.idx) || typeof data.full !== 'string') return;
        statusVersion++;
        updateDashboardUI({current_idx: data.idx, streaming_text: data.full});
    });
    eventSource.onopen = () => { fetchStatus(); };
    // EventSource reconnects itself. Creating another connection here leaks streams.
    eventSource.onerror = () => { text('headerStatusText', 'اتصال زنده قطع شد؛ بررسی وضعیت…'); fetchStatus(); };
}
function startMonitoring() {
    setupSSE(); fetchStatus();
    if (!pollTimer) pollTimer = setInterval(() => { if (!document.hidden) fetchStatus(); }, 4000);
}
window.addEventListener('DOMContentLoaded', () => {
    updateControls();
    loadModelsList();
    startMonitoring();
    document.querySelectorAll('label.form-label').forEach(label => {
        const control = label.parentElement.querySelector('input, select, textarea');
        if (control?.id) label.htmlFor = control.id;
    });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') { closeChunkModal(); closeBulkKeysModal(); } });
    if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {});
});
window.addEventListener('pagehide', () => {
    if (eventSource) eventSource.close();
    eventSource = null;
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
});
window.addEventListener('pageshow', startMonitoring);
document.addEventListener('visibilitychange', () => { if (!document.hidden) fetchStatus(); });

async function loadSettingsForm() {
    try {
        const s = await api('/api/settings');
        if (!s) return;
        if ($('cfgSpaceUrl')) $('cfgSpaceUrl').value = s.space_url || '';
        if ($('cfgBaleToken')) $('cfgBaleToken').value = s.bale_bot_token || '';
        if ($('cfgBaleChatId')) $('cfgBaleChatId').value = s.bale_chat_id || '';
    } catch (e) {}
}

async function saveSettingsForm() {
    return action(async () => {
        const space_url = ($('cfgSpaceUrl')?.value || '').trim();
        const bale_bot_token = ($('cfgBaleToken')?.value || '').trim();
        const bale_chat_id = ($('cfgBaleChatId')?.value || '').trim();
        await post('/api/settings', {
            space_url,
            bale_bot_token,
            bale_chat_id,
            enable_keepalive: true,
            enable_bale_notifications: true
        });
        alert('تنظیمات ربات بله و سرور با موفقیت ذخیره شد.');
    });
}

async function testBalePing() {
    const stEl = $('baleTestStatus');
    if (stEl) stEl.innerText = 'در حال ارسال پیام تست...';
    try {
        await saveSettingsForm();
        await post('/api/settings/test_bale', {});
        if (stEl) stEl.innerText = '✅ پیام ارسال شد! بله خود را بررسی کنید.';
    } catch (e) {
        if (stEl) stEl.innerText = '❌ ' + e.message;
    }
}
