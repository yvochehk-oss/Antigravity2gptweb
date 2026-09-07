import { useCallback, useEffect, useRef, useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { DashboardView } from './components/DashboardView';
import { ProjectRepositoryView } from './components/ProjectRepositoryView';
import { ProjectDetailView } from './components/ProjectDetailView';
import { EntityCorporateView } from './components/EntityCorporateView';
import { TaxLedgerView } from './components/TaxLedgerView';
import { AiDecisionCenterView } from './components/AiDecisionCenterView';
import { RiskCenterView } from './components/RiskCenterView';
import { AuditView } from './components/AuditView';
import { AiAssistantDrawer } from './components/AiAssistantDrawer';
import { NewTaxRecordModal } from './components/NewTaxRecordModal';
import { ExportReportModal } from './components/ExportReportModal';
import { TokenHubSetupWizard } from './components/TokenHubSetupWizard';
import { RagSetupWizard } from './components/RagSetupWizard';
import { DataStatusCard } from './components/DataStatusCard';
import { DEFAULT_SETTINGS } from './components/SettingsModal';
import { askProjectAi, ApiError, fetchAiModelStatus, fetchAuditLogs, fetchConfiguredProjects, fetchRiskEvents } from './api';
import {
  fetchLegalEntityStatutoryVatCollection,
  rebuildLegalEntityStatutoryVatCollection,
} from './legalEntityApi';
import {
  AssistantMessage,
  AiModelStatus,
  AuditTrailRecord,
  DataStatus,
  ProjectItem,
  RiskEvent,
  EntityTaxLedgerRecord,
  SystemSettings,
} from './types';

const rebuildTaxLedger = rebuildLegalEntityStatutoryVatCollection;

function nowLabel(): string {
  return new Date().toLocaleString('zh-CN', { hour12: false });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Tax 服务暂时不可用。';
}

export default function App() {
  const [currentTab, setCurrentTab] = useState<string>('dashboard');
  const [projectSubView, setProjectSubView] = useState<'list' | 'detail'>('list');
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [isAiOpen, setIsAiOpen] = useState<boolean>(true);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>('');

  const [systemSettings, setSystemSettings] = useState<SystemSettings>(() => {
    try {
      const saved = localStorage.getItem('ruibao_system_settings');
      return saved ? { ...DEFAULT_SETTINGS, ...JSON.parse(saved) } : { ...DEFAULT_SETTINGS };
    } catch {
      return { ...DEFAULT_SETTINGS };
    }
  });

  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [projectStatus, setProjectStatus] = useState<DataStatus>('LOADING');
  const [projectStatusMessage, setProjectStatusMessage] = useState('正在从 Tax 服务加载项目数据…');
  const [aiModelStatus, setAiModelStatus] = useState<AiModelStatus>({
    state: 'LOADING',
    message: '正在读取 AI 模型状态…',
    endpoints: [],
  });

  const [riskEvents, setRiskEvents] = useState<RiskEvent[]>([]);
  const [riskStatus, setRiskStatus] = useState<DataStatus>('LOADING');
  const [riskStatusMessage, setRiskStatusMessage] = useState('正在从 Tax 服务加载风险集合…');
  const [taxLedgerStatus, setTaxLedgerStatus] = useState<DataStatus>('LOADING');
  const [taxLedgerStatusMessage, setTaxLedgerStatusMessage] = useState('正在读取 Canonical Statutory VAT 资源…');
  const [isLedgerRebuilding, setIsLedgerRebuilding] = useState(false);
  const [auditLogs, setAuditLogs] = useState<AuditTrailRecord[]>([]);
  const [auditStatus, setAuditStatus] = useState<DataStatus>('LOADING');
  const [entityTaxLedgerRecords, setEntityTaxLedgerRecords] = useState<EntityTaxLedgerRecord[]>([]);
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>([]);
  const [isAiThinking, setIsAiThinking] = useState<boolean>(false);
  const [actionNotice, setActionNotice] = useState<string>('');

  const entityVatAbortRef = useRef<AbortController | null>(null);
  const projectAbortRef = useRef<AbortController | null>(null);
  const riskAbortRef = useRef<AbortController | null>(null);
  const auditAbortRef = useRef<AbortController | null>(null);
  const assistantAbortRef = useRef<AbortController | null>(null);

  const [isNewRecordModalOpen, setIsNewRecordModalOpen] = useState<boolean>(false);
  const [isExportModalOpen, setIsExportModalOpen] = useState<boolean>(false);
  const [isTokenHubSetupOpen, setIsTokenHubSetupOpen] = useState<boolean>(false);
  const [isRagSetupOpen, setIsRagSetupOpen] = useState<boolean>(false);

  const loadEntityVatDomain = useCallback(async () => {
    entityVatAbortRef.current?.abort();
    const controller = new AbortController();
    entityVatAbortRef.current = controller;
    setTaxLedgerStatus('LOADING');
    setTaxLedgerStatusMessage('正在读取 Canonical Statutory VAT 资源…');
    try {
      const result = await fetchLegalEntityStatutoryVatCollection(controller.signal);
      if (controller.signal.aborted) return;
      setEntityTaxLedgerRecords(result.items);
      setTaxLedgerStatus(result.status);
      setTaxLedgerStatusMessage(
        result.message || `已加载 ${result.items.length} 条法人月度确定性税务台账。`,
      );
    } catch (error) {
      if (controller.signal.aborted) return;
      setEntityTaxLedgerRecords([]);
      setTaxLedgerStatus('UNAVAILABLE');
      setTaxLedgerStatusMessage(errorMessage(error));
    } finally {
      if (entityVatAbortRef.current === controller) entityVatAbortRef.current = null;
    }
  }, []);

  const loadProjectDomain = useCallback(async () => {
    projectAbortRef.current?.abort();
    const controller = new AbortController();
    projectAbortRef.current = controller;
    setProjectStatus('LOADING');
    setProjectStatusMessage('正在从 Tax 服务加载项目数据…');
    try {
      const loadedProjects = await fetchConfiguredProjects(controller.signal);
      if (controller.signal.aborted) return;
      const projectsWithLedger = loadedProjects.map(project => ({
        ...project,
        taxRecords: [],
      }));
      setProjects(projectsWithLedger);
      setSelectedProjectId(current => projectsWithLedger.some(project => project.id === current)
        ? current
        : (projectsWithLedger[0]?.id ?? ''));
      setProjectStatus('READY');
      setProjectStatusMessage(`已加载 ${projectsWithLedger.length} 个项目及其基础数据。`);
    } catch (error) {
      if (controller.signal.aborted) return;
      setProjects([]);
      setSelectedProjectId('');
      setProjectStatus(error instanceof ApiError && error.status > 0 ? 'DEGRADED' : 'UNAVAILABLE');
      setProjectStatusMessage(errorMessage(error));
    } finally {
      if (projectAbortRef.current === controller) projectAbortRef.current = null;
    }
  }, []);

  const loadAuditDomain = useCallback(async () => {
    auditAbortRef.current?.abort();
    const controller = new AbortController();
    auditAbortRef.current = controller;
    setAuditStatus('LOADING');
    try {
      const result = await fetchAuditLogs(controller.signal);
      if (controller.signal.aborted) return;
      setAuditLogs(result.items);
      setAuditStatus(result.status);
    } catch {
      if (controller.signal.aborted) return;
      setAuditLogs([]);
      setAuditStatus('UNAVAILABLE');
    } finally {
      if (auditAbortRef.current === controller) auditAbortRef.current = null;
    }
  }, []);

  const loadRiskDomain = useCallback(async (projectIds: number[]) => {
    riskAbortRef.current?.abort();
    const controller = new AbortController();
    riskAbortRef.current = controller;

    if (projectIds.length === 0) {
      setRiskEvents([]);
      setRiskStatus('UNAVAILABLE');
      setRiskStatusMessage('当前没有可用项目 ID，未读取风险集合。');
      if (riskAbortRef.current === controller) riskAbortRef.current = null;
      return;
    }

    setRiskStatus('LOADING');
    setRiskStatusMessage('正在从 Tax 服务加载风险集合…');
    try {
      const riskResults = await Promise.allSettled(
        projectIds.map(projectId => fetchRiskEvents(projectId, controller.signal)),
      );
      if (controller.signal.aborted) return;

      const successfulRiskResults = riskResults.filter(result => result.status === 'fulfilled');
      const riskRequestFailed = riskResults.some(result => result.status === 'rejected');
      const riskAllFailed = successfulRiskResults.length === 0;
      const riskBackendDegraded = riskResults.some(
        result => result.status === 'fulfilled' && result.value.status !== 'READY',
      );
      const loadedRiskEvents = riskResults.flatMap(
        result => result.status === 'fulfilled' ? result.value.items : [],
      );
      setRiskEvents(loadedRiskEvents);
      const nextRiskStatus: DataStatus = riskAllFailed
        ? 'UNAVAILABLE'
        : riskRequestFailed || riskBackendDegraded ? 'DEGRADED' : 'READY';
      setRiskStatus(nextRiskStatus);
      const riskMessages = riskResults
        .filter((result): result is PromiseFulfilledResult<Awaited<ReturnType<typeof fetchRiskEvents>>> => result.status === 'fulfilled')
        .map(result => result.value.message.trim())
        .filter(Boolean);
      setRiskStatusMessage(
        nextRiskStatus === 'UNAVAILABLE'
          ? '风险集合请求全部失败，未使用本地数据填充。'
          : riskMessages[0] || (loadedRiskEvents.length > 0
            ? `已加载 ${loadedRiskEvents.length} 条真实风险事件。`
            : '暂无已识别风险事件。'),
      );
    } finally {
      if (riskAbortRef.current === controller) riskAbortRef.current = null;
    }
  }, []);

  useEffect(() => {
    void loadEntityVatDomain();
    return () => entityVatAbortRef.current?.abort();
  }, [loadEntityVatDomain]);

  useEffect(() => {
    void loadProjectDomain();
    return () => projectAbortRef.current?.abort();
  }, [loadProjectDomain]);

  useEffect(() => {
    void loadAuditDomain();
    return () => auditAbortRef.current?.abort();
  }, [loadAuditDomain]);

  const riskScopeKey = [...new Set<number>(projects.map(project => Number(project.numericId)))]
    .filter(projectId => Number.isInteger(projectId) && projectId > 0)
    .sort((left: number, right: number) => left - right)
    .join(',');

  useEffect(() => {
    const projectIds = riskScopeKey
      ? riskScopeKey.split(',').map(value => Number(value)).filter(value => Number.isInteger(value) && value > 0)
      : [];
    void loadRiskDomain(projectIds);
    return () => riskAbortRef.current?.abort();
  }, [loadRiskDomain, riskScopeKey]);

  useEffect(() => {
    const controller = new AbortController();
    setAiModelStatus({ state: 'LOADING', message: '正在读取 AI 模型状态…', endpoints: [] });
    void fetchAiModelStatus(controller.signal)
      .then(status => {
        if (!controller.signal.aborted) setAiModelStatus(status);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setAiModelStatus({ state: 'UNAVAILABLE', message: '状态暂不可用', endpoints: [] });
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => () => {
    entityVatAbortRef.current?.abort();
    projectAbortRef.current?.abort();
    riskAbortRef.current?.abort();
    auditAbortRef.current?.abort();
    assistantAbortRef.current?.abort();
  }, []);

  const currentProject = projects.find(project => project.id === selectedProjectId);
  const unresolvedRiskCount = riskEvents.filter(risk => risk.status !== '已闭环').length;

  const handleSaveSettings = (newSettings: SystemSettings) => {
    setSystemSettings(newSettings);
    try {
      localStorage.setItem('ruibao_system_settings', JSON.stringify(newSettings));
    } catch {
      setActionNotice('本机浏览器不允许保存设置；本次设置只在当前页面生效。');
    }
    setActionNotice('Tax 风控参数已应用；RAG 地址由管理员测试后保存至 Tax 后端。');
  };

  const handleSelectTab = (tab: string) => {
    setCurrentTab(tab);
    if (tab === 'projects') setProjectSubView('list');
    setIsMobileMenuOpen(false);
    if (tab === 'tokenhub-setup') {
      setIsMobileMenuOpen(false);
      setIsTokenHubSetupOpen(true);
    }
  };

  const handleOpenTokenHubSetup = () => {
    setIsTokenHubSetupOpen(true);
    setIsMobileMenuOpen(false);
  };

  const handleOpenRagSetup = () => {
    setIsRagSetupOpen(true);
    setIsMobileMenuOpen(false);
  };

  const handleSelectProject = (projectId: string) => {
    if (!projects.some(project => project.id === projectId)) {
      setActionNotice('该项目不在当前 Tax API 返回结果中，未执行页面切换。');
      return;
    }
    setSelectedProjectId(projectId);
    setCurrentTab('projects');
    setProjectSubView('detail');
    setIsMobileMenuOpen(false);
  };

  const handleResolveRisk = () => {
    setActionNotice('风险处置接口尚未接通，未在浏览器本地修改风险状态。');
  };

  const handleRagSyncCompleted = () => {
    setActionNotice('RAG 同步已完成，正在重新读取 Tax 项目与法人确定性台账数据。');
    void loadProjectDomain();
    void loadEntityVatDomain();
  };

  const handleRebuildTaxLedger = async (period: string) => {
    if (isLedgerRebuilding) return;
    setIsLedgerRebuilding(true);
    try {
      const result = await rebuildTaxLedger(period);
      await loadEntityVatDomain();
      setActionNotice(
        result.status === 'DEGRADED'
          ? `${result.message} 页面已重新读取成功生成的法人法定 VAT 资源。`
          : `已生成 ${result.rowCount} 条 ${result.period} 台账，页面已重新读取法人确定性台账数据。`,
      );
    } catch (error) {
      setActionNotice(`台账生成/重建失败：${errorMessage(error)}；已保留当前页面最后可信数据。`);
    } finally {
      setIsLedgerRebuilding(false);
    }
  };

  const handleSendAiMessage = async (query: string) => {
    const trimmed = query.trim();
    if (!trimmed || isAiThinking) return;
    const userMessage: AssistantMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      content: trimmed,
      timestamp: nowLabel(),
    };
    setAssistantMessages(previous => [...previous, userMessage]);

    if (!currentProject) {
      setAssistantMessages(previous => [...previous, {
        id: `system-${Date.now()}`,
        sender: 'system',
        content: 'AI 问答不可用：当前没有已加载的真实项目数据。',
        timestamp: nowLabel(),
      }]);
      return;
    }

    assistantAbortRef.current?.abort();
    const controller = new AbortController();
    assistantAbortRef.current = controller;
    setIsAiThinking(true);
    try {
      const answer = await askProjectAi(currentProject.numericId, trimmed, undefined, controller.signal);
      setAssistantMessages(previous => [...previous, {
        id: `ai-${Date.now()}`,
        sender: 'ai',
        content: answer.answer,
        timestamp: nowLabel(),
        aiMetadata: answer.metadata,
      }]);
    } catch (error) {
      if (!controller.signal.aborted) {
        setAssistantMessages(previous => [...previous, {
          id: `system-${Date.now()}`,
          sender: 'system',
          content: `AI 问答 DEGRADED：${errorMessage(error)}`,
          timestamp: nowLabel(),
        }]);
      }
    } finally {
      if (!controller.signal.aborted) setIsAiThinking(false);
    }
  };

  const handleAskAiAboutRisk = (entityName: string) => {
    setIsAiOpen(true);
    void handleSendAiMessage(`请基于真实项目数据核查 ${entityName} 的涉税风险，并列出需要补充的证据。`);
  };

  return (
    <div className="fixed inset-0 h-screen w-screen bg-[#0b1326] text-[#dae2fd] flex flex-col md:flex-row  overflow-hidden font-sans">
      <Sidebar currentTab={currentTab} onSelectTab={handleSelectTab} unresolvedRiskCount={unresolvedRiskCount} aiModelStatus={aiModelStatus} onOpenTokenHubSetup={handleOpenTokenHubSetup} onOpenRagSetup={handleOpenRagSetup} />

      {isMobileMenuOpen && (
        <div className="fixed inset-0 z-50 md:hidden flex">
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm" onClick={() => setIsMobileMenuOpen(false)} />
          <div className="relative w-[var(--sidebar-width)] h-full bg-[var(--color-surface)] z-10 flex flex-col shadow-2xl">
            <Sidebar isMobile onCloseMobile={() => setIsMobileMenuOpen(false)} currentTab={currentTab} onSelectTab={handleSelectTab} unresolvedRiskCount={unresolvedRiskCount} aiModelStatus={aiModelStatus} onOpenTokenHubSetup={handleOpenTokenHubSetup} onOpenRagSetup={handleOpenRagSetup} />
          </div>
        </div>
      )}

      <div className="flex-1 flex flex-col md:ml-[var(--sidebar-width)] min-w-0 h-full overflow-hidden relative">
        <Header
          onToggleAi={() => setIsAiOpen(open => !open)}
          isAiOpen={isAiOpen}
          onOpenExportModal={() => setIsExportModalOpen(true)}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          onToggleMobileMenu={() => setIsMobileMenuOpen(open => !open)}
          settings={systemSettings}
          onSaveSettings={handleSaveSettings}
          riskStatus={riskStatus}
          unresolvedRiskCount={unresolvedRiskCount}
        />

        <div className="flex-1 flex pt-16 h-full w-full overflow-hidden">
          <main className="flex-1 min-w-0 h-full overflow-y-auto overscroll-contain p-3.5 md:p-5 lg:p-6 scrollbar-hide bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-[#171f33]/40 via-[#0b1326] to-[#0b1326]">
            <div className="w-full mx-auto pb-16">
              {actionNotice && (
                <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/5 px-3 py-2 text-[12px] text-[#ffd0a8]" role="status">
                  <span>{actionNotice}</span>
                  <button type="button" onClick={() => setActionNotice('')} className="text-[#8e909f] hover:text-[#dae2fd]">关闭</button>
                </div>
              )}

              {currentTab === 'dashboard' && (
                <DashboardView
                  projects={projects}
                  dataStatus={projectStatus}
                  dataStatusMessage={projectStatusMessage}
                  onRetry={() => void loadProjectDomain()}
                  onSelectProject={handleSelectProject}
                  onOpenEntityCorporate={() => setCurrentTab('entity-profile')}
                  onOpenRiskCenter={() => setCurrentTab('risk-center')}
                  onOpenExportModal={() => setIsExportModalOpen(true)}
                  riskEvents={riskEvents}
                  riskStatus={riskStatus}
                  riskStatusMessage={riskStatusMessage}
                  taxLedgerStatus={taxLedgerStatus}
                  taxLedgerStatusMessage={taxLedgerStatusMessage}
                  taxLedgerRecords={entityTaxLedgerRecords}
                  settings={systemSettings}
                />
              )}

              {currentTab === 'projects' && projectSubView === 'list' && (
                <ProjectRepositoryView
                  projects={projects}
                  dataStatus={projectStatus}
                  dataStatusMessage={projectStatusMessage}
                  onRetry={() => void loadProjectDomain()}
                  onSelectProject={handleSelectProject}
                  onOpenNewRecordModal={() => setIsNewRecordModalOpen(true)}
                  onOpenExportModal={() => setIsExportModalOpen(true)}
                  onNavigateToAiReview={projectId => { setSelectedProjectId(projectId); setCurrentTab('ai-decision'); }}
                  settings={systemSettings}
                />
              )}

              {currentTab === 'projects' && projectSubView === 'detail' && currentProject && (
                <ProjectDetailView
                  project={currentProject}
                  projects={projects}
                  onSelectProject={projectId => setSelectedProjectId(projectId)}
                  onBack={() => setProjectSubView('list')}
                  onOpenNewRecordModal={() => setIsNewRecordModalOpen(true)}
                  onOpenExportModal={() => setIsExportModalOpen(true)}
                  onAskAiAboutRisk={handleAskAiAboutRisk}
                  onGoToPlanning={() => setCurrentTab('ai-decision')}
                  onProjectDataDeleted={() => void loadProjectDomain()}
                  settings={systemSettings}
                />
              )}

              {currentTab === 'projects' && projectSubView === 'detail' && !currentProject && (
                <DataStatusCard status={projectStatus} title="项目详情不可用" message={projectStatusMessage} onRetry={() => void loadProjectDomain()} />
              )}

              {currentTab === 'entity-profile' && <EntityCorporateView />}

              {currentTab === 'tax-ledger' && (
                <TaxLedgerView records={entityTaxLedgerRecords} dataStatus={taxLedgerStatus} dataStatusMessage={taxLedgerStatusMessage} onRetry={() => void loadEntityVatDomain()} onOpenNewRecordModal={() => setIsNewRecordModalOpen(true)} onOpenExportModal={() => setIsExportModalOpen(true)} onAskAiAboutRisk={handleAskAiAboutRisk} onRebuildTaxLedger={handleRebuildTaxLedger} isRebuilding={isLedgerRebuilding} settings={systemSettings} />
              )}
              {currentTab === 'ai-decision' && (
                <AiDecisionCenterView
                  projects={projects}
                  selectedProjectId={selectedProjectId}
                  onSelectProject={setSelectedProjectId}
                  dataStatus={projectStatus}
                  onAskAiAboutRisk={handleAskAiAboutRisk}
                />
              )}
              {currentTab === 'risk-center' && (
                <RiskCenterView riskEvents={riskEvents} dataStatus={riskStatus} dataStatusMessage={riskStatusMessage} onResolveRisk={handleResolveRisk} onAskAiAboutRisk={handleAskAiAboutRisk} settings={systemSettings} />
              )}
              {currentTab === 'audit' && <AuditView auditLogs={auditLogs} dataStatus={auditStatus} onOpenExportModal={() => setIsExportModalOpen(true)} />}
            </div>
          </main>

          <AiAssistantDrawer isOpen={isAiOpen} onClose={() => setIsAiOpen(false)} messages={assistantMessages} onSendMessage={query => void handleSendAiMessage(query)} isAiThinking={isAiThinking} settings={systemSettings} />
        </div>
      </div>

      {isNewRecordModalOpen && (
        <NewTaxRecordModal
          isOpen
          onClose={() => setIsNewRecordModalOpen(false)}
          projects={projects}
          projectId={currentProject?.numericId}
          defaultProjectCode={currentProject?.projectCode}
          defaultProjectName={currentProject?.name}
          onSyncCompleted={handleRagSyncCompleted}
        />
      )}
      <ExportReportModal isOpen={isExportModalOpen} onClose={() => setIsExportModalOpen(false)} projects={projects} />
      <TokenHubSetupWizard isOpen={isTokenHubSetupOpen} onClose={() => setIsTokenHubSetupOpen(false)} onSaved={() => { /* 配置已落盘，AI 状态将由下一次 fetchAiModelStatus 反映 */ }} />
      <RagSetupWizard isOpen={isRagSetupOpen} onClose={() => setIsRagSetupOpen(false)} onSaved={() => { /* RAG 地址已持久化到 Tax 后端 */ }} />
    </div>
  );
}
