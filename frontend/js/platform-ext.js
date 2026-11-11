/** Extended IA: parametric studio / tools / discover / mine / mv */

const TOOL_DEFS = [
  { id: 'ai_lyrics', title: 'AI 歌词', desc: '主题 / 风格写词', cost: 4, needsFile: false },
  { id: 'ai_cover', title: 'AI 翻唱', desc: '上传参考曲 + 歌词走 Music Cover', cost: 15, needsFile: true, cover: true },
  { id: 'separation', title: 'AI 分离', desc: '人声 / 伴奏 / 全轨', cost: 10, needsFile: true, sep: true },
  { id: 'music_to_midi', title: '转 MIDI', desc: '音频 → MIDI', cost: 20, needsFile: true },
  { id: 'midi_to_score', title: '转乐谱', desc: 'MIDI → MusicXML', cost: 20, needsFile: true },
  { id: 'music_analysis', title: '音乐解析', desc: '风格结构分析', cost: 40, needsFile: false },
  { id: 'improve', title: '音质提升', desc: '升采样 / 码率', cost: 10, needsFile: true },
  { id: 'denoise', title: '降噪', desc: '频谱降噪', cost: 10, needsFile: true },
  { id: 'crop', title: '剪辑', desc: '截取片段', cost: 10, needsFile: true, crop: true },
  { id: 'merge', title: '合并', desc: '两段拼接', cost: 10, needsFile: true, merge: true },
  { id: 'convert', title: '格式转换', desc: '转码导出', cost: 5, needsFile: true },
  { id: 'auto_lyrics', title: '局部改词重唱', desc: '改词后 Cover 重唱', cost: 15, needsFile: true, cover: true },
  { id: 'lrc', title: 'LRC 打轴', desc: '歌词时间轴编辑', cost: 0, local: 'lrc' },
  { id: 'multi_track', title: '多音轨', desc: '浏览器多轨编排', cost: 0, local: 'multitrack' },
  { id: 'piano', title: '电子钢琴', desc: '键盘演奏', cost: 0, local: 'piano' },
  { id: 'music_letter', title: '音乐信', desc: '写信寄歌', cost: 0, local: 'letter' },
];

const COST = { minimax: 10, suno: 20, mureka: 50, sfx: 4 };

