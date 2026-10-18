/* Guide policy and persistence are independent of the stage and its characters. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./guide-state.js'));
  else root.GuideService = factory(root.GuideState);
})(typeof window === 'undefined' ? globalThis : window, function (GuideState) {
  'use strict';
  function create(content, saved, options = {}) {
    const state = GuideState.create(content, saved);
    const sameVersion = saved?.version === content.version;
    Object.assign(state, {
      schemaVersion: 2, guideVersion: content.version,
      seenVersion: saved?.seenVersion ?? null, dismissedVersion: saved?.dismissedVersion ?? null,
      suppressAuto: saved?.suppressAuto === true,
      lastBlockId: sameVersion ? saved?.lastBlockId || content.blocks.find(b => saved.blocks?.[b.id]?.status === 'active')?.id || null : null,
      skippedStepIds: sameVersion && Array.isArray(saved?.skippedStepIds) ? [...saved.skippedStepIds] : [],
      status: sameVersion && saved?.status === 'completed' || sameVersion && !saved?.schemaVersion && GuideState.allComplete(state) ? 'completed' : 'in_progress',
      previousVersions: {...(saved?.previousVersions || {})}
    });
    if (!sameVersion && saved?.version != null) {
      const {previousVersions, ...previous} = saved;
      state.previousVersions[String(saved.version)] = previous;
    }
    if (Object.values(state.blocks).every(b => b.status === 'unseen')) state.status = 'not_started';
    if (sameVersion && !saved?.schemaVersion && Object.values(state.blocks).some(b => b.status !== 'unseen')) state.seenVersion = content.version;
    // Legacy completion was global. Respect it for v1 without suppressing future versions.
    if (!saved && options.legacyCompleted && content.version === 1) state.seenVersion = content.version;
    let phase = 'closed', activeBlockId = null, replay = false, replayProgress = null;
    const find = id => content.blocks.find(b => b.id === id);
    const save = () => { options.persist?.(state); return state; };
    const canAutoOpen = () => phase === 'closed' && state.status !== 'completed' && !state.suppressAuto &&
      state.seenVersion !== content.version && state.dismissedVersion !== content.version;
    function enterBlock(id, replayRequested = false) {
      const block = find(id);
      if (!block) throw new RangeError('Unknown guide block');
      activeBlockId = id;
      replay = state.blocks[id].status === 'completed';
      if (replay) replayProgress = {status: 'active', step: block.first, line: 0, lineDone: false};
      else {
        state.lastBlockId = id;
        GuideState.enter(state, block, replayRequested);
        state.status = 'in_progress';
      }
      phase = 'opening'; save();
      return replay ? replayProgress : state.blocks[id];
    }
    function open({manual = true, blockId, replayConfirmed = false} = {}) {
      if (!manual && !canAutoOpen()) return {action: 'none'};
      const target = find(blockId) || (state.status === 'completed' ? content.blocks[0] :
        find(state.lastBlockId) || content.blocks.find(b => state.blocks[b.id].status !== 'completed') || content.blocks[0]);
      if ((state.status === 'completed' || state.blocks[target.id].status === 'completed') && !replayConfirmed) {
        phase = 'confirm_replay'; return {action: 'confirm_replay', blockId: target.id};
      }
      return {action: 'open', blockId: target.id, progress: enterBlock(target.id, replayConfirmed), replay};
    }
    function checkpoint(id, step, line, lineDone = false) {
      const block = find(id);
      if (!block || step < block.first || step > block.last) throw new RangeError('Step is outside its block');
      if (replay && id === activeBlockId) Object.assign(replayProgress, {step, line, lineDone});
      else if (state.blocks[id].status !== 'completed') {
        GuideState.checkpoint(state, block, step, line, lineDone); state.lastBlockId = id;
      }
      save();
    }
    function completeBlock(id) {
      const block = find(id);
      const next = replay ? content.blocks[content.blocks.indexOf(block) + 1] || null : GuideState.complete(state, content.blocks, block);
      if (!replay) state.lastBlockId = next?.id || id;
      save(); return next;
    }
    return {
      state, save, canAutoOpen, open, enterBlock, checkpoint, completeBlock,
      get phase() { return phase; }, get replaying() { return replay; },
      isCompleted: () => state.status === 'completed',
      allBlocksComplete: () => GuideState.allComplete(state),
      presented() { phase = 'playing'; state.seenVersion = content.version; save(); },
      close() { phase = 'closed'; state.dismissedVersion = content.version; save(); },
      skipAll() { phase = 'closed'; state.suppressAuto = true; save(); },
      skipStep(id, step) {
        const block = find(id);
        if (!block || step < block.first || step > block.last) throw new RangeError('Step is outside its block');
        if (!replay && !state.skippedStepIds.includes(step)) state.skippedStepIds.push(step);
        if (step < block.last) checkpoint(id, step + 1, 0);
        else save();
        return step < block.last ? step + 1 : null;
      },
      complete() {
        if (!GuideState.allComplete(state)) throw new Error('Guide still has unfinished blocks');
        state.status = 'completed'; phase = 'closed'; save();
      },
      setMuted(value) { state.muted = !!value; save(); }
    };
  }
  return {create};
});
