/* 极简 IDE 前端：CodeMirror 编辑器 + WebSocket 交互式终端。 */
(function () {
  "use strict";

  // ── 编辑器 ──
  const editor = CodeMirror.fromTextArea(document.getElementById("editor"), {
    mode: "python",
    lineNumbers: true,
    indentUnit: 4,
    tabSize: 4,
    indentWithTabs: false,
    lineWrapping: false,
  });
  editor.setValue(
    'name = input("你叫什么名字？")\nprint("你好,", name)\n'
  );

  // ── 元素 ──
  const workdirEl = document.getElementById("workdir");
  const filenameEl = document.getElementById("filename");
  const btnRun = document.getElementById("btn-run");
  const btnStop = document.getElementById("btn-stop");
  const btnSave = document.getElementById("btn-save");
  const btnClear = document.getElementById("btn-clear");
  const logEl = document.getElementById("con-log");
  const imgsEl = document.getElementById("con-imgs");
  const inputEl = document.getElementById("con-input");
  const statusEl = document.getElementById("con-status");
  const promptEl = document.getElementById("con-prompt");

  let running = false;
  let cwd = "";
  let finishRunWait = null;

  // ── 控制台输出 ──
  function append(text, cls) {
    const span = document.createElement("span");
    if (cls) span.className = cls;
    span.textContent = text;
    logEl.appendChild(span);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function setRunning(on) {
    if (on && !running) finishRunWait = window.MMWait?.begin('运行代码 / 终端命令');
    if (!on) { finishRunWait?.(); finishRunWait = null; }
    running = on;
    btnRun.disabled = on;btnRun.textContent=on?"运行中…":"运行";btnStop.textContent="停止";
    btnStop.disabled = !on;
    statusEl.textContent = on ? "运行中…" : "就绪";
    statusEl.className = on ? "status running" : "status";
    promptEl.textContent = on ? "stdin>" : "$";
    inputEl.placeholder = on
      ? "程序正在等待输入？在此输入并回车（如名字、y/n）"
      : "输入命令回车执行（dir、pip install、python x.py …）";
  }

  // ── WebSocket ──
  const wsProto = location.protocol === "https:" ? "wss" : "ws";
  let ws = null;

  function connect() {
    const finishConnectWait = window.MMWait?.begin('连接终端');
    ws = new WebSocket(`${wsProto}://${location.host}/api/ide/terminal`);
    ws.onopen = () => { finishConnectWait?.(); append("[已连接终端]\n", "sys"); };
    ws.onclose = () => {
      finishConnectWait?.();
      append("\n[终端连接已断开，3 秒后重连…]\n", "sys");
      setRunning(false);statusEl.textContent="连接已断开";
      window.ResearchCompanion?.notify('finish',{id:'code',state:'offline'});
      setTimeout(connect, 3000);
    };
    ws.onerror = () => { finishConnectWait?.(); };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      switch (msg.type) {
        case "stdout":
          append(msg.data);
          break;
        case "evidence":
          window.dispatchEvent(new CustomEvent("solve-evidence", { detail: msg.record }));
          break;
        case "sys":
          append("\n" + msg.data + "\n", "sys");
          break;
        case "image": {
          const img = document.createElement("img");
          img.src = "data:image/png;base64," + msg.data_base64;
          img.title = msg.name || "";
          imgsEl.appendChild(img);
          break;
        }
        case "exit":
          if (typeof msg.cwd === "string") {
            cwd = msg.cwd;
            if (!workdirEl.value.trim()) workdirEl.placeholder = cwd;
          }
          if (running) {
            append(`\n[进程结束，退出码 ${msg.code}]\n`, msg.code === 0 ? "sys" : "err");
            setRunning(false);statusEl.textContent=msg.code===0?"运行完成":"执行失败，请查看终端";
            window.ResearchCompanion?.notify('finish',{id:'code',...(msg.code===0?{}:{state:'evidence_failed'})});
          }
          break;
      }
    };
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  // ── 动作 ──
  function runCode() {
    if (running) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) { append("\n[终端未连接，请稍后重试]\n", "err"); return; }
    const context = window.SolveFlow && window.SolveFlow.runContext();
    if (window.SolveFlow && !context) { append("\n[请先进入一个建模步骤]\n", "sys"); return; }
    imgsEl.innerHTML = "";
    append("\n[运行] " + (filenameEl.value || "代码") + "\n", "sys");
    setRunning(true);
    window.ResearchCompanion?.notify('start',{id:'code',sessionId:context?.solve_session_id,state:'running',label:'运行代码',cancel:()=>send({type:'interrupt'})});
    send({ type: "run_code", code: editor.getValue(), cwd: workdirEl.value.trim(), ...(context || {}) });
  }

  btnRun.addEventListener("click", runCode);
  editor.setOption("extraKeys", { "Ctrl-Enter": runCode, "Cmd-Enter": runCode });

  btnStop.addEventListener("click", () => send({ type: "interrupt" }));
  btnClear.addEventListener("click", () => { logEl.innerHTML = ""; imgsEl.innerHTML = ""; });

  // 控制台输入：运行中=stdin，空闲=命令
  inputEl.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const text = inputEl.value;
    inputEl.value = "";
    if (running) {
      append(text + "\n", "echo");          // 回显用户输入
      send({ type: "stdin", data: text });
    } else {
      const cmd = text.trim();
      if (!cmd) return;
      append("$ " + cmd + "\n", "echo");
      setRunning(true);
      send({ type: "run_cmd", command: cmd, cwd: workdirEl.value.trim() });
    }
  });

  // 保存编辑器内容到磁盘
  btnSave.addEventListener("click", async () => {
    const dir = workdirEl.value.trim() || cwd;
    const fname = (filenameEl.value || "main.py").trim();
    if (!dir) { append("\n[保存失败] 请先填写工作目录\n", "err"); return; }
    const sep = dir.includes("\\") ? "\\" : "/";
    const path = dir.replace(/[\\/]+$/, "") + sep + fname;
    try {
      const res = await fetch("/api/ide/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, content: editor.getValue() }),
      });
      const r = await res.json();
      append("\n[保存] " + (r.ok ? "已写入 " + r.path : "失败：" + (r.error || "未知错误")) + "\n",
             r.ok ? "sys" : "err");
    } catch (err) {
      append("\n[保存失败] " + err + "\n", "err");
    }
  });

  setRunning(false);
  connect();

  // 供统一工作台（workspace.js）取用编辑器实例与运行能力
  window.IDE = {
    editor,
    runCode,
    getCwd: () => cwd,
    isRunning: () => running,
    setWorkdir: (v) => { workdirEl.value = v || ""; },
  };
})();