export function bindPlatformExt(host) {
  const { api, $, $$, showToast, navigate, newIdempotencyKey, escapeHTML, allProjects, projectCard } = host;
  let agentKind = localStorage.getItem('studio-agent-kind') || 'general';
  const agentSessionByKind = {
    general: localStorage.getItem('studio-agent-session-general') || localStorage.getItem('studio-agent-session') || '',
    melody: localStorage.getItem('studio-agent-session-melody') || '',
    lyrics: localStorage.getItem('studio-agent-session-lyrics') || '',
  };
  let agentSessionId = agentSessionByKind[agentKind] || '';
  let selectedEngine = localStorage.getItem('studio-engine') || 'suno';
  let selectedMode = localStorage.getItem('studio-mode') || 'text';
  let activeTool = null;
  let uploadedKey = '';
  let uploadedKey2 = '';
  let mineTab = 'works';
  let discoverTab = 'tracks';
  let paramPoll = null;
  let promptLibrary = null;
  const templateCache = new Map();

  const AGENT_KIND_META = {
    general: {
      title: '对话式生成',
      desc: '综合助手：默认先出旋律，再填词。确认前不扣费。内置提示词库。',
      placeholder: '描述曲风、情绪或一句灵感…',
      quick: [
        ['先出一首温柔民谣纯音乐', '温柔民谣旋律'],
        ['电影感配乐，90 秒无人声', '电影配乐'],
        ['我想直接出带词人声', '带词人声'],
      ],
    },
    melody: {
      title: '旋律 Agent',
      desc: '专攻纯音乐旋律与编曲。出 Take 后可交给作词 Agent。',
      placeholder: '曲风 / 主奏乐器 / 情绪 / BPM…',
      quick: [
        ['木吉他民谣，温柔钩子，78BPM 纯音乐', '民谣钩子'],
        ['电子氛围铺底，有记忆点的主旋律', '电子氛围'],
        ['古风丝竹，空灵感，无人声', '古风丝竹'],
      ],
    },
    lyrics: {
      title: '作词 Agent',
      desc: '根据选定旋律写可唱短句歌词并贴人声。可从旋律 Agent 点「交给作词」。',
      placeholder: '想对谁唱？什么意象？或说明旋律 Take…',
      quick: [
        ['按这首旋律写对一个人倾诉的词', '倾诉填词'],
        ['短句留白，副歌好记，不要念词腔', '短句留白'],
        ['山野夜色意象，温柔克制', '山野意象'],
      ],
    },
  };

  async function ensurePromptLibrary() {
    if (promptLibrary) return promptLibrary;
    try {
      const payload = await api('/api/prompt-library');
      promptLibrary = payload.data || {};
      applyPromptLibraryToUI(promptLibrary);
      return promptLibrary;
    } catch (error) {
      const hint = $('#param-library-hint');
      if (hint) hint.textContent = `提示词库暂不可用：${error.message}`;
      return null;
    }
  }

  function fillTemplateSelect(selectEl, lib) {
    if (!selectEl || selectEl.dataset.ready === '1') return;
    const opts = ['<option value="">不使用模板</option>'].concat(
      (lib.templates || []).map((t) => `<option value="${escapeHTML(t.id)}">${escapeHTML(t.title || t.id)}</option>`)
    );
    selectEl.innerHTML = opts.join('');
    selectEl.dataset.ready = '1';
  }

  function renderStructureChips(rowId, lib) {
    const row = $(`#${rowId}`);
    if (!row || row.dataset.ready === '1') return;
    const tags = (lib.structure_tags || []).slice(0, 14);
    if (!tags.length) return;
    row.innerHTML = tags
      .map((t) => {
        const key = t.key || '';
        const title = t.explain || t.zh || key;
        return `<button class="choice-chip" type="button" data-insert-tag="${escapeHTML(key)}" title="${escapeHTML(title)}">${escapeHTML(key)}</button>`;
      })
      .join('');
    row.dataset.ready = '1';
  }

  function insertAtCursor(textarea, text) {
    if (!textarea || !text) return;
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    const before = textarea.value.slice(0, start);
    const after = textarea.value.slice(end);
    const needsNl = before.length && !before.endsWith('\n');
    const chunk = `${needsNl ? '\n' : ''}${text}\n`;
    textarea.value = `${before}${chunk}${after}`;
    const pos = before.length + chunk.length;
    textarea.focus();
    textarea.setSelectionRange(pos, pos);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function pickGenreChip(label, { rowSelector = '#param-genre-chips', multi = true } = {}) {
    const row = $(rowSelector);
    if (!row || !label) return;
    let chip = $$(`${rowSelector} .choice-chip:not(.add-chip)`).find(
      (b) => (b.dataset.genre || b.textContent.trim()) === label
    );
    if (!chip) {
      chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'choice-chip selected';
      chip.dataset.genre = label;
      chip.textContent = label;
      const add = $('.add-chip', row);
      if (add) row.insertBefore(chip, add);
      else row.appendChild(chip);
    } else if (multi) {
      chip.classList.add('selected');
    } else {
      $$(`${rowSelector} .choice-chip:not(.add-chip)`).forEach((b) => b.classList.remove('selected'));
      chip.classList.add('selected');
    }
  }

  let genreSearchTimer = null;
  async function runGenreSearch(query, resultsEl, { onPick } = {}) {
    if (!resultsEl) return;
    const q = (query || '').trim();
    if (q.length < 1) {
      resultsEl.hidden = true;
      resultsEl.innerHTML = '';
      return;
    }
    try {
      const payload = await api(`/api/prompt-library/genres?q=${encodeURIComponent(q)}&limit=12`);
      const items = payload.data || [];
      if (!items.length) {
        resultsEl.hidden = false;
        resultsEl.innerHTML = '<div class="genre-search-item"><span>无匹配</span><small>换个词试试</small></div>';
        return;
      }
      resultsEl.hidden = false;
      resultsEl.innerHTML = items
        .map((it) => {
          const en = it.en || '';
          const zh = it.zh || '';
          const label = zh && zh !== en ? `${en} · ${zh}` : en;
          return `<button type="button" class="genre-search-item" data-genre-pick="${escapeHTML(en)}" data-genre-zh="${escapeHTML(zh)}"><span>${escapeHTML(label)}</span><small>选用</small></button>`;
        })
        .join('');
      resultsEl.onclick = (event) => {
        const btn = event.target.closest('[data-genre-pick]');
        if (!btn) return;
        const en = btn.dataset.genrePick;
        const zh = btn.dataset.genreZh;
        onPick?.(en, zh);
        resultsEl.hidden = true;
        resultsEl.innerHTML = '';
      };
    } catch (error) {
      resultsEl.hidden = false;
      resultsEl.innerHTML = `<div class="genre-search-item"><span>${escapeHTML(error.message)}</span></div>`;
    }
  }

  function scheduleGenreSearch(inputEl, resultsEl, onPick) {
    if (genreSearchTimer) clearTimeout(genreSearchTimer);
    genreSearchTimer = setTimeout(() => runGenreSearch(inputEl?.value || '', resultsEl, { onPick }), 220);
  }

  function applyPromptLibraryToUI(lib) {
    if (!lib) return;
    fillTemplateSelect($('#param-template'), lib);
    fillTemplateSelect($('#editor-template'), lib);
    renderStructureChips('param-structure-chips', lib);
    renderStructureChips('editor-structure-chips', lib);

    const genreRow = $('#param-genre-chips');
    if (genreRow && lib.featured_genres?.length && genreRow.dataset.library !== '1') {
      const selected = new Set(
        $$('#param-genre-chips .choice-chip.selected').map((b) => b.dataset.genre || b.textContent.trim())
      );
      genreRow.innerHTML = lib.featured_genres
        .map((g, i) => {
          const label = g.label || (g.tags && g.tags[0]) || '风格';
          const on = selected.has(label) || (selected.size === 0 && i === 0);
          return `<button class="choice-chip${on ? ' selected' : ''}" type="button" data-genre="${escapeHTML(label)}">${escapeHTML(label)}</button>`;
        })
        .join('');
      genreRow.dataset.library = '1';
    }

    // Editor featured genres (keep add-chip)
    const editorGenres = $('#genre-chips');
    if (editorGenres && lib.featured_genres?.length && editorGenres.dataset.library !== '1') {
      const add = $('.add-chip', editorGenres);
      const selected = new Set(
        $$('#genre-chips .choice-chip:not(.add-chip).selected').map((b) => b.textContent.trim())
      );
      editorGenres.innerHTML = lib.featured_genres
        .map((g, i) => {
          const label = g.label || (g.tags && g.tags[0]) || '风格';
          const on = selected.has(label) || (selected.size === 0 && i === 0);
          return `<button class="choice-chip${on ? ' selected' : ''}" type="button">${escapeHTML(label)}</button>`;
        })
        .join('');
      if (add) editorGenres.appendChild(add);
      else {
        const plus = document.createElement('button');
        plus.type = 'button';
        plus.className = 'choice-chip add-chip';
        plus.textContent = '＋';
        editorGenres.appendChild(plus);
      }
      editorGenres.dataset.library = '1';
    }

    const quickFromLib = lib.agent_quick || {};
    Object.keys(AGENT_KIND_META).forEach((kind) => {
      const items = quickFromLib[kind];
      if (Array.isArray(items) && items.length) {
        AGENT_KIND_META[kind].quick = items.map((it) => [it.text, it.label || it.text]);
      }
    });
    syncAgentKindUI();
    const hint = $('#param-library-hint');
    if (hint) {
      const c = lib.counts || {};
      hint.textContent = `提示词库 v${lib.version || 1} · 模板 ${c.templates || 0} · 结构标签 ${c.structure_tags || 0} · 流派词典 ${c.genre_dictionary || 0}`;
    }
  }

  async function loadTemplate(templateId) {
    if (!templateId) return null;
    let tpl = templateCache.get(templateId);
    if (!tpl) {
      const payload = await api(`/api/prompt-library/templates/${encodeURIComponent(templateId)}`);
      tpl = payload.data;
      templateCache.set(templateId, tpl);
    }
    return tpl;
  }

  async function applyParamTemplate(templateId) {
    const desc = $('#param-desc');
    const tags = $('#param-tags');
    const title = $('#param-title');
    const tpl = await loadTemplate(templateId);
    if (!tpl) return;
    if (desc) desc.value = tpl.prompt || '';
    if (title && !title.value.trim()) title.value = tpl.title || '';
    if (tags) {
      const mentors = (promptLibrary?.mentor_styles || []).map((m) => m.tag).join(' ');
      tags.value = [tags.value, mentors].filter(Boolean).join(' ').trim();
    }
    showToast(`已载入模板：${tpl.title || templateId}`);
  }

  async function applyEditorTemplate(templateId) {
    const prompt = $('#prompt-input');
    const tpl = await loadTemplate(templateId);
    if (!tpl) return;
    if (prompt) {
      prompt.value = tpl.prompt || '';
      prompt.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (tpl.title) {
      const structure = $('#structure-input');
      if (structure && !structure.value.trim()) structure.value = 'Verse / Chorus / Bridge / Outro';
    }
    showToast(`工作台已载入：${tpl.title || templateId}`);
  }

  function getLibraryCreateExtras() {
    return {
      template_id: ($('#editor-template')?.value || $('#param-template')?.value || '').trim(),
      quality_boost: Boolean($('#editor-quality-boost')?.checked || $('#param-quality-boost')?.checked),
    };
  }

  function persistAgentSession(id, kind = agentKind) {
    agentSessionId = id || '';
    agentSessionByKind[kind] = agentSessionId;
    localStorage.setItem(`studio-agent-session-${kind}`, agentSessionId);
    localStorage.setItem('studio-agent-session', agentSessionId);
    localStorage.setItem('studio-agent-kind', kind);
  }

  function syncAgentKindUI() {
    const meta = AGENT_KIND_META[agentKind] || AGENT_KIND_META.general;
    const title = $('#agent-title');
    const desc = $('#agent-desc');
    const input = $('#agent-input');
    if (title) title.textContent = meta.title;
    if (desc) desc.textContent = meta.desc;
    if (input) input.placeholder = meta.placeholder;
    $$('#agent-kind-tabs [data-agent-kind]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.agentKind === agentKind);
    });
    const quick = $('#agent-quick');
    if (quick) {
      quick.innerHTML = meta.quick
        .map(
          ([text, label]) =>
            `<button class="choice-chip" type="button" data-agent-quick="${escapeHTML(text)}">${escapeHTML(label)}</button>`
        )
        .join('');
    }
  }

  host.getCreateContext = () => ({ engine: selectedEngine, mode: selectedMode, ...getLibraryCreateExtras() });
  window.__studioGetCreateContext = host.getCreateContext;

  function syncEngineTabs() {
    $$('[data-engine]').forEach((btn) => {
      const on = btn.dataset.engine === selectedEngine;
      btn.classList.toggle('active', on);
      btn.classList.toggle('selected', on);
    });
    $$('#mode-tabs [data-mode], #editor-mode-tabs [data-mode]').forEach((btn) => {
      const on = btn.dataset.mode === selectedMode;
      btn.classList.toggle('selected', on);
      btn.classList.toggle('active', on);
    });
    localStorage.setItem('studio-engine', selectedEngine);
    localStorage.setItem('studio-mode', selectedMode);
    const cost = selectedMode === 'sfx' ? COST.sfx : COST[selectedEngine] || 10;
    if ($('#param-cost-label')) $('#param-cost-label').textContent = `${cost} 积分 / 次`;
    if ($('#param-cost-strong')) $('#param-cost-strong').textContent = `${cost} 积分`;
    syncParamForm();
  }

  function syncParamForm() {
    const mode = selectedMode;
    const lyricsWrap = $('#param-lyrics-wrap');
    const tagsWrap = $('#param-tags-wrap');
    const fine = $('#param-fine-grid');
    const inst = $('#param-inst-wrap');
    const descLabel = $('#param-desc-label');
    if (lyricsWrap) lyricsWrap.hidden = !(mode === 'custom' || (selectedEngine === 'mureka' && mode !== 'sfx'));
    if (tagsWrap) tagsWrap.hidden = !(mode === 'custom' || selectedEngine === 'suno');
    if (fine) fine.hidden = mode !== 'parametric';
    if (inst) inst.hidden = mode === 'sfx';
    if (descLabel) {
      descLabel.textContent =
        mode === 'sfx' ? '音效描述（短）' : mode === 'custom' ? '风格补充' : mode === 'parametric' ? '创作描述' : '歌曲描述';
    }
    if ($('#parametric-hint')) {
      let hint = `当前 ${selectedEngine} · ${mode}。填表后点「立即生成」，右侧「我的创作」实时更新。`;
      if (selectedEngine === 'suno') {
        hint += ' 注意：Suno 需 RunningHub 全球站 Key（runninghub.ai）；CN Key 会返回开通提示。';
      }
      $('#parametric-hint').textContent = hint;
    }
  }

  async function renderCreations(targetId = 'param-creations') {
    const el = $(`#${targetId}`);
    if (!el) return;
    try {
      const payload = await api('/api/creations?limit=30');
      const rows = (payload.data || []).filter((a) => a.assetType === 'audio' || !a.assetType);
      el.innerHTML =
        rows
          .map(
            (a) => `<article class="creation-card" data-open-asset="${a.id}" data-project="${a.projectId || ''}">
          <strong>${escapeHTML(a.title || '未命名')}</strong>
          <small>${escapeHTML(a.originLabel || a.provider || '')} · ${escapeHTML(a.date || '')}</small>
          <div class="creation-actions">
            <button type="button" class="mini-action" data-play-asset="${a.id}">试听</button>
            <button type="button" class="mini-action" data-open-project-id="${a.projectId || ''}">打开</button>
            <button type="button" class="mini-action" data-toggle-public="${a.id}" data-public="${a.isPublic ? '1' : '0'}">${a.isPublic ? '已公开' : '公开'}</button>
          </div>
        </article>`
          )
          .join('') || '<div class="empty-block">还没有作品。左侧生成第一首吧。</div>';
    } catch (error) {
      el.innerHTML = `<div class="empty-block">${escapeHTML(error.message)}</div>`;
    }
  }

  async function paramGenerate() {
    const title = ($('#param-title')?.value || '').trim();
    const prompt = ($('#param-desc')?.value || '').trim();
    const lyrics = ($('#param-lyrics')?.value || '').trim();
    const tags = ($('#param-tags')?.value || '').trim();
    const templateId = ($('#param-template')?.value || '').trim();
    const qualityBoost = Boolean($('#param-quality-boost')?.checked);
    const instrumental = Boolean($('#param-instrumental')?.checked) || selectedMode === 'sfx';
    const genre = $$('#param-genre-chips .choice-chip.selected')
      .map((b) => b.dataset.genre)
      .filter(Boolean);
    if (prompt.length < 4) {
      showToast('请先写一点描述', 'error');
      return;
    }
    const btn = $('#param-generate');
    if (btn) btn.disabled = true;
    if ($('#param-job-status')) $('#param-job-status').textContent = '正在提交…';
    try {
      const payload = await api('/api/create/quick', {
        method: 'POST',
        body: JSON.stringify({
          title,
          prompt,
          lyrics,
          tags: tags || genre.join(', '),
          engine: selectedEngine,
          mode: selectedMode,
          instrumental: selectedMode === 'custom' && selectedEngine === 'suno' ? false : instrumental,
          bpm: Number($('#param-bpm')?.value || 90),
          key: $('#param-key')?.value || 'D minor',
          duration_seconds: Number($('#param-duration')?.value || 90),
          genre,
          template_id: templateId,
          quality_boost: qualityBoost,
          idempotency_key: newIdempotencyKey(),
        }),
      });
      showToast(`已入队，预留 ${payload.data.cost} 积分`);
      if ($('#param-job-status')) $('#param-job-status').textContent = `任务 ${payload.data.job.id} · ${payload.data.job.status}`;
      host.refreshSession?.();
      pollJob(payload.data.job.id);
      renderCreations();
    } catch (error) {
      showToast(error.message, 'error');
      if ($('#param-job-status')) $('#param-job-status').textContent = error.message;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function pollJob(jobId) {
    if (paramPoll) clearInterval(paramPoll);
    paramPoll = setInterval(async () => {
      try {
        const payload = await api(`/api/jobs/${jobId}`);
        const job = payload.data;
        if ($('#param-job-status')) {
          $('#param-job-status').textContent = `任务 ${job.id} · ${job.status}${job.error_message ? ` · ${job.error_message}` : ''}`;
        }
        if (['succeeded', 'failed', 'canceled'].includes(job.status)) {
          clearInterval(paramPoll);
          paramPoll = null;
          host.refreshSession?.();
          renderCreations();
          if (job.status === 'succeeded') showToast('生成完成');
        }
      } catch {
        clearInterval(paramPoll);
        paramPoll = null;
      }
    }, 2500);
  }

  function renderToolsGrid() {
    const grid = $('#tools-grid');
    if (!grid) return;
    grid.innerHTML = TOOL_DEFS.map(
      (tool) => `<button class="tool-card" type="button" data-open-tool="${tool.id}">
        <strong>${escapeHTML(tool.title)}</strong>
        <small>${escapeHTML(tool.desc)}</small>
        <span>${tool.cost ? `${tool.cost} 积分` : '免费'}</span>
      </button>`
    ).join('');
  }

  async function fillToolAssetSelects() {
    const sel = $('#tool-asset-select');
    const sel2 = $('#tool-asset-select2');
    try {
      const payload = await api('/api/creations?limit=50');
      const rows = (payload.data || []).filter((a) => {
        const t = a.assetType || 'audio';
        return ['audio', 'stem', 'midi'].includes(t) && (a.audioUrl || t === 'midi');
      });
      const opts =
        `<option value="">— 或不选，改用下方上传 —</option>` +
        rows.map((a) => `<option value="${a.id}">${escapeHTML(a.title || a.id)}</option>`).join('');
      if (sel) sel.innerHTML = opts;
      if (sel2) {
        sel2.innerHTML =
          `<option value="">— 可选第二段 —</option>` +
          rows.map((a) => `<option value="${a.id}">${escapeHTML(a.title || a.id)}</option>`).join('');
      }
    } catch {
      if (sel) sel.innerHTML = '<option value="">加载作品失败</option>';
    }
  }

  function openTool(id) {
    const tool = TOOL_DEFS.find((t) => t.id === id);
    if (!tool) return;
    if (tool.local) {
      mineTab = tool.local;
      navigate('mine');
      $$('#mine-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.mineTab === mineTab));
      renderMine();
      return;
    }
    activeTool = tool;
    uploadedKey = '';
    uploadedKey2 = '';
    $('#tools-grid').hidden = true;
    $('#tool-workbench').hidden = false;
    $('#tool-workbench-title').textContent = tool.title;
    $('#tool-workbench-desc').textContent = tool.desc;
    $('#tool-cost-label').textContent = tool.cost ? `${tool.cost} 积分 / 次` : '免费';
    $('#tool-result').textContent = '结果会出现在这里。';
    if ($('#tool-sep-wrap')) $('#tool-sep-wrap').hidden = !tool.sep;
    if ($('#tool-crop-wrap')) $('#tool-crop-wrap').hidden = !tool.crop;
    if ($('#tool-file2-wrap')) $('#tool-file2-wrap').hidden = !tool.merge;
    if ($('#tool-asset2-wrap')) $('#tool-asset2-wrap').hidden = !tool.merge;
    if ($('#tool-prompt-label')) {
      $('#tool-prompt-label').textContent = tool.cover || tool.id === 'ai_lyrics' ? '歌词 / 主题' : '说明';
    }
    if ($('#tool-file')) $('#tool-file').value = '';
    if ($('#tool-file2')) $('#tool-file2').value = '';
    fillToolAssetSelects();
    renderCreations('tool-history');
  }

  async function uploadFile(file) {
    const form = new FormData();
    form.append('file', file);
    const headers = {};
    if (host.token) headers.Authorization = `Bearer ${host.token}`;
    const res = await fetch(`${host.API_BASE}/api/uploads?purpose=tool`, { method: 'POST', headers, body: form });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.detail || '上传失败');
    return payload.data.storage_key;
  }

  async function runTool() {
    if (!activeTool) return;
    try {
      const assetId = ($('#tool-asset-select')?.value || '').trim() || null;
      const assetId2 = ($('#tool-asset-select2')?.value || '').trim() || null;
      const file = $('#tool-file')?.files?.[0];
      if (activeTool.needsFile && !file && !uploadedKey && !assetId) {
        showToast('请先选择已有作品或上传文件', 'error');
        return;
      }
      if (file) uploadedKey = await uploadFile(file);
      const file2 = $('#tool-file2')?.files?.[0];
      if (activeTool.merge && file2) uploadedKey2 = await uploadFile(file2);
      if (activeTool.merge && !assetId2 && !uploadedKey2 && !(assetId && uploadedKey)) {
        // still allow assetId + assetId2 or two uploads
      }
      if (activeTool.merge && !(assetId && assetId2) && !(uploadedKey && uploadedKey2) && !(assetId && uploadedKey2) && !(uploadedKey && assetId2)) {
        showToast('合并需要两段音频（作品或上传）', 'error');
        return;
      }
      const prompt = ($('#tool-prompt')?.value || activeTool.title).trim();
      const sepMode = $('#tool-sep-mode')?.value || 'vocal';
      const options = {};
      if (activeTool.crop) {
        options.start = Number($('#tool-crop-start')?.value || 0);
        options.end = Number($('#tool-crop-end')?.value || 15);
      }
      let backend = 'runninghub';
      if (sepMode === 'demucs') backend = 'demucs';
      const assetIds = [assetId, assetId2].filter(Boolean);
      let job;
      if (activeTool.cover) {
        if (prompt.length < 10) {
          showToast('翻唱需要至少 10 字歌词', 'error');
          return;
        }
        job = await api('/api/tools/cover', {
          method: 'POST',
          body: JSON.stringify({
            storage_key: uploadedKey || '',
            asset_id: assetId,
            lyrics: prompt.slice(0, 1000),
            prompt: 'Music Cover with new vocals, keep melody skeleton',
            title: activeTool.title,
            idempotency_key: newIdempotencyKey(),
          }),
        });
      } else {
        job = await api('/api/tools/jobs', {
          method: 'POST',
          body: JSON.stringify({
            tool: activeTool.id,
            prompt: prompt.length >= 8 ? prompt : `${activeTool.title} processing request`,
            storage_key: uploadedKey || '',
            storage_keys: uploadedKey2
              ? [uploadedKey || '', uploadedKey2].filter(Boolean)
              : uploadedKey
                ? [uploadedKey]
                : [],
            asset_id: assetId,
            asset_ids: assetIds,
            midi_key: uploadedKey || '',
            options,
            lyrics: prompt,
            sep_mode: sepMode === 'demucs' ? 'demucs' : sepMode,
            backend,
            idempotency_key: newIdempotencyKey(),
          }),
        });
      }
      $('#tool-result').innerHTML = `任务已提交：<code>${job.data.id}</code>（${job.data.status}）。完成后可在素材库 / 我的创作查看。`;
      showToast('工具任务已入队');
      host.refreshSession?.();
      pollTool(job.data.id);
    } catch (error) {
      showToast(error.message, 'error');
    }
  }

  function pollTool(jobId) {
    const timer = setInterval(async () => {
      try {
        const payload = await api(`/api/jobs/${jobId}`);
        const job = payload.data;
        if (['succeeded', 'failed', 'canceled'].includes(job.status)) {
          clearInterval(timer);
          $('#tool-result').textContent =
            job.status === 'succeeded'
              ? `完成。结果已写入工具箱项目。`
              : `失败：${job.error_message || job.status}`;
          renderCreations('tool-history');
          host.refreshSession?.();
        }
      } catch {
        clearInterval(timer);
      }
    }, 2500);
  }

  let agentStreaming = false;
  let agentJobPoll = null;
  let agentPendingFiles = [];
  const agentBlobUrls = new Set();

  function formatDuration(sec) {
    const n = Number(sec) || 0;
    if (n <= 0) return '';
    const m = Math.floor(n / 60);
    const s = Math.floor(n % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  function formatBytes(n) {
    const v = Number(n) || 0;
    if (v < 1024) return `${v} B`;
    if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
    return `${(v / 1024 / 1024).toFixed(1)} MB`;
  }

  function detectMediaKind(item = {}) {
    const explicit = String(item.preview || item.kind || '').toLowerCase();
    if (['audio', 'video', 'image', 'text', 'file'].includes(explicit)) return explicit;
    const type = String(item.assetType || item.type || '').toLowerCase();
    const fmt = String(item.format || item.filename || item.name || '').toLowerCase();
    const mime = String(item.mime || item.contentType || '').toLowerCase();
    if (type === 'mv' || mime.startsWith('video/') || /\.(mp4|webm|mov)$/.test(fmt) || ['mp4', 'webm', 'mov'].includes(fmt)) {
      return 'video';
    }
    if (mime.startsWith('image/') || /\.(png|jpe?g|gif|webp|svg)$/.test(fmt)) return 'image';
    if (type === 'lyrics' || type === 'analysis' || mime.startsWith('text/') || /\.(txt|lrc|json|xml|musicxml)$/.test(fmt) || ['txt', 'lrc', 'json', 'xml', 'musicxml'].includes(fmt)) {
      return 'text';
    }
    if (type === 'midi' || /\.(mid|midi)$/.test(fmt) || ['mid', 'midi'].includes(fmt)) return 'file';
    if (type === 'audio' || type === 'stem' || mime.startsWith('audio/') || /\.(mp3|wav|flac|ogg|m4a|aac)$/.test(fmt) || ['mp3', 'wav', 'flac', 'ogg', 'm4a', 'aac'].includes(fmt)) {
      return 'audio';
    }
    if (item.id || item.url || item.audioUrl) return 'audio';
    return 'file';
  }

  function revokeAgentBlobs() {
    agentBlobUrls.forEach((u) => URL.revokeObjectURL(u));
    agentBlobUrls.clear();
  }

  function renderMediaCards(items) {
    const rows = (items || []).filter((a) => a && (a.id || a.url || a.localUrl || a.blobUrl));
    if (!rows.length) return '';
    return `<div class="agent-media-list">${rows
      .map((a, index) => {
        const kind = detectMediaKind(a);
        const title = escapeHTML(a.title || a.filename || a.name || '附件');
        const meta = [formatDuration(a.duration), a.format || a.assetType || kind, formatBytes(a.fileSize || a.size)]
          .filter(Boolean)
          .join(' · ');
        const srcAttr = a.localUrl || a.blobUrl || '';
        const assetAttr = a.id ? `data-media-asset="${a.id}"` : '';
        const urlAttr = a.url || a.audioUrl ? `data-media-url="${escapeHTML(a.url || a.audioUrl)}"` : '';
        const canFill =
          a.id &&
          (a.instrumental === true ||
            a.origin === 'instrumental' ||
            a.melodyReady === true ||
            agentKind === 'melody' ||
            String(a.title || '').includes('纯音乐'));
        const fillBtn = canFill
          ? `<div class="agent-media-file-actions">
              <button type="button" class="mini-action agent-fill-lyrics" data-fill-lyrics-asset="${a.id}">本会话填词</button>
              <button type="button" class="mini-action" data-handoff-lyrics="${a.id}">交给作词 Agent</button>
            </div>`
          : '';
        if (kind === 'audio') {
          return `<div class="agent-media-card" data-media-kind="audio" ${assetAttr} ${urlAttr}>
            <div class="agent-media-head"><strong>${title}</strong><small>${escapeHTML(meta)}</small></div>
            <audio class="agent-media-el" controls preload="metadata" playsinline ${srcAttr ? `src="${srcAttr}"` : ''}></audio>
            ${fillBtn || ''}
          </div>`;
        }
        if (kind === 'video') {
          return `<div class="agent-media-card" data-media-kind="video" ${assetAttr} ${urlAttr}>
            <div class="agent-media-head"><strong>${title}</strong><small>${escapeHTML(meta)}</small></div>
            <video class="agent-media-el" controls preload="metadata" playsinline ${srcAttr ? `src="${srcAttr}"` : ''}></video>
          </div>`;
        }
        if (kind === 'image') {
          return `<div class="agent-media-card" data-media-kind="image" ${assetAttr} ${urlAttr}>
            <div class="agent-media-head"><strong>${title}</strong><small>${escapeHTML(meta)}</small></div>
            <img class="agent-media-el agent-media-image" alt="${title}" ${srcAttr ? `src="${srcAttr}"` : ''} />
          </div>`;
        }
        if (kind === 'text') {
          return `<div class="agent-media-card" data-media-kind="text" ${assetAttr} ${urlAttr}>
            <div class="agent-media-head"><strong>${title}</strong><small>${escapeHTML(meta)}</small></div>
            <pre class="agent-media-text" data-media-text>加载预览中…</pre>
          </div>`;
        }
        return `<div class="agent-media-card" data-media-kind="file" ${assetAttr} ${urlAttr}>
          <div class="agent-media-head"><strong>${title}</strong><small>${escapeHTML(meta)}</small></div>
          <div class="agent-media-file-actions">
            <button type="button" class="mini-action" data-download-media="${a.id || index}">下载 / 打开</button>
          </div>
        </div>`;
      })
      .join('')}</div>`;
  }

  function pauseOtherMedia(current) {
    document.querySelectorAll('audio, video').forEach((el) => {
      if (el !== current && !el.paused) {
        try {
          el.pause();
        } catch {
          /* ignore */
        }
      }
    });
  }

  function ensureExclusiveMediaPlayback() {
    if (document.documentElement.dataset.studioExclusiveMedia === '1') return;
    document.documentElement.dataset.studioExclusiveMedia = '1';
    document.addEventListener(
      'play',
      (event) => {
        const media = event.target;
        if (!(media instanceof HTMLMediaElement)) return;
        pauseOtherMedia(media);
      },
      true
    );
  }

  async function hydrateAgentMedia(root = $('#agent-chat')) {
    if (!root) return;
    ensureExclusiveMediaPlayback();
    const cards = [...root.querySelectorAll('.agent-media-card')];
    await Promise.all(
      cards.map(async (card) => {
        if (card.dataset.hydrated === '1') return;
        const kind = card.dataset.mediaKind;
        const assetId = card.dataset.mediaAsset;
        const mediaUrl = card.dataset.mediaUrl;
        const el = card.querySelector('.agent-media-el, [data-media-text]');
        if (!el) return;
        if (el.getAttribute('src')) {
          card.dataset.hydrated = '1';
          return;
        }
        const path = assetId ? `/api/assets/${assetId}/content` : mediaUrl;
        if (!path) return;
        try {
          const headers = {};
          if (host.token) headers.Authorization = `Bearer ${host.token}`;
          const res = await fetch(`${host.API_BASE}${path.startsWith('/') ? path : `/${path}`}`, { headers, cache: 'no-store' });
          if (!res.ok) throw new Error(`加载失败 ${res.status}`);
          if (kind === 'text') {
            const text = await res.text();
            el.textContent = text.slice(0, 8000) || '（空文件）';
          } else {
            const blob = await res.blob();
            const obj = URL.createObjectURL(blob);
            agentBlobUrls.add(obj);
            el.src = obj;
          }
          card.dataset.hydrated = '1';
        } catch (error) {
          if (kind === 'text') el.textContent = `预览失败：${error.message}`;
          else {
            const head = card.querySelector('.agent-media-head small');
            if (head) head.textContent = `加载失败：${error.message}`;
          }
        }
      })
    );
  }

  function paintAgentSession(session) {
    const box = $('#agent-chat');
    if (!box) return;
    revokeAgentBlobs();
    const fallbackAssets = session.result_assets || session.draft?.result_assets || [];
    box.innerHTML = (session.messages || [])
      .map((m) => {
        const assets = m.assets?.length
          ? m.assets
          : m.asset_id
            ? [{ id: m.asset_id, title: '生成结果' }]
            : m.attachments || [];
        return `<div class="agent-bubble ${m.role}">
          <div class="agent-bubble-text">${escapeHTML(m.content || '')}</div>
          ${renderMediaCards(assets)}
        </div>`;
      })
      .join('');
    // If ready but no message-level players yet, pin a player under the chat from draft/result
    if (session.state === 'ready' && fallbackAssets.length) {
      const hasInline = (session.messages || []).some((m) => m.assets?.length || m.asset_id);
      if (!hasInline) {
        box.insertAdjacentHTML(
          'beforeend',
          `<div class="agent-bubble assistant"><div class="agent-bubble-text">作品已就绪：</div>${renderMediaCards(fallbackAssets)}</div>`
        );
      }
    }
    box.scrollTop = box.scrollHeight;
    hydrateAgentMedia(box);
    const panel = $('#agent-confirm-panel');
    if (!panel) return;
    const draft = session.draft || {};
    if (session.state === 'awaiting_confirm' && draft.prompt) {
      const engineLabel = { minimax: 'MiniMax', suno: '苏诺', mureka: 'Mureka' }[draft.engine] || draft.engine || '';
      const modeLabel = { text: '文生', custom: '自定义', parametric: '精细参数' }[draft.mode] || draft.mode || '';
      const vocalLabel = draft.instrumental ? '纯音乐' : '含人声';
      const rawSummary = String(draft.summary || '');
      const letters = (rawSummary.match(/[A-Za-z]/g) || []).length;
      const cjk = (rawSummary.match(/[\u4e00-\u9fff]/g) || []).length;
      const summaryLooksEnglish = letters >= 24 && letters > cjk * 2;
      const summaryText = summaryLooksEnglish || !rawSummary.trim()
        ? `《${draft.title || '方案'}》；${engineLabel}·${modeLabel || '创作'}；${vocalLabel}`
        : rawSummary;
      panel.classList.remove('empty-block');
      panel.innerHTML = `<strong>${escapeHTML(draft.title || '方案')}</strong>
            <p>${escapeHTML(summaryText)}</p>
            <p class="field-hint">${escapeHTML(engineLabel)}${modeLabel ? ` · ${modeLabel}` : ''} · ${vocalLabel} · 预计 ${draft.estimated_credits || '?'} 积分</p>
            <button class="button button-primary" type="button" id="agent-confirm-btn">${
              agentKind === 'lyrics' || draft.workflow === 'lyrics_on_melody' ? '确认并填词贴人声' : agentKind === 'melody' ? '确认并生成旋律' : '确认并生成'
            }</button>`;
      $('#agent-confirm-btn')?.addEventListener('click', confirmAgent);
    } else if (session.state === 'ready' && (fallbackAssets.length || draft.result_asset_id)) {
      const melodyReady =
        draft.workflow_stage === 'melody_ready' ||
        draft.instrumental === true ||
        fallbackAssets.some((a) => a.instrumental || a.origin === 'instrumental');
      panel.classList.remove('empty-block');
      panel.innerHTML = `<strong>${melodyReady ? '旋律已就绪' : '已完成'}</strong>
            <p class="field-hint">${
              melodyReady
                ? '先试听选一首好听的 Take，再点「按这首旋律填词」。'
                : '可在对话中直接播放 / 预览，或打开项目查看详情。'
            }</p>
            ${renderMediaCards(
              fallbackAssets.map((a) =>
                melodyReady ? { ...a, instrumental: a.instrumental ?? true, origin: a.origin || 'instrumental' } : a
              )
            )}
            ${
              session.project_id
                ? `<button type="button" class="outline-button" id="agent-open-project" data-open-project-id="${session.project_id}">打开项目</button>`
                : ''
            }`;
      hydrateAgentMedia(panel);
    } else if (session.state === 'failed') {
      panel.classList.remove('empty-block');
      panel.innerHTML = `<strong>生成未成功</strong>
            <p class="field-hint">多半是苏诺节点不可用。可一键改用 MiniMax 重试，或继续对话改方案。</p>
            <button type="button" class="button button-primary" id="agent-retry-minimax">用 MiniMax 重试</button>`;
      $('#agent-retry-minimax')?.addEventListener('click', () => retryAgentWithMiniMax());
    } else {
      panel.className = 'agent-confirm empty-block';
      panel.textContent =
        session.state === 'generating'
          ? '正在生成，完成后会在对话中出现播放器…'
          : session.state === 'streaming'
            ? '助手正在回复…'
            : '确认方案会出现在这里。';
    }
  }

  async function retryAgentWithMiniMax() {
    if (!agentSessionId || agentStreaming) return;
    try {
      await api(`/api/agent/sessions/${agentSessionId}/retry`, {
        method: 'POST',
        body: JSON.stringify({ engine: 'minimax' }),
      });
      const conf = await api(`/api/agent/sessions/${agentSessionId}/confirm`, {
        method: 'POST',
        body: JSON.stringify({ idempotency_key: newIdempotencyKey() }),
      });
      showToast('已改用 MiniMax 重新生成');
      await renderAgentChat();
      host.refreshSession?.();
      if (conf.data?.job?.id) pollAgentJob(conf.data.job.id);
    } catch (error) {
      showToast(error.message || '重试失败', 'error');
      await renderAgentChat();
    }
  }

  async function fillLyricsOnMelody(assetId) {
    if (!agentSessionId || !assetId || agentStreaming) return;
    try {
      showToast('正在按旋律写词并贴人声…');
      const res = await api(`/api/agent/sessions/${agentSessionId}/fill-lyrics`, {
        method: 'POST',
        body: JSON.stringify({
          asset_id: assetId,
          idempotency_key: newIdempotencyKey(),
        }),
      });
      await renderAgentChat();
      host.refreshSession?.();
      if (res.data?.job?.id) pollAgentJob(res.data.job.id);
    } catch (error) {
      showToast(error.message || '填词失败', 'error');
      await renderAgentChat();
    }
  }

  function renderAttachPreview() {
    const wrap = $('#agent-attach-preview');
    if (!wrap) return;
    if (!agentPendingFiles.length) {
      wrap.hidden = true;
      wrap.innerHTML = '';
      return;
    }
    wrap.hidden = false;
    wrap.innerHTML = agentPendingFiles
      .map((f, i) => {
        const kind = detectMediaKind({ name: f.file.name, mime: f.file.type, localUrl: f.previewUrl });
        const body =
          kind === 'audio'
            ? `<audio controls preload="metadata" src="${f.previewUrl}"></audio>`
            : kind === 'video'
              ? `<video controls preload="metadata" src="${f.previewUrl}"></video>`
              : kind === 'image'
                ? `<img src="${f.previewUrl}" alt="${escapeHTML(f.file.name)}" />`
                : `<span class="agent-file-chip">${escapeHTML(f.file.name)} · ${formatBytes(f.file.size)}</span>`;
        return `<div class="agent-attach-item" data-attach-index="${i}">
          <div class="agent-attach-body">${body}</div>
          <button type="button" class="mini-action" data-remove-attach="${i}">移除</button>
        </div>`;
      })
      .join('');
  }

  function clearAgentAttachments() {
    agentPendingFiles.forEach((f) => URL.revokeObjectURL(f.previewUrl));
    agentPendingFiles = [];
    renderAttachPreview();
    if ($('#agent-file')) $('#agent-file').value = '';
  }

  async function renderAgentChat() {
    const box = $('#agent-chat');
    if (!box || !agentSessionId) {
      if (box) box.innerHTML = '<div class="empty-block">点击「新会话」开始对话式创作。</div>';
      return;
    }
    try {
      const payload = await api(`/api/agent/sessions/${agentSessionId}`);
      paintAgentSession(payload.data);
    } catch (error) {
      showToast(error.message, 'error');
    }
  }

  async function newAgentSession(extra = {}) {
    if (agentStreaming) return;
    const body = { kind: agentKind, ...extra };
    const payload = await api('/api/agent/sessions', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    persistAgentSession(payload.data.id, agentKind);
    syncAgentKindUI();
    await renderAgentChat();
    return payload.data;
  }

  async function switchAgentKind(nextKind) {
    if (!nextKind || nextKind === agentKind) return;
    if (agentStreaming) {
      showToast('请等当前回复结束再切换 Agent', 'error');
      return;
    }
    agentKind = nextKind;
    localStorage.setItem('studio-agent-kind', agentKind);
    agentSessionId = agentSessionByKind[agentKind] || '';
    syncAgentKindUI();
    clearAgentAttachments();
    if (!agentSessionId) await newAgentSession();
    else await renderAgentChat();
  }

  async function handoffToLyricsAgent(assetId) {
    if (!assetId || agentStreaming) return;
    agentKind = 'lyrics';
    localStorage.setItem('studio-agent-kind', agentKind);
    syncAgentKindUI();
    clearAgentAttachments();
    await newAgentSession({ melody_asset_id: assetId });
    showToast('已交给作词 Agent');
  }

  function setAgentComposerBusy(busy) {
    agentStreaming = busy;
    const input = $('#agent-input');
    const btn = $('#agent-form button[type="submit"]');
    if (input) input.disabled = busy;
    if (btn) {
      btn.disabled = busy;
      btn.textContent = busy ? '生成中…' : '发送';
    }
  }

  async function uploadAgentFile(file) {
    const form = new FormData();
    form.append('file', file);
    const headers = {};
    if (host.token) headers.Authorization = `Bearer ${host.token}`;
    const res = await fetch(`${host.API_BASE}/api/uploads?purpose=agent`, { method: 'POST', headers, body: form });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.detail || '上传失败');
    return payload.data;
  }

  async function sendAgent(text) {
    const content = (text || '').trim();
    if ((!content && !agentPendingFiles.length) || agentStreaming) return;
    if (!agentSessionId) await newAgentSession();
    const box = $('#agent-chat');
    if (!box) return;

    const pending = [...agentPendingFiles];
    const localCards = pending.map((f) => ({
      title: f.file.name,
      filename: f.file.name,
      mime: f.file.type,
      size: f.file.size,
      localUrl: f.previewUrl,
      preview: detectMediaKind({ name: f.file.name, mime: f.file.type }),
    }));
    const userText =
      content ||
      (pending.length ? `（已附上 ${pending.length} 个参考文件，请结合附件理解我的需求）` : '');

    // Optimistic user bubble + streaming assistant shell
    box.insertAdjacentHTML(
      'beforeend',
      `<div class="agent-bubble user"><div class="agent-bubble-text">${escapeHTML(userText)}</div>${renderMediaCards(localCards)}</div>
       <div class="agent-bubble assistant streaming" id="agent-stream-bubble"><div class="agent-stream-text"></div><span class="agent-caret" aria-hidden="true"></span></div>`
    );
    box.scrollTop = box.scrollHeight;
    const streamText = box.querySelector('#agent-stream-bubble .agent-stream-text');
    setAgentComposerBusy(true);
    clearAgentAttachments();

    try {
      const uploaded = [];
      for (const item of pending) {
        const up = await uploadAgentFile(item.file);
        uploaded.push({
          title: item.file.name,
          filename: item.file.name,
          mime: item.file.type,
          size: item.file.size,
          storage_key: up.storage_key,
          preview: detectMediaKind({ name: item.file.name, mime: item.file.type }),
        });
      }
      const payloadBody = {
        content:
          uploaded.length && content
            ? `${content}\n\n[附件] ${uploaded.map((u) => u.filename).join('、')}`
            : userText,
      };
      const headers = { 'Content-Type': 'application/json', Accept: 'text/event-stream' };
      if (host.token) headers.Authorization = `Bearer ${host.token}`;
      const res = await fetch(`${host.API_BASE}/api/agent/sessions/${agentSessionId}/messages/stream`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payloadBody),
        cache: 'no-store',
      });
      if (!res.ok) {
        let detail = `流式请求失败 (${res.status})`;
        try {
          const err = await res.json();
          detail = err.detail || detail;
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }
      const reader = res.body?.getReader();
      if (!reader) throw new Error('浏览器不支持流式读取');
      const decoder = new TextDecoder();
      let sseBuffer = '';
      let finalSession = null;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        sseBuffer += decoder.decode(value, { stream: true });
        const chunks = sseBuffer.split('\n\n');
        sseBuffer = chunks.pop() || '';
        for (const chunk of chunks) {
          const line = chunk
            .split('\n')
            .map((l) => l.trim())
            .find((l) => l.startsWith('data:'));
          if (!line) continue;
          let evt;
          try {
            evt = JSON.parse(line.slice(5).trim());
          } catch {
            continue;
          }
          if (evt.type === 'delta' && evt.text) {
            if (streamText) {
              streamText.textContent += evt.text;
              box.scrollTop = box.scrollHeight;
            }
          } else if (evt.type === 'assets' && Array.isArray(evt.assets) && evt.assets.length) {
            const bubble = $('#agent-stream-bubble');
            if (bubble && !bubble.querySelector('.agent-media-list')) {
              bubble.insertAdjacentHTML('beforeend', renderMediaCards(evt.assets));
              hydrateAgentMedia(bubble);
              box.scrollTop = box.scrollHeight;
            }
          } else if (evt.type === 'done' && evt.session) {
            finalSession = evt.session;
          } else if (evt.type === 'error') {
            throw new Error(evt.message || '流式回复失败');
          }
        }
      }
      if (finalSession) paintAgentSession(finalSession);
      else await renderAgentChat();
    } catch (error) {
      showToast(error.message, 'error');
      const bubble = $('#agent-stream-bubble');
      if (bubble) {
        bubble.classList.remove('streaming');
        const t = bubble.querySelector('.agent-stream-text');
        if (t && !t.textContent.trim()) t.textContent = `回复失败：${error.message}`;
      } else {
        await renderAgentChat();
      }
    } finally {
      setAgentComposerBusy(false);
    }
  }

  function pollAgentJob(jobId) {
    if (agentJobPoll) clearInterval(agentJobPoll);
    agentJobPoll = setInterval(async () => {
      try {
        const payload = await api(`/api/jobs/${jobId}`);
        const job = payload.data;
        if (['succeeded', 'failed', 'canceled'].includes(job.status)) {
          clearInterval(agentJobPoll);
          agentJobPoll = null;
          host.refreshSession?.();
          await renderAgentChat();
          if (job.status === 'succeeded') showToast('生成完成，可在对话中试听');
          else showToast(job.error_message || '生成失败', 'error');
        } else {
          const panel = $('#agent-confirm-panel');
          if (panel && panel.classList.contains('empty-block')) {
            panel.textContent = `正在生成… ${job.progress_stage || job.status}`;
          }
        }
      } catch {
        clearInterval(agentJobPoll);
        agentJobPoll = null;
      }
    }, 2500);
  }

  async function confirmAgent() {
    if (!agentSessionId) return;
    try {
      const payload = await api(`/api/agent/sessions/${agentSessionId}/confirm`, {
        method: 'POST',
        body: JSON.stringify({ idempotency_key: newIdempotencyKey() }),
      });
      showToast('已确认，任务已入队');
      await renderAgentChat();
      host.refreshSession?.();
      const jobId = payload.data?.job?.id;
      if (jobId) pollAgentJob(jobId);
    } catch (error) {
      showToast(error.message, 'error');
    }
  }

  async function renderDiscover() {
    const el = $('#discover-list');
    if (!el) return;
    if (discoverTab === 'mine') {
      await renderCreations('discover-list');
      return;
    }
    try {
      const payload = await api('/api/discover/plaza');
      if (discoverTab === 'albums') {
        const albums = payload.data?.albums || [];
        el.innerHTML =
          albums.map((a) => `<div class="library-row"><strong>${escapeHTML(a.title)}</strong><span>公开专辑</span></div>`).join('') ||
          '<div class="empty-block">还没有公开专辑。在「我的 → 专辑」创建并设为公开。</div>';
        return;
      }
      const tracks = payload.data?.tracks || [];
      el.innerHTML =
        tracks
          .map(
            (t) => `<div class="library-row">
            <div><strong>${escapeHTML(t.title)}</strong><div class="field-hint">${escapeHTML(t.id)}</div></div>
            <button type="button" class="mini-action" data-play-asset="${t.id}">试听</button>
          </div>`
          )
          .join('') || '<div class="empty-block">广场暂无公开曲。在「我的创作」点「公开」即可上架。</div>';
    } catch (error) {
      el.innerHTML = `<div class="empty-block">${escapeHTML(error.message)}</div>`;
    }
  }

  async function fillMvSelect() {
    const sel = $('#mv-asset-select');
    if (!sel) return;
    try {
      const payload = await api('/api/creations?limit=50');
      const rows = (payload.data || []).filter((a) => (a.assetType || 'audio') === 'audio');
      sel.innerHTML =
        `<option value="">选择一首歌</option>` +
        rows.map((a) => `<option value="${a.id}">${escapeHTML(a.title)}</option>`).join('');
    } catch {
      sel.innerHTML = '<option value="">加载失败</option>';
    }
  }

  async function renderMv() {
    await fillMvSelect();
    const el = $('#mv-list');
    if (!el) return;
    const payload = await api('/api/mv').catch(() => ({ data: [] }));
    el.innerHTML =
      (payload.data || [])
        .map((m) => `<div class="job-row"><strong>${escapeHTML(m.title)}</strong><span>${escapeHTML(m.status)}</span></div>`)
        .join('') || '<div class="empty-block">还没有 MV。</div>';
  }

  function renderLrcEditor() {
    const panel = $('#mine-panel');
    const assets = (host.allAssets?.() || []).filter((a) => a.format !== 'mid');
    panel.innerHTML = `<div class="field-grid">
      <label class="form-field"><span>选择作品</span><select id="lrc-asset">${assets
        .map((a) => `<option value="${a.id}">${escapeHTML(a.title)}</option>`)
        .join('')}</select></label>
      <label class="form-field full-field"><span>歌词（每行一句）</span><textarea id="lrc-text" rows="8" placeholder="第一句&#10;第二句"></textarea></label>
      <button class="button button-primary" type="button" id="lrc-stamp">按当前播放时间打点</button>
      <button class="outline-button" type="button" id="lrc-export">导出 LRC</button>
      <pre id="lrc-out" class="empty-block" style="white-space:pre-wrap;min-height:120px"></pre>
    </div>`;
    const lines = [];
    $('#lrc-stamp')?.addEventListener('click', () => {
      const audio = document.querySelector('audio');
      const t = audio?.currentTime || 0;
      const mm = String(Math.floor(t / 60)).padStart(2, '0');
      const ss = String(Math.floor(t % 60)).padStart(2, '0');
      const cs = String(Math.floor((t % 1) * 100)).padStart(2, '0');
      const text = ($('#lrc-text')?.value || '').split('\n').filter(Boolean);
      const next = text[lines.length] || `第 ${lines.length + 1} 句`;
      lines.push(`[${mm}:${ss}.${cs}]${next}`);
      $('#lrc-out').textContent = lines.join('\n');
    });
    $('#lrc-export')?.addEventListener('click', () => {
      const blob = new Blob([$('#lrc-out')?.textContent || ''], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'lyrics.lrc';
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  function renderMultiTrack() {
    const panel = $('#mine-panel');
    panel.innerHTML = `<div class="multitrack">
      <p class="field-hint">简易多音轨：添加本地音频轨，调节音量与静音后混合试听（浏览器 Web Audio）。</p>
      <button class="button button-primary" type="button" id="mt-add">添加音轨</button>
      <div id="mt-tracks" class="creation-list" style="margin-top:12px"></div>
      <button class="outline-button" type="button" id="mt-play">混合播放</button>
      <button class="outline-button" type="button" id="mt-stop">停止</button>
    </div>`;
    const tracks = [];
    let ctx;
    let nodes = [];
    $('#mt-add')?.addEventListener('click', () => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'audio/*';
      input.onchange = async () => {
        const file = input.files?.[0];
        if (!file) return;
        const url = URL.createObjectURL(file);
        tracks.push({ name: file.name, url, gain: 0.8, mute: false });
        $('#mt-tracks').innerHTML = tracks
          .map(
            (t, i) => `<div class="library-row"><strong>${escapeHTML(t.name)}</strong>
            <label>音量 <input type="range" min="0" max="1" step="0.05" value="${t.gain}" data-mt-gain="${i}" /></label>
            <label><input type="checkbox" data-mt-mute="${i}" /> 静音</label></div>`
          )
          .join('');
      };
      input.click();
    });
    panel.addEventListener('input', (e) => {
      const g = e.target.closest('[data-mt-gain]');
      if (g) tracks[Number(g.dataset.mtGain)].gain = Number(g.value);
      const m = e.target.closest('[data-mt-mute]');
      if (m) tracks[Number(m.dataset.mtMute)].mute = m.checked;
    });
    $('#mt-play')?.addEventListener('click', async () => {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      nodes.forEach((n) => {
        try {
          n.stop();
        } catch {
          /* ignore */
        }
      });
      nodes = [];
      for (const t of tracks) {
        if (t.mute) continue;
        const buf = await fetch(t.url).then((r) => r.arrayBuffer()).then((b) => ctx.decodeAudioData(b));
        const src = ctx.createBufferSource();
        const gain = ctx.createGain();
        gain.gain.value = t.gain;
        src.buffer = buf;
        src.connect(gain);
        gain.connect(ctx.destination);
        src.start();
        nodes.push(src);
      }
    });
    $('#mt-stop')?.addEventListener('click', () => {
      nodes.forEach((n) => {
        try {
          n.stop();
        } catch {
          /* ignore */
        }
      });
      nodes = [];
      ctx?.close();
    });
  }

  async function renderMine() {
    const panel = $('#mine-panel');
    if (!panel) return;
    if (mineTab === 'works') {
      const assets = host.allAssets?.() || [];
      panel.innerHTML =
        assets
          .slice(0, 50)
          .map(
            (a) => `<div class="library-row">
            <div><strong>${escapeHTML(a.title)}</strong><div class="field-hint">${escapeHTML(a.projectName || '')}</div></div>
            <div>
              <button type="button" class="mini-action" data-play-asset="${a.id}">试听</button>
              <button type="button" class="mini-action" data-toggle-public="${a.id}" data-public="${a.isPublic ? '1' : '0'}">${a.isPublic ? '已公开' : '公开'}</button>
            </div>
          </div>`
          )
          .join('') || '<div class="empty-block">还没有作品。</div>';
      return;
    }
    if (mineTab === 'lrc') return renderLrcEditor();
    if (mineTab === 'multitrack') return renderMultiTrack();
    if (mineTab === 'albums') {
      const payload = await api('/api/albums');
      panel.innerHTML = `<button class="button button-primary" type="button" id="album-create">创建专辑</button>
        <div class="library-list" style="margin-top:1rem">${(payload.data || [])
          .map((a) => `<div class="library-row"><strong>${escapeHTML(a.title)}</strong><span>${a.is_public ? '公开' : '私密'}</span></div>`)
          .join('') || '<div class="empty-block">暂无专辑</div>'}</div>`;
      $('#album-create')?.addEventListener('click', async () => {
        const title = prompt('专辑名称');
        if (!title) return;
        const pub = confirm('是否公开到广场？');
        await api('/api/albums', { method: 'POST', body: JSON.stringify({ title, is_public: pub }) });
        showToast('专辑已创建');
        renderMine();
      });
      return;
    }
    if (mineTab === 'stems') {
      const payload = await api('/api/stems');
      panel.innerHTML =
        (payload.data || [])
          .map((s) => `<div class="library-row"><strong>${escapeHTML(s.title)}</strong><span>${s.expired ? '已过期' : '7 天内'}</span></div>`)
          .join('') || '<div class="empty-block">暂无分轨。请到工具箱做 AI 分离。</div>';
      return;
    }
    if (mineTab === 'likes') {
      const payload = await api('/api/likes');
      panel.innerHTML =
        (payload.data || []).map((a) => `<div class="library-row"><strong>${escapeHTML(a.title)}</strong></div>`).join('') ||
        '<div class="empty-block">还没有喜欢。</div>';
      return;
    }
    if (mineTab === 'teams') {
      const payload = await api('/api/teams');
      panel.innerHTML = `<div class="field-grid">
        <button class="button button-primary" type="button" id="team-create">创建团队</button>
        <button class="outline-button" type="button" id="team-join">加入团队</button>
      </div>
      <div class="library-list" style="margin-top:1rem">${(payload.data || [])
        .map((t) => `<div class="library-row"><strong>${escapeHTML(t.name)}</strong><span>${escapeHTML(t.role)}${t.invite_code ? ` · ${t.invite_code}` : ''}</span></div>`)
        .join('') || '<div class="empty-block">暂无团队</div>'}</div>`;
      $('#team-create')?.addEventListener('click', async () => {
        const name = prompt('团队名称（2-20 字）');
        if (!name) return;
        const res = await api('/api/teams', { method: 'POST', body: JSON.stringify({ name }) });
        showToast(`已创建，邀请码 ${res.data.invite_code}`);
        renderMine();
      });
      $('#team-join')?.addEventListener('click', async () => {
        const invite_code = prompt('邀请码');
        if (!invite_code) return;
        await api('/api/teams/join', { method: 'POST', body: JSON.stringify({ invite_code }) });
        showToast('已加入团队');
        renderMine();
      });
      return;
    }
    if (mineTab === 'piano') {
      panel.innerHTML = `<div class="piano-wrap" id="piano-keys">${['C', 'D', 'E', 'F', 'G', 'A', 'B']
        .map((n, i) => `<button type="button" class="piano-key" data-freq="${261.63 * 2 ** (i / 12)}">${n}</button>`)
        .join('')}</div><p class="field-hint">点击琴键发声（Web Audio）。</p>`;
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      panel.querySelectorAll('[data-freq]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.frequency.value = Number(btn.dataset.freq);
          gain.gain.value = 0.15;
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start();
          gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.8);
          osc.stop(ctx.currentTime + 0.85);
        });
      });
      return;
    }
    if (mineTab === 'letter') {
      panel.innerHTML = `<label class="form-field"><span>收件人</span><input id="letter-to" /></label>
        <label class="form-field"><span>正文</span><textarea id="letter-body" maxlength="2000"></textarea></label>
        <button class="button button-primary" type="button" id="letter-send">预览寄信</button>
        <pre id="letter-preview" class="empty-block" style="margin-top:1rem;white-space:pre-wrap"></pre>`;
      $('#letter-send')?.addEventListener('click', () => {
        const to = $('#letter-to')?.value || '你';
        const body = $('#letter-body')?.value || '';
        $('#letter-preview').textContent = `致 ${to}：\n\n${body}\n\n—— 附上一首我用 Studio AI 写的歌`;
      });
    }
  }

  async function playAsset(id) {
    try {
      ensureExclusiveMediaPlayback();
      // Prefer in-chat player if present
      const inline = document.querySelector(`.agent-media-card[data-media-asset="${id}"] audio, .agent-media-card[data-media-asset="${id}"] video`);
      if (inline) {
        pauseOtherMedia(inline);
        await inline.play();
        showToast('正在试听');
        return;
      }
      const audio = new Audio(`${host.API_BASE}/api/assets/${id}/content`);
      if (host.token) {
        const res = await fetch(`${host.API_BASE}/api/assets/${id}/content`, {
          headers: { Authorization: `Bearer ${host.token}` },
        });
        if (!res.ok) throw new Error('无法播放');
        const blob = await res.blob();
        audio.src = URL.createObjectURL(blob);
      }
      pauseOtherMedia(audio);
      await audio.play();
      showToast('正在试听');
    } catch (error) {
      showToast(error.message || '播放失败', 'error');
    }
  }

  host.onPlatformView = async (view) => {
    if (view === 'parametric') {
      syncEngineTabs();
      renderCreations();
    }
    if (view === 'tools') {
      $('#tools-grid').hidden = false;
      $('#tool-workbench').hidden = true;
      renderToolsGrid();
    }
    if (view === 'agent') {
      syncAgentKindUI();
      if (!agentSessionId) await newAgentSession().catch(() => {});
      else await renderAgentChat();
    }
    if (view === 'discover') await renderDiscover();
    if (view === 'mv') await renderMv();
    if (view === 'mine') await renderMine();
    if (view === 'editor') syncEngineTabs();
  };
  window.__studioOnPlatformView = host.onPlatformView;

  document.addEventListener('click', (event) => {
    const engineBtn = event.target.closest('#engine-tabs [data-engine], #editor-engine-tabs [data-engine]');
    if (engineBtn?.dataset.engine) {
      selectedEngine = engineBtn.dataset.engine;
      syncEngineTabs();
    }
    const modeBtn = event.target.closest('#mode-tabs [data-mode], #editor-mode-tabs [data-mode]');
    if (modeBtn?.dataset.mode) {
      selectedMode = modeBtn.dataset.mode;
      syncEngineTabs();
    }
    const genreBtn = event.target.closest('#param-genre-chips [data-genre]');
    if (genreBtn) {
      genreBtn.classList.toggle('selected');
    }
    const insertBtn = event.target.closest('[data-insert-tag]');
    if (insertBtn?.dataset.insertTag) {
      const tag = insertBtn.dataset.insertTag;
      const inParam = Boolean(insertBtn.closest('#param-structure-chips'));
      const target = inParam ? $('#param-lyrics') : $('#lyrics-input');
      if (target) {
        if (inParam) {
          const wrap = $('#param-lyrics-wrap');
          if (wrap) wrap.hidden = false;
        } else {
          const adv = $('#advanced-fields');
          if (adv) adv.hidden = false;
        }
        insertAtCursor(target, tag);
      }
    }
    const toolBtn = event.target.closest('[data-open-tool]');
    if (toolBtn) openTool(toolBtn.dataset.openTool);
    const mineBtn = event.target.closest('[data-mine-tab]');
    if (mineBtn) {
      mineTab = mineBtn.dataset.mineTab;
      $$('#mine-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.mineTab === mineTab));
      renderMine();
    }
    const discBtn = event.target.closest('[data-discover-tab]');
    if (discBtn) {
      discoverTab = discBtn.dataset.discoverTab;
      $$('#discover-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.discoverTab === discoverTab));
      renderDiscover();
    }
    const quick = event.target.closest('[data-agent-quick]');
    if (quick) sendAgent(quick.dataset.agentQuick);
    const handoffBtn = event.target.closest('[data-handoff-lyrics]');
    if (handoffBtn?.dataset.handoffLyrics) {
      event.preventDefault();
      handoffToLyricsAgent(handoffBtn.dataset.handoffLyrics).catch((e) => showToast(e.message, 'error'));
      return;
    }
    const fillLyricsBtn = event.target.closest('[data-fill-lyrics-asset]');
    if (fillLyricsBtn?.dataset.fillLyricsAsset) {
      event.preventDefault();
      fillLyricsOnMelody(fillLyricsBtn.dataset.fillLyricsAsset);
      return;
    }
    const kindBtn = event.target.closest('#agent-kind-tabs [data-agent-kind]');
    if (kindBtn?.dataset.agentKind) {
      switchAgentKind(kindBtn.dataset.agentKind).catch((e) => showToast(e.message, 'error'));
      return;
    }
    const playBtn = event.target.closest('[data-play-asset]');
    if (playBtn) playAsset(playBtn.dataset.playAsset);
    const removeAttach = event.target.closest('[data-remove-attach]');
    if (removeAttach) {
      const idx = Number(removeAttach.dataset.removeAttach);
      const item = agentPendingFiles[idx];
      if (item) URL.revokeObjectURL(item.previewUrl);
      agentPendingFiles.splice(idx, 1);
      renderAttachPreview();
    }
    const downloadBtn = event.target.closest('[data-download-media]');
    if (downloadBtn) {
      const card = downloadBtn.closest('.agent-media-card');
      const assetId = card?.dataset.mediaAsset;
      const mediaUrl = card?.dataset.mediaUrl;
      const path = assetId ? `/api/assets/${assetId}/content` : mediaUrl;
      if (path) {
        fetch(`${host.API_BASE}${path.startsWith('/') ? path : `/${path}`}`, {
          headers: host.token ? { Authorization: `Bearer ${host.token}` } : {},
        })
          .then((r) => r.blob())
          .then((blob) => {
            const obj = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = obj;
            a.download = card?.querySelector('strong')?.textContent || 'download';
            a.click();
            setTimeout(() => URL.revokeObjectURL(obj), 2000);
          })
          .catch((e) => showToast(e.message || '下载失败', 'error'));
      }
    }
    const openProj = event.target.closest('[data-open-project-id]');
    if (openProj?.dataset.openProjectId) host.openProject?.(openProj.dataset.openProjectId);
    const pubBtn = event.target.closest('[data-toggle-public]');
    if (pubBtn) {
      const next = pubBtn.dataset.public !== '1';
      api(`/api/assets/${pubBtn.dataset.togglePublic}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_public: next }),
      })
        .then(() => {
          showToast(next ? '已公开到广场' : '已取消公开');
          renderCreations();
          renderMine();
        })
        .catch((e) => showToast(e.message, 'error'));
    }
  });

  $('#param-generate')?.addEventListener('click', () => paramGenerate());
  $('#agent-new-session')?.addEventListener('click', () => {
    clearAgentAttachments();
    newAgentSession().catch((e) => showToast(e.message, 'error'));
  });
  $('#agent-file')?.addEventListener('change', (event) => {
    const files = [...(event.target.files || [])];
    files.forEach((file) => {
      if (file.size > 200 * 1024 * 1024) {
        showToast(`${file.name} 超过 200MB`, 'error');
        return;
      }
      agentPendingFiles.push({ file, previewUrl: URL.createObjectURL(file) });
    });
    renderAttachPreview();
    event.target.value = '';
  });
  $('#agent-form')?.addEventListener('submit', (event) => {
    event.preventDefault();
    const input = $('#agent-input');
    const text = input?.value || '';
    if (input) input.value = '';
    sendAgent(text).catch((e) => showToast(e.message, 'error'));
  });
  $('#tool-back')?.addEventListener('click', () => {
    $('#tool-workbench').hidden = true;
    $('#tools-grid').hidden = false;
  });
  $('#tool-run')?.addEventListener('click', () => runTool());
  $('#mv-create')?.addEventListener('click', async () => {
    try {
      const asset_id = $('#mv-asset-select')?.value || $('#mv-asset-id')?.value || null;
      await api('/api/mv', {
        method: 'POST',
        body: JSON.stringify({
          asset_id: asset_id || null,
          title: $('#mv-title-input')?.value || '未命名 MV',
          lyrics: $('#mv-lyrics')?.value || '',
        }),
      });
      showToast('MV 任务已提交');
      renderMv();
    } catch (error) {
      showToast(error.message, 'error');
    }
  });

  $('#param-template')?.addEventListener('change', (event) => {
    const id = event.target.value;
    if (id) applyParamTemplate(id).catch((e) => showToast(e.message, 'error'));
  });
  $('#editor-template')?.addEventListener('change', (event) => {
    const id = event.target.value;
    if (id) applyEditorTemplate(id).catch((e) => showToast(e.message, 'error'));
  });
  $('#param-genre-search')?.addEventListener('input', (event) => {
    scheduleGenreSearch(event.target, $('#param-genre-results'), (en, zh) => {
      pickGenreChip(zh || en, { rowSelector: '#param-genre-chips' });
      const tags = $('#param-tags');
      if (tags) tags.value = [tags.value, en].filter(Boolean).join(', ').replace(/^,\s*/, '');
      if ($('#param-genre-search')) $('#param-genre-search').value = '';
      showToast(`已选用流派：${zh || en}`);
    });
  });
  $('#editor-genre-search')?.addEventListener('input', (event) => {
    scheduleGenreSearch(event.target, $('#editor-genre-results'), (en, zh) => {
      pickGenreChip(zh || en, { rowSelector: '#genre-chips', multi: true });
      if ($('#editor-genre-search')) $('#editor-genre-search').value = '';
      showToast(`已选用流派：${zh || en}`);
    });
  });

  host.getLibraryCreateExtras = getLibraryCreateExtras;
  window.__studioGetLibraryCreateExtras = getLibraryCreateExtras;

  ensureExclusiveMediaPlayback();
  syncEngineTabs();
  renderToolsGrid();
  ensurePromptLibrary().catch(() => {});
}
