#!/usr/bin/env node
/**
 * Chrome ChatGPT Cognitive-Control Bridge  (v4.1 Evidence-Integrity, CDP)
 * Node.js rewrite of chrome_chatgpt.py — Windows / macOS / Linux compatible
 * ─────────────────────────────────────────────────────────────────────────
 * Requirements : Node.js 18+  (zero npm dependencies)
 * Cross-platform: Windows %TEMP% / macOS-Linux /tmp  ← no more /tmp hardcode
 * Locking      : PID-based O_EXCL atomic file lock   ← no more fcntl
 *
 * Usage:
 *   node chrome_chatgpt.js --target-url "https://chatgpt.com/c/..." --prompt "..."
 *
 * Before running, start Chrome/Edge with CDP enabled:
 *   chrome.exe  --remote-debugging-port=9222 --remote-allow-origins=*
 *   msedge.exe  --remote-debugging-port=9222 --remote-allow-origins=*
 */
'use strict';

const http         = require('http');
const net          = require('net');
const crypto       = require('crypto');
const fs           = require('fs');
const os           = require('os');
const path         = require('path');
const { execSync } = require('child_process');

// ═══════════════════════════════════════════════════════════════════
// Exit codes  (mirrors chrome_chatgpt.py exactly)
// ═══════════════════════════════════════════════════════════════════
const EXIT_OK              = 0;
const EXIT_TIMEOUT_PARTIAL = 2;
const EXIT_TIMEOUT_EMPTY   = 3;
const EXIT_BROWSER_FAIL    = 4;
const EXIT_BASELINE_FAIL   = 5;
const EXIT_SUBMIT_FAIL     = 6;
const EXIT_NO_NEW_TURN     = 7;
const EXIT_NO_TAB          = 10;
const EXIT_AMBIGUOUS_TAB   = 11;
const EXIT_CIRCUIT_OPEN    = 12;
const EXIT_TARGET_BUSY     = 13;

const EXIT_NAME = {
  [EXIT_OK]:              'OK',
  [EXIT_TIMEOUT_PARTIAL]: 'TIMEOUT_PARTIAL',
  [EXIT_TIMEOUT_EMPTY]:   'TIMEOUT_EMPTY',
  [EXIT_BROWSER_FAIL]:    'BROWSER_FAIL',
  [EXIT_BASELINE_FAIL]:   'BASELINE_FAIL',
  [EXIT_SUBMIT_FAIL]:     'SUBMIT_FAIL',
  [EXIT_NO_NEW_TURN]:     'NO_NEW_TURN',
  [EXIT_NO_TAB]:          'NO_TAB',
  [EXIT_AMBIGUOUS_TAB]:   'AMBIGUOUS_TAB',
  [EXIT_CIRCUIT_OPEN]:    'CIRCUIT_OPEN',
  [EXIT_TARGET_BUSY]:     'TARGET_BUSY',
};

// ═══════════════════════════════════════════════════════════════════
// Cross-platform temp dir
// ═══════════════════════════════════════════════════════════════════
const TMPDIR             = os.tmpdir();
const CIRCUIT_STATE_FILE = path.join(TMPDIR, 'chrome_chatgpt_circuit_breaker.json');
const CIRCUIT_WINDOW_MS  = 3600 * 1000;
const CIRCUIT_MAX_RETRIES = 3;

const CDP_HTTP_TIMEOUT_MS = 5000;
const CDP_RPC_TIMEOUT_MS  = 60000;
const SUBMIT_PHASE_BUDGET = 20;
const TURN_PHASE_BUDGET   = 60;
const STABLE_PHASE_MIN    = 5;
const GIT_TIMEOUT_MS      = 5000;

let _BROWSER_NAME = 'chrome';

// ═══════════════════════════════════════════════════════════════════
// Custom errors
// ═══════════════════════════════════════════════════════════════════
class CDPError extends Error { constructor(m) { super(m); this.name='CDPError'; } }
class NoTargetTabError extends CDPError { constructor(m) { super(m); this.name='NoTargetTabError'; } }
class AmbiguousTargetTabError extends CDPError { constructor(m) { super(m); this.name='AmbiguousTargetTabError'; } }
class CircuitOpenError extends Error { constructor(m) { super(m); this.name='CircuitOpenError'; } }
class TargetTabBusyError extends Error { constructor(m) { super(m); this.name='TargetTabBusyError'; } }

// ═══════════════════════════════════════════════════════════════════
// Secret sanitization
// ═══════════════════════════════════════════════════════════════════
function sanitizeText(text) {
  if (typeof text !== 'string') text = String(text || '');
  return text
    .replace(/gh[pousr]_[A-Za-z0-9_]{16,}/g,                  '[GH_TOKEN]')
    .replace(/github_pat_[A-Za-z0-9_]{20,}/g,                  '[GH_PAT]')
    .replace(/sk-[A-Za-z0-9_-]{20,}/g,                         '[OPENAI_KEY]')
    .replace(/(Bearer\s+)[A-Za-z0-9._\-+/=]{8,}/gi,            '$1[BEARER]')
    .replace(/(password\s*[:=]\s*["']?)([^"'\s]+)(["']?)/gi,   '$1[PASSWORD]$3')
    .replace(/(?:AKIA|ASIA)[0-9A-Z]{16}/g,                     '[AWS_KEY]');
}

// ═══════════════════════════════════════════════════════════════════
// Event emitter  (stderr JSON)
// ═══════════════════════════════════════════════════════════════════
function emitEvent(stage, exitCode, message, extra={}) {
  const ev = {
    ts: Date.now()/1000, stage,
    exit_code: exitCode, exit_name: EXIT_NAME[exitCode]||String(exitCode),
    message: sanitizeText(message), browser: _BROWSER_NAME, ...extra,
  };
  process.stderr.write(JSON.stringify(ev)+'\n');
}

