import { useEffect, useState } from 'react';
import { Compass, Layers, RefreshCw, TrendingUp, Sparkles, CheckCircle2 } from 'lucide-react';
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

interface PackagePreset {
  id: string;
  name: string;
  category: '劳务' | '材料' | '设备' | '专业分包';
  amount: number;
  internalMinRatio: string;
  internalMaxRatio: string;
  preferredRatio: string;
  objective: 'balanced' | 'profit' | 'tax' | 'risk';
  description?: string;
}

const PROJECT_PACKAGE_PRESETS: Record<string, PackagePreset[]> = {
  'YB-DEMO-001': [
    { id: 'yb-1', name: '主体工程建筑劳务用工分包', category: '劳务', amount: 8000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '60', objective: 'balanced', description: '匹配主体用工合同 YB-B-001' },
    { id: 'yb-2', name: '建筑材料与特种物资采购包', category: '材料', amount: 12000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '75', objective: 'balanced', description: '匹配建材直供合同 YB-C-001' },
    { id: 'yb-3', name: '施工起重机械与设备租赁包', category: '设备', amount: 3000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '50', objective: 'tax', description: '匹配塔吊机械租赁 YB-D-001' },
    { id: 'yb-4', name: '厂区雨污水管网生态开挖专业分包', category: '专业分包', amount: 3000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '60', objective: 'balanced', description: '匹配管网开挖 YB-EXT-GY-001' },
  ],
  'CD-TF-001': [
    { id: 'tf-1', name: '超高层主体结构劳务用工分包', category: '劳务', amount: 260000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '65', objective: 'balanced', description: '匹配超高层主体劳务 TF-A08-C01' },
    { id: 'tf-2', name: '大宗钢材与高标号商砼直采包', category: '材料', amount: 450000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '80', objective: 'balanced', description: '匹配大宗材料采购 TF-A08-B01' },
    { id: 'tf-3', name: '动臂塔吊与超重型施工设备租赁包', category: '设备', amount: 110000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '50', objective: 'tax', description: '匹配超重型机械租赁 TF-A08-D01' },
    { id: 'tf-4', name: '幕墙工程与弱电智能化专业分包', category: '专业分包', amount: 280000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '70', objective: 'profit', description: '匹配幕墙智能化分包 TF-A08-A11' },
    { id: 'tf-5', name: '钢结构深化设计与高空吊装分包', category: '专业分包', amount: 40000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '60', objective: 'balanced', description: '匹配钢结构吊装 TF-A08-A05' },
  ],
  'CY-CQ-002': [
    { id: 'cy-1', name: '跨江桥梁深水墩基础劳务作业包', category: '劳务', amount: 140000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '60', objective: 'balanced', description: '匹配深水墩基础劳务 CY-A03-C02' },
    { id: 'cy-2', name: '特种预应力钢绞线与抗冲刷商砼直采包', category: '材料', amount: 320000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '75', objective: 'balanced', description: '匹配特种建材采购 CY-A03-B10' },
    { id: 'cy-3', name: '水上起重打桩船与特种吊装设备包', category: '设备', amount: 80000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '50', objective: 'tax', description: '匹配水上打桩船租赁 CY-A03-D02' },
    { id: 'cy-4', name: '引桥与互通立交路面铺装专业分包', category: '专业分包', amount: 120000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '65', objective: 'balanced', description: '匹配互通立交分包 CY-A03-A04' },
  ],
  'GY-LZ-003': [
    { id: 'gy-1', name: '生态河道防渗护坡及水利建材采购包', category: '材料', amount: 130000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '70', objective: 'balanced', description: '匹配水利物资直供 GY-A10-B05' },
    { id: 'gy-2', name: '库区水生态清淤与边坡绿化专业分包', category: '专业分包', amount: 60000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '60', objective: 'balanced', description: '匹配清淤绿化分包 GY-A10-A09' },
    { id: 'gy-3', name: '绞吸式清淤船与重型土石方设备租赁包', category: '设备', amount: 38000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '50', objective: 'tax', description: '匹配绞吸船设备租赁 GY-A10-D03' },
  ],
  'CD-GX-004': [
    { id: 'gx-1', name: '110kV高压变压器与智能配电设备采购包', category: '材料', amount: 65000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '75', objective: 'balanced', description: '匹配变电成套设备 GX-A07-B08' },
    { id: 'gx-2', name: '变电站高压微网电气安装专业分包', category: '专业分包', amount: 22000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '60', objective: 'balanced', description: '匹配高压电气安装 GX-A07-A02' },
  ],
  'QY-GEM-005': [
    { id: 'gem-1', name: '高原高寒特种通风与保温仓储专业分包', category: '专业分包', amount: 45000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '60', objective: 'balanced', description: '匹配特种仓储分包 GEM-A01-A06' },
    { id: 'gem-2', name: '耐候防腐钢材与特种地坪涂料直采包', category: '材料', amount: 55000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '70', objective: 'balanced', description: '匹配耐候防腐材料 GEM-A01-B09' },
  ],
};

