import { useState } from 'react';
import { 
  BrainCircuit, 
  Stethoscope,
  FileCheck2, 
  UploadCloud, 
  AlertCircle, 
  CheckCircle2, 
  RefreshCw, 
  ShieldCheck, 
  ShieldAlert,
  ArrowRight,
  FileText,
  DollarSign,
  Truck,
  FileSpreadsheet,
  Layers,
  Sparkles,
  Search,
  ExternalLink,
  ChevronRight,
  AlertTriangle,
  Flame,
  Scale
} from 'lucide-react';
import { ProjectItem } from '../types';

interface AiReviewViewProps {
  projects?: ProjectItem[];
  onAskAiAboutRisk?: (entityName: string) => void;
}

export function AiReviewView({ projects = [], onAskAiAboutRisk }: AiReviewViewProps) {
  const [activeTab, setActiveTab] = useState<'single' | 'health' | 'cases'>('single');

  // ==================== 1. AI 专项审查 State ====================
  const [reviewProjectId, setReviewProjectId] = useState('1');
  const [reviewScope, setReviewScope] = useState('tax');
  const [reviewEndpointId, setReviewEndpointId] = useState('1');
  const [reviewInstruction, setReviewInstruction] = useState('深度审查各分包单位的跨区预缴税款与真实底层成本背离隐患');
  const [isReviewRunning, setIsReviewRunning] = useState(false);
  const [reviewResult, setReviewResult] = useState<any>({
    risk_level: 'HIGH',
    score: 82,
    summary: '审查发现该项目增值税抵扣链条存在跨区预缴差额，且部分外部建筑劳务与商贸物资公司的发票与现场工时实名制考勤出现 81.2 万元申报差额，存在偷漏个税及异地预缴清算风险。',
    provider_name: 'DeepSeek·V4-Flash智能体审查器',
    model_name: 'deepseek/deepseek-v4-flash-0731',
    findings: [
      {
        severity: 'HIGH',
        area: '建筑劳务与个税',
        issue: '跨区施工分包未全员全额扣缴个税',
        evidence: '劳务分包结算凭证金额与项目属地个人所得税申报系统人数及工资总额存在明显差异。',
        impact: '属地税务局可能按未代扣代缴个税处以 50% 以上罚款并加收滞纳金。'
      },
      {
        severity: 'MEDIUM',
        area: '商贸物资与发票',
        issue: '特种钢材采购存在大额跨期暂估入账',
        evidence: '年末暂估入账金额 4,300,000 元，超过次年 5 月 31 日汇算清缴期仍未取得合规发票。',
        impact: '企业所得税汇算清缴面临调增应纳税所得额补税风险。'
      }
    ],
    recommendations: [
      {
        priority: 'P1',
        action: '责令劳务分包单位在 7 日内补充属地税局全员全额个税申报完税凭证及农民工实名制工资专户流水。',
        reason: '阻断劳务用工偷漏税稽查连带追偿。',
        owner: '工程项目部·劳务管理专员'
      },
      {
        priority: 'P2',
        action: '催促外部商贸物资供应商限期换开数电增值税专用发票并完成线上勾选确认。',
        reason: '确保进项税额合规抵扣与所得税税前扣除凭证闭环。',
        owner: '财务资产部·税务会计'
      }
    ],
    data_gaps: [
      '缺少 2026 年 5 月份现场监理工程师手写签字的实物过磅入库验收单原始扫描件',
      '跨区域涉税事项报告表 (外管证) 异地核销清算回执待属地局回传'
    ]
  });

  // ==================== 2. AI 综合体检 State ====================
  const [healthProjectId, setHealthProjectId] = useState('1');
  const [healthProfile, setHealthProfile] = useState<'quick' | 'standard' | 'deep'>('standard');
  const [selectedEndpoints, setSelectedEndpoints] = useState<string[]>(['1', '2']);
  const [healthInstruction, setHealthInstruction] = useState('执行多模型交叉会诊，重点排查四流一致性与工程造价超预算风险');
  const [isHealthRunning, setIsHealthRunning] = useState(false);
  const [consensusResult, setConsensusResult] = useState<any>({
    overall_risk: 'HIGH',
    score: 85,
    summary: '多模型协同会诊共识：所有模型均确认该项目四流闭环存在中高危缺口，跨区施工异地预缴与农民工专户个税扣缴链条需紧急补正；在超概算停付阈值判定上，DeepSeek 模型研判更趋严格。',
    common_findings: [
      {
        severity: 'HIGH',
        area: '四流合一核验',
        issue: '合同流与资金流一致，但实物流证据链存在监理复签断点',
        evidence: '两家模型均通过电子底账比对发现磅房称重联与入库单缺少第三方见证记录。',
        impact: '易被主管税局认定为无真实货物交易或虚开进项。'
      },
      {
        severity: 'HIGH',
        area: '税务统筹预缴',
        issue: '异地预缴增值税 (2%) 与总机构所得税分摊计算存在 1.8% 偏差',
        evidence: '项目地跨省施工预缴税额未在次月总包申报表中及时抵减。',
        impact: '造成现金流重复占用或迟延申报滞纳金。'
      }
    ],
    differences: [
      {
        area: '超概算停付风险',
        issue: '造价超预算 5% 强制停付执行争议',
        description: 'DeepSeek 主力模型建议立即触发系统风控锁死支付通道；本地 Mock 审查器判定可附条件申请特别审批。'
      }
    ],
    recommendations: [
      {
        priority: 'P1',
        action: '启动多部门联席会商，对劳务专户实名制发放及外管证预缴台账进行一票否决式清查。',
        reason: '各模型全票认同的核心税务高危点。',
        owner: '总包管理层·风控合规小组'
      }
    ]
  });

  // ==================== 3. 典型案例 State ====================
  const [selectedCase, setSelectedCase] = useState('case-1');
  const testCases = [
    {
      id: 'case-1',
      title: '成都天府国际金融中心 - 外部建筑劳务公司（建筑劳务）跨区施工劳务与税款预缴核销案',
      riskScore: 88,
      status: '高危异常',
      flows: {
        contract: { status: '正常', title: '特种地质锚固与高边坡分包协议', desc: '合同总金额 ¥8,120,500 元，履约期 12 个月' },
        invoice: { status: '存疑', title: '建筑服务增值税发票与预缴凭证', desc: '跨区域涉税事项预缴税款凭证与纳税申报明细存在税率适用争议' },
        payment: { status: '正常', title: '银行工程进度款专用账户清算', desc: '农民工工资专用账户代发流水与合同约定进度吻合' },
        logistics: { status: '异常', title: '现场实名制考勤与工时派工单', desc: '考勤工时与全员全额个税申报人数存在 81.2 万元申报差额' }
      },
      aiConclusion: '该单据链条中【劳务人员实际履约流】与【税务申报扣缴流】出现实质性背离。跨区施工分包单位在项目所在地未按规定全员全额申报代扣代缴个人所得税，且异地预缴增值税及附加税费核销存在滞后，需依法补正申报并向属地主管税局提交税费清算说明。'
    },
    {
      id: 'case-2',
      title: '成都天府国际金融中心 - 外部商贸物资公司（商贸物资）特种钢结构采购暂估入账案',
      riskScore: 42,
      status: '中度预警',
      flows: {
        contract: { status: '正常', title: '钢材集中采购框架协议', desc: '约定包到工地价及过磅计量方式' },
        invoice: { status: '正常', title: '增值税专用发票 (13%)', desc: '发票真伪查验无误，已在电子税务局勾选' },
        payment: { status: '正常', title: '银行承兑汇票背书流转', desc: '资金流向与供应商开户银行一致' },
        logistics: { status: '存疑', title: '现场磅房电子过磅联', desc: '部分批次材料入库单缺少现场监理工程师手写签字' }
      },
      aiConclusion: '四流基本闭合，但现场实物过磅入库验收流程存在轻微管控瑕疵，建议限期补齐监理复签单据以防税务稽查追溯。'
    }
  ];

  const currentCase = testCases.find(c => c.id === selectedCase) || testCases[0];

  // 专项审查执行
  const handleRunReview = async () => {
    setIsReviewRunning(true);
    try {
      const formData = new FormData();
      formData.append('project_id', reviewProjectId);
      formData.append('scope', reviewScope);
      formData.append('endpoint_id', reviewEndpointId);
      formData.append('user_instruction', reviewInstruction);

      const resp = await fetch('/ai-review/run', {
        method: 'POST',
        body: formData,
      });

      if (resp.ok && resp.redirected) {
        const jobId = resp.url.split('/').pop();
        if (jobId && !isNaN(Number(jobId))) {
          const apiResp = await fetch(`/api/ai-review/${jobId}`);
          if (apiResp.ok) {
            const data = await apiResp.json();
            if (data.result) {
              setReviewResult(data.result);
            }
          }
        }
      }
    } catch (e) {
      console.warn('Backend call simulated fallback:', e);
    } finally {
      setTimeout(() => setIsReviewRunning(false), 800);
    }
  };

  // 综合体检执行
  const handleRunHealthCheck = async () => {
    setIsHealthRunning(true);
    try {
      const formData = new FormData();
      formData.append('project_id', healthProjectId);
      formData.append('profile', healthProfile);
      selectedEndpoints.forEach(id => formData.append('endpoint_ids', id));
      formData.append('user_instruction', healthInstruction);

      const resp = await fetch('/health-check/run', {
        method: 'POST',
        body: formData,
      });

      if (resp.ok && resp.redirected) {
        const batchId = resp.url.split('/').pop();
        if (batchId && !isNaN(Number(batchId))) {
          const apiResp = await fetch(`/api/health-check/${batchId}`);
          if (apiResp.ok) {
            const data = await apiResp.json();
            if (data.consensus) {
              setConsensusResult(data.consensus);
            }
          }
        }
      }
    } catch (e) {
      console.warn('Backend call simulated fallback:', e);
    } finally {
      setTimeout(() => setIsHealthRunning(false), 1000);
    }
  };

  const projectList = projects.length > 0 ? projects : [
    { id: '1', name: '宜宾三江新区重大产业示范园区项目', projectCode: 'YB-2026-001' },
    { id: '2', name: '成都天府国际金融中心二期工程', projectCode: 'TF-2026-002' },
    { id: '3', name: '天府国际机场二期配套物流枢纽', projectCode: 'TF-2026-003' },
    { id: '4', name: '成都高新西区高端智能制造基地', projectCode: 'GX-2026-004' },
    { id: '5', name: '成渝中线高铁配套枢纽工程', projectCode: 'CY-2026-005' },
  ];

  return (
    <div className="space-y-6">
      {/* 第一排：标题与系统定位 (全宽展示，绝不折行) */}
      <div>
        <div className="flex items-center gap-2.5">
          <BrainCircuit className="w-7 h-7 text-[#4cd7f6] flex-shrink-0" />
          <h2 className="text-[26px] font-bold text-[#dae2fd] tracking-tight whitespace-nowrap">
            AI 智能审查与多模型体检中枢
          </h2>
        </div>
        <p className="text-[13px] text-[#8e909f] mt-1">
          支持单环节专项深度审查 (DeepSeek)、多模型会诊体检 (共识报告) 与典型四流合一全景剖析。
        </p>
      </div>

      {/* 第二排：三大核心功能独立选项卡栏 */}
      <div className="flex flex-wrap items-center gap-2 p-1 bg-[#131b2e] rounded-xl border border-[#444653]/40 w-fit shadow-inner">
        <button
          onClick={() => setActiveTab('single')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-[13px] font-semibold transition-all cursor-pointer ${
            activeTab === 'single'
              ? 'bg-[#1e40af] text-[#dde1ff] shadow-md border border-[#4cd7f6]/40'
              : 'text-[#8e909f] hover:text-[#dae2fd]'
          }`}
        >
          <BrainCircuit className="w-4 h-4 text-[#4cd7f6]" />
          <span>AI 专项审查</span>
        </button>
        <button
          onClick={() => setActiveTab('health')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-[13px] font-semibold transition-all cursor-pointer ${
            activeTab === 'health'
              ? 'bg-[#1e40af] text-[#dde1ff] shadow-md border border-[#4cd7f6]/40'
              : 'text-[#8e909f] hover:text-[#dae2fd]'
          }`}
        >
          <Stethoscope className="w-4 h-4 text-[#10B981]" />
          <span>AI 综合体检 (多模型共识)</span>
        </button>
        <button
          onClick={() => setActiveTab('cases')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-[13px] font-semibold transition-all cursor-pointer ${
            activeTab === 'cases'
              ? 'bg-[#1e40af] text-[#dde1ff] shadow-md border border-[#4cd7f6]/40'
              : 'text-[#8e909f] hover:text-[#dae2fd]'
          }`}
        >
          <Scale className="w-4 h-4 text-[#ffb59a]" />
          <span>典型审单案例</span>
        </button>
      </div>

      {/* ========================================================================= */}
      {/* 模块 1: 单环节 AI 专项审查 */}
      {/* ========================================================================= */}
      {activeTab === 'single' && (
        <div className="space-y-6">
          {/* 发起审查控制卡 */}
          <div className="glass-panel rounded-xl p-5 border border-[#444653]/40">
            <h3 className="text-[16px] font-bold text-[#dae2fd] mb-3 flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-[#4cd7f6]" />
              <span>配置并启动单环节专项深度审查</span>
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <div>
                <label className="text-[12px] text-[#8e909f] block mb-1">工程项目：</label>
                <select
                  value={reviewProjectId}
                  onChange={(e) => setReviewProjectId(e.target.value)}
                  className="w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none cursor-pointer"
                >
                  {projectList.map((p: any) => (
                    <option key={p.id} value={p.id} className="bg-[#171f33]">
                      {p.name} ({p.projectCode || p.id})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-[12px] text-[#8e909f] block mb-1">审查专项：</label>
                <select
                  value={reviewScope}
                  onChange={(e) => setReviewScope(e.target.value)}
                  className="w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none cursor-pointer"
                >
                  <option value="default" className="bg-[#171f33]">🏢 通用项目经营全面审查</option>
                  <option value="tax" className="bg-[#171f33]">💰 税务与纳税申报专项审查</option>
                  <option value="contract" className="bg-[#171f33]">📜 签约合同与商业实质专项</option>
                  <option value="material" className="bg-[#171f33]">🧱 商贸物资采购与暂估专项</option>
                  <option value="labor" className="bg-[#171f33]">👷 建筑劳务用工与个税专项</option>
                  <option value="equipment" className="bg-[#171f33]">🚜 机械租赁与折旧燃料专项</option>
                </select>
              </div>

              <div>
                <label className="text-[12px] text-[#8e909f] block mb-1">AI 审查模型：</label>
                <select
                  value={reviewEndpointId}
                  onChange={(e) => setReviewEndpointId(e.target.value)}
                  className="w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none cursor-pointer"
                >
                  <option value="1" className="bg-[#171f33]">🌟 DeepSeek·V4-Flash智能体审查器 (默认)</option>
                  <option value="2" className="bg-[#171f33]">🛡️ 本地Mock经营审查器 (离线兜底)</option>
                  <option value="3" className="bg-[#171f33]">🛡️ 本地Mock合规复核器 (离线兜底)</option>
                </select>
              </div>

              <div>
                <label className="text-[12px] text-[#8e909f] block mb-1">特别指导意见：</label>
                <input
                  type="text"
                  value={reviewInstruction}
                  onChange={(e) => setReviewInstruction(e.target.value)}
                  placeholder="如：重点关注跨区施工异地预缴..."
                  className="w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none font-mono-num"
                />
              </div>
            </div>

            <div className="mt-4 flex justify-end">
              <button
                onClick={handleRunReview}
                disabled={isReviewRunning}
                className="flex items-center gap-2 px-5 py-2.5 bg-[#03b5d3] hover:bg-[#03b5d3]/80 text-[#001f26] font-bold text-[13px] rounded-xl transition-all cursor-pointer shadow-[0_0_15px_rgba(76,215,246,0.4)] disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${isReviewRunning ? 'animate-spin' : ''}`} />
                <span>{isReviewRunning ? 'DeepSeek 正在穿透核算中...' : '🚀 启动 AI 专项深度审查'}</span>
              </button>
            </div>
          </div>

          {/* 审查结果报告面板 */}
          {reviewResult && (
            <div className="space-y-6">
              {/* 核心指标卡片 */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="glass-panel rounded-xl p-4 glow-cyan flex items-center justify-between">
                  <div>
                    <p className="text-[11px] font-bold text-[#8e909f] uppercase">专项风险评级</p>
                    <p className={`text-[24px] font-bold font-mono-num mt-1 ${
                      reviewResult.risk_level === 'HIGH' || reviewResult.risk_level === 'CRITICAL' ? 'text-[#EF4444]' : 'text-[#F59E0B]'
                    }`}>
                      {reviewResult.risk_level === 'HIGH' ? '高危预警 (HIGH)' : reviewResult.risk_level === 'CRITICAL' ? '严重违规 (CRITICAL)' : '中度预警 (MEDIUM)'}
                    </p>
                  </div>
                  <ShieldAlert className="w-8 h-8 text-[#EF4444]" />
                </div>

                <div className="glass-panel rounded-xl p-4 glow-cyan flex items-center justify-between">
                  <div>
                    <p className="text-[11px] font-bold text-[#8e909f] uppercase">综合合规风险评分</p>
                    <p className="text-[28px] font-bold font-mono-num text-[#4cd7f6] mt-1">
                      {reviewResult.score} <span className="text-[13px] text-[#8e909f]">/ 100 分</span>
                    </p>
                  </div>
                  <div className="w-10 h-10 rounded-full border-2 border-[#4cd7f6] flex items-center justify-center font-bold text-[#4cd7f6] text-[12px]">
                    {reviewResult.score}%
                  </div>
                </div>

                <div className="glass-panel rounded-xl p-4 glow-cyan flex items-center justify-between">
                  <div>
                    <p className="text-[11px] font-bold text-[#8e909f] uppercase">执行审查模型</p>
                    <p className="text-[14px] font-bold text-[#dae2fd] mt-1 truncate">
                      {reviewResult.provider_name || 'DeepSeek V4-Flash'}
                    </p>
                    <span className="text-[11px] text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded border border-[#10B981]/30 inline-block mt-1">
                      🟢 腾讯云 TokenHub 直连
                    </span>
                  </div>
                  <BrainCircuit className="w-8 h-8 text-[#4cd7f6]" />
                </div>
              </div>

              {/* 审查总括陈述 */}
              <div className="glass-panel rounded-xl p-5 border border-[#4cd7f6]/40 glow-cyan">
                <h4 className="text-[15px] font-bold text-[#dae2fd] mb-2 flex items-center gap-2">
                  <FileText className="w-4 h-4 text-[#4cd7f6]" />
                  <span>AI 审计总括结论与合规研判</span>
                </h4>
                <p className="text-[13px] text-[#dae2fd] leading-relaxed bg-[#0b1326]/60 p-4 rounded-xl border border-[#444653]/30">
                  {reviewResult.summary}
                </p>
              </div>

              {/* 发现问题与事实证据 */}
              <div className="glass-panel rounded-xl p-5 border border-[#444653]/40">
                <h4 className="text-[15px] font-bold text-[#dae2fd] mb-3 flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 text-[#EF4444]" />
                  <span>发现具体合规疑点与事实证据链</span>
                </h4>
                <div className="space-y-3">
                  {reviewResult.findings?.map((f: any, idx: number) => (
                    <div key={idx} className="p-3.5 rounded-xl bg-[#131b2e] border border-[#444653]/30 flex flex-col md:flex-row md:items-start justify-between gap-3">
                      <div className="space-y-1.5 flex-1">
                        <div className="flex items-center gap-2">
                          <span className={`text-[11px] font-bold px-2 py-0.5 rounded ${
                            f.severity === 'HIGH' ? 'bg-[#EF4444]/20 text-[#ffb4ab] border border-[#EF4444]/40' : 'bg-[#F59E0B]/20 text-[#ffa583] border border-[#F59E0B]/40'
                          }`}>
                            {f.severity}
                          </span>
                          <span className="text-[12px] font-bold text-[#4cd7f6]">[{f.area}]</span>
                          <span className="text-[13px] font-bold text-[#dae2fd]">{f.issue}</span>
                        </div>
                        <p className="text-[12px] text-[#c4c5d5]"><strong className="text-[#8e909f]">事实证据：</strong>{f.evidence}</p>
                        <p className="text-[12px] text-[#ffb4ab]"><strong className="text-[#8e909f]">潜在影响：</strong>{f.impact}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* 整改建议清单 */}
              <div className="glass-panel rounded-xl p-5 border border-[#444653]/40">
                <h4 className="text-[15px] font-bold text-[#dae2fd] mb-3 flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-[#10B981]" />
                  <span>AI 建议整改任务与责任分配</span>
                </h4>
                <div className="space-y-3">
                  {reviewResult.recommendations?.map((r: any, idx: number) => (
                    <div key={idx} className="p-3.5 rounded-xl bg-[#131b2e] border border-[#444653]/30 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                      <div className="space-y-1 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] font-bold bg-[#1e40af] text-[#dde1ff] px-2 py-0.5 rounded">
                            {r.priority}
                          </span>
                          <span className="text-[13px] font-bold text-[#dae2fd]">{r.action}</span>
                        </div>
                        <p className="text-[12px] text-[#8e909f]">依据：{r.reason}</p>
                      </div>
                      <div className="text-right">
                        <span className="text-[11px] text-[#4cd7f6] bg-[#03b5d3]/10 px-2.5 py-1 rounded border border-[#4cd7f6]/30 font-medium">
                          责任人：{r.owner}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* 数据缺口 */}
              {reviewResult.data_gaps?.length > 0 && (
                <div className="glass-panel rounded-xl p-4 border border-[#F59E0B]/30 bg-[#F59E0B]/5">
                  <h4 className="text-[13px] font-bold text-[#ffa583] mb-2 flex items-center gap-1.5">
                    <AlertCircle className="w-4 h-4 text-[#F59E0B]" />
                    <span>尚存数据缺口（需补充归档以完成最终核销）</span>
                  </h4>
                  <ul className="list-disc list-inside text-[12px] text-[#c4c5d5] space-y-1">
                    {reviewResult.data_gaps.map((g: string, i: number) => (
                      <li key={i}>{g}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ========================================================================= */}
      {/* 模块 2: AI 综合体检 (多模型共识报告) */}
      {/* ========================================================================= */}
      {activeTab === 'health' && (
        <div className="space-y-6">
          {/* 体检配置卡 */}
          <div className="glass-panel rounded-xl p-5 border border-[#444653]/40">
            <h3 className="text-[16px] font-bold text-[#dae2fd] mb-3 flex items-center gap-2">
              <Stethoscope className="w-4 h-4 text-[#10B981]" />
              <span>启动多模型组合会诊与共识分析体检</span>
            </h3>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="text-[12px] text-[#8e909f] block mb-1">选择体检工程：</label>
                <select
                  value={healthProjectId}
                  onChange={(e) => setHealthProjectId(e.target.value)}
                  className="w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none cursor-pointer"
                >
                  {projectList.map((p: any) => (
                    <option key={p.id} value={p.id} className="bg-[#171f33]">
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-[12px] text-[#8e909f] block mb-1">体检深度预设：</label>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { id: 'quick', label: '⚡ 快速' },
                    { id: 'standard', label: '📊 标准' },
                    { id: 'deep', label: '🔬 深度' }
                  ].map(prof => (
                    <button
                      key={prof.id}
                      type="button"
                      onClick={() => setHealthProfile(prof.id as any)}
                      className={`py-1.5 rounded-lg text-[12px] font-semibold border transition-all cursor-pointer ${
                        healthProfile === prof.id
                          ? 'bg-[#1e40af] border-[#4cd7f6] text-[#dde1ff]'
                          : 'bg-[#131b2e] border-[#444653]/40 text-[#8e909f]'
                      }`}
                    >
                      {prof.label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-[12px] text-[#8e909f] block mb-1">协同参检模型：</label>
                <div className="flex flex-wrap gap-2 pt-1">
                  <label className="flex items-center gap-1.5 text-[11px] text-[#dae2fd] cursor-pointer bg-[#131b2e] px-2 py-1 rounded border border-[#4cd7f6]/40">
                    <input
                      type="checkbox"
                      checked={selectedEndpoints.includes('1')}
                      onChange={(e) => {
                        if (e.target.checked) setSelectedEndpoints([...selectedEndpoints, '1']);
                        else setSelectedEndpoints(selectedEndpoints.filter(x => x !== '1'));
                      }}
                      className="cursor-pointer"
                    />
                    <span>🌟 DeepSeek V4 (主审)</span>
                  </label>
                  <label className="flex items-center gap-1.5 text-[11px] text-[#8e909f] cursor-pointer bg-[#131b2e] px-2 py-1 rounded border border-[#444653]/30">
                    <input
                      type="checkbox"
                      checked={selectedEndpoints.includes('2')}
                      onChange={(e) => {
                        if (e.target.checked) setSelectedEndpoints([...selectedEndpoints, '2']);
                        else setSelectedEndpoints(selectedEndpoints.filter(x => x !== '2'));
                      }}
                      className="cursor-pointer"
                    />
                    <span>🛡️ 本地合规复核器</span>
                  </label>
                </div>
              </div>
            </div>

            <div className="mt-3">
              <label className="text-[12px] text-[#8e909f] block mb-1">体检审查导向与重点指令：</label>
              <input
                type="text"
                value={healthInstruction}
                onChange={(e) => setHealthInstruction(e.target.value)}
                className="w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none font-mono-num"
              />
            </div>

            <div className="mt-4 flex justify-end">
              <button
                onClick={handleRunHealthCheck}
                disabled={isHealthRunning}
                className="flex items-center gap-2 px-5 py-2.5 bg-[#10B981] hover:bg-[#10B981]/80 text-[#002114] font-bold text-[13px] rounded-xl transition-all cursor-pointer shadow-[0_0_15px_rgba(16,185,129,0.4)] disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${isHealthRunning ? 'animate-spin' : ''}`} />
                <span>{isHealthRunning ? '多模型联合交叉会诊中...' : '🩺 启动多模型会诊体检'}</span>
              </button>
            </div>
          </div>

          {/* 共识报告面板 */}
          {consensusResult && (
            <div className="space-y-6">
              {/* 综合健康雷达卡 */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="glass-panel rounded-xl p-4 glow-cyan">
                  <p className="text-[11px] font-bold text-[#8e909f] uppercase">多模型共识风险总评</p>
                  <p className="text-[24px] font-bold font-mono-num text-[#EF4444] mt-1">
                    {consensusResult.overall_risk} (重点管控)
                  </p>
                </div>
                <div className="glass-panel rounded-xl p-4 glow-cyan">
                  <p className="text-[11px] font-bold text-[#8e909f] uppercase">多模型综合健康评分</p>
                  <p className="text-[28px] font-bold font-mono-num text-[#10B981] mt-1">
                    {consensusResult.score} <span className="text-[13px] text-[#8e909f]">/ 100 分</span>
                  </p>
                </div>
                <div className="glass-panel rounded-xl p-4 glow-cyan">
                  <p className="text-[11px] font-bold text-[#8e909f] uppercase">多模型共识比对机制</p>
                  <p className="text-[14px] font-bold text-[#dae2fd] mt-1">
                    确定性共识算法 · 自动消歧
                  </p>
                  <span className="text-[11px] text-[#4cd7f6] inline-block mt-1">
                    支持差异自动标出与整改闭环
                  </span>
                </div>
              </div>

              {/* 共识总结 */}
              <div className="glass-panel rounded-xl p-5 border border-[#10B981]/40 glow-green">
                <h4 className="text-[15px] font-bold text-[#dae2fd] mb-2 flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-[#10B981]" />
                  <span>多模型协同体检共识总括 (Consensus Summary)</span>
                </h4>
                <p className="text-[13px] text-[#dae2fd] leading-relaxed bg-[#0b1326]/60 p-4 rounded-xl border border-[#444653]/30">
                  {consensusResult.summary}
                </p>
              </div>

              {/* 全模型全票共识风险 (Common Findings) */}
              <div className="glass-panel rounded-xl p-5 border border-[#444653]/40">
                <h4 className="text-[15px] font-bold text-[#dae2fd] mb-3 flex items-center gap-2">
                  <ShieldAlert className="w-4 h-4 text-[#EF4444]" />
                  <span>全模型全票认同的核心风险项 (Common Findings)</span>
                </h4>
                <div className="space-y-3">
                  {consensusResult.common_findings?.map((cf: any, idx: number) => (
                    <div key={idx} className="p-3.5 rounded-xl bg-[#131b2e] border border-[#EF4444]/30 space-y-1.5">
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] font-bold bg-[#EF4444]/20 text-[#ffb4ab] px-2 py-0.5 rounded border border-[#EF4444]/40">
                          {cf.severity}
                        </span>
                        <span className="text-[12px] font-bold text-[#4cd7f6]">[{cf.area}]</span>
                        <span className="text-[13px] font-bold text-[#dae2fd]">{cf.issue}</span>
                      </div>
                      <p className="text-[12px] text-[#c4c5d5]"><strong className="text-[#8e909f]">共识证据：</strong>{cf.evidence}</p>
                      <p className="text-[12px] text-[#ffb4ab]"><strong className="text-[#8e909f]">后果影响：</strong>{cf.impact}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* 模型分歧与争议 (Differences) */}
              <div className="glass-panel rounded-xl p-5 border border-[#444653]/40">
                <h4 className="text-[15px] font-bold text-[#dae2fd] mb-3 flex items-center gap-2">
                  <Scale className="w-4 h-4 text-[#F59E0B]" />
                  <span>模型研判分歧与争议项 (Model Differences)</span>
                </h4>
                <div className="space-y-3">
                  {consensusResult.differences?.map((df: any, idx: number) => (
                    <div key={idx} className="p-3.5 rounded-xl bg-[#131b2e] border border-[#F59E0B]/30 space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] font-bold bg-[#F59E0B]/20 text-[#ffa583] px-2 py-0.5 rounded">
                          分歧
                        </span>
                        <span className="text-[12px] font-bold text-[#4cd7f6]">[{df.area}]</span>
                        <span className="text-[13px] font-bold text-[#dae2fd]">{df.issue}</span>
                      </div>
                      <p className="text-[12px] text-[#c4c5d5] leading-relaxed">{df.description}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ========================================================================= */}
      {/* 模块 3: 典型四流审单案例 */}
      {/* ========================================================================= */}
      {activeTab === 'cases' && (
        <div className="space-y-6">
          {/* 案例切换 */}
          <div className="flex gap-3 overflow-x-auto scrollbar-hide pb-1">
            {testCases.map((c) => (
              <button
                key={c.id}
                onClick={() => setSelectedCase(c.id)}
                className={`px-4 py-2.5 rounded-xl text-[13px] font-semibold border transition-all cursor-pointer whitespace-nowrap ${
                  selectedCase === c.id
                    ? 'bg-[#1e40af] border-[#4cd7f6] text-[#dde1ff] shadow-[0_0_12px_rgba(76,215,246,0.2)]'
                    : 'bg-[#171f33] border-[#444653]/40 text-[#8e909f] hover:text-[#dae2fd]'
                }`}
              >
                {c.title}
              </button>
            ))}
          </div>

          {/* 四流合一四象限网格 */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* 1. 合同流 */}
            <div className="glass-panel rounded-xl p-4 border border-[#444653]/40 flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[12px] font-bold text-[#8e909f] flex items-center gap-1.5">
                    <FileText className="w-4 h-4 text-[#4cd7f6]" />
                    <span>1. 合同流 (法律协议)</span>
                  </span>
                  <span className="text-[11px] font-bold text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded">
                    {currentCase.flows.contract.status}
                  </span>
                </div>
                <h4 className="text-[14px] font-bold text-[#dae2fd]">{currentCase.flows.contract.title}</h4>
                <p className="text-[12px] text-[#c4c5d5] mt-2 leading-relaxed">{currentCase.flows.contract.desc}</p>
              </div>
            </div>

            {/* 2. 发票流 */}
            <div className={`glass-panel rounded-xl p-4 flex flex-col justify-between ${
              currentCase.flows.invoice.status === '存疑' ? 'border-[#F59E0B]/50 glow-amber' : 'border-[#444653]/40'
            }`}>
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[12px] font-bold text-[#8e909f] flex items-center gap-1.5">
                    <FileSpreadsheet className="w-4 h-4 text-[#b8c4ff]" />
                    <span>2. 发票流 (税务凭据)</span>
                  </span>
                  <span className={`text-[11px] font-bold px-2 py-0.5 rounded ${
                    currentCase.flows.invoice.status === '存疑' ? 'bg-[#F59E0B]/20 text-[#ffa583]' : 'bg-[#10B981]/15 text-[#10B981]'
                  }`}>
                    {currentCase.flows.invoice.status}
                  </span>
                </div>
                <h4 className="text-[14px] font-bold text-[#dae2fd]">{currentCase.flows.invoice.title}</h4>
                <p className="text-[12px] text-[#c4c5d5] mt-2 leading-relaxed">{currentCase.flows.invoice.desc}</p>
              </div>
            </div>

            {/* 3. 资金流 */}
            <div className="glass-panel rounded-xl p-4 border border-[#444653]/40 flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[12px] font-bold text-[#8e909f] flex items-center gap-1.5">
                    <DollarSign className="w-4 h-4 text-[#10B981]" />
                    <span>3. 资金流 (银行结算)</span>
                  </span>
                  <span className="text-[11px] font-bold text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded">
                    {currentCase.flows.payment.status}
                  </span>
                </div>
                <h4 className="text-[14px] font-bold text-[#dae2fd]">{currentCase.flows.payment.title}</h4>
                <p className="text-[12px] text-[#c4c5d5] mt-2 leading-relaxed">{currentCase.flows.payment.desc}</p>
              </div>
            </div>

            {/* 4. 物资流 */}
            <div className={`glass-panel rounded-xl p-4 flex flex-col justify-between ${
              currentCase.flows.logistics.status === '异常' ? 'border-[#EF4444]/60 glow-red' : 'border-[#444653]/40'
            }`}>
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[12px] font-bold text-[#8e909f] flex items-center gap-1.5">
                    <Truck className="w-4 h-4 text-[#ffb59a]" />
                    <span>4. 物资/履约流 (实际交付)</span>
                  </span>
                  <span className={`text-[11px] font-bold px-2 py-0.5 rounded ${
                    currentCase.flows.logistics.status === '异常' ? 'bg-[#EF4444]/20 text-[#ffb4ab] animate-pulse' : 'bg-[#10B981]/15 text-[#10B981]'
                  }`}>
                    {currentCase.flows.logistics.status}
                  </span>
                </div>
                <h4 className="text-[14px] font-bold text-[#dae2fd]">{currentCase.flows.logistics.title}</h4>
                <p className="text-[12px] text-[#c4c5d5] mt-2 leading-relaxed">{currentCase.flows.logistics.desc}</p>
              </div>
            </div>
          </div>

          {/* 智能审计结论 */}
          <div className="glass-panel-elevated rounded-xl p-6 border border-[#4cd7f6]/40 glow-cyan">
            <div className="flex items-center justify-between pb-3 border-b border-[#444653]/40 mb-4">
              <div className="flex items-center gap-2.5">
                <ShieldCheck className="w-6 h-6 text-[#4cd7f6]" />
                <h3 className="text-[17px] font-bold text-[#dae2fd]">智能审单复核总括结论</h3>
              </div>
              <span className="text-[12px] font-bold text-[#EF4444] bg-[#EF4444]/20 px-3 py-1 rounded-full border border-[#EF4444]/40 font-mono-num">
                风险指数：{currentCase.riskScore} 分 (需重点处置)
              </span>
            </div>

            <div className="p-4 rounded-xl bg-[#0b1326]/80 border border-[#444653]/30 text-[13px] leading-relaxed text-[#dae2fd]">
              {currentCase.aiConclusion}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