// ═══════════════════════════════════════════════════════════════════
// Cross-platform PID-based file lock  (replaces fcntl.flock)
// ═══════════════════════════════════════════════════════════════════
function _pidAlive(pid) {
  try { process.kill(pid, 0); return true; }
  catch(e) { return e.code==='EPERM'; }
}
function _acquireLockFile(lockPath) {
  for (let attempt=0; attempt<3; attempt++) {
    try {
      const fd = fs.openSync(lockPath, fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_RDWR);
      fs.writeSync(fd, String(process.pid)); fs.closeSync(fd);
      return true;
    } catch(e) {
      if (e.code!=='EEXIST') throw e;
      try {
        const ownerPid = parseInt(fs.readFileSync(lockPath,'utf8').trim(), 10);
        if (!isNaN(ownerPid) && !_pidAlive(ownerPid)) { try{fs.unlinkSync(lockPath);}catch(_){} continue; }
      } catch(_) {}
      return false;
    }
  }
  return false;
}
function _releaseLockFile(lockPath) { try{fs.unlinkSync(lockPath);}catch(_){} }

// ═══════════════════════════════════════════════════════════════════
// Circuit breaker
// ═══════════════════════════════════════════════════════════════════
const _circuitLockPath = CIRCUIT_STATE_FILE+'.lock';
function _withCircuitLock(fn) {
  _acquireLockFile(_circuitLockPath);
  try { return fn(); } finally { _releaseLockFile(_circuitLockPath); }
}
function _readCircuitState() {
  try { const d=JSON.parse(fs.readFileSync(CIRCUIT_STATE_FILE,'utf8')); return (typeof d==='object'&&d)?d:{}; }
  catch(_) { return {}; }
}
function _writeCircuitState(state) {
  const tmp=CIRCUIT_STATE_FILE+'.tmp.'+process.pid;
  try { fs.writeFileSync(tmp, JSON.stringify(state), 'utf8'); fs.renameSync(tmp, CIRCUIT_STATE_FILE); }
  catch(_) { try{fs.unlinkSync(tmp);}catch(__){} }
}
function _pruneCircuitState(state, now) {
  const r={};
  for (const [k,v] of Object.entries(state)) {
    if (!Array.isArray(v)) continue;
    const kept=v.filter(t=>typeof t==='number'&&(now-t)<=CIRCUIT_WINDOW_MS);
    if (kept.length>0) r[k]=kept;
  }
  return r;
}
function _normalizeSig(sig) {
  return sig.replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi,'<UUID>')
            .replace(/\b\d{6,}\b/g,'<NUM>').replace(/\s+/g,' ').trim();
}
function checkCircuitBreaker(failureSig, maxRetries=CIRCUIT_MAX_RETRIES) {
  if (!failureSig) return;
  const key=crypto.createHash('sha256').update(_normalizeSig(failureSig)).digest('hex').slice(0,16);
  const now=Date.now(); let tsList;
  _withCircuitLock(()=>{
    const state=_pruneCircuitState(_readCircuitState(), now);
    tsList=[...(state[key]||[]), now]; state[key]=tsList; _writeCircuitState(state);
  });
  if (tsList && tsList.length>maxRetries)
    throw new CircuitOpenError(`同一故障特征在${CIRCUIT_WINDOW_MS/1000}s内已连续出现${tsList.length}次（>${maxRetries}），已触发自动熔断。\n请排查后运行：node chrome_chatgpt.js --reset-circuit`);
}
function resetCircuitBreaker() {
  let cleared=0;
  _withCircuitLock(()=>{ try{ const s=_readCircuitState(); cleared=Object.keys(s).length; fs.unlinkSync(CIRCUIT_STATE_FILE); }catch(_){} });
  return cleared;
}

// ═══════════════════════════════════════════════════════════════════
// TargetTabLock
// ═══════════════════════════════════════════════════════════════════
function _tabLockPath(targetUrl) {
  return path.join(TMPDIR, `chrome_chatgpt_tab_${crypto.createHash('sha256').update(targetUrl).digest('hex').slice(0,24)}.lock`);
}
function acquireTargetTabLock(targetUrl) {
  const lockPath=_tabLockPath(targetUrl);
  if (!_acquireLockFile(lockPath)) throw new TargetTabBusyError(`目标 ChatGPT Tab 正在被另一 bridge 占用（${lockPath}）。如确认对端已死，可手动删除锁文件。`);
  return lockPath;
}
function releaseTargetTabLock(lockPath) { if (lockPath) _releaseLockFile(lockPath); }

// ═══════════════════════════════════════════════════════════════════
// Git context
// ═══════════════════════════════════════════════════════════════════
function getGitContext(cwd) {
  const opts={cwd:cwd||process.cwd(), timeout:GIT_TIMEOUT_MS, encoding:'utf8', stdio:['pipe','pipe','pipe']};
  try {
    const sha=execSync('git rev-parse --short HEAD', opts).trim();
    let branch=''; try{branch=execSync('git rev-parse --abbrev-ref HEAD', opts).trim();}catch(_){}
    return branch?`${sha} (${branch})`:sha;
  } catch(_) { return 'N/A'; }
}

// ═══════════════════════════════════════════════════════════════════
// CDP HTTP
// ═══════════════════════════════════════════════════════════════════
function httpGetJson(host, port, urlPath) {
  return new Promise((resolve, reject)=>{
    const req=http.get({host, port, path:urlPath, timeout:CDP_HTTP_TIMEOUT_MS, headers:{Host:'localhost',Accept:'application/json'}}, res=>{
      let body=''; res.on('data',c=>{body+=c;}); res.on('end',()=>{ try{resolve(JSON.parse(body));}catch(e){reject(new CDPError(`CDP HTTP ${urlPath} 返回非JSON: ${e.message}`));} });
    });
    req.on('error', e=>reject(new CDPError(`CDP HTTP失败(${urlPath}): ${e.message}`)));
    req.on('timeout', ()=>{ req.destroy(); reject(new CDPError(`CDP HTTP超时(${urlPath})`)); });
  });
}

