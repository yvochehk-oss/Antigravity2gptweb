import { useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { DashboardView } from './components/DashboardView';
import { ProjectRepositoryView } from './components/ProjectRepositoryView';
import { ProjectDetailView } from './components/ProjectDetailView';
import { TaxLedgerView } from './components/TaxLedgerView';
import { TaxPlanningView } from './components/TaxPlanningView';
import { RiskCenterView } from './components/RiskCenterView';
import { AiReviewView } from './components/AiReviewView';
import { AuditView } from './components/AuditView';
import { AiAssistantDrawer } from './components/AiAssistantDrawer';
import { NewTaxRecordModal } from './components/NewTaxRecordModal';
import { ExportReportModal } from './components/ExportReportModal';
import { 
  initialProjects, 
  initialRiskEvents, 
  initialAuditLogs, 
  initialAssistantMessages 
} from './data/mockData';
import { ProjectItem, RiskEvent, AuditTrailRecord, AssistantMessage, TaxLedgerRecord, SystemSettings } from './types';
import { DEFAULT_SETTINGS } from './components/SettingsModal';

export default function App() {
  const [currentTab, setCurrentTab] = useState<string>('dashboard');
  const [projectSubView, setProjectSubView] = useState<'list' | 'detail'>('list');
  const [selectedProjectId, setSelectedProjectId] = useState<string>('proj-02');
  const [isAiOpen, setIsAiOpen] = useState<boolean>(true);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>('');

  // 全局系统配置与风控阈值
  const [systemSettings, setSystemSettings] = useState<SystemSettings>(() => {
    try {
      const saved = localStorage.getItem('ruibao_system_settings');
      if (saved) {
        return { ...DEFAULT_SETTINGS, ...JSON.parse(saved) };
      }
    } catch (e) {
      console.error(e);
    }
    return DEFAULT_SETTINGS;
  });

  // 核心业务状态
  const [projects, setProjects] = useState<ProjectItem[]>(initialProjects);
  const [riskEvents, setRiskEvents] = useState<RiskEvent[]>(initialRiskEvents);
  const [auditLogs, setAuditLogs] = useState<AuditTrailRecord[]>(initialAuditLogs);
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>(initialAssistantMessages);
  const [isAiThinking, setIsAiThinking] = useState<boolean>(false);

  // 弹窗状态
  const [isNewRecordModalOpen, setIsNewRecordModalOpen] = useState<boolean>(false);
  const [isExportModalOpen, setIsExportModalOpen] = useState<boolean>(false);

  const currentProject = projects.find(p => p.id === selectedProjectId) || projects[0];
  const unresolvedRiskCount = riskEvents.filter(r => r.status !== '已闭环').length;

  // 更新并应用系统运行参数配置
  const handleSaveSettings = (newSettings: SystemSettings) => {
    setSystemSettings(newSettings);
    try {
      localStorage.setItem('ruibao_system_settings', JSON.stringify(newSettings));
    } catch (e) {
      console.error(e);
    }

    // 记录审计日志
    const newLog: AuditTrailRecord = {
      id: `aud-${Date.now()}`,
      timestamp: '2026-08-17 15:02:10',
      operator: '总会计师 / 系统管理员',
      role: '高级审计权限',
      targetSubject: '全局风控与预算参数配置',
      actionType: '系统设置变更',
      details: `更新系统参数：跨区预缴偏差阈值调整至 ${newSettings.crossRegionTaxThreshold}%，超概算止付令阈值调整至 ${newSettings.budgetOverrunStopPayThreshold}%，四流合一自动比对: ${newSettings.autoFourFlowsMatch ? '开启' : '关闭'}。`,
      integrityHash: `哈希校验-${Math.random().toString(36).substring(2, 10)}`,
    };
    setAuditLogs(prev => [newLog, ...prev]);
  };

  // 切换选项卡
  const handleSelectTab = (tab: string) => {
    setCurrentTab(tab);
    if (tab === 'projects') {
      setProjectSubView('list');
    }
    setIsMobileMenuOpen(false);
  };

  // 切换项目详情
  const handleSelectProject = (projId: string) => {
    setSelectedProjectId(projId);
    setCurrentTab('projects');
    setProjectSubView('detail');
    setIsMobileMenuOpen(false);
  };

  // 解决闭环单项风险
  const handleResolveRisk = (riskId: string) => {
    setRiskEvents(prev => prev.map(r => {
      if (r.id === riskId) {
        return { ...r, status: '已闭环' };
      }
      return r;
    }));

    // 记录审计底稿日志
    const newLog: AuditTrailRecord = {
      id: `aud-${Date.now()}`,
      timestamp: '2026-08-17 14:45:00',
      operator: '首席财务风控总监',
      role: '高级审计签批人',
      targetSubject: '风险处置闭环',
      actionType: '合规复核',
      details: `人工复核确认风险事件 [编号:${riskId}] 整改措施落实，签署闭环归档底稿。`,
      integrityHash: `哈希校验-${Math.random().toString(36).substring(2, 10)}`,
    };
    setAuditLogs(prev => [newLog, ...prev]);
  };

  // 从 RAG 知识湖同步新凭证并启动智能查账
  const handleAddTaxRecord = (newRecordData: Omit<TaxLedgerRecord, 'id' | 'updateTime'>) => {
    const newRecord: TaxLedgerRecord = {
      ...newRecordData,
      id: `tax-${Date.now()}`,
      updateTime: '2026-08-17 14:46',
    };

    setProjects(prev => prev.map(p => {
      if (p.id === selectedProjectId) {
        return {
          ...p,
          taxRecords: [newRecord, ...p.taxRecords],
          spentAmount: p.spentAmount + newRecord.declareAmount,
          remainingBudget: p.totalBudget - (p.spentAmount + newRecord.declareAmount),
        };
      }
      return p;
    }));

    // 若存在疑点，自动联动至风控事件中心
    if (newRecord.riskLevel === '预警' || newRecord.riskLevel === '高危') {
      const newRisk: RiskEvent = {
        id: `risk-${Date.now()}`,
        projectName: currentProject.name,
        entityName: newRecord.entityName,
        riskType: newRecord.riskLevel === '高危' ? '预提所得税争议' : '合同四流背离',
        severity: newRecord.riskLevel === '高危' ? '高危' : '中度',
        triggerTime: '刚刚 (RAG智能穿透发现)',
        description: newRecord.riskDescription || 'RAG 向量比对发现多源票据四流存在背离，已自动触发初筛警报。',
        auditSuggestions: '调阅 RAG 溯源凭证中枢，要求施工分包单位提供原始过磅单、发票底账与银行公户付款单复印件。',
        status: '待处置',
        handler: '系统智能分配-现场稽核组',
      };
      setRiskEvents(prev => [newRisk, ...prev]);
    }

    // 记录审计日志
    const newLog: AuditTrailRecord = {
      id: `aud-${Date.now()}`,
      timestamp: '2026-08-17 14:46:12',
      operator: 'RAG 业财智能同步引擎',
      role: '向量知识湖自动调度',
      targetSubject: newRecord.entityName,
      actionType: 'RAG底账同步',
      details: `成功从 RAG 知识湖检索并同步凭证（金额 ¥${newRecord.declareAmount.toLocaleString('zh-CN')} 元），已完成四流自动交叉核验并生成防篡改存证。`,
      integrityHash: `哈希校验-${Math.random().toString(36).substring(2, 10)}`,
    };
    setAuditLogs(prev => [newLog, ...prev]);
  };

  // 向 AI 发送提问或执行指令
  const handleSendAiMessage = (query: string) => {
    const userMsg: AssistantMessage = {
      id: `msg-${Date.now()}`,
      sender: 'user',
      content: query,
      timestamp: '刚刚',
    };

    setAssistantMessages(prev => [...prev, userMsg]);
    setIsAiThinking(true);

    // 智能决策模型回复逻辑 (全中文专业领域分析)
    setTimeout(() => {
      let aiResponse = '';
      let suggestedActions: string[] = [];

      if (query.includes('建筑劳务') || query.includes('本盛劳务') || query.includes('税务稽查') || query.includes('跨区') || query.includes('个税')) {
        aiResponse = `【锐宝智能助手 · 建筑劳务跨区用工涉税风险深度研判】：\n\n1. 【核心涉税事实】：根据国家税务总局关于跨区域建筑服务税收征管办法及个人所得税法规定，建筑劳务公司在项目所在地（成都市天府新区）未足额核销 2% 预缴增值税及附加，且现场全员全额个税申报人数与农民工实名制考勤流水存在 81.2 万元申报差额；\n\n2. 【税款调整模型】：主管税务机关发起跨区涉税事项专项比对，要求补正预缴完税凭证并核实个税代扣代缴明细（预估应补缴税费及滞纳金约 40.6 万至 81.2 万元）；\n\n3. 【三步合规化解策略】：\n   ① 启动现场劳务实名制通道数据与银行代发工资流水一致性穿透核查；\n   ② 调取跨地市完税分割凭证，向天府新区主管税务机关提交《跨区域建筑施工税费清算核销报告》；\n   ③ 限期 5 个工作日内补齐个税全员明细申报并完成税款核销。`;
        suggestedActions = ['一键生成税局专项沟通说明底稿', '查看跨区施工税费就地预缴法规分析', '重新测算分包工程税负分摊模型'];
      } else if (query.includes('成本超支') || query.includes('下季度') || query.includes('预算')) {
        aiResponse = `【锐宝智能助手 · 工程造价与成本超支趋势预测】：\n\n1. 【高风险超支预警工程】：【宜宾三江口长江特大桥防腐工程】（当前超支率已达 41.6%，超出概算 3.2 亿元）；【成都天府国际金融中心·01.2场地平整】（超支 5.6%）；\n\n2. 【动因归因分析】：耐候防腐新材料价格波动与长江复杂汛期水上水下作业投入超预期 + 汛期暴雨基坑应急强排水设施投入超额；\n\n3. 【管控建议】：全面启动【工程造价红黄灯限额支付机制】，对超出概算 5% 以上的分项立即暂缓非紧急款项拨付，启动全过程跟踪审计实物量核验。`;
        suggestedActions = ['下发超概算分项工程止付令', '发起宜宾大桥现场实物量复核', '调取大宗材料调差补偿合同'];
      } else if (query.includes('合规报告') || query.includes('进销项') || query.includes('增值税')) {
        aiResponse = `【锐宝智能助手 · 全省项目增值税进销项与留抵税额简报】：\n\n• 本月销项税额总计：¥ 6,180 万元；\n• 认证抵扣进项税额总计：¥ 4,610 万元；\n• 预计当期净缴纳增值税：¥ 1,570 万元；\n• 四川本盛劳务有限公司（建筑劳务）形成留抵税额：-¥ 1,500 万元，增值税留抵退税已进入国库审批终审通道；\n• 全流程发票电子底账查验通过率达 99.4%。`;
        suggestedActions = ['查看各实体增值税基准对比', '导出留抵退税全套申请资料', '下载本月增值税测算底稿'];
      } else if (query.includes('四流合一') || query.includes('异常清单') || query.includes('发票')) {
        aiResponse = `【锐宝智能助手 · 发票四流合一背离风险清单】：\n\n当前检测到 2 处潜在不匹配：\n1. 【成都天府国际金融中心·商贸物资钢材采购】：进项发票开具金额与现场电子过磅单存在 18.4% 跨期暂估时间差，建议补齐过磅签收联；\n2. 【绵阳科技城地下综合管廊二标段】：存在第三方账户代收工程款背离现象，需限期重构公对公银行清算结算凭证链。`;
        suggestedActions = ['跳转至智能审单复核工作台', '发送物资过磅单催补通知', '查看代收代付法律风险条款'];
      } else {
        aiResponse = `【锐宝智能助手 · 智能研判回复】：\n\n已为您检索分析四川工程财税数仓中与“${query}”相关的台账凭据与政策规定：\n\n• 当前四川省内 142 个受控工程财务稳健度评分维持在 92.5 分（优良）；\n• 建议重点关注跨地市异地施工税费就地预缴及全员个税代扣代缴合规；\n• 如需进一步穿透某项具体凭证或工程科目，可随时向我下达进一步指令。`;
        suggestedActions = ['分析建筑劳务税务稽查风险详情', '预测下季度工程成本超支趋势', '查看四流合一不匹配异常清单'];
      }

      const aiMsg: AssistantMessage = {
        id: `msg-${Date.now()}`,
        sender: 'ai',
        content: aiResponse,
        timestamp: '刚刚',
        suggestedActions,
      };

      setAssistantMessages(prev => [...prev, aiMsg]);
      setIsAiThinking(false);
    }, 1000);
  };

  // 快捷从单条表格项呼叫 AI
  const handleAskAiAboutRisk = (entityName: string) => {
    setIsAiOpen(true);
    handleSendAiMessage(`深入穿透核查【${entityName}】的涉税稽查风险与四流合一凭据，给出税局合规化解应对策略。`);
  };

  return (
    <div className="fixed inset-0 h-screen w-screen bg-[#0b1326] text-[#dae2fd] flex flex-col md:flex-row antialiased overflow-hidden font-sans">
      {/* 桌面端侧边导航 */}
      <Sidebar
        currentTab={currentTab}
        onSelectTab={handleSelectTab}
        unresolvedRiskCount={unresolvedRiskCount}
      />

      {/* 移动端侧边抽屉菜单 */}
      {isMobileMenuOpen && (
        <div className="fixed inset-0 z-50 md:hidden flex">
          <div 
            className="fixed inset-0 bg-black/70 backdrop-blur-sm"
            onClick={() => setIsMobileMenuOpen(false)}
          ></div>
          <div className="relative w-64 h-full bg-[#0b1326] z-10 flex flex-col shadow-2xl">
            <Sidebar
              isMobile={true}
              onCloseMobile={() => setIsMobileMenuOpen(false)}
              currentTab={currentTab}
              onSelectTab={handleSelectTab}
              unresolvedRiskCount={unresolvedRiskCount}
            />
          </div>
        </div>
      )}

      {/* 主工作区 */}
      <div className="flex-1 flex flex-col md:ml-48 min-w-0 h-full overflow-hidden relative">
        {/* 顶部导航栏 */}
        <Header
          onToggleAi={() => setIsAiOpen(!isAiOpen)}
          isAiOpen={isAiOpen}
          onOpenExportModal={() => setIsExportModalOpen(true)}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          onToggleMobileMenu={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
          settings={systemSettings}
          onSaveSettings={handleSaveSettings}
        />

        {/* 主内容展示区与右侧 AI 助手独立分栏布局（物理隔离互不干扰） */}
        <div className="flex-1 flex pt-16 h-full w-full overflow-hidden">
          {/* 左侧可滚动内容画布：独立滚动，不影响右侧助手 */}
          <main className="flex-1 min-w-0 h-full overflow-y-auto overscroll-contain p-3.5 md:p-5 lg:p-6 scrollbar-hide bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-[#171f33]/40 via-[#0b1326] to-[#0b1326]">
            <div className="w-full mx-auto pb-16">
              {currentTab === 'dashboard' && (
                <DashboardView
                  projects={projects}
                  onSelectProject={handleSelectProject}
                  onOpenRiskCenter={() => setCurrentTab('risk-center')}
                  onOpenExportModal={() => setIsExportModalOpen(true)}
                  settings={systemSettings}
                />
              )}

              {currentTab === 'projects' && projectSubView === 'list' && (
                <ProjectRepositoryView
                  projects={projects}
                  onSelectProject={handleSelectProject}
                  onOpenNewRecordModal={() => setIsNewRecordModalOpen(true)}
                  onOpenExportModal={() => setIsExportModalOpen(true)}
                  onNavigateToAiReview={(projId) => {
                    setSelectedProjectId(projId);
                    setCurrentTab('ai-review');
                  }}
                  settings={systemSettings}
                />
              )}

              {currentTab === 'projects' && projectSubView === 'detail' && (
                <ProjectDetailView
                  project={currentProject}
                  projects={projects}
                  onSelectProject={(projId) => setSelectedProjectId(projId)}
                  onBack={() => setProjectSubView('list')}
                  onOpenNewRecordModal={() => setIsNewRecordModalOpen(true)}
                  onOpenExportModal={() => setIsExportModalOpen(true)}
                  onAskAiAboutRisk={handleAskAiAboutRisk}
                  onGoToPlanning={() => {
                    setCurrentTab('tax-planning');
                  }}
                  settings={systemSettings}
                />
              )}

              {currentTab === 'tax-ledger' && (
                <TaxLedgerView
                  projects={projects}
                  onOpenNewRecordModal={() => setIsNewRecordModalOpen(true)}
                  onOpenExportModal={() => setIsExportModalOpen(true)}
                  onAskAiAboutRisk={handleAskAiAboutRisk}
                  settings={systemSettings}
                />
              )}

              {currentTab === 'tax-planning' && (
                <TaxPlanningView
                  projects={projects}
                  selectedProjectId={selectedProjectId}
                  onSelectProject={(projId) => setSelectedProjectId(projId)}
                  onAskAiAboutRisk={handleAskAiAboutRisk}
                />
              )}

              {currentTab === 'risk-center' && (
                <RiskCenterView
                  riskEvents={riskEvents}
                  onResolveRisk={handleResolveRisk}
                  onAskAiAboutRisk={handleAskAiAboutRisk}
                  settings={systemSettings}
                />
              )}

              {currentTab === 'ai-review' && (
                <AiReviewView
                  projects={projects}
                  onAskAiAboutRisk={handleAskAiAboutRisk}
                />
              )}

              {currentTab === 'audit' && (
                <AuditView
                  auditLogs={auditLogs}
                  onOpenExportModal={() => setIsExportModalOpen(true)}
                />
              )}
            </div>
          </main>

          {/* 右侧常驻智能助手抽屉 */}
          <AiAssistantDrawer
            isOpen={isAiOpen}
            onClose={() => setIsAiOpen(false)}
            messages={assistantMessages}
            onSendMessage={handleSendAiMessage}
            isAiThinking={isAiThinking}
            settings={systemSettings}
          />
        </div>
      </div>

      {/* 弹窗：RAG 知识湖凭证智能检索与查账同步 */}
      <NewTaxRecordModal
        isOpen={isNewRecordModalOpen}
        onClose={() => setIsNewRecordModalOpen(false)}
        onAddRecord={handleAddTaxRecord}
        defaultProjectName={currentProject.name}
      />

      {/* 弹窗：导出综合财务报告 */}
      <ExportReportModal
        isOpen={isExportModalOpen}
        onClose={() => setIsExportModalOpen(false)}
        projects={projects}
      />
    </div>
  );
}
