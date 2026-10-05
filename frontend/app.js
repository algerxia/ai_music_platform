const AUTH_KEY = 'studio-ai-token-v2';
const API_BASE = (window.STUDIO_CONFIG?.API_BASE || '').replace(/\/$/, '');
const CREDIT_COST = 10;
const PROMPT_MAX = 6000;

let token = localStorage.getItem(AUTH_KEY) || '';
let currentUser = null;
let projects = [];
let ledger = [];
let creditBalance = 0;
let monthlyQuota = 200;
let currentProjectId = null;
let currentView = 'studio';
let libraryFilter = 'all';
let qwenConfigured = false;
let musicConfigured = false;
let latestQwenSuggestion = '';
let activeAudio = null;
let playingAssetId = null;
let playerObjectUrl = '';
let providerSettings = null;
let pollTimers = new Map();
let capabilities = null;
let plans = [];
let compareSlots = { a: null, b: null };
let monitorAssetId = null;
let playerClock = null;
let recentJobs = [];
let notices = [];

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = (value = '') => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers, cache: 'no-store' });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    logout(false);
    throw new Error(payload.detail || '请先登录');
  }
  if (!response.ok) {
    const detail = payload.detail || payload.error?.message || payload.message || `请求失败 (${response.status})`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return payload;
}

function logout(showMessage = true) {
  token = '';
  currentUser = null;
  localStorage.removeItem(AUTH_KEY);
  projects = [];
  ledger = [];
  currentProjectId = null;
  stopAllPolls();
  navigate('login', { replace: true });
  if (showMessage) showToast('已退出登录');
}

function showAuth(visible) {
  const gate = $('#auth-gate');
  if (!gate) return;
  gate.hidden = !visible;
  document.body.classList.toggle('auth-locked', visible);
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '刚刚';
  const diff = Math.max(0, Date.now() - date.getTime());
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
}

function allProjects() {
  return [...projects].sort((a, b) => new Date(b.updatedAt || b.createdAt) - new Date(a.updatedAt || a.createdAt));
}

function listedProjects() {
  const status = $('#project-status-filter')?.value || 'active';
  return allProjects().filter((project) => {
    if (status === 'archived') return project.status === 'archived';
    if (status === 'all') return true;
    return project.status !== 'archived';
  });
}

function allAssets() {
  return allProjects().flatMap((project) => (project.assets || []).map((asset) => ({ ...asset, projectName: project.name, projectId: project.id })));
}

function render() {
  $('#studio-date').textContent = new Intl.DateTimeFormat('en-US', { weekday: 'long', month: 'long', day: '2-digit', year: 'numeric' }).format(new Date()).toUpperCase();
  if (currentUser) {
    $('.workspace-copy strong').textContent = `${currentUser.display_name} 的工作室`;
    $('.profile-copy strong').textContent = currentUser.display_name;
    $('.profile-avatar').textContent = currentUser.display_name.slice(0, 1);
    $('.workspace-avatar').textContent = currentUser.display_name.slice(0, 1);
    if ($('#settings-avatar')) $('#settings-avatar').textContent = currentUser.display_name.slice(0, 1);
    if ($('#settings-identity')) $('#settings-identity').textContent = `${currentUser.display_name} · ${currentUser.email}`;
    if ($('#welcome-title')) $('#welcome-title').innerHTML = `你好，${escapeHTML(currentUser.display_name)}。<span class="wave-emoji">✳</span>`;
    if ($('#profile-name') && document.activeElement !== $('#profile-name')) $('#profile-name').value = currentUser.display_name || '';
    if ($('#profile-daw') && document.activeElement !== $('#profile-daw')) $('#profile-daw').value = currentUser.daw || '';
    if ($('#profile-role') && document.activeElement !== $('#profile-role')) $('#profile-role').value = currentUser.role_label || '';
    const planLabel = $('.profile-copy small');
    if (planLabel) planLabel.textContent = `${currentUser.plan_name || 'Creator'} 方案`;
  }
  renderProjectCards();
  renderLibrary();
  renderUsage();
  renderRecentJobs();
  renderNotices();
  $('#project-count').textContent = listedProjects().length;
  $('#recent-count').textContent = allProjects().filter((item) => item.status !== 'archived').length;
  $('#sidebar-credits').textContent = creditBalance;
  const ratio = Math.max(0, Math.min(100, (creditBalance / Math.max(monthlyQuota, 1)) * 100));
  $('#credit-bar').style.width = `${ratio}%`;
  $('#usage-meter-fill').style.width = `${ratio}%`;
  $('#usage-credits').textContent = creditBalance;
  const quotaLabel = $('#usage-credits')?.parentElement?.querySelector('span');
  if (quotaLabel) quotaLabel.textContent = ` / ${monthlyQuota}`;
  $('#usage-projects').textContent = allProjects().filter((item) => item.status !== 'archived').length;
  const spent = Math.max(0, monthlyQuota - creditBalance);
  if ($('#usage-spent')) $('#usage-spent').textContent = `已使用 ${spent} 积分`;
  if ($('#usage-plan-name') && currentUser) $('#usage-plan-name').innerHTML = `${escapeHTML(currentUser.plan_name || 'Creator')} <span class="plan-status">演示</span>`;
  if ($('#usage-plan-desc') && currentUser?.plan_note) $('#usage-plan-desc').textContent = currentUser.plan_note;
  const generate = $('#generate-button');
  if (generate) generate.disabled = creditBalance < CREDIT_COST || !musicConfigured;
  if (currentView === 'editor' && currentProjectId) renderEditor();
  updatePromptCount();
}

function projectCard(project, index = 0) {
  const count = (project.assets || []).length;
  return `<article class="project-card tone-${project.tone || 'sage'}" data-open-project="${project.id}" style="--delay:${index * 40}ms" tabindex="0" role="button">
    <div class="project-card-art"><span>${escapeHTML(project.icon || '◌')}</span></div>
    <div class="project-card-body"><div class="project-card-top"><strong>${escapeHTML(project.name)}</strong><button class="ghost-icon" data-project-menu="${project.id}" type="button" aria-label="项目菜单">···</button></div>
    <p>${escapeHTML(project.purpose || '灵感草稿')}</p><div class="project-card-meta"><span>${formatDate(project.updatedAt || project.createdAt)}</span><span>${count} 个版本</span></div></div></article>`;
}

function renderProjectCards() {
  const query = ($('#project-search')?.value || '').trim().toLowerCase();
  const studio = allProjects().filter((project) => project.status !== 'archived' && (!query || project.name.toLowerCase().includes(query)));
  const list = listedProjects().filter((project) => !query || project.name.toLowerCase().includes(query));
  if ($('#recent-projects')) $('#recent-projects').innerHTML = studio.slice(0, 6).map(projectCard).join('') || '<div class="empty-block">还没有项目。点击「新建项目」开始。</div>';
  if ($('#all-projects')) $('#all-projects').innerHTML = list.map((project, index) => projectCard(project, index)).join('') || '<div class="empty-block">没有匹配的项目。</div>';
}

function renderEditor() {
  const project = projects.find((item) => item.id === currentProjectId);
  if (!project) return;
  $('#editor-title').textContent = project.name;
  $('#editor-purpose').textContent = project.purpose || '灵感草稿';
  $('#editor-updated').textContent = formatDate(project.updatedAt || project.createdAt);
  $('#result-count').textContent = (project.assets || []).length;
  renderCapabilityFlags();
  const assets = project.assets || [];
  const selected = assets.find((asset) => asset.selected) || assets[0];
  if (project.pendingJob) {
    $('#result-stage').innerHTML = renderPendingJob(project);
    $('#monitor-stage').innerHTML = renderPendingJob(project);
    return;
  }
  if (!assets.length) {
    $('#result-stage').innerHTML = `<div class="empty-result"><h3>还没有 Take。</h3><p>每次生成都会留下独立版本，不会覆盖旧文件。</p></div>`;
    $('#monitor-stage').innerHTML = `<div class="empty-result"><h3>监听台空闲</h3><p>生成完成后在这里试听、A/B 和导出到 DAW。</p></div>`;
    return;
  }
  monitorAssetId = selected.id;
  $('#monitor-stage').innerHTML = renderMonitor(selected, project);
  bindMonitor();
  $('#result-stage').innerHTML = assets.map((asset) => renderResultCard(project, asset)).join('');
}