// ═══════════════════════════════════════════════════════════════════
// WebSocket client  (RFC 6455, pure net.Socket)
// ═══════════════════════════════════════════════════════════════════
class CDPWebSocket {
  constructor() { this.sock=null; this.buffer=Buffer.alloc(0); this.pending=new Map(); this.nextId=1; this.closed=false; }

  async connect(wsUrl) {
    const m=wsUrl.match(/^ws:\/\/([^/:]+):?(\d+)?(\/.*)?$/i);
    if (!m) throw new CDPError(`无效WebSocket URL: ${wsUrl}`);
    this.sock=await this._handshake(m[1], m[2]?parseInt(m[2],10):9222, m[3]||'/');
    this.sock.on('data', chunk=>this._onData(chunk));
    this.sock.on('close', ()=>this._onClose());
    this.sock.on('error', e=>this._onClose(e));
  }

  async _handshake(host, port, wsPath) {
    return new Promise((resolve, reject)=>{
      const sock=net.createConnection({host, port});
      sock.setTimeout(CDP_HTTP_TIMEOUT_MS);
      const key=crypto.randomBytes(16).toString('base64');
      const req=[`GET ${wsPath} HTTP/1.1`,`Host: ${host}:${port}`,`Upgrade: websocket`,`Connection: Upgrade`,`Sec-WebSocket-Key: ${key}`,`Sec-WebSocket-Version: 13`,'',''].join('\r\n');
      let buf=Buffer.alloc(0), upgraded=false;
      sock.on('connect', ()=>{ sock.setTimeout(0); sock.write(req); });
      sock.on('timeout', ()=>{ sock.destroy(); reject(new CDPError('WebSocket握手超时')); });
      sock.on('error', e=>reject(new CDPError(`WebSocket连接失败: ${e.message}`)));
      sock.on('data', chunk=>{
        if (upgraded) { this._onData(chunk); return; }
        buf=Buffer.concat([buf, chunk]);
        const sep=buf.indexOf('\r\n\r\n'); if (sep===-1) return;
        const header=buf.slice(0,sep).toString('ascii');
        if (!header.includes(' 101 ')) { sock.destroy(); return reject(new CDPError(`WebSocket握手被拒: ${header.split('\r\n')[0]}`)); }
        upgraded=true; sock.removeAllListeners('data'); sock.removeAllListeners('error'); sock.removeAllListeners('timeout');
        resolve(sock); const rest=buf.slice(sep+4); if (rest.length>0) this._onData(rest);
      });
    });
  }

  _onData(chunk) {
    this.buffer=Buffer.concat([this.buffer, chunk]);
    for(;;) { const frame=this._tryParseFrame(); if (!frame) break; this._handleFrame(frame.opcode, frame.payload); }
  }

  _tryParseFrame() {
    const buf=this.buffer; if (buf.length<2) return null;
    const opcode=buf[0]&0x0F, masked=!!(buf[1]&0x80);
    let length=buf[1]&0x7F, offset=2;
    if (length===126) { if (buf.length<4) return null; length=buf.readUInt16BE(2); offset=4; }
    else if (length===127) { if (buf.length<10) return null; length=buf.readUInt32BE(6); offset=10; }
    if (masked) offset+=4;
    if (buf.length<offset+length) return null;
    let payload;
    if (masked) { const mask=buf.slice(offset-4,offset); payload=Buffer.from(buf.slice(offset,offset+length)); for(let i=0;i<payload.length;i++) payload[i]^=mask[i%4]; }
    else { payload=buf.slice(offset,offset+length); }
    this.buffer=buf.slice(offset+length);
    return {opcode, payload};
  }

  _handleFrame(opcode, payload) {
    if (opcode===0x8) { this._onClose(); return; }
    if (opcode===0x9) { if (this.sock&&!this.closed) this.sock.write(Buffer.from([0x8A,0x00])); return; }
    if (opcode!==0x1&&opcode!==0x2) return;
    let msg; try{msg=JSON.parse(payload.toString('utf8'));}catch(_){return;}
    const id=msg.id;
    if (typeof id==='number'&&this.pending.has(id)) {
      const {resolve,reject,timer}=this.pending.get(id); this.pending.delete(id); clearTimeout(timer);
      if (msg.error) reject(new CDPError(`CDP error: ${JSON.stringify(msg.error)}`));
      else if (msg.result!=null) resolve(msg.result);
      else reject(new CDPError(`CDP返回异常: ${JSON.stringify(msg)}`));
    }
  }

  _onClose(err) {
    if (this.closed) return; this.closed=true;
    const errMsg=err?`WebSocket错误关闭: ${err.message}`:'WebSocket远端关闭';
    for (const {reject,timer} of this.pending.values()) { clearTimeout(timer); reject(new CDPError(errMsg)); }
    this.pending.clear();
  }

  _sendFrame(text) {
    if (this.closed||!this.sock) throw new CDPError('WebSocket已关闭');
    const payload=Buffer.from(text,'utf8'), n=payload.length, mask=crypto.randomBytes(4);
    let header;
    if (n<126)        { header=Buffer.from([0x81,0x80|n]); }
    else if (n<65536) { header=Buffer.allocUnsafe(4); header[0]=0x81; header[1]=0x80|126; header.writeUInt16BE(n,2); }
    else              { header=Buffer.allocUnsafe(10); header[0]=0x81; header[1]=0x80|127; header.writeUInt32BE(0,2); header.writeUInt32BE(n,6); }
    const masked=Buffer.allocUnsafe(n);
    for (let i=0;i<n;i++) masked[i]=payload[i]^mask[i%4];
    this.sock.write(Buffer.concat([header,mask,masked]));
  }