const DEFAULT_PRESETS: PackagePreset[] = [
  { id: 'def-1', name: '主体工程建筑劳务用工分包', category: '劳务', amount: 30000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '60', objective: 'balanced' },
  { id: 'def-2', name: '大宗钢材与特种建材采购供应包', category: '材料', amount: 80000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '70', objective: 'balanced' },
  { id: 'def-3', name: '大型工程起重机械与设备租赁包', category: '设备', amount: 20000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '50', objective: 'tax' },
  { id: 'def-4', name: '机电安装与智能化专业工程分包', category: '专业分包', amount: 50000000, internalMinRatio: '0', internalMaxRatio: '100', preferredRatio: '65', objective: 'balanced' },
];

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
  
  const [selectedPresetId, setSelectedPresetId] = useState('');
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
  const [statusMessage, setStatusMessage] = useState('尚未执行真实筹划测算。');
  const [status, setStatus] = useState<'READY' | 'DEGRADED' | 'UNAVAILABLE'>('UNAVAILABLE');

  const currentPresets = currentProject
    ? (PROJECT_PACKAGE_PRESETS[currentProject.projectCode] || DEFAULT_PRESETS)
    : DEFAULT_PRESETS;

  // Apply a preset directly into form state
  const applyPreset = (preset: PackagePreset) => {
    setSelectedPresetId(preset.id);
    setPackageName(preset.name);
    setCategory(preset.category);
    setPackageAmount(String(preset.amount));
    setObjective(preset.objective);
    setInternalMinRatio(preset.internalMinRatio);
    setInternalMaxRatio(preset.internalMaxRatio);
    setPreferredRatio(preset.preferredRatio);
  };

  useEffect(() => {
    const nextId = selectedProjectId && projects.some(project => project.id === selectedProjectId)
      ? selectedProjectId
      : projects[0]?.id ?? '';
    setCurrentProjectId(nextId);
  }, [projects, selectedProjectId]);

  useEffect(() => {
    setPlanningResult(null);
    setPenetrationData(null);
    setStatus('READY');
    setStatusMessage(currentProject ? '已加载项目筹划上下文，正在自动测算基础沙盘…' : '没有可用项目，无法读取筹划上下文。');

    if (!currentProject) {
      setPackageName('');
      setPackageAmount('');
      return;
    }

    // Automatically prefill with the first preset of the current project
    const presets = PROJECT_PACKAGE_PRESETS[currentProject.projectCode] || DEFAULT_PRESETS;
    if (presets.length > 0) {
      applyPreset(presets[0]);
    }

    const controller = new AbortController();
    void fetchJson<unknown>(`/api/projects/${currentProject.numericId}/system-penetration`, { signal: controller.signal })
      .then(data => {
        setPenetrationData(data);
      })
      .catch(error => {
        if (!controller.signal.aborted) setStatusMessage(`穿透数据 DEGRADED：${error instanceof Error ? error.message : '接口不可用'}`);
      });
    
    // 自动执行初次沙盘测算
    if (presets.length > 0) {
      const p = presets[0];
      const body = {
        package_name: p.name,
        category: p.category,
        package_amount: p.amount,
        internal_min_ratio: Number(p.internalMinRatio) / 100,
        internal_max_ratio: Number(p.internalMaxRatio) / 100,
        preferred_ratio: Number(p.preferredRatio) / 100,
        objective: p.objective,
      };
      void postJson<PlanningResult>(
        `/api/projects/${currentProject.numericId}/allocation-planning/recommend`,
        body,
        controller.signal
      ).then(data => {
        setPlanningResult(data);
        setStatus('READY');
        setStatusMessage('筹划方案已根据确定性税率与四流匹配模型实时测算完成');
      }).catch(err => {
        if (!controller.signal.aborted) {
          setStatus('DEGRADED');
          setStatusMessage(`筹划测算异常：${err instanceof Error ? err.message : '接口不可用'}`);
        }
      });
    }

    return () => controller.abort();
  }, [currentProject]);

  const handleProjectChange = (id: string) => {
    setCurrentProjectId(id);
    onSelectProject?.(id);
  };

  const handlePresetSelectChange = (presetId: string) => {
    if (presetId === 'custom') {
      setSelectedPresetId('custom');
      return;
    }
    const found = currentPresets.find(p => p.id === presetId);
    if (found) {
      applyPreset(found);
    }
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
    if (!packageName.trim() || !Number.isFinite(amount) || amount <= 0 || !Number.isFinite(minRatio) || !Number.isFinite(maxRatio) || minRatio < 0 || maxRatio > 1 || minRatio > maxRatio || (preferred !== undefined && (!Number.isFinite(preferred) || preferred < 0 || preferred > 1))) {
      setStatus('DEGRADED');
      setStatusMessage('请填写业务包名称、正数金额，并确认比例范围为 0–100%。');
      return;
    }

    setIsCalculating(true);
    setStatusMessage('正在调用确定性筹划引擎…');
    try {
      const result = await postJson<PlanningResult>(`/api/projects/${currentProject.numericId}/allocation-planning/recommend`, {
        package_name: packageName.trim(),
        category,
        package_amount: amount,
        objective,
        internal_min_ratio: minRatio,
        internal_max_ratio: maxRatio,
        ...(preferred === undefined ? {} : { preferred_internal_ratio: preferred }),
        persist: true,
      });
      setPlanningResult(result);
      setStatus('READY');
      setStatusMessage('筹划结果来自 Tax 确定性引擎。');
      try {
        const penetration = await fetchJson<unknown>(`/api/projects/${currentProject.numericId}/system-penetration`);
        setPenetrationData(penetration);
      } catch (error) {
        setStatus('DEGRADED');
        setStatusMessage(`筹划已完成，但穿透快照不可用：${error instanceof Error ? error.message : '接口不可用'}`);
      }
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
        <DataStatusCard status="UNAVAILABLE" title="筹划数据不可用" message="请先配置 VITE_PROJECT_IDS 并让 Tax API 返回真实项目，系统不会使用静态筹划结果。" />
      </div>
    );
  }

  const recommended = record(planningResult?.recommended);
  const allocations = list(recommended.allocations);
  const scenarios = list(planningResult?.scenarios);
  const recommendation = record(planningResult?.ai_recommendation);

  return (
    <div className="space-y-6">
      {/* 头部项目选择器 */}
      <div className="glass-panel p-5 rounded-2xl border border-[#444653]/30 glow-cyan flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#8b5cf6]/20 flex items-center justify-center border border-[#a78bfa]/40">
            <Compass className="w-5 h-5 text-[#a78bfa]" />
          </div>
          <div>
            <h2 className="text-[20px] font-bold text-[#dde1ff]">AI 税务筹划</h2>
            <p className="text-[12px] text-[#8e909f] mt-0.5">结果只来自后端确定性筹划接口；接口失败时显示 DEGRADED。</p>
          </div>
        </div>
        <select
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

      {/* 状态卡片 */}
      <DataStatusCard status={status} title="筹划接口状态" message={statusMessage} />

      {/* 业务包参数面板（带下拉选项与自动预填充） */}
      <div className="glass-panel p-5 rounded-2xl border border-[#444653]/30">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-[#a78bfa]" />
            <h3 className="text-[15px] font-bold text-[#dde1ff]">业务包参数</h3>
            <span className="text-[11px] text-[#4cd7f6] bg-[#03b5d3]/10 px-2 py-0.5 rounded-full border border-[#4cd7f6]/30 flex items-center gap-1">
              <Sparkles className="w-3 h-3" />
              已启用智能预填充
            </span>
          </div>

          {/* 业务包下拉预选菜单 */}
          <div className="flex items-center gap-2">
            <span className="text-[12px] text-[#8e909f]">业务包预设下拉:</span>
            <select
              value={selectedPresetId}
              onChange={e => handlePresetSelectChange(e.target.value)}
              className="bg-[#131b2e] border border-[#a78bfa]/50 text-[#dae2fd] rounded-lg px-3 py-1.5 text-[12px] font-medium"
            >
              {currentPresets.map(p => (
                <option key={p.id} value={p.id}>
                  [{p.category}] {p.name} (¥{(p.amount / 10000).toFixed(0)}万)
                </option>
              ))}
              <option value="custom">✏️ 自定义业务包参数</option>
            </select>
          </div>
        </div>

        {/* 快捷业务包切换标签 */}
        <div className="flex flex-wrap gap-2 mb-4 pb-3 border-b border-[#444653]/20">
          <span className="text-[11px] text-[#8e909f] self-center mr-1">快捷选择:</span>
          {currentPresets.map(p => {
            const isSelected = selectedPresetId === p.id;
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => applyPreset(p)}
                className={`text-[11px] px-2.5 py-1 rounded-lg border transition-all flex items-center gap-1.5 cursor-pointer ${
                  isSelected
                    ? 'bg-[#8b5cf6]/20 border-[#a78bfa] text-[#dae2fd] font-semibold shadow-sm'
                    : 'bg-[#131b2e] border-[#444653]/40 text-[#8e909f] hover:text-[#dae2fd] hover:border-[#444653]'
                }`}
              >
                {isSelected && <CheckCircle2 className="w-3 h-3 text-[#a78bfa]" />}
                <span>{p.name}</span>
                <span className="opacity-60 text-[10px]">¥{(p.amount / 10000).toFixed(0)}万</span>
              </button>
            );
          })}
        </div>

        {/* 表单输入字段 */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 text-[12px]">
          <label className="text-[#8e909f]">
            业务包名称
            <input
              value={packageName}
              onChange={event => {
                setPackageName(event.target.value);
                setSelectedPresetId('custom');
              }}
              placeholder="请输入或选择业务包名称"
              className="mt-1 w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd]"
            />
          </label>
          <label className="text-[#8e909f]">
            业务类型
            <select
              value={category}
              onChange={event => {
                setCategory(event.target.value);
                setSelectedPresetId('custom');
              }}
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
              type="number"
              min="0"
              step="0.01"
              value={packageAmount}
              onChange={event => {
                setPackageAmount(event.target.value);
                setSelectedPresetId('custom');
              }}
              placeholder="例如 8000000"
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
            系统内最低比例（%）
            <input
              type="number"
              min="0"
              max="100"
              value={internalMinRatio}
              onChange={event => setInternalMinRatio(event.target.value)}
              className="mt-1 w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd]"
            />
          </label>
          <label className="text-[#8e909f]">
            系统内最高比例（%）
            <input
              type="number"
              min="0"
              max="100"
              value={internalMaxRatio}
              onChange={event => setInternalMaxRatio(event.target.value)}
              className="mt-1 w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd]"
            />
          </label>
          <label className="text-[#8e909f]">
            偏好比例（可选，%）
            <input
              type="number"
              min="0"
              max="100"
              value={preferredRatio}
              onChange={event => setPreferredRatio(event.target.value)}
              placeholder="例如 60"
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
              {isCalculating ? '计算中…' : '调用确定性引擎'}
            </button>
          </div>
        </div>
      </div>

      {/* 筹划推荐结果展示 */}
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
                  <p className="text-[#dae2fd] font-semibold mt-1 break-all">
                    {label(recommended[item.k])}
                  </p>
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
                        <td className="p-2">
                          {label(item.party_code)} · {label(item.party_name)}
                        </td>
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
          message="填写参数并调用后端确定性引擎后，真实结果会显示在这里。"
        />
      )}

      {penetrationData && (
        <details className="glass-panel rounded-xl p-5 border border-[#444653]/30">
          <summary className="cursor-pointer text-[13px] font-semibold text-[#dae2fd]">
            查看项目穿透快照（原始 API 返回）
          </summary>
          <pre className="mt-3 text-[11px] text-[#c4c5d5] whitespace-pre-wrap overflow-auto">
            {JSON.stringify(penetrationData, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
