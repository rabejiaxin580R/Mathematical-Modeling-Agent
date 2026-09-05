/* progress-log.js — 论文生成等待界面：可滚动日志 + 章节进度 + 心跳/卡住提示
 *
 * 两种可切换风格：
 *   quest     简短风（emoji + 一句话，逐行 rise-in）
 *   terminal  终端黑客风（深色等宽 + 彩色 tag + 光标）
 *
 * 用法：
 *   const ctl = GenLog.mount(document.getElementById('mount'), { total: 10, kind: 'paper' });
 *   ctl.push(ev);            // 传入后端 SSE 反序列化后的 event 对象
 *   ctl.setStyle('terminal');
 *   ctl.finish(ev); / ctl.fail(msg);
 *
 * 不依赖任何第三方库；不 shadow MMRender / app.js 的同名函数。
 */
(function () {
  'use strict';

  var SECTION_ZH = {
    restate: '问题重述', assume: '模型假设', notation: '符号说明',
    build: '模型建立', solve: '模型求解', analyze: '结果分析',
    sensitivity: '敏感性分析', evaluate: '模型评价', extend: '模型扩展',
    abstract: '摘要'
  };

  // 与后端 SECTION_ORDER[:9] 对齐：大纲 generate_outline 事件不带 section，按 current 推章节名
  var SECTION_ORDER = ['restate', 'assume', 'notation', 'build', 'solve', 'analyze', 'sensitivity', 'evaluate', 'extend', 'abstract'];

  function deriveSection(ev) {
    if (ev.section) return ev.section;
    if (ev.step === 'generate_outline' && ev.current) {
      return SECTION_ORDER[(ev.current - 1) % SECTION_ORDER.length] || '';
    }
    return '';
  }

  // 翻译层：key = progress.step 或事件 type。{...} 占位符从 event 填充。
  // quest / term 为字符串数组（多写几条随机轮换，避免单调）。
  var LINES = {
    create_session: {
      emoji: '📄', kind: 'info',
      quest: ['你提交了题目，助教铺开了稿纸…', '稿纸已铺开', '开工啦，助教接过你的题目'],
      term: ['[INFO]  create session', '[BOOT] 初始化会话…']
    },
    session_created: {
      emoji: '🆔', kind: 'info',
      quest: ['会话已建立，随时可以断点续传', '记下你的进度啦，随时能续传'],
      term: ['[INFO]  session created {session_id}', '[INFO]  session created']
    },
    session_reused: {
      emoji: '🔁', kind: 'info',
      quest: ['检测到之前的进度，接着上次继续', '上次没写完，接着肝'],
      term: ['[INFO]  session reused']
    },
    search_literature: {
      emoji: '🔍', kind: 'step',
      quest: ['你走进图书馆，助教在书架间翻找文献…', '在书架间翻找文献…', '文献大扫荡中'],
      term: ['[STEP]  search_literature', '[SCAN] 检索文献库…']
    },
    literature_found: {
      emoji: '📚', kind: 'info',
      quest: ['找到 {count} 篇相关文献', '翻到 {count} 本有用的'],
      term: ['[INFO]  literature_found: {count}', '[SCAN] 命中 {count} 篇文献']
    },
    literature_stored: {
      emoji: '💾', kind: 'info',
      quest: ['文献已归档入库', '收进抽屉，归档完毕'],
      term: ['[INFO]  literature_stored: {stored}']
    },
    prepare_context: {
      emoji: '🧩', kind: 'step',
      quest: ['正在整理文献要点…', '整理要点中…'],
      term: ['[STEP]  prepare_context']
    },
    init_literature: {
      emoji: '📖', kind: 'step',
      quest: ['正在初始化文献知识库…'],
      term: ['[STEP]  init_literature']
    },
    extract_content: {
      emoji: '🧠', kind: 'step',
      quest: ['正在提炼文献的关键结论…', '啃文献中，挑重点'],
      term: ['[STEP]  extract_content']
    },
    content_extracted: {
      emoji: '🧠', kind: 'info',
      quest: ['提炼出 {chunks} 个知识片段', '提炼出 {chunks} 个知识点'],
      term: ['[INFO]  content_extracted: {chunks}', '[PARSE] 提取要点 → {chunks} chunks']
    },
    gather_context: {
      emoji: '📋', kind: 'step',
      quest: ['正在收集你的做题记录…'],
      term: ['[STEP]  gather_context']
    },
    load_context: {
      emoji: '🧷', kind: 'step',
      quest: ['正在加载前序的符号、模型与结果…'],
      term: ['[STEP]  load_context']
    },
    kb_search: {
      emoji: '🔎', kind: 'step',
      quest: ['翻开了知识库的抽屉…'],
      term: ['[STEP]  kb_search']
    },
    literature_retrieve: {
      emoji: '📚', kind: 'step',
      quest: ['正在检索文献知识库…'],
      term: ['[STEP]  literature_retrieve']
    },
    stage1_test_data: {
      emoji: '🎲', kind: 'step',
      quest: ['正在生成测试数据…'],
      term: ['[STEP]  stage1_test_data']
    },
    stage2_results: {
      emoji: '✍️', kind: 'write',
      quest: ['笔尖落纸，正在写「{heading}」…'],
      term: ['[WRITE]  {section}']
    },
    llm_write: {
      emoji: '✍️', kind: 'write',
      quest: ['笔尖落纸，正在写「{heading}」…'],
      term: ['[WRITE]  {section}']
    },
    generate_outline: {
      emoji: '🗂️', kind: 'write',
      quest: ['第 {current}/{total} 节 · {section}：正在起草大纲', '第 {current}/{total} 节 · {section}：大纲起草中', '正在给「{section}」搭骨架'],
      term: ['[WRITE]  outline {current}/{total} {section}', '[WRITE]  outline {current}/{total} {section} ████░░']
    },
    generate_section: {
      emoji: '✍️', kind: 'write',
      quest: ['第 {current}/{total} 节 · {section}：正在撰写正文', '第 {current}/{total} 节 · {section}：笔尖落纸', '正在憋「{heading}」…', '「{heading}」写作中，请多指教', '这一节有点长，但值得'],
      term: ['[WRITE]  section {current}/{total} {section}', '[WRITE]  section {current}/{total} · {section} ████░░']
    },
    verify_warning: {
      emoji: '⚠️', kind: 'warn',
      quest: ['本章可能不完整（已保存，可稍后微调）'],
      term: ['[WARN]  verify_warning']
    },
    assemble: {
      emoji: '🧱', kind: 'step',
      quest: ['把散落的章节装订成册…', '拼装论文中…'],
      term: ['[STEP]  assemble', '[PACK] 拼装文档…']
    },
    export_docx: {
      emoji: '📤', kind: 'step',
      quest: ['正在给论文套上 Word 封皮…', '打包成 Word…'],
      term: ['[STEP]  export_docx']
    },
    // 事件级
    section_done: {
      emoji: '✅', kind: 'ok',
      quest: ['「{heading}」完成', '又过一关：「{heading}」', '这一节搞定！'],
      term: ['[OK]    {section} done', '[OK]  {section} committed']
    },
    section_error: {
      emoji: '❌', kind: 'err',
      quest: ['「{heading}」失败，继续下一节', '这节翻车了，先继续下一节'],
      term: ['[ERR]   {section} failed']
    },
    outline_ready: {
      emoji: '🎉', kind: 'done',
      quest: ['大纲完成！', '大纲出炉！'],
      term: ['[DONE]  outline_ready']
    },
    complete: {
      emoji: '🎉', kind: 'done',
      quest: ['论文新鲜出炉！', '大功告成！'],
      term: ['[DONE]  complete', '[DONE] 论文落盘，链路 closed']
    },
    error: {
      emoji: '💥', kind: 'err',
      quest: ['出错了：{message}'],
      term: ['[ERR]   {message}']
    }
  };

  // 长等待消遣库（软预警期间，每 ~20s 随机滚一条；quest + term 成对，{section}/{done}/{total} 可替换）
  var INTERLUDES = [
    // 随机访客
    { emoji: '🦖', quest: '一只恐龙路过，看了一眼说「这很硬核」', term: '[SYS]  dinosaur.sight() → 一只恐龙路过…' },
    { emoji: '🦖', quest: '恐龙探头：写完了吗？我等到灭绝都还没完', term: '[SYS]  dinosaur.waiting() …' },
    { emoji: '🐦', quest: '一只小鸟落在窗口，陪你把这一节写完', term: '[SYS]  bird.land() → 窗口' },
    { emoji: '🐦', quest: '小鸟叽叽喳喳：加油呀，就差一点点啦', term: '[SYS]  bird.cheer()' },
    { emoji: '🐱', quest: '一只猫踩过键盘：喵（帮你打了 0 个字）', term: '[SYS]  cat.type() → 0 chars' },
    { emoji: '🦄', quest: '一只独角兽路过，给你的模型撒了点灵感', term: '[SYS]  unicorn.inspire()' },
    { emoji: '🐢', quest: '一只乌龟驮着进度条慢慢爬过来', term: '[SYS]  turtle.progress()' },
    { emoji: '🐸', quest: '一只青蛙跳进来：呱，「{section}」快好啦', term: '[SYS]  frog.ribbit() → {section}' },
    // 数学冷笑话
    { emoji: '❄️', quest: '你知道吗：无限个数学家进酒吧，第一个要一杯、第二个要半杯、第三个要四分之一杯…酒保直接倒了整整两杯', term: '[FUN]  无限个数学家进酒吧…酒保倒了整整两杯' },
    { emoji: '❄️', quest: '你知道吗：平行线为什么不合？因为它们没有交集', term: '[FUN]  平行线没有交集' },
    { emoji: '❄️', quest: '你知道吗：为什么 6 怕 7？因为 7 8 9（seven ate nine）', term: '[FUN]  why 6 afraid of 7? 7 8 9' },
    { emoji: '❄️', quest: '你知道吗：0 和 8 谁更帅？8 更帅，因为它系了腰带', term: '[FUN]  8 比 0 帅，因为它系了腰带' },
    { emoji: '❄️', quest: '你知道吗：圆周率为什么不告白？怕被看穿它无限不循环的心', term: '[FUN]  π 无限不循环，不敢告白' },
    { emoji: '❄️', quest: '你知道吗：sin 和 cos 为什么总在一起？因为它们互补', term: '[FUN]  sin & cos 互补' },
    // 数学冷知识
    { emoji: '💡', quest: '你知道吗：蜂巢是正六边形——用最少的蜡围出最大的面积', term: '[TIP]  蜂巢是正六边形（最省蜡）' },
    { emoji: '💡', quest: '你知道吗：向日葵种子按斐波那契螺旋排列（约 34/55 圈）', term: '[TIP]  向日葵种子呈斐波那契螺旋' },
    { emoji: '💡', quest: '你知道吗：正态分布的钟形曲线，其实是「误差的误差」堆出来的', term: '[TIP]  正态分布 = 误差的堆积' },
    { emoji: '💡', quest: '你知道吗：七桥问题，诞生于哥尼斯堡的一次散步', term: '[TIP]  七桥问题源于一次散步' },
    { emoji: '💡', quest: '你知道吗：纸带剪一刀能变两个环——那是莫比乌斯带', term: '[TIP]  莫比乌斯带剪一刀变两环' },
    // 浪漫温柔
    { emoji: '🌸', quest: '慢慢来，好论文都是熬出来的', term: '[LOVE] 好论文都是熬出来的' },
    { emoji: '🌙', quest: '夜深了，助教还在陪你写「{section}」', term: '[LOVE] 助教还在陪你写 {section}' },
    { emoji: '✨', quest: '每一个公式，都是替你向世界说的情话', term: '[LOVE] 公式是替你向世界说的情话' },
    { emoji: '💛', quest: '你负责想象，我负责把想象变成模型', term: '[LOVE] 你想象，我建模' },
    { emoji: '🎈', quest: '别急，进度条像气球，总要慢慢吹起来', term: '[LOVE] 进度条像气球' },
    { emoji: '🌊', quest: '等风来，不如先把这一节写好', term: '[LOVE] 等风来不如先写好' },
    // 鼓励
    { emoji: '💪', quest: '快了快了，再等一下下', term: '[TIP]  快了，再等一下下' },
    { emoji: '🌟', quest: '你已经完成 {done}/{total}，很了不起', term: '[TIP]  已完成 {done}/{total} 节' },
    { emoji: '🚀', quest: '模型正在全力输出，请再给它一点时间', term: '[WAIT] model still thinking…' },
    { emoji: '🍀', quest: '好饭不怕晚，好论文不怕等', term: '[TIP]  好论文不怕等' }
  ];

  // 软预警期间脚注轮换的「助教状态」短句
  var STATUS_LINES = [
    '🛐 助教正在祈祷模型别翻车…',
    '☕ 助教喝了口咖啡压压惊',
    '😤 助教深吸一口气，继续等',
    '🧘 助教闭目养神中（其实在等模型）',
    '🙏 助教给「{section}」求个好结果',
    '🐢 模型跑得慢，但很认真'
  ];

  var STALL_SOFT = 45;         // 秒：静默超过则进入「助教状态 + 消遣」
  var STALL_HARD = 600;        // 秒：静默超过才提示「可能卡住 / 可续传」（单节约 7-8 分钟）
  var INTERLUDE_INTERVAL = 20000;  // 毫秒：消遣库的插入间隔

  function fill(tpl, ctx) {
    return String(tpl).replace(/\{(\w+)\}/g, function (m, k) {
      return (ctx[k] !== undefined && ctx[k] !== null) ? String(ctx[k]) : '';
    });
  }

  function pick(x) {
    if (Array.isArray(x)) return x.length ? x[Math.floor(Math.random() * x.length)] : '';
    return x || '';
  }

  function buildCtx(ev) {
    var secKey = deriveSection(ev);
    var secZh = SECTION_ZH[secKey] || secKey || '';
    return {
      count: ev.count, chunks: ev.chunks, stored: ev.stored,
      section: secZh, current: ev.current, total: ev.total,
      heading: ev.heading || secZh || '',
      message: ev.message, session_id: ev.session_id
    };
  }

  function two(n) { return (n < 10 ? '0' : '') + n; }
  function nowTime() {
    var d = new Date();
    return two(d.getHours()) + ':' + two(d.getMinutes()) + ':' + two(d.getSeconds());
  }

  // 只注入一次样式
  var CSS_INJECTED = false;
  function injectCSS() {
    if (CSS_INJECTED) return;
    CSS_INJECTED = true;
    var css = [
      '.genlog{--rouge:#C04851;--rouge-deep:#a23a43;--gold:#D9B611;--ink:#2B2D30;--ink-soft:#5a5d63;--paper:#F7F7F6;--mist:#E8E9EB;--line:#d8d6d0;display:flex;flex-direction:column;gap:10px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);text-align:left;}',
      '.genlog-head{display:flex;align-items:center;justify-content:space-between;gap:10px;}',
      '.genlog-title{font-size:14px;font-weight:600;color:var(--ink);}',
      '.genlog-toggle{display:inline-flex;border:1px solid var(--line);border-radius:999px;overflow:hidden;background:var(--paper);}',
      '.genlog-toggle button{border:none;background:transparent;padding:4px 12px;font-size:12px;color:var(--ink-soft);cursor:pointer;transition:all .16s;}',
      '.genlog-toggle button.active{background:var(--rouge);color:#fff;}',
      '.genlog-progress{display:flex;flex-direction:column;gap:5px;}',
      '.genlog-progress-track{height:6px;border-radius:999px;background:var(--mist);overflow:hidden;}',
      '.genlog-progress-fill{height:100%;width:0%;border-radius:999px;background:linear-gradient(90deg,var(--gold),var(--rouge));transition:width .4s ease;}',
      '.genlog-progress-meta{display:flex;align-items:center;justify-content:space-between;font-size:12px;color:var(--ink-soft);}',
      '.genlog-ticks{display:flex;gap:4px;flex-wrap:wrap;}',
      '.genlog-ticks i{flex:1;min-width:14px;height:5px;border-radius:3px;background:var(--mist);transition:background .3s;}',
      '.genlog-ticks i.on{background:var(--gold);}',
      '.genlog-ticks i.cur{background:var(--rouge);animation:genlogPulse 1s ease-in-out infinite;}',
      '.genlog-log{height:220px;overflow-y:auto;border:1px solid var(--line);border-radius:10px;padding:12px;font-size:13px;line-height:1.6;background:var(--paper);scroll-behavior:smooth;}',
      '.genlog-log::-webkit-scrollbar{width:6px;}',
      '.genlog-log::-webkit-scrollbar-thumb{background:var(--line);border-radius:3px;}',
      '.genlog-qentry{display:flex;gap:8px;padding:4px 2px;animation:genlogIn .3s ease both;}',
      '.genlog-q-emoji{flex:0 0 auto;}',
      '.genlog-q-text{color:var(--ink);}',
      '.genlog-q-time{font-size:11px;color:var(--ink-soft);opacity:.6;margin-left:auto;flex:0 0 auto;}',
      '.genlog-tentry{display:flex;gap:8px;white-space:pre-wrap;word-break:break-word;animation:genlogIn .2s ease both;}',
      '.genlog-t-time{color:#7a7d85;flex:0 0 auto;}',
      '.genlog-t-tag{flex:0 0 auto;font-weight:600;}',
      '.genlog-t-tag.tag--info{color:#7fc7ff;}',
      '.genlog-t-tag.tag--step{color:#e6c34f;}',
      '.genlog-t-tag.tag--write{color:#ff9d7a;}',
      '.genlog-t-tag.tag--ok{color:#7fd69b;}',
      '.genlog-t-tag.tag--warn{color:#e6c34f;}',
      '.genlog-t-tag.tag--err{color:#ff8f8f;}',
      '.genlog-t-tag.tag--done{color:#7fd69b;}',
      '.genlog-t-tag.tag--fun{color:#d0b8ff;}',
      '.genlog-cursor{display:inline-block;width:8px;height:14px;background:#e4e4e6;vertical-align:middle;animation:genlogBlink 1s step-end infinite;margin-left:2px;}',
      '.genlog-foot{font-size:12px;color:var(--ink-soft);display:flex;align-items:center;gap:6px;}',
      '.genlog-dot{width:8px;height:8px;border-radius:50%;background:#3caf6a;display:inline-block;}',
      '.genlog-dot.thinking{background:var(--gold);animation:genlogPulse 1s ease-in-out infinite;}',
      '.genlog-dot.warn{background:var(--rouge);}',
      '.genlog--terminal .genlog-log{background:#1e1f22;color:#e4e4e6;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Courier New",monospace;border-color:#33353a;}',
      '@keyframes genlogIn{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:none;}}',
      '@keyframes genlogBlink{0%,100%{opacity:1;}50%{opacity:0;}}',
      '@keyframes genlogPulse{0%,100%{opacity:1;}50%{opacity:.35;}}'
    ].join('\n');
    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }

  function GenLogView(container, total, kind) {
    this.container = container;
    this.total = total || 10;
    this.kind = kind || 'paper';
    this.entries = [];
    this.style = (typeof localStorage !== 'undefined' && localStorage.getItem('genlog.style')) || 'quest';
    this.done = 0;        // 已完成节数
    this.working = 0;     // 当前进行中的节（1-based）
    this.workingSec = '';
    this.startTs = Date.now();
    this.lastActivity = Date.now();
    this.finished = false;
    this._statusIdx = 0;
    this._statusTick = 0;
    this._lastInterlude = Date.now();

    injectCSS();
    this._build();
    this._renderTicks();
    this._tick();

    var self = this;
    this._timer = setInterval(function () { self._tick(); }, 1000);
  }

  GenLogView.prototype._build = function () {
    var c = this.container;
    c.innerHTML = '';
    c.classList.add('genlog');
    c.dataset.style = this.style;

    c.innerHTML =
      '<div class="genlog-head">' +
        '<div class="genlog-title">' + (this.kind === 'outline' ? '正在生成大纲' : '正在生成完整论文') + '</div>' +
        '<div class="genlog-toggle">' +
          '<button type="button" data-style="quest">简短</button>' +
          '<button type="button" data-style="terminal">⌨️ 终端</button>' +
        '</div>' +
      '</div>' +
      '<div class="genlog-progress">' +
        '<div class="genlog-progress-track"><i class="genlog-progress-fill"></i></div>' +
        '<div class="genlog-progress-meta"><span class="genlog-label">准备中…</span><span class="genlog-count"></span></div>' +
        '<div class="genlog-ticks"></div>' +
      '</div>' +
      '<div class="genlog-log"></div>' +
      '<div class="genlog-foot"><span class="genlog-dot"></span><span class="genlog-foot-text">正在连接…</span></div>';

    this.rootEl = c;
    this.logEl = c.querySelector('.genlog-log');
    this.fillEl = c.querySelector('.genlog-progress-fill');
    this.labelEl = c.querySelector('.genlog-label');
    this.countEl = c.querySelector('.genlog-count');
    this.ticksEl = c.querySelector('.genlog-ticks');
    this.footDotEl = c.querySelector('.genlog-dot');
    this.footTextEl = c.querySelector('.genlog-foot-text');

    var self = this;
    c.querySelectorAll('.genlog-toggle button').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.style === self.style);
      btn.addEventListener('click', function () { self.setStyle(btn.dataset.style); });
    });

    this._applyStyleClass();
  };

  GenLogView.prototype._applyStyleClass = function () {
    this.rootEl.classList.toggle('genlog--terminal', this.style === 'terminal');
    this.rootEl.classList.toggle('genlog--quest', this.style === 'quest');
  };

  GenLogView.prototype.setStyle = function (s) {
    if (s !== 'quest' && s !== 'terminal') return;
    this.style = s;
    try { localStorage.setItem('genlog.style', s); } catch (e) {}
    this.container.querySelectorAll('.genlog-toggle button').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.style === s);
    });
    this._applyStyleClass();
    this._renderLog();
  };

  GenLogView.prototype.push = function (ev) {
    if (!ev || this.finished) return;
    this.lastActivity = Date.now();
    this._lastInterlude = Date.now();
    this._statusTick = 0;

    var meta;
    if (ev.type === 'progress') {
      meta = LINES[ev.step];
    } else {
      meta = LINES[ev.type];
    }

    // 进度状态推进
    if (ev.type === 'progress') {
      if (ev.total) this.total = ev.total;
      if (ev.step === 'generate_outline' || ev.step === 'generate_section') {
        this.working = ev.current || 0;
        var secKey = deriveSection(ev);
        this.workingSec = SECTION_ZH[secKey] || secKey || '';
        // 大纲无 section_done，直接用 current-1 计已完成
        if (ev.step === 'generate_outline') this.done = Math.max(this.done, (ev.current || 1) - 1);
      }
    } else if (ev.type === 'section_done') {
      this.done += 1;
      this.workingSec = SECTION_ZH[ev.section] || ev.section || '';
    }

    if (!meta) {
      if (ev.message) {
        meta = { emoji: 'ℹ️', quest: String(ev.message), term: '[INFO]  ' + String(ev.message), kind: 'info' };
      } else {
        this._renderProgress();
        return; // 无文案（如独立的 session_created 已由页面处理）
      }
    }

    var ctx = buildCtx(ev);
    var quest = pick(meta.quest);
    var term = pick(meta.term);

    // 续传/重生成时的「已生成，跳过」特殊文案
    if ((ev.step === 'generate_outline' || ev.step === 'generate_section') && ev.message && String(ev.message).indexOf('跳过') !== -1) {
      quest = '⏭️ 第 {current}/{total} 节 · {section}：已生成，跳过';
      meta = { emoji: '⏭️', quest: quest, term: term, kind: 'ok' };
      term = pick(meta.term);
    }

    this.entries.push({
      emoji: meta.emoji,
      quest: fill(quest, ctx),
      term: fill(term, ctx),
      kind: meta.kind,
      time: nowTime()
    });

    this._appendEntry(this.entries[this.entries.length - 1]);
    this._renderProgress();
  };

  GenLogView.prototype._appendEntry = function (e) {
    var node = document.createElement('div');
    if (this.style === 'terminal') {
      node.className = 'genlog-tentry';
      node.innerHTML =
        '<span class="genlog-t-time">' + e.time + '</span>' +
        '<span class="genlog-t-tag tag--' + e.kind + '">' + (e.term.split(/\s{2,}/)[0] || '') + '</span>' +
        '<span>' + e.term.replace(/^\S+\s{2,}/, '') + '</span>';
    } else {
      node.className = 'genlog-qentry';
      node.innerHTML =
        '<span class="genlog-q-emoji">' + e.emoji + '</span>' +
        '<span class="genlog-q-text">' + e.quest + '</span>' +
        '<span class="genlog-q-time">' + e.time + '</span>';
    }
    this.logEl.appendChild(node);
    this._ensureCursor();
    this.logEl.scrollTop = this.logEl.scrollHeight;
  };

  // 终端模式下，在末尾挂一个闪烁光标
  GenLogView.prototype._ensureCursor = function () {
    if (this.style !== 'terminal') return;
    var cur = this.logEl.querySelector('.genlog-cursor');
    if (!cur) {
      cur = document.createElement('span');
      cur.className = 'genlog-cursor';
      this.logEl.appendChild(cur);
    }
  };

  GenLogView.prototype._renderLog = function () {
    var self = this;
    this.logEl.innerHTML = '';
    this.entries.forEach(function (e) { self._appendEntry(e); });
  };

  GenLogView.prototype._renderProgress = function () {
    var done = Math.min(this.done, this.total);
    var pct = this.total ? Math.round(done / this.total * 100) : 0;
    this.fillEl.style.width = pct + '%';

    var label, count;
    if (this.working > 0 && this.workingSec) {
      label = '第 ' + this.working + '/' + this.total + ' 节 · ' + this.workingSec;
      count = pct + '%';
    } else if (this.done > 0) {
      label = '已完成 ' + done + '/' + this.total + ' 节';
      count = pct + '%';
    } else {
      label = '准备中…';
      count = '';
    }
    this.labelEl.textContent = label;
    this.countEl.textContent = count;
    this._renderTicks();
  };

  GenLogView.prototype._renderTicks = function () {
    var html = '';
    for (var i = 0; i < this.total; i++) {
      var cls = 'genlog-tick';
      if (i < this.done) cls += ' on';
      else if (this.working > 0 && i === this.working - 1) cls += ' cur';
      html += '<i class="' + cls + '"></i>';
    }
    this.ticksEl.innerHTML = html;
  };

  // 软预警期间：往日志里随机滚一条消遣（两种风格都播）
  GenLogView.prototype._emitInterlude = function () {
    if (!INTERLUDES.length) return;
    var pickItem = INTERLUDES[Math.floor(Math.random() * INTERLUDES.length)];
    var ctx = { section: this.workingSec || '', done: Math.min(this.done, this.total), total: this.total };
    var entry = {
      emoji: pickItem.emoji,
      quest: fill(pickItem.quest, ctx),
      term: fill(pickItem.term, ctx),
      kind: 'fun',
      time: nowTime()
    };
    this.entries.push(entry);
    this._appendEntry(entry);
  };

  GenLogView.prototype._tick = function () {
    if (this.finished) return;
    var now = Date.now();
    var elapsed = Math.round((now - this.startTs) / 1000);
    var quiet = Math.round((now - this.lastActivity) / 1000);
    var done = Math.min(this.done, this.total);

    var dotCls = 'genlog-dot', text;
    if (quiet >= STALL_HARD) {
      dotCls += ' warn';
      text = '⚠️ 已静默 ' + quiet + 's，可能网络波动；可稍后刷新续传（已生成 ' + done + ' 节）';
    } else if (quiet >= STALL_SOFT) {
      dotCls += ' thinking';
      // 助教状态轮换（约每 6s 换一句）
      this._statusTick += 1;
      if (this._statusTick % 6 === 0) {
        this._statusIdx = (this._statusIdx + 1) % STATUS_LINES.length;
      }
      var status = fill(STATUS_LINES[this._statusIdx], { section: this.workingSec || '' });
      text = status + ' · 已用 ' + elapsed + 's · 已生成 ' + done + '/' + this.total + ' 节';
      // 消遣库：每 INTERLUDE_INTERVAL 滚一条
      if (now - this._lastInterlude >= INTERLUDE_INTERVAL) {
        this._lastInterlude = now;
        this._emitInterlude();
      }
    } else {
      text = '网络正常 · 上次活动 ' + quiet + 's 前 · 已用 ' + elapsed + 's';
    }

    this.footDotEl.className = dotCls;
    this.footTextEl.textContent = text;
  };

  GenLogView.prototype._stop = function () {
    this.finished = true;
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  };

  GenLogView.prototype.finish = function (ev) {
    this.lastActivity = Date.now();
    if (ev) this.push(ev);
    this._stop();
    this.done = this.total;
    this._renderProgress();
    this.footDotEl.className = 'genlog-dot';
    this.footTextEl.textContent = '✅ 完成 · 用时 ' + Math.round((Date.now() - this.startTs) / 1000) + 's';
  };

  GenLogView.prototype.fail = function (msg) {
    this.push({ type: 'error', message: msg });
    this._stop();
    this.footDotEl.className = 'genlog-dot warn';
    this.footTextEl.textContent = '❌ ' + (msg || '生成失败');
  };

  window.GenLog = {
    mount: function (container, opts) {
      if (!container) return null;
      opts = opts || {};
      return new GenLogView(container, opts.total || 10, opts.kind || 'paper');
    }
  };
})();
