/* Deterministic research feedback. No clocks, DOM, network, or model calls. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CompanionState = api;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';
  const copy = {
    idle: ['一起研究', '从一个想法开始，留下判断的依据。', '这道题最不确定的地方是什么？', 'calm', 'curious'],
    reading: ['正在读题', '正在整理题目和解题路线。', '你的初步想法会保留在这个项目里。', 'analysis', 'curious'],
    thinking: ['正在讨论', '助教正在整理回复。', '我们要找的是能检验这个想法的依据。', 'analysis', 'curious'],
    running: ['正在验证', '代码正在运行，结果尚未确定。', '这次计算想检验哪个条件？', 'analysis', 'curious'],
    evidence_ok: ['验证记录已保存', '运行完成，已保存验证记录。', '运行成功不等于模型成立。这份结果支持你的想法吗？', 'calm', 'curious'],
    evidence_failed: ['这次验证未完成', '运行失败，记录已保留供排查，不能作为验证成功的依据。', '可以查看失败详情，再调整代码或输入。', 'warning', 'curious'],
    draft_ready: ['等待你的判断', '本步成果仍是草稿，确认权在你。', '依据、适用条件和限制都写清楚了吗？', 'calm', 'curious'],
    confirmed: ['本步已确认', '本步决定、依据和限制已保存。', '即使结果不支持最初的想法，也是一份有价值的研究记录。', 'approval', 'cheerful'],
    needs_review: ['有成果需要复核', '前面的决定发生变化，后续成果需要重新检查。', '新的条件会影响哪些依据？', 'warning', 'curious'],
    complete: ['研究材料已齐备', '各步成果已确认，可以整理写作材料。', '把依据和限制一起带进论文。', 'approval', 'proud'],
    offline: ['连接暂时中断', '暂时无法取得最新结果，请查看原操作的错误提示。', '已保存的研究记录仍可在项目中查看。', 'warning', 'curious']
  };
  function preferences(saved) {
    return {enabled: saved?.enabled !== false, motion: ['system', 'on', 'off'].includes(saved?.motion) ? saved.motion : 'system', read: typeof saved?.read === 'string' ? saved.read.slice(0, 160) : ''};
  }
  function create(snapshot = {}) { return {snapshot, operations: {}, notice: null, dismissedEvidence: null}; }
  function stepOf(snapshot) {
    const steps = snapshot.problem?.steps || [];
    return steps.find(s => s.id === snapshot.currentStepId) || steps[snapshot.progress?.cur_step] || steps[0];
  }
  function reduce(model, type, payload = {}) {
    if (payload.sessionId && model.snapshot.id && payload.sessionId !== model.snapshot.id) return model;
    if (type === 'sync') {
      const changed = payload.id !== model.snapshot.id || stepOf(payload)?.id !== stepOf(model.snapshot)?.id;
      if (changed) return create(payload);
      const stepId=stepOf(payload)?.id, previous=model.snapshot.outcomes?.[stepId], outcome=payload.outcomes?.[stepId];
      const evidence=(payload.evidence || []).filter(e=>e.step_id===stepId).at(-1);
      const outcomeChanged=outcome && JSON.stringify(outcome)!==JSON.stringify(previous);
      const evidenceChanged=evidence?.id!==(model.snapshot.evidence || []).filter(e=>e.step_id===stepId).at(-1)?.id;
      return {...model,snapshot:payload,notice:outcomeChanged || evidenceChanged ? null : model.notice,
        dismissedEvidence:outcomeChanged ? evidence?.id || null : model.dismissedEvidence};
    }
    if (type === 'reset') return create();
    if (type === 'start') {
      const evidence = (model.snapshot.evidence || []).filter(e => e.step_id === stepOf(model.snapshot)?.id).at(-1);
      return {...model, notice: null, dismissedEvidence: evidence?.id || null, operations: {...model.operations, [payload.id]: payload}};
    }
    if (type === 'finish') {
      if (!model.operations[payload.id]) return model; // Late completion after changing project/step.
      const operations = {...model.operations}; delete operations[payload.id];
      return {...model, operations, notice: payload.state ? payload : model.notice};
    }
    if (type === 'result') return {...model, notice: payload};
    if (type === 'acknowledge') return {...model, notice: null};
    return model;
  }
  function select(model) {
    const s = model.snapshot, steps = s.problem?.steps || [], step = stepOf(s), outcomes = s.outcomes || {};
    const review = steps.find(item => outcomes[item.id]?.status === 'needs_review');
    const outcome = outcomes[step?.id];
    const evidence = (s.evidence || []).filter(e => e.step_id === step?.id).at(-1);
    const operations = Object.values(model.operations);
    const busy = state => operations.some(op => op.state === state);
    const allConfirmed = steps.length > 0 && steps.every(item => outcomes[item.id]?.status === 'confirmed');
    const freshEvidence = evidence && !evidence.stale && evidence.id !== model.dismissedEvidence && (!outcome?.confirmed_at || evidence.created_at > outcome.confirmed_at);
    let state = 'idle', action = null;
    if (review) { state = 'needs_review'; action = {view: 'outcome', stepId: review.id, label: '查看待复核成果'}; }
    else if (model.notice?.state === 'evidence_failed') state = 'evidence_failed';
    else if (model.notice?.state === 'offline') state = 'offline';
    else if (busy('running')) state = 'running';
    else if (busy('reading')) state = 'reading';
    else if (busy('thinking')) state = 'thinking';
    else if (freshEvidence && evidence.status === 'failed') state = 'evidence_failed';
    else if (freshEvidence && evidence.status === 'success') state = 'evidence_ok';
    else if (model.notice?.state && copy[model.notice.state]) state = model.notice.state;
    else if (allConfirmed) state = 'complete';
    else if (outcome?.status === 'draft' && outcome.decision) state = 'draft_ready';
    else if (outcome?.status === 'confirmed') state = 'confirmed';
    const [title, alpha, xiaobei, alphaExpression, beiExpression] = copy[state];
    const result = {state, title, alpha, xiaobei, alphaExpression, beiExpression, action};
    if (review) result.alpha = `「${review.title}」需要复核：前面的决定已经修改。`;
    if (model.notice?.alpha && state === model.notice.state) result.alpha = model.notice.alpha;
    if (model.notice?.xiaobei && state === model.notice.state) result.xiaobei = model.notice.xiaobei;
    if (['evidence_ok','evidence_failed'].includes(state)) result.action = {view: 'verification', label: '查看验证记录'};
    if (['confirmed','draft_ready','complete'].includes(state)) result.action = {view: 'outcome', label: '查看本步成果'};
    return result;
  }
  function totals(items) {
    return items.reduce((r, item) => ({evidence: r.evidence + (item.successful_evidence_count || 0), confirmed: r.confirmed + (item.confirmed_count || 0), revised: r.revised + (item.revised_thought_count || 0), completed: r.completed + (item.project_complete ? 1 : 0)}), {evidence:0, confirmed:0, revised:0, completed:0});
  }
  function handoff(s = {}) {
    const steps = s.problem?.steps || [], current = stepOf(s), review = steps.find(step => s.outcomes?.[step.id]?.status === 'needs_review');
    const count = steps.filter(step => s.outcomes?.[step.id]?.status === 'confirmed').length;
    if (!steps.length) return {alpha:'带上题目和初步想法，我们从这里开始。', xiaobei:'还没想好也可以开始。'};
    return {alpha:`上次停在「${current?.title || '问题分析'}」，已确认 ${count}/${steps.length} 步。`, xiaobei:review ? `「${review.title}」需要复核，继续项目可查看。` : count === steps.length ? '各步材料已齐备，接下来可以整理成论文。' : `接下来继续「${current?.title || steps[0].title}」，保留你的依据和限制。`};
  }
  return {create, reduce, select, preferences, handoff, totals};
});