function renderPendingJob(project) {
  const job = project.pendingJob;
  const cancel = job.can_cancel ? `<button class="mini-action" data-cancel-job="${job.id}" type="button">取消排队</button>` : '';
  return `<div class="pending-job"><div class="pending-orbit"></div><h3>正在生成…</h3><p>阶段：${escapeHTML(job.progress_stage || job.status || 'queued')} · 已预留 ${job.credit_hold || CREDIT_COST} 积分</p><small>任务 ${escapeHTML(job.id || '')} · ${escapeHTML(job.job_type || '')}</small>${cancel}</div>`;
}

function renderResultCard(project, asset) {
  const inA = compareSlots.a === asset.id;
  const inB = compareSlots.b === asset.id;
  return `<article class="result-card ${asset.selected ? 'selected' : ''} ${monitorAssetId === asset.id ? 'playing' : ''}" data-asset-id="${asset.id}">
    <div class="result-wave">${makeWaveform(asset.id)}</div>
    <div class="result-body"><div class="result-top"><strong>${escapeHTML(asset.title)}</strong><button class="ghost-icon" data-favorite="${asset.id}" type="button">${asset.favorite ? '★' : '☆'}</button></div>
    <div class="result-meta"><span>${asset.duration || project.duration || '--'}s</span><span>${asset.bpm || project.bpm} BPM</span><span>${escapeHTML(asset.key || project.key || '')}</span><span>Take ${asset.version || 1}</span></div>
    <div class="result-actions">
      <button class="mini-action" data-select="${asset.id}" type="button">${asset.selected ? '当前' : '设为当前'}</button>
      <button class="mini-action" data-compare="${asset.id}" type="button">${inA ? 'A' : inB ? 'B' : 'A/B'}</button>
      <button class="mini-action" data-download="${asset.id}" type="button">MP3</button>
      <button class="mini-action" data-export="${asset.id}" type="button">导出包</button>
      <button class="mini-action" data-write-lyrics="${asset.id}" type="button">填词</button>
      <button class="mini-action" data-variation="${asset.id}" type="button">变体</button>
    </div></div></article>`;
}

function renderMonitor(asset, project) {
  if (!asset) return '';
  const a = allAssets().find((item) => item.id === compareSlots.a);
  const b = allAssets().find((item) => item.id === compareSlots.b);
  const lyrics = String(asset.lyrics || project.lyrics || '').trim();
  return `<div class="player-shell">
    <p class="player-title">${escapeHTML(asset.title)}</p>
    <p class="player-sub">${asset.bpm || project.bpm} BPM · ${escapeHTML(asset.key || '')} · ${escapeHTML(asset.format || 'mp3').toUpperCase()} · 条款 ${escapeHTML(asset.termsVersion || '1.0')}</p>
    <audio id="main-audio" preload="metadata"></audio>
    <input class="player-bar" id="player-seek" type="range" min="0" max="1000" value="0" aria-label="进度" />
    <div class="player-times"><span id="player-elapsed">0:00</span><span id="player-remain">--</span></div>
    <div class="player-controls">
      <button class="button button-primary" data-play="${asset.id}" type="button">播放 / 暂停</button>
      <label>音量 <input id="player-volume" type="range" min="0" max="1" step="0.05" value="0.9" /></label>
    </div>
    <div class="ab-row">
      <div class="ab-card"><strong>A</strong><div>${a ? escapeHTML(a.title) : '未选'}</div>${a ? `<button data-play="${a.id}" type="button">播 A</button>` : ''}</div>
      <div class="ab-card"><strong>B</strong><div>${b ? escapeHTML(b.title) : '未选'}</div>${b ? `<button data-play="${b.id}" type="button">播 B</button>` : ''}</div>
    </div>
    <div class="lyrics-pane">
      <div class="lyrics-pane-head">
        <strong>歌词</strong>
        <button class="mini-action" data-write-lyrics="${asset.id}" type="button">填词</button>
        ${lyrics ? `<button class="mini-action" data-copy-lyrics="1" type="button">复制</button>` : ''}
        ${lyrics ? `<button class="mini-action" data-vocal-regen="1" type="button">用人声再生成</button>` : ''}
      </div>
      <pre class="lyrics-body">${lyrics ? escapeHTML(lyrics) : '还没有歌词。生成完成后可按这首曲子填词；填词不会改这段音频。'}</pre>
      <p class="player-sub">「用人声再生成」会把当前 MP3 上传到 RunningHub，再调用 MiniMax Music Cover。歌词提交上限 1000 字；会出新 Take，不覆盖旧文件。</p>
    </div>
    <p class="player-sub">MIDI / 分轨 / 精确 WAV：当前模型不支持。导出包含 MP3 原文件与 generation.txt 元数据，便于丢进 DAW。</p>
  </div>`;
}

function renderCapabilityFlags() {
  const box = $('#capability-flags');
  if (!box) return;
  const music = capabilities?.music || {};
  const outs = music.outputs || {};
  box.innerHTML = [
    ['MP3', outs.mp3?.supported],
    ['变体', music.job_types?.variation?.supported],
    ['人声翻唱', music.job_types?.vocal_cover?.supported],
    ['续写', music.job_types?.extend?.supported],
    ['局部重生成', music.job_types?.region_regen?.supported],
    ['MIDI', outs.midi?.supported],
    ['分轨', outs.stems?.supported],
  ].map(([name, ok]) => `<span class="flag ${ok ? '' : 'off'}">${name}${ok ? '' : ' 不可用'}</span>`).join('');
}

function makeWaveform(seed = '') {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) hash = (hash * 31 + seed.charCodeAt(i)) % 997;
  const bars = Array.from({ length: 28 }, (_, index) => {
    const height = 18 + ((hash + index * 17) % 62);
    return `<i style="height:${height}%"></i>`;
  }).join('');
  return `<div class="wave-bars">${bars}</div>`;
}

function bindMonitor() {}

function setCurrentAsset(assetId) {
  if (!assetId || !currentProjectId) return;
  const project = projects.find((item) => item.id === currentProjectId);
  if (!project?.assets?.some((asset) => asset.id === assetId)) return;
  monitorAssetId = assetId;
  project.assets.forEach((asset) => {
    asset.selected = asset.id === assetId;
  });
  renderEditor();
  api(`/api/assets/${assetId}`, { method: 'PATCH', body: JSON.stringify({ selected: true }) })
    .catch((error) => showToast(error.message, 'error'));
}

function renderLibrary() {
  if (libraryFilter === 'midi') {
    $('#library-count').textContent = 'MIDI 当前模型不支持';
    $('#library-list').innerHTML = '<div class="empty-block">MiniMax Music 不输出 MIDI。素材库只收录已生成的音频 Take。</div>';
    return;
  }
  const assets = allAssets().filter((asset) => libraryFilter === 'all' || libraryFilter === 'audio');
  $('#library-count').textContent = `${assets.length} 个素材`;
  $('#library-list').innerHTML = assets.map((asset) => `
    <article class="library-item" data-library-asset="${asset.id}" role="button" tabindex="0">
      <div class="library-item-icon" aria-hidden="true">▶</div>
      <div class="library-item-copy"><strong>${escapeHTML(asset.title)}</strong><small>${escapeHTML(asset.date || '')} · Take ${asset.version || 1}</small></div>
      <div class="library-item-project">${escapeHTML(asset.projectName)}</div>
      <button data-play="${asset.id}" type="button">播放</button>
      <button data-download="${asset.id}" type="button">下载</button>
      <button data-open-project="${asset.projectId}" type="button">打开项目</button>
    </article>`).join('') || '<div class="empty-block">素材库还是空的。</div>';
  syncPlayUi();
}

