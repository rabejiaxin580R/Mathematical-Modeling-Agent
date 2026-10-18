/* 本步论文草稿的专属整理区：整理与确认都在这里完成，不再占用聊天界面。
   数据接口复用做题工作台的 summarize / outcome；完成后给出「是否进入完整论文写作区」的决策点。 */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const sid = params.get('session_id') || '';
  const stepId = params.get('step_id') || '';
  let session = null;
  let step = null;
  let revision = 0;

  const STATUS_LABELS = { confirmed: '已确认', needs_review: '需要重新确认', draft: '待确认' };

  function setStatus(text, isError) {
    const node = $('draft-status');
    node.textContent = text || '';
    node.className = 'draft-status' + (isError ? ' error' : '');
  }
  function setError(text) { $('draft-error').textContent = text || ''; }
  function setHint(text) { $('draft-hint').textContent = text || ''; }
  function setBusy(on) {
    ['draft-summarize', 'draft-save', 'draft-confirm'].forEach((id) => {
      const button = $(id);
      if (button) button.disabled = !!on;
    });
  }

  async function request(url, method = 'GET', body) {
    const response = await fetch(url, {
      method,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail;
      const message = typeof detail === 'string' ? detail : detail?.message || '操作失败，请重试';
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function readFields() {
    return {
      decision: $('d-decision').value.trim(),
      reason: $('d-reason').value.trim(),
      basis: $('d-basis').value.trim(),
      limits: $('d-limits').value.trim(),
      verdict: $('d-verdict').value,
      not_applicable: $('d-na').checked,
      evidence_ids: [...$('draft-evidence').querySelectorAll('input[type=checkbox]:checked')]
        .map((input) => input.dataset.id),
    };
  }

  function paintGoal() {
    const goal = $('draft-goal');
    const items = Array.isArray(step.checklist) ? step.checklist : [];
    if (!step.objective && !items.length) { goal.hidden = true; return; }
    goal.hidden = false;
    goal.replaceChildren();
    const heading = document.createElement('h3');
    heading.textContent = '本步目标';
    goal.append(heading);
    if (step.objective || step.deliverable) {
      const text = document.createElement('p');
      text.textContent = step.objective || step.deliverable;
      goal.append(text);
    }
    if (items.length) {
      const list = document.createElement('ul');
      items.forEach((item) => {
        const li = document.createElement('li');
        li.textContent = item.text || item;
        list.append(li);
      });
      goal.append(list);
    }
  }

  function paintEvidence(outcome) {
    const box = $('draft-evidence');
    box.replaceChildren();
    const heading = document.createElement('p');
    heading.innerHTML = '<b>引用验证记录</b>';
    box.append(heading);
    const records = (session.evidence || []).filter((item) => item.step_id === stepId);
    if (!records.length) {
      const empty = document.createElement('p');
      empty.textContent = '本步还没有验证记录，可返回建模运行代码或记录推导。';
      box.append(empty);
      return;
    }
    const chosen = new Set(outcome.evidence_ids || []);
    records.forEach((record) => {
      const label = document.createElement('label');
      const box_input = document.createElement('input');
      box_input.type = 'checkbox';
      box_input.dataset.id = record.id;
      box_input.checked = chosen.has(record.id);
      const state = record.status === 'failed' ? '运行失败' : record.status === 'success' ? '已运行' : '已提供';
      label.append(box_input, document.createTextNode(` ${record.purpose || '验证记录'} · ${record.source || ''} · ${state}`));
      box.append(label);
    });
  }

  function paint() {
    const steps = session.problem?.steps || [];
    const index = steps.findIndex((item) => item.id === stepId);
    step = steps[index];
    const outcome = (session.outcomes || {})[stepId] || {};
    revision = session.revision || 0;
    $('draft-back').href = '/solve?session_id=' + encodeURIComponent(sid)
      + '&step_id=' + encodeURIComponent(stepId) + '&view=outcome';
    $('draft-step').textContent = `${session.title || '我的项目'} · 第 ${index + 1} 步 · ${step.title || ''}`;
    $('draft-state').textContent = `${STATUS_LABELS[outcome.status] || '尚未确认'} · 成果第 ${outcome.version || 0} 版`;
    $('d-decision').value = outcome.decision || '';
    $('d-reason').value = outcome.reason || '';
    $('d-basis').value = outcome.basis || '';
    $('d-limits').value = outcome.limits || '';
    $('d-verdict').value = outcome.verdict || 'pending';
    $('d-na').checked = !!outcome.not_applicable;
    paintGoal();
    paintEvidence(outcome);
  }

  async function summarize() {
    setError('');
    setBusy(true);
    setHint('正在根据本步实际讨论整理草稿，约需 10 秒…');
    try {
      const result = await request(
        `/api/solve/sessions/${encodeURIComponent(sid)}/steps/${encodeURIComponent(stepId)}/summarize`, 'POST', {});
      const draft = result.draft || {};
      $('d-decision').value = draft.decision || '';
      $('d-reason').value = draft.reason || '';
      $('d-basis').value = draft.basis || '';
      $('d-limits').value = draft.limits || '';
      setHint('草稿已整理，请检查后点「确认本步成果」。');
      setStatus('草稿已整理');
    } catch (error) {
      setError(error.message);
      setHint('');
      setStatus('整理未完成', true);
    } finally {
      setBusy(false);
    }
  }

  function showNext(confirmed) {
    $('draft-next-title').textContent = confirmed ? '本步草稿已整理并确认' : '本步草稿已保存';
    $('draft-next-note').textContent = '';
    $('draft-next').showModal();
  }

  async function submit(action) {
    setError('');
    setBusy(true);
    setStatus(action === 'confirm' ? '正在确认…' : '正在保存…');
    try {
      const saved = await request(
        `/api/solve/sessions/${encodeURIComponent(sid)}/steps/${encodeURIComponent(stepId)}/outcome`,
        'POST', { ...readFields(), expected_revision: revision, action });
      session = saved;
      paint();
      setStatus(action === 'confirm' ? '✓ 已确认本步' : '✓ 草稿已保存');
      showNext(action === 'confirm');
    } catch (error) {
      setError(error.message);
      setStatus('', true);
    } finally {
      setBusy(false);
    }
  }

  async function goToPaper() {
    const button = $('draft-goto-paper');
    button.disabled = true;
    $('draft-next-note').textContent = '正在准备完整论文写作区…';
    try {
      const created = await request('/api/paper/create', 'POST', { solve_session_id: sid });
      if (created?.paper_session_id) {
        location.href = '/outline?session_id=' + encodeURIComponent(created.paper_session_id);
        return;
      }
      $('draft-next-note').textContent = '暂时无法创建论文会话，请稍后重试。';
    } catch (error) {
      $('draft-next-note').textContent = error.message + '（确认完全部步骤后即可进入完整论文写作区。）';
    } finally {
      button.disabled = false;
    }
  }

  async function load() {
    if (!sid || !stepId) {
      setStatus('缺少项目或步骤参数，请从建模页的「本步成果」进入。', true);
      return;
    }
    try {
      session = await request('/api/solve/sessions/' + encodeURIComponent(sid));
    } catch (error) {
      setStatus(error.message, true);
      return;
    }
    const steps = session.problem?.steps || [];
    if (!steps.some((item) => item.id === stepId)) {
      setStatus('找不到这一步，建模路线可能已调整，请返回建模重新进入。', true);
      return;
    }
    paint();
    setStatus('');
  }

  $('draft-summarize').onclick = summarize;
  $('draft-save').onclick = () => submit('save');
  $('draft-confirm').onclick = () => submit('confirm');
  $('draft-stay').onclick = () => $('draft-next').close();
  $('draft-goto-paper').onclick = goToPaper;
  load();
})();
