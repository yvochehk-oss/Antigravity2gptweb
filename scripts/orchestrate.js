#!/usr/bin/env node
/*
 * Windows-only orchestration layer for chrome_chatgpt.js.
 * The cloud Custom GPT owns all GitHub mutations. This program only keeps
 * task state, fast-forwards a clean checkout, runs declared verification, and
 * returns the resulting evidence for a task-review turn.
 */
'use strict';

const childProcess = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const HOME = path.join(os.homedir(), '.antigravity', 'orchestrator');
const BRIDGE = path.join(__dirname, 'chrome_chatgpt.js');
const MAX_FIX_ATTEMPTS = 5;
const GIT_TIMEOUT_MS = 30000;

function fail(message) { process.stderr.write(`[orchestrate] ${message}\n`); process.exitCode = 1; }
function now() { return new Date().toISOString(); }
function statePath(name) { return path.join(HOME, `${name}.state.json`); }
function projectDir(name) { return path.join(HOME, name); }
function ensureDir(dir) { fs.mkdirSync(dir, { recursive: true }); }
function readJson(file) { return JSON.parse(fs.readFileSync(file, 'utf8')); }
function writeJson(file, value) { ensureDir(path.dirname(file)); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n', 'utf8'); }
function run(exe, args, options = {}) {
  return childProcess.spawnSync(exe, args, { cwd: options.cwd, encoding: 'utf8', timeout: options.timeout || GIT_TIMEOUT_MS, shell: false });
}
function git(args, cwd, label) {
  const result = run('git', args, { cwd });
  if (result.error) throw new Error(`${label}: ${result.error.message}`);
  if (result.status !== 0) throw new Error(`${label}: ${(result.stderr || result.stdout || '').trim().slice(0, 500)}`);
  return (result.stdout || '').trim();
}
function cleanCheckout(cwd) {
  if (git(['status', '--porcelain'], cwd, '无法检查工作区')) throw new Error('本地工作区有未提交改动；拒绝同步，避免覆盖验收环境');
}
function syncBranch(cwd, branch) {
  cleanCheckout(cwd);
  git(['fetch', 'origin', branch], cwd, '无法获取远端提交');
  git(['pull', '--ff-only', 'origin', branch], cwd, '无法 fast-forward 拉取远端提交');
  const head = git(['rev-parse', 'HEAD'], cwd, '无法读取本地 HEAD');
  const remote = git(['rev-parse', `origin/${branch}`], cwd, '无法读取远端 HEAD');
  if (head !== remote) throw new Error(`拉取后 HEAD 不一致：local=${head}, origin=${remote}`);
  return head;
}
function parseTestCommands(text) {
  const out = [];
  const blocks = text.matchAll(/```bash\s*\n([\s\S]*?)```/g);
  for (const block of blocks) {
    const lines = block[1].split(/\r?\n/);
    for (let i = 0; i < lines.length; i += 1) {
      const m = lines[i].match(/^\s*TEST:\s*(.+)\s*$/);
      if (!m) continue;
      const expected = (lines[i + 1] || '').match(/^\s*EXPECTED:\s*(.+)\s*$/);
      if (expected) out.push({ command: m[1].trim(), expected: expected[1].trim() });
    }
  }
  return out;
}
function parseVerdict(text) {
  const clean = text.trim();
  if (/^APPROVED\b/.test(clean)) return { verdict: 'APPROVED', detail: '' };
  const m = clean.match(/^(NEEDS_FIX|BLOCKED)\s*[-–—:]?\s*([\s\S]*)$/);
  return m ? { verdict: m[1], detail: m[2].trim() } : { verdict: 'BLOCKED', detail: `无法解析 GPT 裁决：${clean.slice(0, 200)}` };
}
function bridge(type, prompt, state, extra = {}) {
  const args = [BRIDGE, '--type', type, '--prompt', prompt, '--target-url', state.chatgpt_url, '--cwd', state.cwd, '--timeout', String(extra.timeout || 360)];
  if (extra.evidence) args.push('--evidence', extra.evidence);
  if (extra.level) args.push('--level', extra.level);
  if (extra.signature) args.push('--signature', extra.signature);
  const result = run('node', args, { cwd: state.cwd, timeout: (extra.timeout || 360) * 1000 + 60000 });
  if (result.error) throw new Error(`bridge 启动失败: ${result.error.message}`);
  return { code: result.status ?? 4, text: result.stdout || '', events: result.stderr || '' };
}
function runTests(state, tests) {
  const results = [];
  for (const test of tests) {
    // Commands are received only after the cloud GPT has pushed the code. They
    // are intentionally visible in state and evidence; no local code is made.
    const r = childProcess.spawnSync(test.command, { cwd: state.cwd, encoding: 'utf8', timeout: 120000, shell: true });
    const code = r.status === null ? 124 : r.status;
    results.push(`COMMAND: ${test.command}\nEXPECTED: ${test.expected}\nEXIT_CODE: ${code}\nSTDOUT:\n${(r.stdout || '').slice(0, 4000)}\nSTDERR:\n${(r.stderr || r.error?.message || '').slice(0, 4000)}`);
  }
  return results.join('\n\n---\n\n');
}
function save(state) {
  state.updated_at = now();
  writeJson(statePath(state.name), state);
  writeJson(path.join(projectDir(state.name), 'TASKS.json'), { name: state.name, task_locked: state.task_locked, current_task_id: state.current_task_id, tasks: state.tasks });
}
function load(name) { return readJson(statePath(name)); }
function taskPrompt(state, task, fixNote = '') {
  return `【@GitHub 协同基线强制对齐】\n1. 目标仓库：${state.repo_url}\n2. 目标分支：${state.branch}\n3. 最新提交基线（Parent Commit）：${state.commit_sha_head}\n\n【任务 ${task.id}/${state.tasks.length}】${task.title}\n${task.description || ''}\n${fixNote ? `\n【需要修复】${fixNote}\n` : ''}\n请使用 GitHub 工具在目标分支修改、提交并推送。不要在回复中输出代码；仅在一个 bash 代码块中给出：\nTEST: <命令>\nEXPECTED: <预期结果>`;
}
function reviewPrompt(state, task, evidence) {
  return `【任务 ${task.id}】${task.title}\n【已验证远端提交】${state.commit_sha_head}\n\n【本地测试结果】\n${evidence}\n\n只输出一项：APPROVED、NEEDS_FIX(<原因>) 或 BLOCKED(<原因>)。`;
}
function executeTask(state, task) {
  task.status = task.attempts ? 'fixing' : 'coding';
  while (task.attempts < MAX_FIX_ATTEMPTS) {
    task.attempts += 1;
    const response = bridge('task-code', taskPrompt(state, task, task.last_fix_note), state, { signature: `task-code:${state.name}:${task.id}:${task.attempts}` });
    if (response.code !== 0) { task.status = 'blocked'; task.last_verdict = 'BLOCKED'; task.last_fix_note = `GPT 调用失败 exit=${response.code}`; save(state); return false; }
    const tests = parseTestCommands(response.text);
    if (!tests.length) { task.status = 'blocked'; task.last_verdict = 'BLOCKED'; task.last_fix_note = 'GPT 未提供有效 TEST/EXPECTED 命令'; save(state); return false; }
    try { state.commit_sha_head = syncBranch(state.cwd, state.branch); }
    catch (error) { task.status = 'blocked'; task.last_verdict = 'BLOCKED'; task.last_fix_note = `无法安全同步远端提交: ${error.message}`; save(state); return false; }
    task.status = 'testing'; task.test_commands = tests.map((x) => x.command); task.test_results = runTests(state, tests); task.test_passed = !/\nEXIT_CODE: (?!0\b)/.test(task.test_results);
    const review = bridge('task-review', reviewPrompt(state, task, task.test_results), state, { evidence: task.test_results, level: 'L2', timeout: 180, signature: `task-review:${state.name}:${task.id}:${task.attempts}` });
    if (review.code !== 0) { task.status = 'blocked'; task.last_verdict = 'BLOCKED'; task.last_fix_note = `GPT 审查失败 exit=${review.code}`; save(state); return false; }
    const verdict = parseVerdict(review.text); task.last_verdict = verdict.verdict; task.last_fix_note = verdict.detail;
    if (verdict.verdict === 'APPROVED') { task.status = 'approved'; task.completed_at = now(); save(state); return true; }
    if (verdict.verdict === 'BLOCKED') { task.status = 'blocked'; save(state); return false; }
    save(state);
  }
  task.status = 'failed'; task.last_verdict = 'BLOCKED'; task.last_fix_note = `达到最大修复轮次 ${MAX_FIX_ATTEMPTS}`; save(state); return false;
}
function parseTasks(plan) {
  const rows = [...plan.matchAll(/^\s*(?:任务\s*)?(\d+)[.、:：\-\s]+(.+)$/gm)];
  return rows.map((m, i) => ({ id: i + 1, title: m[2].trim().slice(0, 100), description: m[0].trim(), status: 'pending', attempts: 0, test_commands: [], test_results: '', test_passed: null, last_verdict: '', last_fix_note: '' }));
}
function arg(name, required = true) { const i = process.argv.indexOf(name); const value = i >= 0 ? process.argv[i + 1] : null; if (required && !value) throw new Error(`缺少 ${name}`); return value; }
function init() {
  const name = arg('--name'), requirement = arg('--requirement'), target = arg('--target-url'), repo = arg('--repo'), cwd = path.resolve(arg('--cwd'));
  const branch = arg('--branch', false) || `orchestrate/${name}`;
  if (fs.existsSync(statePath(name))) throw new Error(`项目 ${name} 已存在`);
  const state = { name, created_at: now(), updated_at: now(), requirement, chatgpt_url: target, repo_url: repo, branch, cwd, browser: 'windows-cdp', bridge_script: BRIDGE, plan_md_path: path.join(projectDir(name), 'PLAN.md'), task_locked: false, current_task_id: null, tasks: [], commit_sha_head: '' };
  state.commit_sha_head = syncBranch(cwd, branch);
  const plan = bridge('plan', `【项目需求】\n${requirement}\n请输出可执行的编号任务计划，每项都必须可单独提交和验证。`, state, { timeout: 300, signature: `init:${name}` });
  if (plan.code !== 0) throw new Error(`方案生成失败 exit=${plan.code}`);
  ensureDir(projectDir(name)); fs.writeFileSync(state.plan_md_path, `# 项目计划：${name}\n\n${plan.text}\n`, 'utf8'); save(state);
}
function lock() { const state = load(arg('--name')); const plan = fs.readFileSync(state.plan_md_path, 'utf8'); state.tasks = parseTasks(plan); if (!state.tasks.length) throw new Error('未能从 PLAN.md 解析任务'); state.task_locked = true; save(state); }
function runTask() { const state = load(arg('--name')); if (!state.task_locked) throw new Error('请先 lock'); const wanted = arg('--task-id', false); const task = wanted ? state.tasks.find((x) => String(x.id) === wanted) : state.tasks.find((x) => x.status === 'pending'); if (!task) throw new Error('没有待执行任务'); state.current_task_id = task.id; const ok = executeTask(state, task); process.exitCode = ok ? 0 : 1; }
function status() { const state = load(arg('--name')); process.stdout.write(JSON.stringify({ name: state.name, branch: state.branch, head: state.commit_sha_head, tasks: state.tasks.map((x) => ({ id: x.id, title: x.title, status: x.status, verdict: x.last_verdict })) }, null, 2) + '\n'); }
function main() { const command = process.argv[2]; if (command === 'init') return init(); if (command === 'lock') return lock(); if (command === 'run-task') return runTask(); if (command === 'status') return status(); throw new Error('用法: orchestrate.js <init|lock|run-task|status> ...'); }
try { main(); } catch (error) { fail(error.message); }