function renderUsage() {
  $('#ledger-body').innerHTML = ledger.map((row) => `<tr><td>${escapeHTML(row.label)}</td><td>${escapeHTML(row.project || '-')}</td><td>${escapeHTML(row.date)}</td><td class="${row.amount < 0 ? 'debit' : 'credit'}">${row.amount > 0 ? '+' : ''}${row.amount}</td><td>${escapeHTML(row.status)}</td></tr>`).join('') || '<tr><td colspan="5">暂无积分记录</td></tr>';
  const grid = $('#pricing-preview');
  if (grid) {
    grid.innerHTML = `<div><div class="section-kicker">PLANS</div><h2>方案对照（未开放真实购买）</h2><p>价格为 PRD 示例。当前环境发放演示积分，不经过支付 webhook。</p></div><div class="plan-grid">${plans.map((plan) => `<article class="plan-tile"><h3>${escapeHTML(plan.name)}</h3><div>${escapeHTML(plan.price_display)}</div><p>${escapeHTML(plan.note || '')}</p><ul>${(plan.features || []).map((f) => `<li>${escapeHTML(f)}</li>`).join('')}</ul></article>`).join('')}</div>`;
  }
}

function renderRecentJobs() {
  const box = $('#recent-jobs');
  if (!box) return;
  if (!recentJobs.length) {
    box.innerHTML = '<div class="empty-block">还没有生成任务。</div>';
    return;
  }
  box.innerHTML = recentJobs.slice(0, 8).map((job) => {
    const retry = (job.status === 'failed' || job.status === 'canceled')
      ? `<button class="mini-action" data-retry-job="${job.id}" type="button">重试</button>`
      : '';
    const open = job.project_id ? `<button class="mini-action" data-open-project="${job.project_id}" type="button">打开</button>` : '';
    return `<div class="job-row"><div><strong>${escapeHTML(job.project_name || '项目')}</strong><small>${escapeHTML(job.progress_stage || job.status)} · ${escapeHTML(job.job_type || '')}</small></div><div class="library-row-actions">${open}${retry}</div></div>`;
  }).join('');
}

function renderNotices() {
  const panel = $('#notice-panel');
  const dot = $('#notice-dot');
  if (dot) dot.hidden = !notices.length;
  if (!panel) return;
  panel.innerHTML = notices.length
    ? notices.map((item) => `<p class="notice-${item.level || 'info'}">${escapeHTML(item.text)}</p>`).join('')
    : '<p>暂无通知。</p>';
}

const PUBLIC_VIEWS = new Set(['landing', 'login', 'register', 'forgot', 'terms', 'privacy', 'rights', 'help']);
const TITLE_MAP = {
  landing: 'Studio AI',
  login: '登录 · Studio AI',
  register: '注册 · Studio AI',
  forgot: '重置密码 · Studio AI',
  studio: '工作室 · Studio AI',
  projects: '所有项目 · Studio AI',
  library: '素材库 · Studio AI',
  usage: '用量与方案 · Studio AI',
  settings: '设置 · Studio AI',
  editor: '创作工作台 · Studio AI',
  terms: '服务条款 · Studio AI',
  privacy: '隐私政策 · Studio AI',
  rights: '版权说明 · Studio AI',
  help: '帮助中心 · Studio AI',
};

function pathFor(view, projectId) {
  if (view === 'editor') return `/projects/${projectId || currentProjectId || ''}`;
  return ({
    landing: '/',
    login: '/login',
    register: '/register',
    forgot: '/forgot',
    studio: '/studio',
    projects: '/projects',
    library: '/library',
    usage: '/usage',
    settings: '/settings',
    terms: '/terms',
    privacy: '/privacy',
    rights: '/rights',
    help: '/help',
  })[view] || '/studio';
}

function parseLocation() {
  const path = location.pathname.replace(/\/+$/, '') || '/';
  if (path === '/') return { view: 'landing' };
  if (path === '/login') return { view: 'login' };
  if (path === '/register') return { view: 'register' };
  if (path === '/forgot') return { view: 'forgot' };
  if (path === '/studio') return { view: 'studio' };
  if (path === '/projects') return { view: 'projects' };
  const projectMatch = path.match(/^\/projects\/([^/]+)$/);
  if (projectMatch) return { view: 'editor', projectId: projectMatch[1] };
  if (path === '/library') return { view: 'library' };
  if (path === '/usage') return { view: 'usage' };
  if (path === '/settings') return { view: 'settings' };
  if (path === '/terms') return { view: 'terms' };
  if (path === '/privacy') return { view: 'privacy' };
  if (path === '/rights') return { view: 'rights' };
  if (path === '/help') return { view: 'help' };
  return { view: 'studio' };
}

