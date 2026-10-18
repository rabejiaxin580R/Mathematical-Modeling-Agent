/* Pure progress transitions, shared by the browser and Node acceptance tests. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.GuideState = api;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';
  function create(content, saved) {
    const state = {version: content.version, muted: !!saved?.muted, blocks: {}};
    for (const block of content.blocks) {
      const old = saved?.version === content.version ? saved.blocks?.[block.id] : null;
      const step = Number.isInteger(old?.step) && old.step >= block.first && old.step <= block.last ? old.step : block.first;
      const lines = content.steps.find(s => s.id === step).lines.length;
      const line = Number.isInteger(old?.line) ? Math.max(0, Math.min(lines - 1, old.line)) : 0;
      const status = ['unseen', 'pending', 'active', 'completed'].includes(old?.status) ? old.status : 'unseen';
      state.blocks[block.id] = {status, step, line, lineDone: !!old?.lineDone};
    }
    return state;
  }
  function enter(state, block, replay = false) {
    const progress = state.blocks[block.id];
    if (replay || progress.status === 'completed') Object.assign(progress, {step: block.first, line: 0, lineDone: false});
    progress.status = 'active';
    return progress;
  }
  function checkpoint(state, block, step, line, lineDone = false) {
    if (step < block.first || step > block.last) throw new RangeError('Step is outside its block');
    Object.assign(state.blocks[block.id], {status: 'active', step, line, lineDone});
  }
  function complete(state, blocks, block) {
    Object.assign(state.blocks[block.id], {status: 'completed', step: block.last, line: 0, lineDone: false});
    const next = blocks.slice(blocks.findIndex(b => b.id === block.id) + 1)
      .find(b => state.blocks[b.id].status !== 'completed');
    if (next && state.blocks[next.id].status === 'unseen') state.blocks[next.id].status = 'pending';
    return next || null;
  }
  function match(blocks, pathname) { return blocks.find(b => b.routes.includes(pathname)); }
  function allComplete(state) { return Object.values(state.blocks).every(b => b.status === 'completed'); }
  return {create, enter, checkpoint, complete, match, allComplete};
});
