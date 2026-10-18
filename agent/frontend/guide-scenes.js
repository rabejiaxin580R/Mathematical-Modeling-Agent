(function () {
  'use strict';
  const scene = (page, targets, extra = {}) => ({page, targets, ...extra});
  window.GuideScenes = {
    1: scene('home', ['#home-feature']),
    2: scene('home', ['.ui-title-row', '#home-feature', '.ui-nav']),
    3: scene('solve', ['#sv-statement', '#sv-thought', '.sv-setup-actions']),
    4: scene('solve', ['#sv-roadmap', '#sv-bg-toggle']),
    5: scene('solve', ['#sv-idea', '#sv-chat', '.sv-composer']),
    6: scene('solve', ['[data-view="verification"]', '#sv-evidence'], {action: 'verification'}),
    7: scene('solve', ['[data-task="code"]', '.ws-editor', '#sv-editor-tools', '.wf-task-tabs'], {action: 'code'}),
    8: scene('solve', ['#sv-evidence .flow-record']),
    9: scene('solve', ['[data-view="outcome"]', '.outcome-note', '#flow-confirm'], {action: 'outcome'}),
    10: scene('solve', ['#guide-history', '#sv-roadmap', '#sv-to-paper']),
    11: scene('outline', ['#sectionsList', '.outline-source-details', '#focusSummary']),
    12: scene('outline', ['#focusSummary', '#focusFormulas', '#focusCitations', '#focusActions']),
    13: scene('paper', ['#statusBox', '#retryPaper']),
    14: scene('paper', ['#paper-toc', '#paper-preview', '.result-actions']),
    15: scene('outline', ['.ui-filters', '#historyList']),
    16: scene('learn', ['.learn-tabs', '#asm-path-banner', '.learn-controls', '#map']),
    17: scene('learn', ['#guide-concept', '#reader-body', '#reader-done'], {action: 'reader'}),
    18: scene('assessment', ['#view-intro', '#view-quiz']),
    19: scene('assessment', ['.asm-result-head', '.asm-weak', '.asm-review', '.asm-result-foot']),
    20: scene('practice', ['.ui-filters', '#view-list .pcard']),
    21: scene('practice', ['.ui-practice-steps', '.pstep-prompt', '.pstep-chat', '.pstep-assessbar']),
    22: scene('build', ['.ui-tool:first-child', '.ui-tool:last-child']),
    23: scene('workspace', ['.workspace-tabs', '#explorer-tree', '.ws-editor', '.ws-right']),
    24: scene('ide', ['.ide-editor', '.ide-top', '.ide-console']),
    25: scene('home', ['#guide-model-options', '#guide-model-fields', '#guide-role-options']),
    26: scene('home', ['.ui-title-row'])
  };
  // One group per Alpha sentence. Xiaobei keeps the final focus while adding detail.
  const focuses={
    1:[[0],[0]],2:[[0],[0,1],[2]],3:[[0,2],[1]],4:[[0],[0],[1]],5:[[0,1],[2]],
    6:[[0],[1]],7:[[0,1,2],[3],[3]],8:[[0],[0],[0]],9:[[1],[2]],10:[[0,1],[2]],
    11:[[0],[1,2]],12:[[0,3],[1,2,3]],13:[[0],[0,1]],14:[[0,1],[2]],15:[[1],[0,1]],
    16:[[0],[1,2,3]],17:[[0,1],[2]],18:[[0],[1]],19:[[0,1],[2,3]],20:[[1],[0,1]],
    21:[[0],[1,2],[3]],22:[[0],[0,1]],23:[[0,1],[2,3],[0]],24:[[0],[1,2],[1]],
    25:[[0],[0,1],[2]],26:[[0],[0]]
  };
  for(const [id,groups] of Object.entries(focuses))window.GuideScenes[id].focuses=groups;
})();