function applyRoute(route) {
  currentView = route.view;
  const publicPage = ['landing', 'terms', 'privacy', 'rights', 'help'].includes(route.view);
  const authPage = ['login', 'register', 'forgot'].includes(route.view);
  document.body.classList.toggle('is-public', publicPage || authPage);
  document.body.classList.toggle('is-app', !publicPage && !authPage);
  const publicShell = $('#public-shell');
  if (publicShell) publicShell.hidden = !publicPage;
  showAuth(authPage);
  if (authPage) {
    $$('[data-auth-tab]').forEach((button) => button.classList.toggle('active', button.dataset.authTab === route.view));
    const loginForm = $('#auth-login-form');
    const registerForm = $('#auth-register-form');
    const forgotForm = $('#auth-forgot-form');
    if (loginForm) loginForm.hidden = route.view !== 'login';
    if (registerForm) registerForm.hidden = route.view !== 'register';
    if (forgotForm) forgotForm.hidden = route.view !== 'forgot';
  }
  $$('.view').forEach((section) => section.classList.toggle('active', section.id === `view-${route.view}`));
  $$('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.view === (route.view === 'editor' ? 'projects' : route.view)));
  const names = { studio: ['工作室', '概览'], projects: ['项目', '所有项目'], library: ['素材库', '全部素材'], usage: ['工作空间', '用量与方案'], settings: ['工作空间', '设置'], editor: ['项目', route.label || '创作工作台'] };
  const [root, current] = names[route.view] || ['工作室', '概览'];
  if ($('#crumb-root')) $('#crumb-root').textContent = root;
  if ($('#crumb-current')) $('#crumb-current').textContent = current;
  document.title = TITLE_MAP[route.view] || 'Studio AI';
  if (route.view === 'settings' && token) loadProviderSettings();
}

function navigate(view, options = {}) {
  const projectId = options.projectId;
  const url = pathFor(view, projectId);
  if (options.replace) history.replaceState({ view, projectId }, '', url);
  else if (location.pathname !== url) history.pushState({ view, projectId }, '', url);
  applyRoute({ view, projectId, label: options.label });
}

function showView(view, label) {
  navigate(view, { label, projectId: view === 'editor' ? currentProjectId : undefined });
}

async function handleLocation() {
  const route = parseLocation();
  if (token && (route.view === 'login' || route.view === 'register' || route.view === 'forgot')) {
    history.replaceState({ view: 'studio' }, '', '/studio');
    applyRoute({ view: 'studio' });
    return;
  }
  if (!token && !PUBLIC_VIEWS.has(route.view)) {
    history.replaceState({ view: 'login' }, '', '/login');
    applyRoute({ view: 'login' });
    return;
  }
  applyRoute(route);
  if (token && route.view === 'editor' && route.projectId && route.projectId !== currentProjectId) {
    await openProject(route.projectId, { skipNav: true });
  }
}

async function refreshSession() {
  const me = await api('/api/me');
  currentUser = me.data;
  const [projectPayload, creditPayload, capPayload, planPayload, jobPayload, activityPayload] = await Promise.all([
    api('/api/projects?status=all'),
    api('/api/credits/ledger'),
    api('/api/capabilities').catch(() => ({ data: null })),
    api('/api/plans').catch(() => ({ data: [] })),
    api('/api/jobs').catch(() => ({ data: [] })),
    api('/api/activity').catch(() => ({ data: { notices: [] } })),
  ]);
  projects = projectPayload.data || [];
  ledger = creditPayload.data?.entries || [];
  creditBalance = creditPayload.data?.balance ?? currentUser.credit_balance;
  monthlyQuota = currentUser.monthly_quota || creditPayload.data?.monthly_quota || 200;
  capabilities = capPayload.data;
  plans = planPayload.data || [];
  recentJobs = jobPayload.data || [];
  notices = activityPayload.data?.notices || [];
  projects.forEach((project) => {
    if (project.pendingJob) startJobPoll(project.id, project.pendingJob.id);
  });
  render();
}

async function loadServiceHealth() {
  try {
    const health = await api('/api/health');
    qwenConfigured = Boolean(health.qwen_configured);
    musicConfigured = Boolean(health.music_adapter_enabled);
    const musicLabel = `${health.music_provider} ${health.music_model || ''}`.trim();
    $('#mode-label').textContent = currentUser
      ? `${currentUser.display_name} · ${musicConfigured ? musicLabel : '音乐服务待配置'}`
      : '未登录';
    $('.rights-note span:last-child').textContent = musicConfigured
      ? `生成将调用 ${musicLabel}，并预留 ${health.music_cost_credits || CREDIT_COST} 平台积分；上游按 RunningHub 账号计费。`
      : '生成服务尚未配置。请在 Docker/.env 中填写 RUNNINGHUB_API_KEY。';
    $('.generation-cost strong').textContent = musicConfigured ? `${health.music_cost_credits || CREDIT_COST} 积分 / 次` : '需要配置服务';
    $('.generation-cost small').textContent = musicConfigured ? '异步队列 · 失败自动退回' : '设置音乐模型与凭据';
    $('#generate-button').innerHTML = `<span class="generate-stars">✳</span>${musicConfigured ? '生成音乐' : '配置生成服务'} <span class="button-arrow">→</span>`;
    $('#mode-pill').classList.toggle('service-ready', musicConfigured);
    $('.small-demo').innerHTML = `<span class="status-dot"></span>${musicConfigured ? `${musicLabel} 已就绪` : '音乐服务待配置'}`;
    $('#prompt-assist').querySelector('span').textContent = qwenConfigured ? 'Qwen 创作副驾' : '配置 Qwen 后可用';
    const fillHint = $('#fill-controls span');
    if (fillHint) fillHint.textContent = qwenConfigured ? '一键填写参数' : '一键填写参数（无 Qwen 时用规则推断）';
    const usagePill = $('#view-usage .demo-pill');
    if (usagePill) usagePill.innerHTML = '<span class="status-dot"></span>真实积分账本';
  } catch {
    $('#mode-label').textContent = '后端未连接';
  }
}

async function loadProviderSettings() {
  try {
    const payload = await api('/api/settings/providers');
    providerSettings = payload.data;
    $('#provider-status').textContent = `Qwen ${providerSettings.qwen.configured ? '已配置' : '未配置'} · Music ${providerSettings.music.configured ? '已配置' : '未配置'}（${providerSettings.music.model}）`;
    $('#provider-music-model').value = providerSettings.music.model || 'minimax-music-2.5';
    $('#provider-music-provider').value = providerSettings.music.provider || 'runninghub';
    $('#provider-qwen-model').value = providerSettings.qwen.model || 'qwen-plus';
  } catch (error) {
    $('#provider-status').textContent = error.message;
  }
}

async function saveProviderSettings(event) {
  event.preventDefault();
  try {
    await api('/api/settings/providers', {
      method: 'PUT',
      body: JSON.stringify({
        qwen_model: $('#provider-qwen-model').value.trim(),
        music_provider: $('#provider-music-provider').value,
        music_model: $('#provider-music-model').value,
        dashscope_api_key: $('#provider-api-key').value.trim() || null,
        runninghub_api_key: $('#provider-runninghub-key').value.trim() || null,
      }),
    });
    $('#provider-api-key').value = '';
    $('#provider-runninghub-key').value = '';
    showToast('服务配置已更新（当前进程生效）。');
    await loadServiceHealth();
    await loadProviderSettings();
  } catch (error) {
    showToast(error.message, 'error');
  }
}

async function openProject(projectId, options = {}) {
  const payload = await api(`/api/projects/${projectId}`);
  const project = payload.data;
  const index = projects.findIndex((item) => item.id === projectId);
  if (index >= 0) projects[index] = project;
  else projects.unshift(project);
  currentProjectId = projectId;
  $('#prompt-input').value = project.prompt || '';
  $('#bpm-input').value = project.bpm || 82;
  $('#key-select').value = project.key || 'D minor';
  $('#duration-select').value = String(project.duration || 90);
  $('#vocal-select').value = project.vocal || 'instrumental';
  $('#mood-input').value = project.mood || '';
  $('#structure-input').value = project.structure || '';
  $('#negative-input').value = project.negativePrompt || '';
  if ($('#lyrics-input')) $('#lyrics-input').value = project.lyrics || '';
  if ($('#time-signature')) $('#time-signature').value = project.timeSignature || '4/4';
  const selectedGenres = new Set(project.genre || []);
  $$('#genre-chips .choice-chip:not(.add-chip)').forEach((chip) => chip.classList.toggle('selected', selectedGenres.has(chip.textContent.trim())));
  updatePromptCount();
  if (!options.skipNav) navigate('editor', { label: project.name, projectId });
  else applyRoute({ view: 'editor', projectId, label: project.name });
  render();
}

async function createProject(name, purpose) {
  const initialPrompt = $('#new-project-form').dataset.initialPrompt || '';
  delete $('#new-project-form').dataset.initialPrompt;
  const payload = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({ name: name.trim(), purpose, prompt: initialPrompt }),
  });
  projects.unshift(payload.data);
  closeModal();
  render();
  await openProject(payload.data.id);
  showToast('项目已创建。');
  $('#prompt-input').focus();
}

function promptLimit() {
  return Number(capabilities?.music?.parameters?.prompt?.max) || PROMPT_MAX;
}

function updatePromptCount() {
  const input = $('#prompt-input');
  if (!input) return;
  const max = promptLimit();
  input.maxLength = max;
  $('#prompt-count').textContent = `${input.value.length} / ${max}`;
}

async function saveEditorFields() {
  if (!currentProjectId) return;
  const project = projects.find((item) => item.id === currentProjectId);
  if (!project) return;
  const body = {
    prompt: $('#prompt-input').value.trim(),
    bpm: Number($('#bpm-input').value) || 82,
    key_signature: $('#key-select').value,
    duration_seconds: Number($('#duration-select').value),
    vocal_mode: $('#vocal-select').value,
    mood: $('#mood-input').value.trim(),
    structure: $('#structure-input').value.trim(),
    negative_prompt: $('#negative-input').value.trim(),
    lyrics: $('#lyrics-input')?.value.trim() || '',
    time_signature: $('#time-signature')?.value || '4/4',
    genre: $$('#genre-chips .choice-chip.selected').map((chip) => chip.textContent.trim()),
  };
  Object.assign(project, {
    prompt: body.prompt,
    bpm: body.bpm,
    key: body.key_signature,
    duration: body.duration_seconds,
    vocal: body.vocal_mode,
    mood: body.mood,
    structure: body.structure,
    negativePrompt: body.negative_prompt,
    lyrics: body.lyrics,
    timeSignature: body.time_signature,
    genre: body.genre,
  });
  try {
    await api(`/api/projects/${currentProjectId}`, { method: 'PATCH', body: JSON.stringify(body) });
  } catch (error) {
    console.warn(error);
  }
}

function stopAllPolls() {
  pollTimers.forEach((timer) => clearInterval(timer));
  pollTimers.clear();
}

