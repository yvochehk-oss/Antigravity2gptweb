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
  BellRing
} from 'lucide-react';
import { SystemSettings } from '../types';

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

  useEffect(() => {
    if (isOpen) {
      setFormData(settings);
    }
  }, [isOpen, settings]);

  if (!isOpen) return null;

  const handleSave = () => {
    setIsSaving(true);
    setTimeout(() => {
      onSaveSettings(formData);
      setIsSaving(false);
      setShowSavedToast(true);
      setTimeout(() => {
        setShowSavedToast(false);
        onClose();
      }, 1000);
    }, 400);
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
                <p className="text-[11px] text-[#8e909f]">回答时自动检索全省 142 个标段全部底稿与发票明细</p>
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
              disabled={isSaving}
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
