import { useState, useEffect } from 'react';
import {
  X, 
  Settings, 
  Check, 
  RotateCcw, 
  ShieldCheck, 
  Sliders, 
  AlertTriangle, 
  Cpu, 
  RefreshCw,
  BellRing,
  Radio,
} from 'lucide-react';
import { fetchRagSettings, saveRagSettings, testRagSettings } from '../api';
import { RagServiceSettings, SystemSettings } from '../types';

export const DEFAULT_SETTINGS: SystemSettings = {
  autoFourFlowsMatch: true,
  crossRegionTaxThreshold: 5,
  budgetOverrunStopPayThreshold: 5,
  taxAuditAutoNotify: true,
  dataRefreshInterval: 60,
  aiDeepAnalysisMode: true,
};

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  settings: SystemSettings;
  onSaveSettings: (newSettings: SystemSettings) => void;
}

export function SettingsModal({
  isOpen,
  onClose,
  settings,
  onSaveSettings,
}: SettingsModalProps) {
  const [formData, setFormData] = useState<SystemSettings>(settings);
  const [isSaving, setIsSaving] = useState(false);
  const [showSavedToast, setShowSavedToast] = useState(false);
  const [ragSettings, setRagSettings] = useState<RagServiceSettings | null>(null);
  const [ragUrl, setRagUrl] = useState('');
  const [approvePrivate, setApprovePrivate] = useState(false);
  const [ragLoading, setRagLoading] = useState(false);
  const [ragAction, setRagAction] = useState<'test' | 'save' | null>(null);
  const [ragTestResult, setRagTestResult] = useState<RagServiceSettings | null>(null);
  const [ragError, setRagError] = useState('');

  useEffect(() => {
    if (isOpen) {
      setFormData(settings);
    }
  }, [isOpen, settings]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const controller = new AbortController();
    setRagLoading(true);
    setRagError('');
    setRagTestResult(null);
    void fetchRagSettings(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setRagSettings(result);
        setRagUrl(result.url);
        setApprovePrivate(result.approvedPrivate);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setRagSettings(null);
        setRagError(error instanceof Error ? error.message : 'RAG 设置读取失败。');
      })
      .finally(() => {
        if (!controller.signal.aborted) setRagLoading(false);
      });
    return () => controller.abort();
  }, [isOpen]);

  if (!isOpen) return null;

  const ragChanged = ragSettings === null
    ? Boolean(ragUrl.trim())
    : (ragUrl.trim() !== ragSettings.url || approvePrivate !== ragSettings.approvedPrivate);

  const handleTestRag = async () => {
    if (!ragUrl.trim()) {
      setRagError('请输入 RAG 系统的 IP 地址或域名。');
      return;
    }
    setRagAction('test');
    setRagError('');
    setRagTestResult(null);
    try {
      const result = await testRagSettings({
        url: ragUrl,
        approvePrivate,
      });
      setRagTestResult(result);
      if (!result.ok) setRagError(result.error || 'RAG 连接测试失败。');
    } catch (error: unknown) {
      setRagError(error instanceof Error ? error.message : 'RAG 连接测试失败。');
    } finally {
      setRagAction(null);
    }
  };

  const handleSave = async () => {
    if (isSaving || ragAction !== null) return;
    setIsSaving(true);
    setRagError('');
    try {
      if (ragChanged) {
        setRagAction('save');
        const result = await saveRagSettings({
          url: ragUrl,
          approvePrivate,
        });
        setRagAction(null);
        setRagTestResult(result);
        if (!result.ok) {
          throw new Error(result.error || 'RAG 设置保存失败。');
        }
        setRagSettings(result);
      }
      onSaveSettings(formData);
      setIsSaving(false);
      setShowSavedToast(true);
      setTimeout(() => {
        setShowSavedToast(false);
        onClose();
      }, 1000);
    } catch (error: unknown) {
      setRagAction(null);
      setIsSaving(false);
      setRagError(error instanceof Error ? error.message : '设置保存失败。');
    }
  };

  const handleResetDefaults = () => {
    setFormData(DEFAULT_SETTINGS);
  };

  return (
    <div className="fixed inset-0 bg-black/75 backdrop-blur-md z-50 flex items-center justify-center p-4">
      <div className="bg-[#171f33] border border-[#4cd7f6]/40 rounded-2xl max-w-lg w-full p-6 shadow-2xl flex flex-col max-h-[90vh] overflow-hidden text-[#dae2fd]">
        {/* 标题栏 */}
        <div className="flex justify-between items-center pb-4 border-b border-[#444653]/40">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-[#03b5d3]/20 flex items-center justify-center border border-[#4cd7f6]/40 text-[#4cd7f6]">
              <Settings className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-[16px] font-bold text-[#dae2fd]">系统运行参数与风控阈值配置</h3>
              <p className="text-[11px] text-[#8e909f]">设置变更将即时应用至全站风控计算、预算止付与 AI 审查逻辑</p>
            </div>
          </div>
          <button 
            onClick={onClose} 
            className="p-1.5 rounded-lg text-[#8e909f] hover:text-[#dae2fd] hover:bg-[#222a3d] transition-colors cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* 设置表单内容 */}
        <div className="flex-1 overflow-y-auto py-4 space-y-5 scrollbar-hide text-[13px]">
          
          {/* 1. 税控与四流合一规则 */}
          <div className="bg-[#131b2e] p-4 rounded-xl border border-[#444653]/40 space-y-3.5">
            <div className="flex items-center gap-2 text-[#4cd7f6] font-bold text-[13px] border-b border-[#444653]/30 pb-2">
              <ShieldCheck className="w-4 h-4" />
              <span>税控合规与四流核验规则</span>
            </div>

            {/* 自动四流合一开关 */}
            <div className="flex items-center justify-between">
              <div>
                <p className="font-semibold text-[#dae2fd]">自动启动“四流合一”交叉比对</p>
                <p className="text-[11px] text-[#8e909f]">自动关联核验发票代码、采购合同、银行流水与过磅单据</p>
              </div>
              <button
                type="button"
                onClick={() => setFormData(prev => ({ ...prev, autoFourFlowsMatch: !prev.autoFourFlowsMatch }))}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors cursor-pointer ${
                  formData.autoFourFlowsMatch ? 'bg-[#03b5d3]' : 'bg-[#2d3449]'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    formData.autoFourFlowsMatch ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            {/* 跨区异地预缴核销偏差阈值 */}
            <div className="pt-2">
              <div className="flex justify-between items-center mb-1.5">
                <span className="font-semibold text-[#dae2fd]">跨区施工异地预缴核销偏差阈值</span>
                <span className="text-[#4cd7f6] font-mono-num font-bold bg-[#03b5d3]/15 px-2 py-0.5 rounded border border-[#4cd7f6]/30 text-[12px]">
                  {formData.crossRegionTaxThreshold}%
                </span>
              </div>
              <p className="text-[11px] text-[#8e909f] mb-2">
                当跨地市施工预缴税额与个税扣缴申报差额比例超过此值时触发预警
              </p>
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min="1"
                  max="15"
                  step="1"
                  value={formData.crossRegionTaxThreshold}
                  onChange={(e) => setFormData(prev => ({ ...prev, crossRegionTaxThreshold: Number(e.target.value) }))}
                  className="w-full h-1.5 bg-[#222a3d] rounded-lg appearance-none cursor-pointer accent-[#4cd7f6]"
                />
              </div>
              <div className="flex justify-between text-[10px] text-[#8e909f] mt-1 font-mono-num">
                <span>1% (严格审慎)</span>
                <span>5% (推荐基准)</span>
                <span>15% (宽松容差)</span>
              </div>
            </div>
          </div>

          {/* 2. 成本管控与自动止付令 */}
          <div className="bg-[#131b2e] p-4 rounded-xl border border-[#444653]/40 space-y-3.5">
            <div className="flex items-center gap-2 text-[#ffb59a] font-bold text-[13px] border-b border-[#444653]/30 pb-2">
              <Sliders className="w-4 h-4" />
              <span>资金与概算硬约束</span>
            </div>

            {/* 超概算阈值自动触发止付令 */}
            <div>
              <div className="flex justify-between items-center mb-1.5">
                <span className="font-semibold text-[#dae2fd]">超概算阈值自动触发止付令</span>
                <span className="text-[#ffb59a] font-mono-num font-bold bg-[#ffb59a]/15 px-2 py-0.5 rounded border border-[#ffb59a]/30 text-[12px]">
                  {formData.budgetOverrunStopPayThreshold}%
                </span>
              </div>
              <p className="text-[11px] text-[#8e909f] mb-2">
                当施工分部或标段实际支出超预算达到该比例时，系统自动锁定付款通道并生成止付令
              </p>
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min="1"
                  max="20"
                  step="1"
                  value={formData.budgetOverrunStopPayThreshold}
                  onChange={(e) => setFormData(prev => ({ ...prev, budgetOverrunStopPayThreshold: Number(e.target.value) }))}
                  className="w-full h-1.5 bg-[#222a3d] rounded-lg appearance-none cursor-pointer accent-[#ffb59a]"
                />
              </div>
              <div className="flex justify-between text-[10px] text-[#8e909f] mt-1 font-mono-num">
                <span>1% (立即熔断)</span>
                <span>5% (标准预警)</span>
                <span>20% (弹性缓冲)</span>
              </div>
            </div>
          </div>

          {/* 3. 智能决策与系统协同 */}
          <div className="bg-[#131b2e] p-4 rounded-xl border border-[#444653]/40 space-y-3.5">
            <div className="flex items-center gap-2 text-[#b8c4ff] font-bold text-[13px] border-b border-[#444653]/30 pb-2">
              <Cpu className="w-4 h-4" />
              <span>AI 决策大脑与实时通知</span>
            </div>

            {/* AI 深度穿透模式 */}
            <div className="flex items-center justify-between">
              <div>
                <p className="font-semibold text-[#dae2fd]">AI 智能助手深度穿透核验模式</p>
                <p className="text-[11px] text-[#8e909f]">回答时仅检索当前真实项目及后端已接通的底稿数据</p>
              </div>
              <button
                type="button"
                onClick={() => setFormData(prev => ({ ...prev, aiDeepAnalysisMode: !prev.aiDeepAnalysisMode }))}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors cursor-pointer ${
                  formData.aiDeepAnalysisMode ? 'bg-[#1e40af]' : 'bg-[#2d3449]'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    formData.aiDeepAnalysisMode ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            {/* 风险自动弹窗通知 */}
            <div className="flex items-center justify-between">
              <div>
                <p className="font-semibold text-[#dae2fd]">高危涉税风险实时自动弹窗告警</p>
                <p className="text-[11px] text-[#8e909f]">侦测到偷漏税隐患或虚开异常时立即高亮推送</p>
              </div>
              <button
                type="button"
                onClick={() => setFormData(prev => ({ ...prev, taxAuditAutoNotify: !prev.taxAuditAutoNotify }))}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors cursor-pointer ${
                  formData.taxAuditAutoNotify ? 'bg-[#EF4444]' : 'bg-[#2d3449]'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    formData.taxAuditAutoNotify ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            {/* 数据大屏自动同步周期 */}
            <div className="pt-1">
              <p className="font-semibold text-[#dae2fd] mb-1.5">数据大屏与台账自动同步频率</p>
              <div className="grid grid-cols-4 gap-2">
                {[
                  { label: '30秒', value: 30 },
                  { label: '1分钟', value: 60 },
                  { label: '5分钟', value: 300 },
                  { label: '仅手动', value: 0 }
                ].map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    onClick={() => setFormData(prev => ({ ...prev, dataRefreshInterval: item.value }))}
                    className={`py-1.5 px-2 rounded-lg text-[11.5px] font-medium border text-center transition-all cursor-pointer ${
                      formData.dataRefreshInterval === item.value
                        ? 'bg-[#03b5d3]/20 border-[#4cd7f6] text-[#4cd7f6] font-bold shadow-[0_0_8px_rgba(76,215,246,0.2)]'
                        : 'bg-[#171f33] border-[#444653]/40 text-[#8e909f] hover:text-[#dae2fd]'
                    }`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* 4. Tax -> RAG 连接设置。凭证只由 Tax 服务端环境变量提供。 */}
          <div className="bg-[#131b2e] p-4 rounded-xl border border-[#4cd7f6]/30 space-y-3.5">
            <div className="flex items-center gap-2 text-[#4cd7f6] font-bold text-[13px] border-b border-[#444653]/30 pb-2">
              <Radio className="w-4 h-4" />
              <span>RAG 知识库连接（管理员）</span>
            </div>

            <div>
              <label htmlFor="rag-service-url" className="font-semibold text-[#dae2fd]">
                RAG 系统 IP 地址或域名
              </label>
              <p className="text-[11px] text-[#8e909f] mt-1 mb-2">
                Tax 与 RAG 可部署在不同电脑；填写 RAG 服务的完整地址，例如 http://192.168.1.20:8922 或 https://rag.example.com。
              </p>
              <div className="flex gap-2">
                <input
                  id="rag-service-url"
                  type="url"
                  value={ragUrl}
                  onChange={(event) => {
                    setRagUrl(event.target.value);
                    setRagTestResult(null);
                    setRagError('');
                  }}
                  placeholder="http://192.168.1.20:8922"
                  maxLength={300}
                  disabled={ragLoading || ragAction !== null}
                  className="min-w-0 flex-1 bg-[#171f33] border border-[#444653]/60 focus:border-[#4cd7f6] text-[12px] font-mono-num text-[#dae2fd] px-3 py-2 rounded-lg focus:outline-none disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={() => void handleTestRag()}
                  disabled={ragLoading || ragAction !== null || !ragUrl.trim()}
                  className="shrink-0 px-3 py-2 bg-[#222a3d] hover:bg-[#2d3449] border border-[#4cd7f6]/40 text-[#4cd7f6] text-[12px] font-semibold rounded-lg flex items-center gap-1.5 disabled:opacity-50"
                >
                  {ragAction === 'test' ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : null}
                  测试连接
                </button>
              </div>
            </div>

            <label className="flex items-start gap-2.5 cursor-pointer">
              <input
                type="checkbox"
                checked={approvePrivate}
                onChange={(event) => {
                  setApprovePrivate(event.target.checked);
                  setRagTestResult(null);
                  setRagError('');
                }}
                disabled={ragLoading || ragAction !== null}
                className="mt-0.5 h-4 w-4 accent-[#03b5d3]"
              />
              <span>
                <span className="block font-semibold text-[#dae2fd]">批准本机/公司内网 RAG 地址</span>
                <span className="block text-[11px] text-[#8e909f] mt-0.5">
                  仅当 RAG 位于本机或可信内网时勾选；系统会记录 DNS 地址快照，地址变化后要求重新批准。
                </span>
              </span>
            </label>

            <div className="rounded-lg bg-[#0b1326]/70 border border-[#444653]/40 px-3 py-2 text-[11px] text-[#aeb6d4]">
              {ragLoading ? (
                <span className="inline-flex items-center gap-1.5"><RefreshCw className="w-3 h-3 animate-spin" />正在读取 Tax 后端保存的连接设置…</span>
              ) : ragSettings?.configured ? (
                <span>当前已保存：{ragSettings.host || ragSettings.url}{ragSettings.lastTestedAt ? ` · 最近测试 ${ragSettings.lastTestedAt}` : ''}</span>
              ) : ragSettings ? (
                <span>尚未保存管理员配置；当前使用 Tax 服务端默认地址。</span>
              ) : (
                <span>无法读取当前配置，请确认已登录并联系管理员。</span>
              )}
            </div>

            {ragTestResult?.ok && (
              <div className="rounded-lg bg-[#10B981]/10 border border-[#10B981]/30 px-3 py-2 text-[11px] text-[#6ee7b7]" role="status">
                连接测试成功{ragTestResult.ragVersion ? ` · RAG ${ragTestResult.ragVersion}` : ''}；发现 {ragTestResult.projects.length} 个 RAG 项目。点击底部“保存设置并应用”后才会持久化地址。
              </div>
            )}
            {ragError && (
              <div className="rounded-lg bg-[#EF4444]/10 border border-[#EF4444]/30 px-3 py-2 text-[11px] text-[#fca5a5]" role="alert">
                <AlertTriangle className="w-3.5 h-3.5 inline mr-1 align-[-2px]" />{ragError}
              </div>
            )}
            <p className="text-[10px] text-[#8e909f]">
              shared key 仅保存在 Tax 服务端环境变量 RAG_SHARED_API_KEY 中，不会在此页面显示或发送。
            </p>
          </div>
        </div>

        {/* 底部按钮操作区 */}
        <div className="flex justify-between items-center pt-4 border-t border-[#444653]/40">
          <button
            type="button"
            onClick={handleResetDefaults}
            className="flex items-center gap-1.5 px-3 py-2 text-[#8e909f] hover:text-[#dae2fd] text-[12px] rounded-lg hover:bg-[#222a3d] transition-colors cursor-pointer"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>恢复默认值</span>
          </button>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-[#2d3449] hover:bg-[#31394d] text-[#dae2fd] text-[13px] font-medium rounded-lg cursor-pointer"
            >
              取消
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={isSaving || ragAction !== null || ragLoading}
              className="px-5 py-2 bg-[#03b5d3] hover:bg-[#03b5d3]/80 text-[#001f26] text-[13px] font-bold rounded-lg flex items-center gap-1.5 cursor-pointer shadow-[0_0_12px_rgba(76,215,246,0.3)] disabled:opacity-50"
            >
              {isSaving ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" />
                  <span>正在应用...</span>
                </>
              ) : showSavedToast ? (
                <>
                  <Check className="w-4 h-4" />
                  <span>已保存并生效！</span>
                </>
              ) : (
                <span>保存设置并应用</span>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ===========================================================
   保持 Windows ClearType 亚像素渲染（系统字体方案下无需 swap）
   -webkit-font-smoothing: auto  -> Windows 使用 ClearType
                                -> macOS  使用视网膜灰度平滑
   =========================================================== */
*, *::before, *::after, html, body, .antialiased {
  -webkit-font-smoothing: auto !important;
  -moz-osx-font-smoothing: auto !important;
  text-rendering: auto !important;
}