function startJobPoll(projectId, jobId) {
  if (pollTimers.has(jobId)) return;
  const timer = setInterval(async () => {
    try {
      const payload = await api(`/api/jobs/${jobId}`);
      const job = payload.data;
      const project = projects.find((item) => item.id === projectId);
      if (project) project.pendingJob = job;
      if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'canceled') {
        clearInterval(timer);
        pollTimers.delete(jobId);
        await refreshSession();
        if (currentProjectId === projectId) await openProject(projectId);
        if (job.status === 'succeeded') {
          showToast(job.job_type === 'vocal_cover' ? '人声翻唱完成，已保存为新 Take。' : (qwenConfigured ? '音乐生成完成。可以填词后再做「用人声再生成」。' : '音乐生成完成。'));
        }
        else showToast(job.error_message || '生成失败，积分已退回。', 'error');
      } else {
        render();
      }
    } catch (error) {
      clearInterval(timer);
      pollTimers.delete(jobId);
      showToast(error.message, 'error');
    }
  }, 3000);
  pollTimers.set(jobId, timer);
}

async function startGeneration(sourceAssetId = null, options = {}) {
  const vocalCover = Boolean(options.vocalCover);
  const project = projects.find((item) => item.id === currentProjectId);
  if (!project) {
    showToast('请先打开一个项目。', 'error');
    return;
  }
  if (creditBalance < CREDIT_COST) {
    showToast(`积分不足，本次需要 ${CREDIT_COST} 积分。`, 'error');
    return;
  }
  if (!musicConfigured) {
    showToast('请先在设置中配置音乐服务。', 'error');
    return;
  }
  const prompt = $('#prompt-input').value.trim();
  if (prompt.length < 8) {
    showToast('请再多描述一些声音细节（至少 8 个字符）。', 'error');
    return;
  }
  if (project.pendingJob && ['queued', 'running', 'validating', 'finalizing'].includes(project.pendingJob.status)) {
    showToast('这个项目已有一项生成任务正在进行。', 'error');
    return;
  }
  await saveEditorFields();
  const format = 'mp3';
  const lyrics = $('#lyrics-input')?.value.trim() || '';
  const instrumental = vocalCover ? false : $('#vocal-select').value === 'instrumental';
  if (vocalCover) {
    if (!sourceAssetId) {
      showToast('请先选中一个已生成的版本，再做人声翻唱。', 'error');
      return;
    }
    if (lyrics.length < 10) {
      showToast('Music Cover 歌词至少 10 字，请先填词。', 'error');
      return;
    }
  } else if (!instrumental && lyrics.length < 8) {
    showToast('人声模式请填写歌词（至少 8 字）。', 'error');
    return;
  }
  const providerPromptMax = Number(capabilities?.music?.parameters?.prompt?.provider_max) || 2000;
  if (prompt.length > providerPromptMax) {
    showToast(`风格描述 ${prompt.length} 字，生成时会压缩到 MiniMax 上限 ${providerPromptMax} 字。`);
  }
  const coverMax = Number(capabilities?.music?.parameters?.lyrics?.cover_max) || 1000;
  if (vocalCover && lyrics.length > coverMax) {
    showToast(`歌词 ${lyrics.length} 字，Music Cover 提交时会压缩到 ${coverMax} 字。`);
  }
  const cost = CREDIT_COST;
  const confirmed = window.confirm(
    vocalCover
      ? `将把当前成品上传到 RunningHub，再调用 MiniMax Music Cover 加人声。\n预留 ${cost} 积分；生成新 Take，不覆盖旧文件。\n歌词提交上限 1000 字。不保证 1:1 保留编曲。继续吗？`
      : `将创建异步任务并预留 ${cost} 积分。旧 Take 不会被覆盖。\n当前模型只交付 MP3，不提供 MIDI/分轨。失败自动退回积分。继续吗？`,
  );
  if (!confirmed) return;
  const button = $('#generate-button');
  button.disabled = true;
  try {
    const payload = await api(`/api/projects/${project.id}/jobs`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({
        prompt,
        format,
        instrumental,
        lyrics,
        parent_asset_id: sourceAssetId,
        job_type: vocalCover ? 'vocal_cover' : (sourceAssetId ? 'variation' : 'initial_generate'),
        capability_schema_version: capabilities?.schema_version,
        context: {
          genre: project.genre || [],
          bpm: project.bpm,
          key: project.key,
          duration_seconds: project.duration,
          mood: project.mood,
          structure: project.structure,
          negative_prompt: project.negativePrompt,
          time_signature: project.timeSignature || '4/4',
          vocal_cover: vocalCover,
        },
      }),
    });
    project.pendingJob = payload.data;
    creditBalance = Math.max(0, creditBalance - CREDIT_COST);
    render();
    startJobPoll(project.id, payload.data.id);
    showToast(vocalCover ? '人声翻唱已进入队列（先上传成品，再 Cover）。' : '任务已进入队列。');
    await refreshSession();
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

function showToast(message, type = '') {
  const region = $('#toast-region');
  if (!region || !message) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`.trim();
  toast.setAttribute('role', 'status');
  toast.textContent = message;
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'toast-close';
  close.setAttribute('aria-label', '关闭提示');
  close.textContent = '×';
  const dismiss = () => {
    window.clearTimeout(toast._timer);
    toast.remove();
  };
  close.addEventListener('click', dismiss);
  toast.append(close);
  region.append(toast);
  while (region.children.length > 3) region.firstElementChild.remove();
  toast._timer = window.setTimeout(dismiss, type === 'error' ? 5600 : 3600);
}

function openModal() {
  $('#modal-backdrop').hidden = false;
  document.body.style.overflow = 'hidden';
  window.setTimeout(() => $('#new-project-name').focus(), 50);
}

function closeModal() {
  $('#modal-backdrop').hidden = true;
  document.body.style.overflow = '';
  $('#new-project-form').reset();
}

function assetContentUrl(asset) {
  if (asset?.id) return `${API_BASE}/api/assets/${asset.id}/content`;
  const raw = asset?.audioUrl || '';
  if (!raw) return '';
  if (raw.startsWith('/')) return `${API_BASE}${raw}`;
  try {
    const parsed = new URL(raw);
    return `${API_BASE}${parsed.pathname}`;
  } catch {
    return `${API_BASE}${raw}`;
  }
}

function getPlayer() {
  let node = $('#global-audio');
  if (!node) {
    node = document.createElement('audio');
    node.id = 'global-audio';
    node.preload = 'auto';
    document.body.appendChild(node);
  }
  return node;
}

function syncPlayUi() {
  $$('[data-library-asset]').forEach((row) => {
    const active = row.dataset.libraryAsset === playingAssetId;
    row.classList.toggle('playing', active);
    const icon = $('.library-item-icon', row);
    if (icon) icon.textContent = active ? 'Ⅱ' : '▶';
    const playButton = $('[data-play]', row);
    if (playButton) playButton.textContent = active ? '暂停' : '播放';
  });
  const nowPlaying = $('#library-now-playing');
  if (nowPlaying) {
    const asset = allAssets().find((item) => item.id === playingAssetId);
    nowPlaying.textContent = asset ? `正在播放：${asset.title}` : '点列表中的歌曲即可试听';
  }
}

async function playAsset(assetId) {
  const asset = allAssets().find((item) => item.id === assetId);
  const url = assetContentUrl(asset);
  if (!url) {
    showToast('没有可播放的音频。', 'error');
    return;
  }
  const node = getPlayer();
  if (playingAssetId === assetId && !node.paused) {
    node.pause();
    playingAssetId = null;
    syncPlayUi();
    return;
  }
  playingAssetId = assetId;
  monitorAssetId = assetId;
  activeAudio = node;
  syncPlayUi();
  try {
    node.muted = true;
    node.play().catch(() => {});
    const response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) throw new Error(`音频加载失败 (${response.status})`);
    const blob = await response.blob();
    if (!blob.size) throw new Error('音频文件是空的');
    if (playerObjectUrl) URL.revokeObjectURL(playerObjectUrl);
    playerObjectUrl = URL.createObjectURL(blob);
    node.src = playerObjectUrl;
    node.muted = false;
    node.volume = Number($('#player-volume')?.value || 0.9) || 0.9;
    node.onended = () => {
      playingAssetId = null;
      syncPlayUi();
    };
    node.ontimeupdate = () => {
      if (!node.duration) return;
      const seek = $('#player-seek');
      if (seek) seek.value = String(Math.round((node.currentTime / node.duration) * 1000));
      if ($('#player-elapsed')) $('#player-elapsed').textContent = `${Math.floor(node.currentTime / 60)}:${String(Math.floor(node.currentTime % 60)).padStart(2, '0')}`;
    };
    await node.play();
  } catch (error) {
    playingAssetId = null;
    syncPlayUi();
    const name = error?.name || '';
    if (name === 'NotAllowedError') showToast('浏览器拦截了自动播放，请再点一次播放。', 'error');
    else showToast(error.message || '音频无法播放。', 'error');
  }
}

async function downloadAsset(assetId) {
  const asset = allAssets().find((item) => item.id === assetId);
  if (!asset) {
    showToast('没有可下载的音频。', 'error');
    return;
  }
  try {
    const url = assetContentUrl(asset);
    const response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) throw new Error('下载失败');
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = `${asset.title.replace(/[\\/:*?"<>|]/g, '-')}.${asset.format || 'mp3'}`;
    link.click();
    URL.revokeObjectURL(objectUrl);
  } catch (error) {
    showToast(error.message || '下载失败', 'error');
  }
}

function splitQwenSuggestion(text) {
  const normalized = text.replace(/^\s+/, '');
  const match = normalized.match(/创作描述\s*[:：]\s*([\s\S]*?)(?:\n+\s*制作建议\s*[:：]|$)/);
  return (match?.[1] || normalized).trim();
}

async function applyParsedControls(controls) {
  if (!controls) return;
  const row = $('#genre-chips');
  if (row && Array.isArray(controls.genre)) {
    $$('#genre-chips .choice-chip:not(.add-chip)').forEach((chip) => chip.classList.remove('selected'));
    controls.genre.forEach((name) => {
      const existing = $$('#genre-chips .choice-chip:not(.add-chip)').find((chip) => chip.textContent.trim() === name);
      if (existing) {
        existing.classList.add('selected');
        return;
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'choice-chip selected';
      button.textContent = name;
      row.insertBefore(button, $('.add-chip', row));
    });
  }
  if (controls.bpm) $('#bpm-input').value = String(controls.bpm);
  const keyOptions = [...$('#key-select').options].map((option) => option.value);
  if (controls.key && keyOptions.includes(controls.key)) $('#key-select').value = controls.key;
  if (controls.time_signature) $('#time-signature').value = controls.time_signature;
  if (controls.duration_seconds) $('#duration-select').value = String(controls.duration_seconds);
  if (controls.vocal) $('#vocal-select').value = controls.vocal;
  if (controls.mood) $('#mood-input').value = controls.mood;
  if (controls.structure) $('#structure-input').value = controls.structure;
  if (controls.negative_prompt) $('#negative-input').value = controls.negative_prompt;
  if (controls.lyrics) $('#lyrics-input').value = controls.lyrics;
  if (controls.mood || controls.structure || controls.negative_prompt || controls.lyrics) {
    $('#advanced-fields').hidden = false;
    $('#advanced-toggle').classList.add('open');
    $('.advanced-plus', $('#advanced-toggle')).textContent = '−';
  }
  await saveEditorFields();
}

function openLyricsEditor() {
  const fields = $('#advanced-fields');
  if (fields) {
    fields.hidden = false;
    $('#advanced-toggle')?.classList.add('open');
    const plus = $('.advanced-plus', $('#advanced-toggle'));
    if (plus) plus.textContent = '−';
  }
}

function applyLyricsToEditor(lyrics) {
  openLyricsEditor();
  if ($('#lyrics-input')) $('#lyrics-input').value = lyrics;
  const project = projects.find((item) => item.id === currentProjectId);
  if (project) project.lyrics = lyrics;
}

async function requestWriteLyrics(assetId) {
  const project = projects.find((item) => item.id === currentProjectId);
  if (!project) {
    showToast('请先打开一个已生成的项目。', 'error');
    return;
  }
  if (!(project.assets || []).length) {
    showToast('请先生成音乐，再为成品填词。', 'error');
    return;
  }
  if (!qwenConfigured) {
    showToast('Qwen 未配置，无法填词。', 'error');
    return;
  }
  const targetId = assetId || monitorAssetId || project.assets.find((item) => item.selected)?.id || project.assets[0].id;
  const instruction = ($('#lyrics-instruction')?.value || '').trim();
  const button = $('#write-lyrics-button');
  if (button) button.disabled = true;
  showToast('正在按这首曲子填词…');
  try {
    const result = await api(`/api/projects/${project.id}/write-lyrics`, {
      method: 'POST',
      body: JSON.stringify({ asset_id: targetId, instruction, apply: true }),
    });
    const lyrics = result.data?.lyrics || '';
    applyLyricsToEditor(lyrics);
    const asset = (project.assets || []).find((item) => item.id === targetId);
    if (asset) asset.lyrics = lyrics;
    render();
    showToast('歌词已写入当前版本。可用「用人声再生成」把成品送到 Music Cover。');
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

async function regenerateWithVocals() {
  const lyrics = ($('#lyrics-input')?.value || '').trim();
  if (lyrics.length < 10) {
    showToast('请先填词（至少 10 字），再用人声翻唱。', 'error');
    return;
  }
  const project = projects.find((item) => item.id === currentProjectId);
  const assetId = monitorAssetId || project?.assets?.find((item) => item.selected)?.id || project?.assets?.[0]?.id;
  await startGeneration(assetId, { vocalCover: true });
}

async function requestFillControls() {
  const prompt = $('#prompt-input').value.trim();
  if (prompt.length < 8) {
    showToast('先写完创作描述（至少 8 个字符），再一键填写参数。', 'error');
    return;
  }
  const button = $('#fill-controls');
  button.disabled = true;
  try {
    const result = await api('/api/qwen/parse-controls', {
      method: 'POST',
      body: JSON.stringify({ prompt }),
    });
    const controls = result.data || {};
    await applyParsedControls(controls);
    const source = controls.source === 'qwen' ? `Qwen（${controls.model || ''}）` : '规则推断（未配置 Qwen）';
    showToast(`${source}已填写参数${controls.rationale ? `：${controls.rationale}` : '。'}`);
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

async function requestQwenAssist() {
  const input = $('#prompt-input');
  const prompt = input.value.trim();
  if (prompt.length < 8) {
    showToast('先写下一句灵感，Qwen 才能帮你拓展方向。', 'error');
    return;
  }
  const button = $('#prompt-assist');
  button.disabled = true;
  try {
    const result = await api('/api/qwen/assist', {
      method: 'POST',
      body: JSON.stringify({
        prompt,
        context: {
          genre: $$('#genre-chips .choice-chip.selected').map((chip) => chip.textContent.trim()),
          bpm: Number($('#bpm-input').value) || null,
          key: $('#key-select').value,
          duration_seconds: Number($('#duration-select').value),
          vocal: $('#vocal-select').value,
        },
      }),
    });
    latestQwenSuggestion = result.data.text;
    $('#qwen-result-text').textContent = latestQwenSuggestion;
    $('#qwen-result').hidden = false;
    showToast(`Qwen 已完成创作建议（${result.data.model}）。`);
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

async function handleProjectMenu(projectId) {
  const project = projects.find((entry) => entry.id === projectId);
  if (!project) return;
  const choice = window.prompt(`项目：${project.name}\n输入 rename / archive / duplicate / delete`);
  if (!choice) return;
  if (choice.toLowerCase() === 'rename') {
    const name = window.prompt('新的项目名称', project.name)?.trim();
    if (!name) return;
    await api(`/api/projects/${projectId}`, { method: 'PATCH', body: JSON.stringify({ name }) });
    await refreshSession();
    if (currentProjectId === projectId) await openProject(projectId);
    showToast('项目名称已更新。');
  } else if (choice.toLowerCase() === 'archive') {
    await api(`/api/projects/${projectId}/archive`, { method: 'POST' });
    await refreshSession();
    showToast('归档状态已切换。');
  } else if (choice.toLowerCase() === 'duplicate') {
    const payload = await api(`/api/projects/${projectId}/duplicate`, { method: 'POST' });
    await refreshSession();
    await openProject(payload.data.id);
    showToast('已复制项目参数（不含音频文件）。');
  } else if (choice.toLowerCase() === 'delete') {
    if (!window.confirm(`确定删除“${project.name}”吗？`)) return;
    await api(`/api/projects/${projectId}`, { method: 'DELETE' });
    if (currentProjectId === projectId) {
      currentProjectId = null;
      showView('projects');
    }
    await refreshSession();
    showToast('项目已删除。');
  }
}

async function bootstrapAuth() {
  window.addEventListener('popstate', () => {
    handleLocation();
  });
  if (token) {
    try {
      await refreshSession();
      await loadServiceHealth();
    } catch {
      token = '';
      currentUser = null;
      localStorage.removeItem(AUTH_KEY);
    }
  }
  await handleLocation();
}

document.addEventListener('click', (event) => {
  const link = event.target.closest('a[href]');
  if (link && link.origin === location.origin && !link.hasAttribute('download') && !link.target) {
    const url = new URL(link.href);
    if (url.pathname !== location.pathname || url.search !== location.search) {
      event.preventDefault();
      history.pushState({}, '', `${url.pathname}${url.search}${url.hash}`);
      handleLocation().then(() => {
        if (url.hash) document.querySelector(url.hash)?.scrollIntoView({ behavior: 'smooth' });
      });
      return;
    }
  }
  const viewTarget = event.target.closest('[data-view]');
  if (viewTarget && !viewTarget.closest('a[href]')) {
    event.preventDefault();
    showView(viewTarget.dataset.view);
    return;
  }
  const actionTarget = event.target.closest('[data-action]');
  if (actionTarget) {
    const { action } = actionTarget.dataset;
    if (action === 'new-project') openModal();
    if (action === 'back') showView('projects');
    if (action === 'show-plans') $('#pricing-preview')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (action === 'toast-profile') navigate('settings');
    if (action === 'logout') logout(true);
    if (action === 'toggle-notices') {
      const panel = $('#notice-panel');
      if (panel) panel.hidden = !panel.hidden;
    }
    if (action === 'export-data') {
      api('/api/privacy/export').then((payload) => {
        const blob = new Blob([JSON.stringify(payload.data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = 'studio-ai-export.json';
        link.click();
        URL.revokeObjectURL(url);
        showToast('已导出账号元数据。');
      }).catch((error) => showToast(error.message, 'error'));
    }
    if (action === 'delete-account') {
      const note = window.prompt('确认申请删除账号？可填写原因。') || '';
      api('/api/privacy/deletion-requests', { method: 'POST', body: JSON.stringify({ scope: 'account', note }) })
        .then((payload) => showToast(payload.data.message || '已记录删除申请。'))
        .catch((error) => showToast(error.message, 'error'));
    }
    if (action === 'report-content') {
      const reason = window.prompt('请描述要举报的问题')?.trim();
      if (!reason) return;
      api('/api/reports', { method: 'POST', body: JSON.stringify({ target_type: 'platform', target_id: currentProjectId || 'studio', reason }) })
        .then(() => showToast('举报已提交。'))
        .catch((error) => showToast(error.message, 'error'));
    }
    return;
  }
  const promptTarget = event.target.closest('[data-prompt]');
  if (promptTarget) {
    openModal();
    $('#new-project-form').dataset.initialPrompt = promptTarget.dataset.prompt;
    return;
  }
  const menuTarget = event.target.closest('[data-project-menu]');
  if (menuTarget) {
    event.stopPropagation();
    handleProjectMenu(menuTarget.dataset.projectMenu);
    return;
  }
  const monitorTarget = event.target.closest('[data-monitor]');
  if (monitorTarget) {
    monitorAssetId = monitorTarget.dataset.monitor;
    renderEditor();
    return;
  }
  const selectTarget = event.target.closest('[data-select]');
  if (selectTarget) {
    setCurrentAsset(selectTarget.dataset.select);
    return;
  }
  const resultCard = event.target.closest('.result-card[data-asset-id]');
  if (resultCard && !event.target.closest('button')) {
    setCurrentAsset(resultCard.dataset.assetId);
    return;
  }
  const compareTarget = event.target.closest('[data-compare]');
  if (compareTarget) {
    const id = compareTarget.dataset.compare;
    if (compareSlots.a === id) compareSlots.a = null;
    else if (compareSlots.b === id) compareSlots.b = null;
    else if (!compareSlots.a) compareSlots.a = id;
    else compareSlots.b = id;
    renderEditor();
    return;
  }
  const exportTarget = event.target.closest('[data-export]');
  if (exportTarget) {
    const id = exportTarget.dataset.export;
    fetch(`${API_BASE}/api/assets/${id}/export`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
      .then((response) => {
        if (!response.ok) throw new Error('导出失败');
        return response.blob();
      })
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = 'studio-export.zip';
        link.click();
        URL.revokeObjectURL(url);
      })
      .catch((error) => showToast(error.message, 'error'));
    return;
  }
  const retryTarget = event.target.closest('[data-retry-job]');
  if (retryTarget) {
    api(`/api/jobs/${retryTarget.dataset.retryJob}/retry`, { method: 'POST' })
      .then(async (payload) => {
        showToast('已重新排队，将再次预留积分。');
        await refreshSession();
        if (payload.data?.project_id) startJobPoll(payload.data.project_id, payload.data.id);
      })
      .catch((error) => showToast(error.message, 'error'));
    return;
  }
  const cancelTarget = event.target.closest('[data-cancel-job]');
  if (cancelTarget) {
    api(`/api/jobs/${cancelTarget.dataset.cancelJob}/cancel`, { method: 'POST' })
      .then(() => refreshSession())
      .then(() => showToast('已取消排队任务。'))
      .catch((error) => showToast(error.message, 'error'));
    return;
  }
  const downloadTarget = event.target.closest('[data-download]');
  if (downloadTarget) {
    downloadAsset(downloadTarget.dataset.download);
    return;
  }
  const playTarget = event.target.closest('[data-play]');
  if (playTarget) {
    playAsset(playTarget.dataset.play);
    return;
  }
  const projectTarget = event.target.closest('[data-open-project]');
  if (projectTarget) {
    openProject(projectTarget.dataset.openProject);
    return;
  }
  const libraryRow = event.target.closest('[data-library-asset]');
  if (libraryRow) {
    playAsset(libraryRow.dataset.libraryAsset);
    return;
  }
  const favoriteTarget = event.target.closest('[data-favorite]');
  if (favoriteTarget) {
    const project = projects.find((entry) => entry.id === currentProjectId);
    const asset = project?.assets.find((entry) => entry.id === favoriteTarget.dataset.favorite);
    if (!asset) return;
    asset.favorite = !asset.favorite;
    api(`/api/assets/${asset.id}`, { method: 'PATCH', body: JSON.stringify({ favorite: asset.favorite }) }).catch(() => {});
    renderEditor();
    return;
  }
  const variationTarget = event.target.closest('[data-variation]');
  if (variationTarget) {
    startGeneration(variationTarget.dataset.variation);
    return;
  }
  const writeLyricsTarget = event.target.closest('[data-write-lyrics]');
  if (writeLyricsTarget) {
    requestWriteLyrics(writeLyricsTarget.dataset.writeLyrics);
    return;
  }
  const copyLyricsTarget = event.target.closest('[data-copy-lyrics]');
  if (copyLyricsTarget) {
    const project = projects.find((entry) => entry.id === currentProjectId);
    const asset = (project?.assets || []).find((entry) => entry.id === monitorAssetId) || project;
    const lyrics = String(asset?.lyrics || project?.lyrics || '').trim();
    if (!lyrics) return;
    navigator.clipboard.writeText(lyrics).then(() => showToast('歌词已复制。')).catch(() => showToast('复制失败。', 'error'));
    return;
  }
  const vocalRegenTarget = event.target.closest('[data-vocal-regen]');
  if (vocalRegenTarget) {
    regenerateWithVocals();
    return;
  }
  const libraryFilterButton = event.target.closest('[data-library-filter]');
  if (libraryFilterButton) {
    libraryFilter = libraryFilterButton.dataset.libraryFilter;
    $$('[data-library-filter]').forEach((button) => button.classList.toggle('active', button === libraryFilterButton));
    renderLibrary();
  }
});

$('#new-project-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const name = $('#new-project-name').value.trim();
  if (!name) return;
  try {
    await createProject(name, $('#new-project-purpose').value);
  } catch (error) {
    showToast(error.message, 'error');
  }
});

$('.modal-close').addEventListener('click', closeModal);
$('.modal-cancel').addEventListener('click', closeModal);
$('#modal-backdrop').addEventListener('click', (event) => {
  if (event.target === $('#modal-backdrop')) closeModal();
});

$('#project-search').addEventListener('input', renderProjectCards);
$('#prompt-input').addEventListener('input', () => {
  updatePromptCount();
  saveEditorFields();
});
['bpm-input', 'key-select', 'duration-select', 'vocal-select', 'mood-input', 'structure-input', 'negative-input', 'lyrics-input', 'time-signature'].forEach((id) => {
  const el = $(`#${id}`);
  if (!el) return;
  el.addEventListener('change', saveEditorFields);
  if (id === 'bpm-input' || id.endsWith('-input')) el.addEventListener('input', saveEditorFields);
});
$('#generate-button').addEventListener('click', () => startGeneration());
$('#provider-settings-form').addEventListener('submit', saveProviderSettings);
$('#prompt-assist').addEventListener('click', requestQwenAssist);
$('#fill-controls')?.addEventListener('click', requestFillControls);
$('#write-lyrics-button')?.addEventListener('click', () => requestWriteLyrics());
$('#qwen-dismiss').addEventListener('click', () => { $('#qwen-result').hidden = true; });
$('#qwen-apply').addEventListener('click', () => {
  const suggestion = splitQwenSuggestion(latestQwenSuggestion);
  if (!suggestion) return;
  $('#prompt-input').value = suggestion.slice(0, promptLimit());
  updatePromptCount();
  saveEditorFields();
  $('#qwen-result').hidden = true;
  showToast('已采用 Qwen 创作描述。');
});
$('#qwen-copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(latestQwenSuggestion);
    showToast('建议已复制。');
  } catch {
    showToast('复制失败。', 'error');
  }
});
$('#advanced-toggle').addEventListener('click', () => {
  const fields = $('#advanced-fields');
  fields.hidden = !fields.hidden;
  $('#advanced-toggle').classList.toggle('open', !fields.hidden);
  $('.advanced-plus', $('#advanced-toggle')).textContent = fields.hidden ? '＋' : '−';
});
$('#genre-chips').addEventListener('click', (event) => {
  const chip = event.target.closest('.choice-chip');
  if (!chip) return;
  if (chip.classList.contains('add-chip')) {
    const genre = window.prompt('输入一个声音方向')?.trim();
    if (genre) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'choice-chip selected';
      button.textContent = genre;
      $('#genre-chips').insertBefore(button, $('.add-chip', $('#genre-chips')));
      saveEditorFields();
    }
    return;
  }
  chip.classList.toggle('selected');
  saveEditorFields();
});
$('#rename-project').addEventListener('click', async () => {
  const project = projects.find((entry) => entry.id === currentProjectId);
  if (!project) return;
  const name = window.prompt('新的项目名称', project.name)?.trim();
  if (!name) return;
  await api(`/api/projects/${project.id}`, { method: 'PATCH', body: JSON.stringify({ name }) });
  await openProject(project.id);
});

$('#auth-login-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const payload = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({
        email: $('#auth-email').value.trim(),
        password: $('#auth-password').value,
      }),
    });
    token = payload.data.token;
    localStorage.setItem(AUTH_KEY, token);
    await refreshSession();
    await loadServiceHealth();
    navigate('studio', { replace: true });
    showToast(`欢迎回来，${payload.data.user.display_name}`);
  } catch (error) {
    showToast(error.message, 'error');
  }
});

$('#auth-register-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const payload = await api('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        email: $('#reg-email').value.trim(),
        password: $('#reg-password').value,
        display_name: $('#reg-name').value.trim(),
        daw: $('#reg-daw')?.value.trim() || '',
        accept_terms: Boolean($('#reg-terms')?.checked),
        accept_privacy: Boolean($('#reg-terms')?.checked),
        marketing_opt_in: Boolean($('#reg-marketing')?.checked),
      }),
    });
    token = payload.data.token;
    localStorage.setItem(AUTH_KEY, token);
    await refreshSession();
    await loadServiceHealth();
    navigate('studio', { replace: true });
    showToast(`账号已创建，赠送 ${monthlyQuota} 积分`);
  } catch (error) {
    showToast(error.message, 'error');
  }
});

