'use strict';
// No dependencies, remote assets, analytics or provider calls. All URLs are same-origin.
const $ = id => document.getElementById(id);
let messages = {}, language = localStorage.getItem('autoclip-caption-language') || 'zh';
let current = null, localDraft = null, dirty = false, sourceMode = false, rendered = false;
let sceneIndex = 0, pollTimer = null, captionEdits = new Map(), editorDirty = false;
let chapterState = null, chapterKey = null;
const t = key => messages[language]?.[key] || messages.zh?.[key] || key;
const time = value => Number(value).toFixed(3);
function text(tag, value) { const node = document.createElement(tag); node.textContent = value; return node; }
function friendly(code) {
  const labels = {analysis_range:'analysis_range',analysis_model_unavailable:'analysis_model_missing',analysis_model_changed:'analysis_model_missing',disk_full:'磁盘空间不足，请清理空间后重试。',no_audio:'视频没有音轨，无法按语音制作。',invalid_media:'文件已损坏，请重新下载或换一个文件。',model_unavailable:'暂时无法读取本地模型状态。',model_changed:'暂时无法读取本地模型状态。',cancelled:'任务已取消'};
  return labels[code] ? t(labels[code]) : `${t('失败')} · ${code} · ${t('重试')}`;
}
function fail(error) { $('error').textContent = friendly(error.message || String(error)); }
async function api(path, body, method = 'POST') {
  const headers = body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
  if (method !== 'GET') headers['X-Autoclip-Local'] = '1';
  const response = await fetch(path, { method, headers, body: method === 'GET' ? undefined : body instanceof FormData ? body : JSON.stringify(body || {}) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'invalid_request');
  return data;
}
function translate() {
  document.documentElement.lang = language;
  document.querySelectorAll('[data-t]').forEach(node => { node.textContent = t(node.dataset.t); });
}
function changed() { dirty = true; $('status').textContent = t('有修改未保存 · 本机暂存'); current.plan_hash = null; }
async function listProjects(selected) {
  const data = await api('/api/projects', null, 'GET');
  $('projects').replaceChildren(text('option', 'AutoClip · MP4'));
  $('projects').firstChild.value = '';
  for (const p of data.projects) { const option = text('option', p.name); option.value = p.id; $('projects').append(option); }
  if (selected) $('projects').value = selected;
  return data.projects;
}
function hasChanges() { return dirty || editorDirty; }
function mayLeave() { return !hasChanges() || window.confirm(t('discard_notice')); }
async function loadProject(pid) {
  if (!pid) return;
  current = await api(`/api/projects/${pid}`, null, 'GET');
  chapterState = null; chapterKey = null; $('smartStart').value = ''; $('smartEnd').value = '';
  $('chapters').replaceChildren(); $('smartStatus').textContent = '';
  localDraft = structuredClone(current.draft); dirty = false; captionEdits.clear(); editorDirty = false;
  $('mediaInfo').textContent = `${time(current.info.duration)} s · ${current.info.width}×${current.info.height} · AAC`;
  rendered = false; sourceMode = false; sceneIndex = 0;
  $('video').src = `/api/projects/${pid}/source`; $('workspace').hidden = false;
  $('aspect').value = localDraft.aspect; $('style').value = localDraft.subtitle_style;
  $('subtitles').checked = localDraft.subtitles; drawScenes(); updateTrack(); updateJobs();
}
function drawScenes() {
  $('scenes').replaceChildren();
  localDraft.scenes.forEach((scene, index) => {
    const row = document.createElement('div'); row.className = 'scene'; row.append(text('span', `${index + 1}`));
    if (scene.label?.startsWith('smart-')) row.append(text('small', t('role_' + scene.label.slice(6))));
    for (const key of ['start', 'end']) {
      if (key === 'end') row.append(text('span', '→'));
      const input = document.createElement('input'); input.type = 'number'; input.min = '0'; input.step = '.001'; input.value = time(scene[key]);
      input.setAttribute('aria-label', `${index + 1} ${key} (s)`);
      input.oninput = () => { scene[key] = Number(input.value); changed(); updateCaption(); }; row.append(input);
    }
    for (const [label, action] of [['上移', () => { if (index > 0) [localDraft.scenes[index - 1], localDraft.scenes[index]] = [scene, localDraft.scenes[index - 1]]; }], ['删除', () => { if (localDraft.scenes.length > 1) localDraft.scenes.splice(index, 1); }]]) {
      const b = text('button', label === '上移' ? '↑' : '×'); b.setAttribute('aria-label', `${t(label)} ${index + 1}`);
      b.disabled = label === '上移' ? index === 0 : localDraft.scenes.length === 1;
      b.onclick = () => { action(); changed(); drawScenes(); }; row.append(b);
    }
    $('scenes').append(row);
  });
}
async function updateTrack() {
  if (!current) return;
  $('stage').classList.toggle('portrait', localDraft.aspect === 'portrait');
  $('caption').className = `caption ${localDraft.subtitle_style}`;
  updateCaption();
}
function updateCaption() {
  if (!current) return;
  const v = $('video'), scene = localDraft.scenes[sceneIndex];
  const cues = sourceMode ? current.cues : (current.track?.source || []).filter(c => c.scene === sceneIndex);
  const cue = cues.find(c => c.start <= v.currentTime && v.currentTime < c.end);
  $('caption').textContent = !rendered && localDraft.subtitles && cue ? cue.text : '';
  $('clock').textContent = `${time(v.currentTime)} s${!sourceMode && scene ? ` · ${sceneIndex + 1}/${localDraft.scenes.length}` : ''}`;
}
function tick() {
  const v = $('video');
  if (current && !sourceMode && !rendered && !v.paused && !v.seeking) {
    const scene = localDraft.scenes[sceneIndex];
    if (scene && v.currentTime < scene.start - .1) v.currentTime = scene.start;
    if (scene && v.currentTime >= scene.end) {
      if (sceneIndex + 1 < localDraft.scenes.length) { sceneIndex++; v.currentTime = localDraft.scenes[sceneIndex].start; }
      else { v.pause(); v.currentTime = scene.end; }
    }
  }
  updateCaption(); requestAnimationFrame(tick);
}
async function savePlan() {
  if (editorDirty) throw new Error(`${t('保存')} · ${t('字幕')}`);
  const payload = { revision: current.revision, scenes: localDraft.scenes.map(s => ({ id: s.id, start: s.start, end: s.end, label: s.label || '片段' })), aspect: localDraft.aspect, subtitle_style: localDraft.subtitle_style, subtitles: localDraft.subtitles };
  current = await api(`/api/projects/${current.id}/plan`, payload);
  current = await api(`/api/projects/${current.id}`, null, 'GET');
  localDraft = structuredClone(current.draft); dirty = false; $('status').textContent = t('完成'); updateTrack();
}
function clockLabel(seconds) {
  const rounded = Math.floor(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, '0')}`;
}
function optionalSeconds(value) {
  const pieces = value.trim().split(':');
  if (!value.trim()) return null;
  if (pieces.length > 3 || !pieces.every(v => /^\d+(\.\d+)?$/.test(v))) throw new Error('analysis_range');
  return pieces.reduce((n, part) => 60 * n + Number(part), 0);
}
function drawChapters() {
  $('chapters').replaceChildren();
  if (!chapterState) return;
  const {chapters, failed_windows: failed, window_count: windows} = chapterState;
  $('smartStatus').textContent = `${chapters.length ? t('review_topics') + ' · ' + chapters.length : t('empty_topics')}${failed ? ` · ${t('partial_windows')} ${failed}/${windows}` : ''}`;
  const rows = new Map(current.cues.map((c, i) => [c.id, i]));
  for (const chapter of chapters) {
    const card = document.createElement('details'); card.className = 'chapter';
    const label = chapter.mode === 'hook_first' ? t('hook_first') : t('source_order');
    card.append(text('summary', `${chapter.id} · ${chapter.title} · ${clockLabel(chapter.start)}–${clockLabel(chapter.end)} · ${label}`));
    card.append(text('p', `${chapter.seconds.toFixed(1)} s · ${chapter.scenes.length} ${t('cuts')} · ${t('pauses')} ${chapter.removed_silence.length} · ${t('omissions')} ${chapter.dropped.length}`));
    if (chapter.warnings.length) card.append(text('p', t('review_context')));
    const list = document.createElement('ol');
    for (const [i, scene] of chapter.scenes.entries()) {
      const role = scene.role === 'close' && i !== chapter.scenes.length - 1 ? 'body' : scene.role;
      const item = text('li', `${t('role_' + role)} · ${time(scene.start)} → ${time(scene.end)} s`);
      item.append(text('p', current.cues.slice(rows.get(scene.first), rows.get(scene.last)+1).map(c => c.text).join(' ')));
      list.append(item);
    }
    card.append(list);
    const removed = [...chapter.dropped, ...chapter.removed_silence];
    if (removed.length) {
      const detail = document.createElement('details');
      detail.append(text('summary', t('omissions')));
      for (const range of removed) {
        detail.append(text('p', `${time(range.start)} → ${time(range.end)} s · ` + (range.first ?
          current.cues.slice(rows.get(range.first), rows.get(range.last)+1).map(c => c.text).join(' ') : t('pauses'))));
      }
      card.append(detail);
    }
    const use = text('button', t('use_topic'));
    use.disabled = current.jobs.some(j => ['queued','running'].includes(j.status));
    use.onclick = guarded(async () => {
      if (!mayLeave()) return;
      localDraft.scenes = chapter.scenes.map(({id, start, end, role}, i) => ({
        id, start, end, label: `smart-${role === 'close' && i !== chapter.scenes.length - 1 ? 'body' : role}`
      }));
      $('video').pause(); changed(); drawScenes(); await savePlan();
      $('video').scrollIntoView({block: 'center'}); await playDraft();
    });
    card.append(use); $('chapters').append(card);
  }
}
async function refreshChapters() {
  const project = current.id;
  const job = [...current.jobs].reverse().find(j => j.kind === 'analyze' && j.status === 'completed'
    && j.subtitle_sha256 === current.subtitle_sha256);
  if (!job) {
    if (chapterKey) { chapterKey = null; chapterState = null; $('chapters').replaceChildren(); $('smartStatus').textContent = ''; }
    return;
  }
  if (chapterKey === job.id) return;
  const result = await api(`/api/projects/${project}/analysis`, null, 'GET');
  if (current?.id !== project || current?.subtitle_sha256 !== job.subtitle_sha256) return;
  chapterKey = job.id; chapterState = result.analysis; drawChapters();
}
async function playDraft() {
  if (dirty || !current.track) await savePlan();
  rendered = false; sourceMode = false; sceneIndex = 0;
  const video = $('video'); video.src = `/api/projects/${current.id}/source`;
  video.currentTime = localDraft.scenes[0].start;
  await video.play();
}
function updateJobs() {
  const active = current.jobs.find(j => ['queued', 'running'].includes(j.status));
  $('settings').disabled = !!active || !current.cues.length;
  $('cancel').hidden = !active; $('retry').hidden = !!active || current.cues.length > 0;
  $('status').textContent = active ? `${active.kind === 'render' ? t('正在渲染') : t('字幕')}…` : (current.jobs.at(-1)?.error ? friendly(current.jobs.at(-1).error) : t('完成'));
  $('editTab').disabled = !current.cues.length || !!active;
  $('analyze').disabled = !current.cues.length || !!active;
  if (active?.kind === 'analyze') {
    const p = active.progress;
    $('smartStatus').textContent = `${t('analysis_running')}${p?.total ? ` · ${p.done}/${p.total}` : ''}`;
  } else {
    refreshChapters().catch(fail);
    const last = current.jobs.at(-1);
    if (last?.kind === 'analyze' && last.error) $('smartStatus').textContent = friendly(last.error);
  }
  for (const button of $('chapters').querySelectorAll('button')) button.disabled = !!active;
  $('exports').replaceChildren();
  for (const job of current.jobs.filter(j => j.kind === 'render' && j.status === 'completed')) {
    const group = document.createElement('div');
    const b = text('button', t('播放成片')); b.onclick = () => { rendered = true; sourceMode = false; $('video').src = `/api/projects/${current.id}/jobs/${job.id}/video`; $('video').play().catch(fail); }; group.append(b);
    for (const [kind, label] of [['video', 'MP4'], ['subtitles', 'SRT'], ['plan', 'JSON']]) {
      const link = text('a', `${t('下载')} ${label}`); link.href = `/api/projects/${current.id}/jobs/${job.id}/${kind}`; link.download = kind === 'video' ? 'autoclip-captioned.mp4' : kind === 'subtitles' ? 'captions.srt' : 'plan.json'; group.append(link);
    }
    $('exports').prepend(group);
  }
  clearInterval(pollTimer);
  if (active) pollTimer = setInterval(async () => {
    try {
      const id = current.id;
      const fresh = await api(`/api/projects/${id}`, null, 'GET');
      if (current?.id !== id) return;
      current = fresh; updateJobs(); updateTrack();
    }
    catch (e) { clearInterval(pollTimer); fail(e); }
  }, 1000);
}
async function importFile(file) {
  if (!file || !mayLeave()) return;
  $('error').textContent = ''; $('status').textContent = t('加载中');
  const body = new FormData(); body.append('video', file);
  try { const data = await api('/api/import', body); await listProjects(data.id); await loadProject(data.id); }
  catch (e) { fail(e); $('status').textContent = t('失败'); }
}
function drawCues() {
  $('cueList').replaceChildren(); let count = 0;
  const query = $('search').value.trim();
  for (const cue of current.cues) {
    if (query && !cue.text.includes(query)) continue;
    if (!query && !localDraft.scenes.some(s => cue.start < s.end && cue.end > s.start)) continue;
    if (++count > 150) break;
    const row = document.createElement('div'); row.className = 'cue-row';
    const seek = text('button', `${time(cue.start)}–${time(cue.end)} s`);
    seek.onclick = () => { sourceMode = true; rendered = false; $('video').src = `/api/projects/${current.id}/source`; $('video').currentTime = cue.start; closeEditor(); $('video').play().catch(fail); };
    const area = document.createElement('textarea'); area.value = captionEdits.get(cue.id) ?? cue.text;
    area.setAttribute('aria-label', `${t('字幕')} ${cue.id}`);
    area.oninput = () => { captionEdits.set(cue.id, area.value); editorDirty = true; $('editCount').textContent = `${captionEdits.size}`; };
    row.append(seek, area); $('cueList').append(row);
  }
}
function closeEditor() { if (!editorDirty || window.confirm(t('discard_notice'))) { editorDirty = false; captionEdits.clear(); $('editor').close(); } }
function guarded(fn) { return () => Promise.resolve().then(fn).catch(fail); }
async function start() {
  messages = await (await fetch('/assets/locales.json')).json();
  for (const code of Object.keys(messages)) { const option = text('option', code); option.value = code; $('language').append(option); }
  if (!messages[language]) language = 'zh'; $('language').value = language; translate();
  $('language').onchange = () => { language = $('language').value; localStorage.setItem('autoclip-caption-language', language); translate(); if (current) { drawScenes(); drawChapters(); } };
  $('cancel').hidden = true; $('retry').hidden = true;
  $('projects').onchange = guarded(async () => { if (mayLeave()) await loadProject($('projects').value); else $('projects').value = current?.id || ''; });
  $('file').onchange = () => importFile($('file').files[0]);
  document.addEventListener('dragover', e => { e.preventDefault(); $('importer').classList.add('dragging'); });
  document.addEventListener('dragleave', () => $('importer').classList.remove('dragging'));
  document.addEventListener('drop', e => { e.preventDefault(); $('importer').classList.remove('dragging'); importFile(e.dataTransfer.files[0]); });
  $('aspect').onchange = () => { localDraft.aspect = $('aspect').value; changed(); updateTrack(); };
  $('style').onchange = () => { localDraft.subtitle_style = $('style').value; changed(); updateTrack(); };
  $('subtitles').onchange = () => { localDraft.subtitles = $('subtitles').checked; changed(); updateCaption(); };
  $('addScene').onclick = () => { if (localDraft.scenes.length >= 30) return; localDraft.scenes.push({ id: `s${crypto.randomUUID().replaceAll('-', '')}`, start: 0, end: Math.min(10, current.info.duration) }); changed(); drawScenes(); };
  $('save').onclick = guarded(savePlan);
  $('play').onclick = guarded(playDraft);
  $('analyze').onclick = guarded(async () => {
    $('error').textContent = '';
    const scope = {start: optionalSeconds($('smartStart').value), end: optionalSeconds($('smartEnd').value)};
    await api(`/api/projects/${current.id}/analysis`, scope);
    current = await api(`/api/projects/${current.id}`, null, 'GET'); updateJobs();
  });
  $('original').onclick = () => { rendered = false; sourceMode = true; $('video').src = `/api/projects/${current.id}/source`; };
  $('export').onclick = guarded(async () => { await savePlan(); $('review').textContent = localDraft.scenes.map((s, i) => `${i + 1}. ${time(s.start)} → ${time(s.end)} s\n` + current.track.source.filter(c => c.scene === i).map(c => c.text).join('\n')).join('\n\n') + `\n\n${t('字幕压进画面')}: ${localDraft.subtitles}\n${localDraft.aspect}\nV${current.revision}\n${current.plan_hash}`; $('approval').showModal(); });
  $('approve').onclick = guarded(async () => { await api(`/api/projects/${current.id}/export`, { approved: true, plan_hash: current.plan_hash }); $('approval').close(); current = await api(`/api/projects/${current.id}`, null, 'GET'); updateJobs(); });
  $('closeApproval').onclick = () => $('approval').close();
  $('editTab').onclick = guarded(async () => { if (!current?.cues.length) return; if (dirty) await savePlan(); $('search').value = ''; drawCues(); $('editor').showModal(); });
  $('previewTab').onclick = () => $('video').scrollIntoView({ behavior: 'smooth', block: 'center' });
  $('closeEditor').onclick = closeEditor;
  $('editor').addEventListener('cancel', e => { e.preventDefault(); closeEditor(); });
  $('search').oninput = drawCues;
  $('saveCaptions').onclick = guarded(async () => { await api(`/api/projects/${current.id}/captions`, { revision: current.revision, edits: [...captionEdits].map(([id, text]) => ({ id, text })) }); editorDirty = false; captionEdits.clear(); $('editor').close(); await loadProject(current.id); });
  $('cancel').onclick = guarded(async () => { const job = current.jobs.find(j => ['queued', 'running'].includes(j.status)); await api(`/api/projects/${current.id}/jobs/${job.id}/cancel`); });
  $('retry').onclick = guarded(async () => { await api(`/api/projects/${current.id}/transcribe`); await loadProject(current.id); });
  window.addEventListener('beforeunload', e => { if (hasChanges()) { e.preventDefault(); e.returnValue = ''; } });
  $('video').addEventListener('seeked', updateCaption);
  $('video').addEventListener('loadedmetadata', () => { if (current && !sourceMode && !rendered) $('video').currentTime = localDraft.scenes[sceneIndex].start; });
  const projects = await listProjects(); if (projects.length) { $('projects').value = projects[0].id; await loadProject(projects[0].id); }
  requestAnimationFrame(tick);
}
start().catch(fail);
