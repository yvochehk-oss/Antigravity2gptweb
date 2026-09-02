import { useEffect, useState } from 'react';
import { Compass, Layers, RefreshCw, TrendingUp } from 'lucide-react';
import { fetchJson, postJson, ApiError } from '../api';
import { ProjectItem } from '../types';
import { DataStatusCard } from './DataStatusCard';

interface TaxPlanningViewProps {
  projects: ProjectItem[];
  selectedProjectId?: string;
  onSelectProject?: (id: string) => void;
  onAskAiAboutRisk?: (topic: string) => void;
}

type PlanningResult = Record<string, unknown>;

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function list(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter(item => item && typeof item === 'object') as Array<Record<string, unknown>> : [];
}

function label(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString('zh-CN') : '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  return String(value);
}

export function TaxPlanningView({ projects, selectedProjectId, onSelectProject }: TaxPlanningViewProps) {
  const [currentProjectId, setCurrentProjectId] = useState(selectedProjectId ?? '');
  const currentProject = projects.find(project => project.id === currentProjectId);

  const [packageName, setPackageName] = useState('');
  const [category, setCategory] = useState('劳务');
  const [packageAmount, setPackageAmount] = useState('');
  const [objective, setObjective] = useState('balanced');
  const [internalMinRatio, setInternalMinRatio] = useState('0');
  const [internalMaxRatio, setInternalMaxRatio] = useState('100');
  const [preferredRatio, setPreferredRatio] = useState('');

  const [planningResult, setPlanningResult] = useState<PlanningResult | null>(null);
  const [penetrationData, setPenetrationData] = useState<unknown>(null);
  const [isCalculating, setIsCalculating] = useState(false);
  const [statusMessage, setStatusMessage] = useState('尚未执行筹划模拟。');
  const [status, setStatus] = useState<'READY' | 'DEGRADED' | 'UNAVAILABLE'>('UNAVAILABLE');

  useEffect(() => {
    const nextId = selectedProjectId && projects.some(project => project.id === selectedProjectId)
      ? selectedProjectId
      : projects[0]?.id ?? '';
    setCurrentProjectId(nextId);
  }, [projects, selectedProjectId]);

  useEffect(() => {
    setPlanningResult(null);
    setPenetrationData(null);
    setPackageName('');
    setCategory('劳务');
    setPackageAmount('');
    setObjective('balanced');
    setInternalMinRatio('0');
    setInternalMaxRatio('100');
    setPreferredRatio('');

    if (!currentProject) {
      setStatus('UNAVAILABLE');
      setStatusMessage('没有可用项目，无法读取筹划上下文。');
      return;
    }

    setStatus('READY');
    setStatusMessage('已加载项目筹划上下文。请填写用户假设后手动运行模拟。');

    const controller = new AbortController();
    void fetchJson<unknown>(`/api/projects/${currentProject.numericId}/system-penetration`, { signal: controller.signal })
      .then(data => {
        setPenetrationData(data);
      })
      .catch(error => {
        if (!controller.signal.aborted) {
          setStatus('DEGRADED');
          setStatusMessage(`穿透数据 DEGRADED：${error instanceof Error ? error.message : '接口不可用'}`);
        }
      });

    return () => controller.abort();
  }, [currentProject]);

  const handleProjectChange = (id: string) => {
    setCurrentProjectId(id);
    onSelectProject?.(id);
  };

  const handleRunPlanning = async () => {
    if (!currentProject) {
      setStatus('UNAVAILABLE');
      setStatusMessage('没有可用项目，无法启动筹划。');
      return;
    }

    const amount = Number(packageAmount);
    const minRatio = Number(internalMinRatio) / 100;
    const maxRatio = Number(internalMaxRatio) / 100;
    const preferred = preferredRatio.trim() === '' ? undefined : Number(preferredRatio) / 100;

    if (
      !packageName.trim()
      || !Number.isFinite(amount)
      || amount <= 0
      || !Number.isFinite(minRatio)
      || !Number.isFinite(maxRatio)
      || minRatio < 0
      || maxRatio > 1
      || minRatio > maxRatio
      || (preferred !== undefined && (!Number.isFinite(preferred) || preferred < 0 || preferred > 1))
    ) {
      setStatus('DEGRADED');
      setStatusMessage('请填写业务包名称、正数金额，并确认比例范围为 0–100%。');
      return;
    }

    setIsCalculating(true);
    setStatusMessage('正在调用确定性筹划引擎进行 Scenario 模拟…');
    try {
      const result = await postJson<PlanningResult>(
        `/api/projects/${currentProject.numericId}/allocation-planning/recommend`,
        {
          package_name: packageName.trim(),
          category,
          package_amount: amount,
          objective,
          internal_min_ratio: minRatio,
          internal_max_ratio: maxRatio,
          ...(preferred === undefined ? {} : { preferred_internal_ratio: preferred }),
          persist: false,
        },
      );
      setPlanningResult(result);
      setStatus('READY');
      setStatusMessage('Scenario 模拟结果来自 Tax 确定性引擎，未持久化。');
    } catch (error) {
      setPlanningResult(null);
      setStatus('DEGRADED');
      setStatusMessage(error instanceof ApiError ? error.message : '筹划接口调用失败。');
    } finally {
      setIsCalculating(false);
    }
  };

  if (!currentProject) {
    return (
      <div className="space-y-6">
        <h2 className="text-[26px] font-bold text-[#dae2fd]">AI 税务筹划</h2>
        <DataStatusCard status="UNAVAILABLE" title="筹划数据不可用" message="请先配置真实项目并让 Tax API 返回项目数据，系统不会使用静态筹划结果。" />
      </div>
    );
  }

  const recommended = record(planningResult?.recommended);
  const allocations = list(recommended.allocations);
  const scenarios = list(planningResult?.scenarios);
  const recommendation = record(planningResult?.ai_recommendation);

  return (
    <div className="space-y-6">
      <div className="glass-panel p-5 rounded-2xl border border-[#444653]/30 glow-cyan flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#8b5cf6]/20 flex items-center justify-center border border-[#a78bfa]/40">
            <Compass className="w-5 h-5 text-[#a78bfa]" />
          </div>
          <div>
            <h2 className="text-[20px] font-bold text-[#dde1ff]">AI 税务筹划</h2>
            <p className="text-[12px] text-[#8e909f] mt-0.5">项目切换只加载真实上下文；推荐计算仅由用户显式触发。</p>
          </div>
        </div>
        <select
          aria-label="选择筹划项目"
          value={currentProjectId}
          onChange={event => handleProjectChange(event.target.value)}
          className="bg-[#0b1326] border border-[#4cd7f6]/60 text-[#dae2fd] rounded-lg px-3 py-2 text-[13px] min-w-[280px]"
        >
          {projects.map(project => (
            <option key={project.id} value={project.id}>
              {project.projectCode} · {project.name}
            </option>
          ))}
        </select>
      </div>

      <div className="rounded-xl border border-[#F59E0B]/40 bg-[#F59E0B]/10 px-4 py-3" role="note">
        <p className="text-[13px] font-bold text-[#F59E0B]">
          SCENARIO · 模拟方案 | 本视图基于用户输入假设，不代表当前项目真实经营结果，不属于法人法定申报依据。
        </p>
      </div>

      <DataStatusCard status={status} title="筹划接口状态" message={statusMessage} />

      <div className="glass-panel p-5 rounded-2xl border border-[#444653]/30">
        <div className="flex items-center gap-2 mb-4">
          <Layers className="w-4 h-4 text-[#a78bfa]" />
          <h3 className="text-[15px] font-bold text-[#dde1ff]">Scenario 用户输入假设</h3>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 text-[12px]">
          <label className="text-[#8e909f]">
            业务包名称
            <input
              value={packageName}
              onChange={event => setPackageName(event.target.value)}
              placeholder="请输入业务包名称"
              className="mt-1 w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd]"
            />
          </label>
          <label className="text-[#8e909f]">
            业务类型
            <select
              value={category}
              onChange={event => setCategory(event.target.value)}
              className="mt-1 w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd]"
            >
              <option value="劳务">劳务</option>
              <option value="材料">材料</option>
              <option value="设备">设备</option>
              <option value="专业分包">专业分包</option>
            </select>
          </label>
          <label className="text-[#8e909f]">
            业务包金额（元）
            <input
              aria-label="业务包金额（元）"
              type="number"
              min="0"
              step="0.01"
              value={packageAmount}
              onChange={event => setPackageAmount(event.target.value)}
              placeholder="请输入模拟金额"
              className="mt-1 w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd]"
            />
          </label>
          <label className="text-[#8e909f]">
            优化目标
            <select
              value={objective}
              onChange={event => setObjective(event.target.value)}
              className="mt-1 w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd]"
            >
              <option value="balanced">综合平衡</option>
              <option value="profit">利润优先</option>
              <option value="tax">税务现金优先</option>
              <option value="risk">风控合规优先</option>
            </select>
          </label>
          <label className="text-[#8e909f]">
            系统内最低比例（%，用户假设）
            <input
              aria-label="系统内最低比例（%，用户假设）"
              type="number"
              min="0"
              max="100"
              value={internalMinRatio}
              onChange={event => setInternalMinRatio(event.target.value)}
              className="mt-1 w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd]"
            />
          </label>
          <label className="text-[#8e909f]">
            系统内最高比例（%，用户假设）
            <input
              aria-label="系统内最高比例（%，用户假设）"
              type="number"
              min="0"
              max="100"
              value={internalMaxRatio}
              onChange={event => setInternalMaxRatio(event.target.value)}
              className="mt-1 w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd]"
            />
          </label>
          <label className="text-[#8e909f]">
            偏好比例（可选，%，用户假设）
            <input
              aria-label="偏好比例（可选，%，用户假设）"
              type="number"
              min="0"
              max="100"
              value={preferredRatio}
              onChange={event => setPreferredRatio(event.target.value)}
              placeholder="可留空"
              className="mt-1 w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd]"
            />
          </label>
          <div className="flex items-end">
            <button
              type="button"
              onClick={() => void handleRunPlanning()}
              disabled={isCalculating}
              className="w-full h-9 rounded-lg bg-[#8b5cf6] hover:bg-[#8b5cf6]/80 disabled:opacity-50 text-white font-bold flex items-center justify-center gap-2 shadow-lg shadow-[#8b5cf6]/20 cursor-pointer"
            >
              <RefreshCw className={`w-4 h-4 ${isCalculating ? 'animate-spin' : ''}`} />
              {isCalculating ? '计算中…' : '测算筹划沙盘'}
            </button>
          </div>
        </div>
      </div>

      {planningResult ? (
        <div className="space-y-4">
          <div className="glass-panel rounded-xl p-5 border border-[#4cd7f6]/30">
            <h3 className="font-bold text-[#dae2fd]">推荐结果</h3>
            <p className="text-[13px] text-[#c4c5d5] mt-2">{label(recommendation.summary)}</p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 text-[12px]">
              {[
                { k: 'scenario_id', l: '方案ID' },
                { k: 'internal_ratio', l: '系统内分配比例' },
                { k: 'internal_amount', l: '系统内分配金额' },
                { k: 'external_amount', l: '系统外分配金额' },
                { k: 'score', l: '综合评分' },
                { k: 'system_external_cost', l: '系统外真实成本' },
                { k: 'incremental_tax_cash', l: '边际税费支出' },
                { k: 'projected_management_profit', l: '预估管理利润' },
              ].map(item => (
                <div key={item.k} className="rounded-lg bg-[#131b2e] p-3 border border-[#444653]/20">
                  <p className="text-[#8e909f]">{item.l}</p>
                  <p className="text-[#dae2fd] font-semibold mt-1 break-all">{label(recommended[item.k])}</p>
                </div>
              ))}
            </div>
          </div>

          {allocations.length > 0 && (
            <div className="glass-panel rounded-xl p-5 border border-[#444653]/30">
              <h3 className="font-bold text-[#dae2fd] mb-3">分配明细</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-[12px] text-left">
                  <thead>
                    <tr className="text-[#8e909f] border-b border-[#444653]/30">
                      <th className="p-2">范围</th>
                      <th className="p-2">主体</th>
                      <th className="p-2 text-right">金额</th>
                      <th className="p-2 text-right">比例</th>
                    </tr>
                  </thead>
                  <tbody>
                    {allocations.map((item, index) => (
                      <tr key={index} className="border-b border-[#444653]/20">
                        <td className="p-2">{label(item.scope)}</td>
                        <td className="p-2">{label(item.party_code)} · {label(item.party_name)}</td>
                        <td className="p-2 text-right">{label(item.amount)}</td>
                        <td className="p-2 text-right">{label(item.share)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {scenarios.length > 0 && (
            <div className="glass-panel rounded-xl p-5 border border-[#444653]/30">
              <h3 className="font-bold text-[#dae2fd] mb-3 flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-[#4cd7f6]" />
                方案比较
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full text-[12px] text-left">
                  <thead>
                    <tr className="text-[#8e909f] border-b border-[#444653]/30">
                      <th className="p-2">方案</th>
                      <th className="p-2">系统内比例</th>
                      <th className="p-2">外部成本</th>
                      <th className="p-2">评分</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scenarios.map((item, index) => (
                      <tr key={index} className="border-b border-[#444653]/20">
                        <td className="p-2">{label(item.scenario_id)}</td>
                        <td className="p-2">{label(item.internal_ratio)}</td>
                        <td className="p-2">{label(item.system_external_cost)}</td>
                        <td className="p-2">{label(item.score)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      ) : (
        <DataStatusCard
          status="UNAVAILABLE"
          title="尚无筹划结果"
          message="填写用户假设并点击“测算筹划沙盘”后，真实模拟结果会显示在这里。"
        />
      )}

      {penetrationData && (
        <details className="glass-panel rounded-xl p-5 border border-[#444653]/30">
          <summary className="cursor-pointer text-[13px] font-semibold text-[#dae2fd]">查看项目穿透快照（原始 API 返回）</summary>
          <pre className="mt-3 text-[11px] text-[#c4c5d5] whitespace-pre-wrap overflow-auto">
            {JSON.stringify(penetrationData, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