$('#auth-forgot-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const payload = await api('/api/auth/forgot-password', {
      method: 'POST',
      body: JSON.stringify({ email: $('#forgot-email').value.trim() }),
    });
    if (payload.data.reset_token) {
      $('#reset-token').value = payload.data.reset_token;
      $('#forgot-token-hint').textContent = payload.data.message;
    }
    showToast(payload.data.message);
  } catch (error) {
    showToast(error.message, 'error');
  }
});

$('#reset-password-button')?.addEventListener('click', async () => {
  try {
    await api('/api/auth/reset-password', {
      method: 'POST',
      body: JSON.stringify({
        email: $('#forgot-email').value.trim(),
        token: $('#reset-token').value.trim(),
        new_password: $('#reset-password').value,
      }),
    });
    showToast('密码已更新，请登录。');
    navigate('login', { replace: true });
  } catch (error) {
    showToast(error.message, 'error');
  }
});

$('#profile-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const payload = await api('/api/me', {
      method: 'PATCH',
      body: JSON.stringify({
        display_name: $('#profile-name').value.trim(),
        daw: $('#profile-daw').value.trim(),
        role_label: $('#profile-role').value.trim(),
      }),
    });
    currentUser = payload.data;
    render();
    showToast('资料已保存。');
  } catch (error) {
    showToast(error.message, 'error');
  }
});

