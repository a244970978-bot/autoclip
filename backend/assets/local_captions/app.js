'use strict';
// No dependencies, remote assets, analytics or provider calls. All URLs are same-origin.
const $ = id => document.getElementById(id);
let messages = {}, language = localStorage.getItem('autoclip-caption-language') || 'zh';
let current = null, localDraft = null, dirty = false, sourceMode = false, rendered = false;
let sceneIndex = 0, pollTimer = null, captionEdits = new Map(), editorDirty = false;
let chapterState = null, chapterKey = null;
let projectLoad = 0, startingOneClick = false, oneClickResult = null, oneClickRequest = null;
let activePreview = null, previewRequest = null, previewEnded = false, approvalTarget = null;
const t = key => messages[language]?.[key] || messages.zh?.[key] || key;
const time = value => Number(value).toFixed(3);
const isActive = job => ['queued', 'running'].includes(job?.status);
function text(tag, value) { const node = document.createElement(tag); node.textContent = value; return node; }
function friendly(code) {
  const labels = {analysis_range:'analysis_range',analysis_model_unavailable:'analysis_model_missing',analysis_model_changed:'analysis_model_missing',disk_full:'磁盘空间不足，请清理空间后重试。',no_audio:'视频没有音轨，无法按语音制作。',invalid_media:'文件已损坏，请重新下载或换一个文件。',model_unavailable:'暂时无法读取本地模型状态。',model_changed:'暂时无法读取本地模型状态。',cancelled:'任务已取消',analysis_no_cues:'one_click_no_cues',approval_required:'one_click_stale',revision_conflict:'one_click_stale'};
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
  document.querySelectorAll('[data-label]').forEach(node => { node.setAttribute('aria-label', t(node.dataset.label)); });
}
function changed() { clearPreview(); dirty = true; $('status').textContent = t('有修改未保存 · 本机暂存'); current.plan_hash = null; }
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
  const request = ++projectLoad;
  clearTimeout(pollTimer);
  const project = await api(`/api/projects/${pid}`, null, 'GET');
  if (request !== projectLoad) return;
  current = project;
  startingOneClick = false; oneClickRequest = null; oneClickResult = null; activePreview = null; previewRequest = null;
  approvalTarget = null; drawCandidateChoices();
  chapterState = null; chapterKey = null; $('smartStart').value = ''; $('smartEnd').value = '';
  $('chapters').replaceChildren(); $('smartStatus').textContent = '';
  resetDraftFields(); captionEdits.clear(); editorDirty = false;
  $('mediaInfo').textContent = `${time(current.info.duration)} s · ${current.info.width}×${current.info.height} · AAC`;
  rendered = false; sourceMode = false; sceneIndex = 0;
  $('video').src = `/api/projects/${pid}/source`; $('workspace').hidden = false;
  updateTrack(); updateJobs();
}
function resetDraftFields() {
  localDraft = structuredClone(current.draft); dirty = false;
  $('aspect').value = localDraft.aspect; $('style').value = localDraft.subtitle_style;
  $('subtitles').checked = localDraft.subtitles; drawScenes();
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
function showingPreview() { return !!activePreview && !sourceMode && !rendered; }
function viewedDraft() { return showingPreview() ? activePreview.draft : localDraft; }
function updatePreviewControls() {
  const v = $('video'), isPreview = showingPreview();
  v.controls = !isPreview;
  $('previewSeek').hidden = !isPreview;
  $('exportCandidate').hidden = !activePreview;
  $('exportCandidate').disabled = !!previewRequest || current?.jobs.some(isActive) || startingOneClick;
  $('play').textContent = activePreview ? t(isPreview && !v.paused ? 'one_click_pause' : 'one_click_play') : t('预览');
  for (const b of $('candidateList').querySelectorAll('button')) {
    b.setAttribute('aria-pressed', String(b.dataset.chapter === activePreview?.chapter_id));
  }
}
function showPlan(draft) {
  const v = $('video');
  v.pause(); rendered = false; sourceMode = false; sceneIndex = 0; previewEnded = false;
  const path = `/api/projects/${current.id}/source`;
  if (v.getAttribute('src') !== path) v.src = path;
  v.currentTime = draft.scenes[0].start;
  updateTrack();
}
function clearPreview() {
  previewRequest = null;
  if (!activePreview) { updatePreviewControls(); return; }
  const wasPlaying = showingPreview();
  activePreview = null;
  if (wasPlaying) showPlan(localDraft);
  else updateTrack();
}
function updateTrack() {
  if (!current) return;
  const draft = viewedDraft();
  $('stage').classList.toggle('portrait', draft.aspect === 'portrait');
  $('caption').className = `caption ${draft.subtitle_style}`;
  updatePreviewControls();
  updateCaption();
}
function updateCaption() {
  if (!current) return;
  const v = $('video'), isPreview = showingPreview(), draft = viewedDraft(), scene = draft.scenes[sceneIndex];
  const track = isPreview ? activePreview.track : current.track;
  const cues = sourceMode ? current.cues : (track?.source || []).filter(c => c.scene === sceneIndex);
  const cue = cues.find(c => c.start <= v.currentTime && v.currentTime < c.end);
  $('caption').textContent = !rendered && draft.subtitles && cue && (!isPreview || !previewEnded) ? cue.text : '';
  if (isPreview) {
    const timing = activePreview.timing, seek = $('previewSeek');
    const elapsed = previewEnded ? timing.at(-1) : timing[sceneIndex] + Math.min(scene.end - scene.start, Math.max(0, v.currentTime - scene.start));
    if (Number(seek.max) !== timing.at(-1)) seek.max = timing.at(-1);
    if (document.activeElement !== seek || !v.seeking) seek.value = elapsed;
    $('clock').textContent = `${clockLabel(elapsed)} / ${clockLabel(timing.at(-1))}`;
  } else {
    $('clock').textContent = `${time(v.currentTime)} s${!sourceMode && !rendered && scene ? ` · ${sceneIndex + 1}/${draft.scenes.length}` : ''}`;
  }
}
function tick() {
  const v = $('video');
  if (current && !sourceMode && !rendered && !v.paused && !v.seeking) {
    const scenes = viewedDraft().scenes, scene = scenes[sceneIndex];
    if (scene && v.currentTime < scene.start - .1) v.currentTime = scene.start;
    if (scene && v.currentTime >= scene.end) {
      if (sceneIndex + 1 < scenes.length) { sceneIndex++; v.currentTime = scenes[sceneIndex].start; }
      else { v.pause(); v.currentTime = scene.end; previewEnded = showingPreview(); }
    }
  }
  updateCaption(); requestAnimationFrame(tick);
}
function persistDraft(pid, revision, draft) {
  const payload = { revision, scenes: draft.scenes.map(s => ({ id: s.id, start: s.start, end: s.end, label: s.label || '片段' })), aspect: draft.aspect, subtitle_style: draft.subtitle_style, subtitles: draft.subtitles };
  return api(`/api/projects/${pid}/plan`, payload);
}
async function savePlan() {
  if (editorDirty) throw new Error(`${t('保存')} · ${t('字幕')}`);
  clearPreview();
  const id = current.id, load = projectLoad;
  await persistDraft(id, current.revision, localDraft);
  const fresh = await api(`/api/projects/${id}`, null, 'GET');
  if (projectLoad !== load) return;
  current = fresh;
  resetDraftFields(); $('status').textContent = t('完成'); updateTrack();
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
function latestOneClick() { return current.jobs.findLast(job => job.one_click === true); }
function drawCandidateChoices() {
  $('candidateList').replaceChildren();
  const choices = oneClickResult?.choices || [];
  $('candidatePicker').hidden = !choices.length;
  for (const [i, choice] of choices.entries()) {
    const button = text('button', `${i + 1}. ${choice.title}`);
    button.dataset.chapter = choice.id;
    button.append(text('small', `${choice.seconds.toFixed(1)} s · ${t('hook_first')}${i === 0 ? ' · ' + t('recommended_topic') : ''}`));
    button.onclick = guarded(() => selectCandidate(choice.id));
    $('candidateList').append(button);
  }
  updatePreviewControls();
}
async function selectCandidate(chapterId, automatic = false) {
  const result = oneClickResult;
  if (!result?.choices.some(choice => choice.id === chapterId)) return;
  const request = {};
  previewRequest = request;
  updatePreviewControls();
  const pid = current.id, subtitleHash = current.subtitle_sha256, revision = current.revision, load = projectLoad;
  const sameSelection = () => previewRequest === request && projectLoad === load && oneClickResult === result;
  const path = `/api/projects/${pid}/analysis/${result.jobId}/candidates/${encodeURIComponent(chapterId)}/preview`;
  $('error').textContent = '';
  try {
    const preview = await api(path, null, 'GET');
    if (!sameSelection()) return;
    if (current.revision !== revision || current.subtitle_sha256 !== subtitleHash || preview.revision !== revision
        || preview.job_id !== result.jobId || preview.chapter_id !== chapterId) throw new Error('revision_conflict');
    if (automatic && (hasChanges() || sourceMode || rendered || !$('video').paused || $('editor').open || $('approval').open)) return;
    const timing = [0];
    for (const scene of preview.draft.scenes) timing.push(timing.at(-1) + (scene.end - scene.start));
    activePreview = { ...preview, timing };
    showPlan(activePreview.draft);
  } catch (error) {
    if (!sameSelection()) return;
    if (!['not_found', 'revision_conflict'].includes(error.message)) throw error;
    // Another tab may have edited captions or saved a different draft. Refresh
    // the server view without discarding unsaved edits in this tab.
    const fresh = await api(`/api/projects/${pid}`, null, 'GET');
    if (!sameSelection()) return;
    const revisionChanged = fresh.revision !== current.revision;
    current = fresh;
    if (revisionChanged && !hasChanges()) resetDraftFields();
    updateJobs(); updateTrack();
    throw new Error('revision_conflict');
  } finally {
    if (previewRequest === request) { previewRequest = null; updatePreviewControls(); }
  }
}
async function loadOneClickResult(job) {
  const request = {};
  oneClickRequest = request;
  const pid = current.id, subtitleHash = current.subtitle_sha256;
  const stillCurrent = () => oneClickRequest === request && current?.id === pid
    && current.subtitle_sha256 === subtitleHash && latestOneClick()?.id === job.id;
  try {
    const { analysis } = await api(`/api/projects/${pid}/analysis?job_id=${job.id}`, null, 'GET');
    if (!stillCurrent()) return;
    if (analysis?.job_id !== job.id || !Array.isArray(analysis.previewable)) throw new Error('not_found');
    const byId = new Map(analysis.chapters.map(choice => [choice.id, choice]));
    oneClickResult = { jobId: job.id, choices: (analysis.previewable || []).map(id => byId.get(id)).filter(Boolean),
      failed: analysis.failed_windows, windows: analysis.window_count };
    drawCandidateChoices();
    if (oneClickResult.choices.length) await selectCandidate(oneClickResult.choices[0].id, true);
  } catch (error) {
    if (stillCurrent() && !oneClickResult) oneClickResult = {jobId: job.id, error: error.message};
    else if (stillCurrent()) fail(error);
  } finally {
    if (oneClickRequest === request) { oneClickRequest = null; updateOneClick(current.jobs.find(isActive)); }
  }
}
function updateOneClick(active) {
  const last = latestOneClick();
  $('oneClick').disabled = startingOneClick || !!active && (active.one_click === true || active.kind !== 'transcribe');
  if (activePreview && activePreview.revision !== current.revision) clearPreview();
  if (oneClickResult && (oneClickResult.jobId !== last?.id || last.subtitle_sha256 !== current.subtitle_sha256)) {
    oneClickResult = null; drawCandidateChoices();
  }
  let status = '';
  if (startingOneClick) status = t('加载中');
  else if (last?.one_click_error || last?.error) status = friendly(last.one_click_error || last.error);
  else if (isActive(last)) status = last.kind === 'transcribe' ? t('one_click_asr') : analysisProgress(last.progress);
  else if (last?.kind === 'analyze' && last.status === 'completed') {
    if (last.subtitle_sha256 !== current.subtitle_sha256) status = t('one_click_stale');
    else if (oneClickResult?.jobId === last.id) {
      status = oneClickResult.error ? friendly(oneClickResult.error) : oneClickResult.choices.length
        ? `${t('one_click_ready')} · ${oneClickResult.choices.length}` : t('one_click_no_hook');
      if (oneClickResult.failed) status += ` · ${t('partial_windows')} ${oneClickResult.failed}/${oneClickResult.windows}`;
    } else {
      status = t('加载中');
      if (!oneClickRequest) loadOneClickResult(last);
    }
  }
  if ($('oneClickStatus').textContent !== status) $('oneClickStatus').textContent = status;
  updatePreviewControls();
}
async function startOneClick() {
  if (startingOneClick || $('oneClick').disabled) return;
  const pid = current.id, load = projectLoad;
  $('error').textContent = '';
  startingOneClick = true; updateOneClick(current.jobs.find(isActive));
  try {
    await api(`/api/projects/${pid}/one-click`);
    if (projectLoad !== load) return;
    oneClickRequest = null; oneClickResult = null; clearPreview(); drawCandidateChoices();
    const fresh = await api(`/api/projects/${pid}`, null, 'GET');
    if (projectLoad === load) current = fresh;
  } finally {
    if (projectLoad === load) { startingOneClick = false; updateJobs(); }
  }
}
function seekPreview(e) {
  if (!showingPreview()) return;
  const { timing, draft } = activePreview, at = Number(e.target.value), v = $('video');
  sceneIndex = Math.min(draft.scenes.length - 1, timing.findLastIndex(offset => offset <= at));
  previewEnded = at >= timing.at(-1);
  if (previewEnded) v.pause();
  v.currentTime = draft.scenes[sceneIndex].start + at - timing[sceneIndex];
  updateCaption();
}
function drawChapters() {
  $('chapters').replaceChildren();
  if (!chapterState) return;
  const {chapters, failed_windows: failed, window_count: windows, recommended} = chapterState;
  $('smartStatus').textContent = `${chapters.length ? t('review_topics') + ' · ' + chapters.length : t('empty_topics')}${failed ? ` · ${t('partial_windows')} ${failed}/${windows}` : ''}`;
  const rows = new Map(current.cues.map((c, i) => [c.id, i]));
  for (const chapter of chapters) {
    const card = document.createElement('details'); card.className = 'chapter';
    const label = chapter.mode === 'hook_first' ? t('hook_first') : t('source_order');
    const picks = chapter.id === recommended ? ` · ${t('recommended_topic')}` : '';
    card.append(text('summary', `${chapter.id} · ${chapter.title} · ${clockLabel(chapter.start)}–${clockLabel(chapter.end)} · ${label}${picks}`));
    card.append(text('p', `${chapter.seconds.toFixed(1)} s · ${chapter.scenes.length} ${t('cuts')} · ${t('pauses')} ${chapter.removed_silence.length} · ${t('omissions')} ${chapter.dropped.length}`));
    if (chapter.editorial_review === 'passed') card.append(text('p', t('editorial_review_passed')));
    else if (chapter.editorial_review === 'needs_review') card.append(text('p', t('editorial_review_needed')));
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
        const reason = ['filler','repetition','digression'].includes(range.reason) ? `${t('omit_' + range.reason)} · ` : '';
        detail.append(text('p', `${time(range.start)} → ${time(range.end)} s · ` + (range.first ?
          reason + current.cues.slice(rows.get(range.first), rows.get(range.last)+1).map(c => c.text).join(' ') : t('pauses'))));
      }
      card.append(detail);
    }
    const use = text('button', t('use_topic'));
    use.disabled = current.jobs.some(j => ['queued','running'].includes(j.status));
    use.onclick = guarded(async () => {
      if (!mayLeave()) return;
      clearPreview();
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
  const result = await api(`/api/projects/${project}/analysis?job_id=${job.id}`, null, 'GET');
  if (current?.id !== project || current?.subtitle_sha256 !== job.subtitle_sha256) return;
  chapterKey = job.id; chapterState = result.analysis; drawChapters();
}
async function playDraft() {
  const video = $('video');
  if (activePreview) {
    if (activePreview.revision !== current.revision) { clearPreview(); throw new Error('revision_conflict'); }
    if (showingPreview() && !video.paused) { video.pause(); return; }
    if (!showingPreview() || previewEnded || video.ended) showPlan(activePreview.draft);
  } else {
    if (dirty || !current.track) await savePlan();
    showPlan(localDraft);
  }
  await video.play();
}
function analysisProgress(progress) {
  const phases = {audio:'analysis_audio', scan:'analysis_topics', topics:'analysis_topics', body:'analysis_body', hook:'analysis_hook', review:'analysis_review'};
  const phase = Object.hasOwn(phases, progress?.phase) ? phases[progress.phase] : 'analysis_running';
  const hasCount = Number.isInteger(progress?.done) && Number.isInteger(progress?.total) && progress.total > 0;
  return `${t(phase)}${hasCount ? ` · ${progress.done}/${progress.total}` : ''}`;
}
function updateJobs() {
  const active = current.jobs.find(isActive);
  $('settings').disabled = !!active || !current.cues.length;
  $('cancel').hidden = !active; $('retry').hidden = !!active || current.cues.length > 0;
  $('status').textContent = dirty ? t('有修改未保存 · 本机暂存') : active ? `${t(active.kind === 'render' ? '正在渲染' : active.kind === 'analyze' ? 'analysis_running' : '字幕')}…` : (current.jobs.at(-1)?.error ? friendly(current.jobs.at(-1).error) : t('完成'));
  updateOneClick(active);
  $('editTab').disabled = !current.cues.length || !!active;
  $('analyze').disabled = !current.cues.length || !!active;
  if (active?.kind === 'analyze') {
    $('smartStatus').textContent = analysisProgress(active.progress);
  } else {
    refreshChapters().catch(fail);
    const last = current.jobs.at(-1);
    if (last?.kind === 'analyze' && last.error) $('smartStatus').textContent = friendly(last.error);
  }
  for (const button of $('chapters').querySelectorAll('button')) button.disabled = !!active;
  $('exports').replaceChildren();
  for (const job of current.jobs.filter(j => j.kind === 'render' && j.status === 'completed')) {
    const group = document.createElement('div');
    const b = text('button', t('播放成片')); b.onclick = () => { rendered = true; sourceMode = false; $('video').src = `/api/projects/${current.id}/jobs/${job.id}/video`; updateTrack(); $('video').play().catch(fail); }; group.append(b);
    for (const [kind, label] of [['video', 'MP4'], ['subtitles', 'SRT'], ['plan', 'JSON']]) {
      const link = text('a', `${t('下载')} ${label}`); link.href = `/api/projects/${current.id}/jobs/${job.id}/${kind}`; link.download = kind === 'video' ? 'autoclip-captioned.mp4' : kind === 'subtitles' ? 'captions.srt' : 'plan.json'; group.append(link);
    }
    $('exports').prepend(group);
  }
  clearTimeout(pollTimer);
  if (active) pollTimer = setTimeout(async () => {
    const load = projectLoad, observed = current;
    try {
      const id = current.id;
      const fresh = await api(`/api/projects/${id}`, null, 'GET');
      if (projectLoad !== load || current !== observed) return;
      const draftChanged = current.revision !== fresh.revision;
      current = fresh;
      if (draftChanged && !hasChanges()) resetDraftFields();
      updateJobs(); updateTrack();
    }
    catch (e) { if (projectLoad === load) fail(e); }
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
    seek.onclick = () => { showOriginal(cue.start); closeEditor(); $('video').play().catch(fail); };
    const area = document.createElement('textarea'); area.value = captionEdits.get(cue.id) ?? cue.text;
    area.setAttribute('aria-label', `${t('字幕')} ${cue.id}`);
    area.oninput = () => { captionEdits.set(cue.id, area.value); editorDirty = true; $('editCount').textContent = `${captionEdits.size}`; };
    row.append(seek, area); $('cueList').append(row);
  }
}
function closeEditor() { if (!editorDirty || window.confirm(t('discard_notice'))) { editorDirty = false; captionEdits.clear(); $('editor').close(); } }
function guarded(fn) { return () => Promise.resolve().then(fn).catch(fail); }
function showOriginal(start = 0) {
  rendered = false; sourceMode = true;
  const v = $('video'), path = `/api/projects/${current.id}/source`;
  v.pause();
  if (v.getAttribute('src') !== path) v.src = path;
  v.currentTime = start; updateTrack();
}
function describeExport(draft, track, revision, hash) {
  return draft.scenes.map((s, i) => `${i + 1}. ${time(s.start)} → ${time(s.end)} s\n`
    + track.source.filter(c => c.scene === i).map(c => c.text).join('\n')).join('\n\n')
    + `\n\n${t('字幕压进画面')}: ${draft.subtitles}\n${draft.aspect}\nV${revision}${hash ? '\n' + hash : ''}`;
}
async function prepareExport(candidate) {
  if (candidate && (!activePreview || previewRequest || activePreview.revision !== current.revision)) throw new Error('revision_conflict');
  if (!candidate) await savePlan();
  const draft = candidate ? activePreview.draft : localDraft;
  const track = candidate ? activePreview.track : current.track;
  const hash = candidate ? null : current.plan_hash;
  approvalTarget = {pid: current.id, revision: current.revision, hash,
    preview: candidate ? activePreview : null};
  $('review').textContent = describeExport(draft, track, current.revision, hash);
  $('approvalError').textContent = '';
  $('approval').showModal();
}
async function approveExport() {
  const target = approvalTarget;
  if (!target || target.pid !== current.id || $('approve').disabled) return;
  $('approve').disabled = true; $('approvalError').textContent = '';
  try {
    if (target.preview && !target.hash) {
      if (target.revision !== current.revision || target.preview !== activePreview) throw new Error('revision_conflict');
      if (editorDirty || (dirty && !window.confirm(t('one_click_replace')))) return;
      // Selection and the review dialog are read-only. Save this exact draft only now.
      const saved = await persistDraft(target.pid, target.revision, target.preview.draft);
      target.hash = saved.plan_hash;
      current = { ...saved, track: target.preview.track };
      target.preview.revision = saved.revision;
      resetDraftFields(); updateTrack();
    }
    await api(`/api/projects/${target.pid}/export`, { approved: true, plan_hash: target.hash });
    $('approval').close();
    current = await api(`/api/projects/${target.pid}`, null, 'GET'); updateJobs();
  } catch (error) {
    if ($('approval').open) $('approvalError').textContent = friendly(error.message);
    else fail(error);
  } finally {
    $('approve').disabled = false;
  }
}
async function start() {
  messages = await (await fetch('/assets/locales.json')).json();
  for (const code of Object.keys(messages)) { const option = text('option', code); option.value = code; $('language').append(option); }
  if (!messages[language]) language = 'zh'; $('language').value = language; translate();
  $('language').onchange = () => { language = $('language').value; localStorage.setItem('autoclip-caption-language', language); translate(); if (current) { drawScenes(); drawChapters(); drawCandidateChoices(); updateJobs(); } };
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
  $('oneClick').onclick = guarded(startOneClick);
  $('previewSeek').oninput = seekPreview;
  $('analyze').onclick = guarded(async () => {
    $('error').textContent = '';
    const scope = {start: optionalSeconds($('smartStart').value), end: optionalSeconds($('smartEnd').value)};
    await api(`/api/projects/${current.id}/analysis`, scope);
    current = await api(`/api/projects/${current.id}`, null, 'GET'); updateJobs();
  });
  $('original').onclick = () => showOriginal();
  $('exportCandidate').onclick = guarded(() => prepareExport(true));
  $('export').onclick = guarded(() => prepareExport(false));
  $('approve').onclick = guarded(approveExport);
  $('closeApproval').onclick = () => { if (!$('approve').disabled) $('approval').close(); };
  $('approval').addEventListener('cancel', e => { if ($('approve').disabled) e.preventDefault(); });
  $('approval').addEventListener('close', () => { approvalTarget = null; });
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
  $('video').addEventListener('play', updatePreviewControls);
  $('video').addEventListener('pause', updatePreviewControls);
  $('video').addEventListener('ended', () => {
    if (showingPreview()) { previewEnded = true; updateCaption(); updatePreviewControls(); }
  });
  $('video').addEventListener('loadedmetadata', () => {
    const scene = current && viewedDraft().scenes[sceneIndex];
    if (scene && !sourceMode && !rendered) $('video').currentTime = scene.start;
  });
  const projects = await listProjects(); if (projects.length) { $('projects').value = projects[0].id; await loadProject(projects[0].id); }
  requestAnimationFrame(tick);
}
start().catch(fail);