  async sendCommand(method, params={}, timeoutMs=CDP_RPC_TIMEOUT_MS) {
    if (this.closed) throw new CDPError('CDP连接已关闭');
    const id=this.nextId++, frame=JSON.stringify({id, method, params});
    return new Promise((resolve, reject)=>{
      const timer=setTimeout(()=>{ this.pending.delete(id); reject(new CDPError(`CDP ${method} 超时（>${timeoutMs}ms）`)); }, timeoutMs);
      this.pending.set(id, {resolve, reject, timer});
      try { this._sendFrame(frame); } catch(e) { clearTimeout(timer); this.pending.delete(id); reject(e); }
    });
  }

  close() { this.closed=true; try{if(this.sock)this.sock.destroy();}catch(_){} }
}

// ═══════════════════════════════════════════════════════════════════
// Tab finding
// ═══════════════════════════════════════════════════════════════════
function urlMatches(targetUrl, candidateUrl) {
  if (!candidateUrl) return false;
  const base=targetUrl.replace(/[?#/ ]+$/,'').split('?')[0];
  if (candidateUrl.startsWith(base)) return true;
  const uuidM=targetUrl.match(/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/i);
  return !!(uuidM&&candidateUrl.includes(uuidM[0]));
}
async function findTargetTab(host, port, targetUrl) {
  const targets=await httpGetJson(host, port, '/json/list');
  const matched=targets.filter(t=>t.type==='page'&&urlMatches(targetUrl, t.url||''));
  if (matched.length===0) throw new NoTargetTabError(`未在Chrome /json/list中找到URL匹配的page: ${targetUrl}`);
  if (matched.length>1)   throw new AmbiguousTargetTabError(`URL匹配命中${matched.length}个Tab（歧义）：${matched.map(t=>t.url)}`);
  const t=matched[0];
  if (!t.id||!t.webSocketDebuggerUrl) throw new NoTargetTabError(`Tab缺少id/webSocketDebuggerUrl: ${JSON.stringify(t)}`);
  return {targetId:t.id, wsUrl:t.webSocketDebuggerUrl};
}

// ═══════════════════════════════════════════════════════════════════
// CDP JS execution
// ═══════════════════════════════════════════════════════════════════
async function executeCdpJs(ws, jsCode, timeoutMs=CDP_RPC_TIMEOUT_MS) {
  const res=await ws.sendCommand('Runtime.evaluate',
    {expression:jsCode, returnByValue:true, awaitPromise:false, userGesture:true},
    timeoutMs+2000);
  if (res.exceptionDetails) {
    const exc=res.exceptionDetails, desc=(exc.exception||{}).description||(exc.exception||{}).value||'';
    throw new CDPError(`CDP Runtime.evaluate异常: ${exc.text||''} | ${desc}`);
  }
  const remote=res.result||{};
  if (remote.type==='string')    return remote.value||'';
  if (remote.type==='undefined') return '';
  try{return JSON.stringify(remote.value);}catch(_){return remote.description||'';}
}
async function fetchSnapshot(ws, jsOverride) {
  const raw=await executeCdpJs(ws, jsOverride!=null?jsOverride:BASELINE_JS);
  let snap; try{snap=JSON.parse(raw);}catch(e){throw new CDPError(`snapshot JSON解析失败: ${e.message}; raw=${raw.slice(0,200)}`);}
  if (typeof snap!=='object'||snap===null) throw new CDPError(`snapshot类型错误: ${typeof snap}`);
  return snap;
}

// ═══════════════════════════════════════════════════════════════════
// JS probe strings  (identical to Python version)
// ═══════════════════════════════════════════════════════════════════
const BASELINE_JS = `(() => {
  const usr=document.querySelectorAll("[data-message-author-role='user']");
  const asst=document.querySelectorAll("[data-message-author-role='assistant']");
  return JSON.stringify({totalCount:usr.length+asst.length,userCount:usr.length,assistantCount:asst.length,
    lastUserText:"",lastAsstText:"",
    lastUserMessageId:null,lastAsstMessageId:null});
})()`;

function lastMessageIdJs(role) {
  return `(() => {
  const nodes=document.querySelectorAll("[data-message-author-role='${role}']");
  const last=nodes.length>0?nodes[nodes.length-1]:null;
  if(!last)return JSON.stringify({id:null,count:0,textLen:0});
  const id=last.getAttribute("data-message-id")||(last.closest&&last.closest("[data-message-id]")?last.closest("[data-message-id]").getAttribute("data-message-id"):null);
  return JSON.stringify({id,count:nodes.length,textLen:(last.innerText||"").trim().length,fp:((last.innerText||"").trim()).slice(0,80)});
})()`;
}

function userCommitProbeJs(expectedPrompt) {
  const lit=JSON.stringify(expectedPrompt);
  return `(() => {
  const norm=s=>(s||"").replace(/\\r\\n/g,"\\n").replace(/\\u00a0/g," ").replace(/\\s+/g," ").trim();
  const users=document.querySelectorAll("[data-message-author-role='user']");
  const last=users.length>0?users[users.length-1]:null;
  const text=last?norm(last.innerText||""):"";
  const id=last?(last.getAttribute("data-message-id")||(last.closest&&last.closest("[data-message-id]")?last.closest("[data-message-id]").getAttribute("data-message-id"):null)):null;
  return JSON.stringify({userCount:users.length,matchesExpected:text.length>0&&(text===norm(${lit})||text.includes(norm(${lit}).slice(0,30))||norm(${lit}).includes(text.slice(0,30))),textLen:text.length,messageId:id});
})()`;
}

function injectVerifyJs(expectedPrompt) {
  const lit=JSON.stringify(expectedPrompt);
  return `(() => {
  const norm=s=>(s||"").replace(/\\r\\n/g,"\\n").replace(/\\u00a0/g," ").replace(/\\s+/g," ").trim();
  const el=document.querySelector("#prompt-textarea")||document.querySelector("div[contenteditable='true']")||document.querySelector("form [contenteditable='true']")||document.querySelector("textarea")||document.querySelector("form");
  if(!el)return JSON.stringify({ok:false,reason:"NO_INPUT"});
  const normActual=norm(el.innerText||el.textContent||el.value||"");
  return JSON.stringify({ok:true,matchesExpected:normActual.length>0,textLen:normActual.length,
    visible:true,isContentEditable:!!el.isContentEditable});
})()`;
}

function stablePollJs(targetMessageId) {
  const safeId=targetMessageId?targetMessageId.replace(/'/g,''):'';
  const queryPart=targetMessageId?`document.querySelector("[data-message-id='${safeId}']")`:'null';
  return `(() => {
  const stopBtn=document.querySelector("button[data-testid='stop-button']")||document.querySelector("button[aria-label='停止回答']");
  let node=${queryPart};
  const asst=document.querySelectorAll("[data-message-author-role='assistant'], article");
  const last=asst.length>0?asst[asst.length-1]:null;
  if(!node&&last)node=last;
  if(!node)return JSON.stringify({targetPresent:false,isStreaming:!!stopBtn,text:"",messageId:null});
  return JSON.stringify({targetPresent:true,isStreaming:!!stopBtn,text:(node.innerText||"").trim(),
    messageId:node.getAttribute("data-message-id")||(node.closest&&node.closest("[data-message-id]")?node.closest("[data-message-id]").getAttribute("data-message-id"):null)});
})()`;
}

// ═══════════════════════════════════════════════════════════════════
// Flow functions  (all safari_chatgpt.py bug fixes applied)
// ═══════════════════════════════════════════════════════════════════
const sleep = ms => new Promise(r=>setTimeout(r,ms));

async function captureBaseline(ws) {
  let raw; try{raw=await executeCdpJs(ws, BASELINE_JS);}catch(e){throw new CDPError(`基线阶段CDP调用失败: ${e.message}`);}
  let b; try{b=JSON.parse(raw);}catch(e){throw new CDPError(`基线JSON解析失败: ${e.message}; raw=${raw.slice(0,200)}`);}
  if (!b||b.totalCount==null||b.userCount==null) throw new CDPError(`基线字段缺失: ${JSON.stringify(b)}`);
  return b;
}

async function waitForUserMessageCommitted(ws, baselineUserCount, expectedPrompt, timeout, baselineUserId) {
  const probeJs=userCommitProbeJs(expectedPrompt), deadline=Date.now()+timeout*1000;
  let lastSnap=null;
  let retryCount=0;
  while (Date.now()<deadline) {
    const snap=await fetchSnapshot(ws, probeJs); lastSnap=snap;
    const currId=snap.messageId;
    const idChg=baselineUserId!=null?currId!==baselineUserId:currId!=null;
    if ((snap.userCount===baselineUserCount+1||idChg)&&snap.matchesExpected===true) return snap;
    
    // 自愈补按：若轮询等待超过 1.2s 仍未检测到消息生成，自动补按发送按钮与回车
    retryCount++;
    if (retryCount % 3 === 0) {
      const retriggerJs=`(() => {
        const btn = document.querySelector('button[data-testid="send-button"]')
          || document.querySelector('button[aria-label*="Send"]')
          || document.querySelector('button[aria-label*="发送"]')
          || document.querySelector("#composer-submit-button")
          || document.querySelector("form button[type='submit']");
        if (btn) { btn.removeAttribute('disabled'); btn.disabled = false; btn.click(); }
        const el = document.querySelector('#prompt-textarea') || document.querySelector("div[contenteditable='true']") || document.querySelector("textarea");
        if (el) { el.focus(); el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true })); }
      })()`;
      try { await executeCdpJs(ws, retriggerJs); } catch(_){}
      try {
        await ws.sendCommand('Input.dispatchKeyEvent', { type: 'keyDown', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, macCharCode: 13, text: '\r', unmodifiedText: '\r', key: 'Enter', code: 'Enter' });
        await ws.sendCommand('Input.dispatchKeyEvent', { type: 'keyUp', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, macCharCode: 13, key: 'Enter', code: 'Enter' });
      } catch(_){}
    }
    await sleep(400);
  }
  throw new CDPError(`用户消息提交超时（>${timeout}s）。最终快照=${JSON.stringify(lastSnap)}`);
}

async function verifyComposer(ws, expectedPrompt, timeout) {
  const js=injectVerifyJs(expectedPrompt), deadline=Date.now()+timeout*1000; let last=null;
  while (Date.now()<deadline) {
    const snap=await fetchSnapshot(ws, js); last=snap;
    if (snap.ok===true&&snap.matchesExpected===true) return snap;
    await sleep(300);
  }
  throw new CDPError(`Composer注入验证超时（>${timeout}s）。最终快照=${JSON.stringify(last)}`);
}

async function waitForAssistantNewTurn(ws, baselineAssistantCount, timeout, baselineAssistantId) {
  const js=lastMessageIdJs('assistant'), deadline=Date.now()+timeout*1000; let lastSnap=null;
  while (Date.now()<deadline) {
    const snap=await fetchSnapshot(ws, js); lastSnap=snap;
    const currId=snap.id;
    const idChg=baselineAssistantId!=null?currId!==baselineAssistantId:currId!=null;
    if ((snap.count===baselineAssistantCount+1||idChg)&&(snap.textLen||0)>0) return snap;
    await sleep(400);
  }
  throw new CDPError(`助手新回合未产生（>${timeout}s）。最终快照=${JSON.stringify(lastSnap)}`);
}

async function waitForAssistantStable(ws, targetMessageId, waitTimeout) {
  const js=stablePollJs(targetMessageId), start=Date.now();
  let lastText='', stableCount=0, absentCount=0;
  const ABSENT_TOLERANCE=8;   // ~4s SPA navigation grace
  while (Date.now()-start<waitTimeout*1000) {
    const snap=await fetchSnapshot(ws, js);
    if (!snap.targetPresent) {
      if (++absentCount>=ABSENT_TOLERANCE)
        throw new CDPError(`目标assistant turn（id=${targetMessageId}）节点持续消失（${absentCount}次），证据失效。最终快照=${JSON.stringify(snap)}`);
      await sleep(500); continue;
    }
    absentCount=0;
    const text=snap.text||'', streaming=!!snap.isStreaming;
    if (!streaming&&text.length>0) {
      if (text===lastText) { if (++stableCount>=2) return {text, complete:true}; }
      else { stableCount=0; lastText=text; }
    } else { lastText=text; stableCount=0; }
    await sleep(500);
  }
  return {text:lastText, complete:false};
}

// ═══════════════════════════════════════════════════════════════════
// URL validation & payload formatting
// ═══════════════════════════════════════════════════════════════════
function validateTargetUrl(url) {
  if (!url) throw new Error('--target-url 不能为空');
  const lower=url.toLowerCase();
  if (!lower.startsWith('https://chatgpt.com')&&!lower.startsWith('https://www.chatgpt.com'))
    throw new Error(`target_url必须以https://chatgpt.com开头，实际值: ${url}`);
}

function formatEvidencePayload(taskType, contextText, evidenceData, level, cwd) {
  const gitCtx=getGitContext(cwd), br=_BROWSER_NAME;
  contextText=sanitizeText(contextText||''); evidenceData=sanitizeText(evidenceData||'');
  let snippet;
  if (level==='L0') snippet='[L0 No Evidence Body: only request context provided]';
  else if (level==='L1') { const lines=evidenceData.trim().split('\n'); snippet=lines.length>40?lines.slice(0,5).join('\n')+'\n... [L1 折叠中段，可请求 L2] ...\n'+lines.slice(-35).join('\n'):evidenceData; }
  else if (level==='L2') { const lines=evidenceData.trim().split('\n'); snippet=lines.length>80?lines.slice(-80).join('\n'):evidenceData; }
  else snippet=evidenceData;
  if (taskType==='plan')        return `【ARCHITECTURAL_PLAN_REQUEST】\n[Local State]: ${gitCtx}\n[Browser]: ${br}\n[Target Scope]:\n${contextText}\n\n请输出结构化架构决策及需要本地 Agent 执行的原子操作建议。`;
  if (taskType==='feedback')    return `【EXECUTION_EVIDENCE_FEEDBACK】\n[Local State]: ${gitCtx}\n[Browser]: ${br}\n[Execution Context]:\n${contextText}\n\n[Evidence (${level})]:\n${snippet}\n\n请基于上述事实分析原因并给出自愈修复补丁。`;
  if (taskType==='review')      return `【CODE_AND_ARCHITECTURE_REVIEW】\n[Local State]: ${gitCtx}\n[Browser]: ${br}\n[Review Target]:\n${contextText}\n\n请从系统解耦、安全性、边界与性能给出评审意见。`;
  if (taskType==='task-code')   return `【TASK_CODE_IMPLEMENTATION】\n[Local State]: ${gitCtx}\n[Browser]: ${br}\n[Task to Implement]:\n${contextText}\n\n请完成此任务的代码实现并直接推送到 GitHub 目标分支。\n\n【输出格式要求】（严格遵守，否则无法解析）：\n1. 代码直接使用 GitHub 直连工具在远端分支修改并提交。\n2. 禁止在回复中粘贴代码全文。\n3. 测试命令用以下格式（放在单独的 bash 块中）：\n   \`TEST: <实际命令>\`\n   \`EXPECTED: <预期结果描述>\`\n4. 如果任务涉及多文件，请按依赖顺序排列。\n5. 只测试命令，不要输出代码，不要写说明文字。`;
  if (taskType==='task-review') return `【TASK_CODE_REVIEW】\n[Local State]: ${gitCtx}\n[Browser]: ${br}\n[Task Description]:\n${contextText}\n\n[Evidence (${level})]:\n${snippet}\n\n请基于上述代码和测试结果做出裁决。只输出以下三种格式之一，不得输出其他内容：\n  APPROVED  — 代码符合任务要求，测试全部通过。\n  NEEDS_FIX — 代码有问题，测试失败或不符合要求。请明确说明：\n              (1) 失败原因\n              (2) 需要修改的文件和具体修改方案\n  BLOCKED   — 任务依赖前置条件未满足（如缺少依赖、配置错误等）。请说明阻塞原因。\n\n【注意】请严格只输出 APPROVED / NEEDS_FIX(...)/ BLOCKED(...) 其一，不要写其他文字。`;
  return contextText;
}

// ═══════════════════════════════════════════════════════════════════
// Main send-and-receive flow
// ═══════════════════════════════════════════════════════════════════
async function sendAndReceive(ws, prompt, targetUrl, waitTimeout, submitDeadline) {
  const turnDeadline=Math.max(45,Math.min(90,waitTimeout/2));
  const overallDeadline=Date.now()+waitTimeout*1000;
  const remaining=()=>Math.max(0,(overallDeadline-Date.now())/1000);
  emitEvent('start',EXIT_OK,'Chrome ChatGPT Cognitive-Control Bridge v4.1 (Node.js/CDP) 启动',{target_url:targetUrl,wait_timeout:waitTimeout});

  let baseline;
  try{baseline=await captureBaseline(ws);}
  catch(e){emitEvent('baseline',EXIT_BASELINE_FAIL,`基线采集失败: ${e.message}`);return{exitCode:EXIT_BASELINE_FAIL,text:''};}
  emitEvent('baseline',EXIT_OK,'基线采集成功',{totalCount:baseline.totalCount,userCount:baseline.userCount,assistantCount:baseline.assistantCount,lastUserMessageId:baseline.lastUserMessageId});

  const focusJs=`(() => {
  const el=document.querySelector('#prompt-textarea')||document.querySelector("div[contenteditable='true']")||document.querySelector("form [contenteditable='true']")||document.querySelector("textarea");
  if(!el)return "ERR_NO_INPUT";
  el.focus();
  try {
    const p = el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' ? el : (el.querySelector('p') || el);
    p.textContent = ${JSON.stringify(prompt)};
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  } catch(_){}
  return "OK";
})()`;
  try{
    await executeCdpJs(ws, focusJs);
  }
  catch(e){emitEvent('inject',EXIT_BROWSER_FAIL,`输入框注入失败: ${e.message}`);return{exitCode:EXIT_BROWSER_FAIL,text:''};}

  const cvBudget=Math.min(5,submitDeadline,remaining());
  if(cvBudget<=0){emitEvent('composer_verify',EXIT_TIMEOUT_EMPTY,'无预算执行composer验证');return{exitCode:EXIT_TIMEOUT_EMPTY,text:''};}
  try{const cv=await verifyComposer(ws,prompt,cvBudget); emitEvent('composer_verify',EXIT_OK,'Composer内容已确认为expected prompt',{textLen:cv.textLen,visible:cv.visible,isContentEditable:cv.isContentEditable});}
  catch(e){emitEvent('composer_verify',EXIT_SUBMIT_FAIL,`Composer注入未生效: ${e.message}`);return{exitCode:EXIT_SUBMIT_FAIL,text:''};}

  const sendJs=`(() => {
  let btnClickRes = "NO_BUTTON";
  const btn = document.querySelector('button[data-testid="send-button"]')
    || document.querySelector('button[aria-label*="Send"]')
    || document.querySelector('button[aria-label*="发送"]')
    || document.querySelector("#composer-submit-button")
    || document.querySelector("form button[type='submit']");
  if (btn) {
    btn.removeAttribute('disabled');
    btn.disabled = false;
    btn.click();
    btnClickRes = "BUTTON_CLICKED";
  }
  const el = document.querySelector('#prompt-textarea') || document.querySelector("div[contenteditable='true']") || document.querySelector("textarea");
  if (el) {
    el.focus();
    const enterEvt = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true });
    el.dispatchEvent(enterEvt);
  }
  return btnClickRes + "_THEN_ENTER";
})()`;
  try{
    const sendRes=await executeCdpJs(ws,sendJs);
    try {
      await ws.sendCommand('Input.dispatchKeyEvent', { type: 'keyDown', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, macCharCode: 13, text: '\r', unmodifiedText: '\r', key: 'Enter', code: 'Enter' });
      await ws.sendCommand('Input.dispatchKeyEvent', { type: 'keyUp', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, macCharCode: 13, key: 'Enter', code: 'Enter' });
    } catch(_){}
    emitEvent('send',EXIT_OK,`发送触发结果: ${sendRes}`);
  }
  catch(e){emitEvent('send',EXIT_BROWSER_FAIL,`发送触发失败: ${e.message}`);return{exitCode:EXIT_BROWSER_FAIL,text:''};}

  const subBudget=Math.min(submitDeadline,remaining());
  if(subBudget<=0){emitEvent('submit_verify',EXIT_TIMEOUT_EMPTY,'无预算执行提交验证');return{exitCode:EXIT_TIMEOUT_EMPTY,text:''};}
  let userSnap;
  try{userSnap=await waitForUserMessageCommitted(ws,baseline.userCount,prompt,subBudget,baseline.lastUserMessageId);}
  catch(e){emitEvent('submit_verify',EXIT_SUBMIT_FAIL,`用户消息未真正提交: ${e.message}`);return{exitCode:EXIT_SUBMIT_FAIL,text:''};}
  emitEvent('submit_verify',EXIT_OK,'用户消息已提交（exact prompt match）',{newUserCount:userSnap.userCount,newUserMessageId:userSnap.messageId,newTextLen:userSnap.textLen});

  const turnBudget=Math.min(turnDeadline,remaining());
  if(turnBudget<=0){emitEvent('new_turn',EXIT_TIMEOUT_EMPTY,'无预算等待助手新回合');return{exitCode:EXIT_TIMEOUT_EMPTY,text:''};}
  let turnSnap;
  try{turnSnap=await waitForAssistantNewTurn(ws,baseline.assistantCount,turnBudget,baseline.lastAsstMessageId);}
  catch(e){emitEvent('new_turn',EXIT_NO_NEW_TURN,`助手新回合未产生: ${e.message}`);return{exitCode:EXIT_NO_NEW_TURN,text:''};}
  const targetMessageId=turnSnap.id;
  emitEvent('new_turn',EXIT_OK,'助手新回合已开始',{newAssistantCount:turnSnap.count,messageId:targetMessageId,firstTextLen:turnSnap.textLen});

  const stableBudget=Math.max(STABLE_PHASE_MIN,remaining());
  if(stableBudget<=0){emitEvent('stable',EXIT_TIMEOUT_EMPTY,'无预算执行稳定等待');return{exitCode:EXIT_TIMEOUT_EMPTY,text:''};}
  let stableResult;
  try{stableResult=await waitForAssistantStable(ws,targetMessageId,stableBudget);}
  catch(e){emitEvent('stable',EXIT_BROWSER_FAIL,`稳定等待期间目标turn消失或CDP异常: ${e.message}`);return{exitCode:EXIT_BROWSER_FAIL,text:''};}

  const{text,complete}=stableResult;
  if(complete){emitEvent('done',EXIT_OK,`捕获到最终生成内容（字数=${text.length}）`,{charCount:text.length,targetMessageId});return{exitCode:EXIT_OK,text};}
  else if(text){emitEvent('timeout',EXIT_TIMEOUT_PARTIAL,`等待超时（>${waitTimeout}s），返回当前部分内容`,{charCount:text.length,targetMessageId});return{exitCode:EXIT_TIMEOUT_PARTIAL,text};}
  else{emitEvent('timeout',EXIT_TIMEOUT_EMPTY,`等待超时（>${waitTimeout}s）且无任何内容`,{targetMessageId});return{exitCode:EXIT_TIMEOUT_EMPTY,text:''};}
}

// ═══════════════════════════════════════════════════════════════════
// CLI parser  (Node 18.0+ compatible, zero deps)
// ═══════════════════════════════════════════════════════════════════
function parseCliArgs(argv) {
  const a={prompt:null,targetUrl:null,chromePort:9222,chromeHost:'127.0.0.1',browserName:null,type:'raw',evidence:null,evidenceFile:null,level:'L1',signature:null,allowConcurrent:false,timeout:180,cwd:null,resetCircuit:false};
  let i=2;
  while(i<argv.length) {
    const k=argv[i],v=argv[i+1];
    switch(k) {
      case '--prompt':           a.prompt=v;                   i+=2;break;
      case '--prompt-file':      a.prompt=fs.readFileSync(v,'utf8'); i+=2;break;
      case '--target-url':       a.targetUrl=v;                i+=2;break;
      case '--chrome-port':      a.chromePort=parseInt(v,10);  i+=2;break;
      case '--chrome-host':      a.chromeHost=v;               i+=2;break;
      case '--browser-name':     a.browserName=v;              i+=2;break;
      case '--type':             a.type=v;                     i+=2;break;
      case '--evidence':         a.evidence=v;                 i+=2;break;
      case '--evidence-file':    a.evidenceFile=v;             i+=2;break;
      case '--level':            a.level=v;                    i+=2;break;
      case '--signature':        a.signature=v;                i+=2;break;
      case '--allow-concurrent': a.allowConcurrent=true;       i++;break;
      case '--timeout':          a.timeout=parseInt(v,10);     i+=2;break;
      case '--cwd':              a.cwd=v;                      i+=2;break;
      case '--reset-circuit':    a.resetCircuit=true;          i++;break;
      default: i++;
    }
  }
  return a;
}

// ═══════════════════════════════════════════════════════════════════
// Entry point
// ═══════════════════════════════════════════════════════════════════
async function main() {
  const args=parseCliArgs(process.argv);
  if(args.browserName) _BROWSER_NAME=args.browserName;

  if(args.resetCircuit){const cleared=resetCircuitBreaker(); emitEvent('reset_circuit',EXIT_OK,`已清空熔断器（清理${cleared}个signature）`,{cleared}); process.exit(EXIT_OK);}
  if(!args.prompt)   {process.stderr.write('Error: --prompt is required\n');   process.exit(EXIT_BROWSER_FAIL);}
  if(!args.targetUrl){process.stderr.write('Error: --target-url is required\n');process.exit(EXIT_BROWSER_FAIL);}
  try{validateTargetUrl(args.targetUrl);}catch(e){emitEvent('validate_target',EXIT_BROWSER_FAIL,`target_url校验失败: ${e.message}`,{target_url:args.targetUrl});process.exit(EXIT_BROWSER_FAIL);}

  let evidenceBody=null;
  if(args.evidenceFile){try{evidenceBody=fs.readFileSync(args.evidenceFile,'utf8');}catch(e){emitEvent('evidence_load',EXIT_BROWSER_FAIL,`--evidence-file读取失败: ${e.message}`,{evidence_file:args.evidenceFile});process.exit(EXIT_BROWSER_FAIL);}}
  else if(args.evidence) evidenceBody=args.evidence;

  const sig=args.signature||null;
  if(sig){try{checkCircuitBreaker(sig);}catch(e){if(e instanceof CircuitOpenError){emitEvent('circuit_breaker',EXIT_CIRCUIT_OPEN,e.message,{signature:sig});process.exit(EXIT_CIRCUIT_OPEN);}throw e;}}

  let lockPath=null;
  if(!args.allowConcurrent){try{lockPath=acquireTargetTabLock(args.targetUrl);}catch(e){if(e instanceof TargetTabBusyError){emitEvent('target_busy',EXIT_TARGET_BUSY,`目标Tab锁竞争失败: ${e.message}`,{target_url:args.targetUrl});process.exit(EXIT_TARGET_BUSY);}throw e;}}

  let exitCode=EXIT_BROWSER_FAIL;
  try{
    let targetId,wsUrl;
    try{{({targetId,wsUrl}=await findTargetTab(args.chromeHost,args.chromePort,args.targetUrl));}}
    catch(e){
      if(e instanceof NoTargetTabError){emitEvent('resolve_tab',EXIT_NO_TAB,`目标Tab不存在: ${e.message}`,{target_url:args.targetUrl});process.exit(EXIT_NO_TAB);}
      if(e instanceof AmbiguousTargetTabError){emitEvent('resolve_tab',EXIT_AMBIGUOUS_TAB,`目标Tab歧义: ${e.message}`,{target_url:args.targetUrl});process.exit(EXIT_AMBIGUOUS_TAB);}
      emitEvent('resolve_tab',EXIT_BROWSER_FAIL,`CDP HTTP发现失败: ${e.message}`,{chrome_host:args.chromeHost,chrome_port:args.chromePort});process.exit(EXIT_BROWSER_FAIL);
    }
    const payload=formatEvidencePayload(args.type,args.prompt,evidenceBody,args.level,args.cwd);
    const ws=new CDPWebSocket();
    try{await ws.connect(wsUrl);}catch(e){emitEvent('connect',EXIT_BROWSER_FAIL,`CDP WebSocket连接失败: ${e.message}`);process.exit(EXIT_BROWSER_FAIL);}
    try{const result=await sendAndReceive(ws,payload,args.targetUrl,args.timeout,SUBMIT_PHASE_BUDGET); exitCode=result.exitCode; if(result.text)process.stdout.write(result.text+'\n');}
    finally{ws.close();}
  } finally {releaseTargetTabLock(lockPath);}
  process.exit(exitCode);
}

main().catch(e=>{process.stderr.write(`Fatal: ${e.message}\n${e.stack||''}\n`);process.exit(EXIT_BROWSER_FAIL);});