$('#password-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/api/me/password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: $('#pw-current').value,
        new_password: $('#pw-new').value,
      }),
    });
    $('#password-form').reset();
    showToast('密码已更新。');
  } catch (error) {
    showToast(error.message, 'error');
  }
});

$('#project-status-filter')?.addEventListener('change', renderProjectCards);

document.addEventListener('keydown', (event) => {
  if (event.code !== 'Space') return;
  const tag = event.target.tagName;
  if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(tag) || event.target.isContentEditable) return;
  event.preventDefault();
  const node = getPlayer();
  if (!node.src) return;
  if (node.paused) node.play().catch(() => {});
  else {
    node.pause();
    playingAssetId = null;
    syncPlayUi();
  }
});

$('.workspace-switcher')?.addEventListener('click', () => {
  showToast('团队工作区是 P1 能力，当前仅个人工作区。');
});

$$('[data-auth-tab]').forEach((button) => {
  button.addEventListener('click', () => {
    navigate(button.dataset.authTab, { replace: true });
  });
});

bootstrapAuth();

document.addEventListener('input', (event) => {
  if (event.target.id === 'player-volume' && activeAudio) activeAudio.volume = Number(event.target.value);
  if (event.target.id === 'player-seek' && activeAudio?.duration) {
    activeAudio.currentTime = (Number(event.target.value) / 1000) * activeAudio.duration;
  }
});
