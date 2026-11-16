'use strict';
// No browser package or network required: execute the real UI in a minimal DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Node {
    constructor(tag = 'div') {
        this.tagName = tag; this.children = []; this.textContent = ''; this.value = '';
        this.style = {}; this.attributes = {}; this.disabled = false; this.hidden = false;
        this.scrollHeight = 100; this.scrollTop = 100; this.clientHeight = 20;
        this.classList = {add() {}, remove() {}, toggle() {}};
    }
    append(...nodes) { nodes.forEach(node => { node.parentElement = this; this.children.push(node); }); }
    appendChild(node) { this.append(node); }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    setAttribute(key, value) { this.attributes[key] = value; }
    get firstElementChild() { return this.children[0]; }
    get options() { return this.children; }
    remove() { this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); }
    focus() {}
    set innerHTML(_) { throw new Error('Unsafe innerHTML sink used'); }
}
const nodes = new Map();
const getNode = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
const events = [];
class EventSource {
    constructor(url) { this.url = url; this.listeners = {}; events.push(this); }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    close() { this.closed = true; }
}
let response = {ok: true, status: 200, json: async () => ({is_running: false, stats: {}})};
let requests = [];
const context = vm.createContext({
    console, AbortController, EventSource, URL,
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
    document: {getElementById: getNode, createElement: tag => new Node(tag), querySelectorAll: () => [], addEventListener() {}, hidden: false},
    window: {addEventListener() {}, EventSource, isSecureContext: true},
    navigator: {}, fetch: async (url, opts) => { requests.push({url, opts}); return response; },
});
const evaluate = code => vm.runInContext(code, context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), context);
async function run() {
    evaluate('setupSSE(); setupSSE();');
    assert.equal(events.length, 1, 'exactly one SSE connection');
    events[0].onerror(); events[0].onerror();
    assert.equal(events.length, 1, 'native reconnect does not spawn connections');
    await new Promise(resolve => setImmediate(resolve));
    events[0].listeners.status({data: '{bad'});
    evaluate('updateDashboardUI({is_running:true,current_idx:0,streaming_text:"old",stats:{total:2,done:1,total_cost_usd:"0.12"}})');
    assert.equal(getNode('streamCard').style.display, 'block');
    evaluate('updateDashboardUI({streaming_text:"",current_idx:1})');
    assert.equal(getNode('streamCard').style.display, 'none', 'clear stale text at chunk boundary');
    assert.equal(evaluate('currentStatus.stats.done'), 1, 'partial SSE retains statistics');
    assert.equal(getNode('txtCostToken').textContent.includes('0.1200'), true);
    evaluate('connected = true; updateDashboardUI({ready:false,error:"source missing",is_running:false});');
    assert.equal(getNode('btnStart').disabled, true);
    assert.equal(getNode('errorBanner').textContent, 'source missing');
    response = {ok: false, status: 409, json: async () => ({error: 'busy'})};
    await assert.rejects(evaluate('api("/api/start")'), /busy/);
    response = {ok: true, status: 200, json: async () => ({error: 'semantic failure'})};
    await assert.rejects(evaluate('api("/api/start")'), /semantic failure/);
    response = {ok: true, status: 200, json: async () => ({ready: false, error: 'missing', is_running: false})};
    assert.equal((await evaluate('api("/api/status")')).ready, false);

    const payload = '<img src=x onerror=alert(1)>';
    response = {ok: true, status: 200, json: async () => ({providers: [{name: payload, base_url: payload, enabled: true, keys: [{name: payload, key_masked: '••••masked', key_ref: 'ref', status: 'active'}]}], pool_snapshot: [{provider: payload, name: payload, masked_key: '••••masked', state: 'cooldown', request_count: 4, last_error: 'limit'}]})};
    await evaluate('loadProviders()');
    assert.equal(getNode('providersListContainer').children[0].children[0].children[0].children[0].textContent, payload);
    assert.ok(getNode('providersListContainer').children[0].children[1].children[0].textContent.includes('masked'));
    assert.equal(getNode('providersListContainer').children[0].children[1].children[1].textContent, 'cooldown');
    assert.equal(getNode('bulkProviderSelect').children.length, 2, 'new provider option remains available');
    response = {ok: true, status: 200, json: async () => ({ready: true, is_running: false, stats: {total: 2, done: 1}})};
    evaluate('updateDashboardUI({ready:false,is_running:false,error:"missing",stats:{}})');
    assert.equal(getNode('statDone').textContent, '0', 'invalid source clears old project stats');
    assert.match(getNode('headerStatusText').textContent, /منبع/);
    evaluate('updateDashboardUI({ready:true,is_running:false,stats:{total:2,done:1}})');
    assert.equal(getNode('errorBanner').hidden, true, 'recovery clears status error');
    assert.equal(getNode('statDone').textContent, '1');
    response = {ok: true, status: 200, json: async () => [{idx: 0, state: payload, heading: payload, arabic_preview: payload}]};
    await evaluate('loadChunksList()');
    assert.ok(getNode('chunksListContainer').children[0].children[0].children[0].textContent.includes(payload));
    response = {ok: true, status: 200, json: async () => [{name: payload + '.html', size: 100, modified: payload}]};
    await evaluate('loadOutputsList()');
    assert.equal(getNode('outputsListContainer').children[0].children[0].children[0].textContent, payload + '.html');
    assert.equal(getNode('outputsListContainer').children[0].children[1].href, '/api/download/' + encodeURIComponent(payload + '.html'));
    evaluate('for (let i=0;i<500;i++) appendLog("error", "line", "now")');
    assert.equal(getNode('terminalLogs').children.length, 200);
    evaluate('fillProjectForm({delay:0,model:"custom-model"})');
    assert.equal(getNode('projDelay').value, 0, 'zero delay is preserved');
    assert.equal(getNode('projModel').value, 'custom-model');
    evaluate('openNewProjectModal()');
    assert.ok(evaluate('activeProject.id'));

    let pendingResolve;
    context.fetch = () => new Promise(resolve => { pendingResolve = resolve; });
    evaluate('statusPending = false');
    const pending = evaluate('fetchStatus()');
    events[0].listeners.status({data: JSON.stringify({is_running: true, streaming_text: 'newer', ready: true})});
    pendingResolve({ok: true, status: 200, json: async () => ({is_running: false, streaming_text: 'stale'})});
    await pending;
    assert.equal(evaluate('currentStatus.streaming_text'), 'newer', 'stale poll cannot overwrite newer SSE');
    // Service worker must never intercept API, SSE, output, POST, or remote requests.
    const swEvents = {};
    const cached = {offline: true};
    const sw = vm.createContext({URL, Response, self: {location: {origin: 'http://localhost:8080'}, addEventListener: (name, fn) => { swEvents[name] = fn; }},
        fetch: async () => { throw new Error('offline'); }, caches: {match: async () => cached}});
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/sw.js'), 'utf8'), sw);
    for (const [url, method] of [['/api/events', 'GET'], ['/api/status', 'GET'], ['/api/download/book.html', 'GET'], ['/', 'POST'], ['https://remote.test/app.js', 'GET'], ['/app.js?secret=x', 'GET']]) {
        let intercepted = false;
        swEvents.fetch({request: {url: new URL(url, 'http://localhost:8080').href, method}, respondWith() { intercepted = true; }});
        assert.equal(intercepted, false, `must bypass ${method} ${url}`);
    }
    let shell;
    swEvents.fetch({request: {url: 'http://localhost:8080/app.js', method: 'GET'}, respondWith(value) { shell = value; }});
    assert.equal(await shell, cached, 'offline public shell fallback');
    console.log('mobile UI regression checks passed');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
