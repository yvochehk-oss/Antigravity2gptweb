import { useState } from 'react';
import { 
  X, 
  Database, 
  Search, 
  Sparkles, 
  FileCheck, 
  AlertTriangle, 
  ShieldCheck, 
  RefreshCw, 
  Layers,
  FileText,
  Building2,
  CheckCircle2
} from 'lucide-react';
import { TaxLedgerRecord, TaxCategory, FilingStatus, RiskLevel } from '../types';

interface NewTaxRecordModalProps {
  isOpen: boolean;
  onClose: () => void;
  onAddRecord: (record: Omit<TaxLedgerRecord, 'id' | 'updateTime'>) => void;
  defaultProjectName?: string;
}

// 模拟企业 RAG 知识湖中已索引但待查账归集的业财票据包
const RAG_PENDING_DOCUMENTS = [
  {
    id: 'rag-bundle-01',
    docId: 'RAG-Doc://财税底账/2026-Q3/幕墙分包合同与数电票.pdf',
    entityName: '四川锐宝幕墙科技（智慧幕墙与光伏工程分包）',
    entityCategory: '建筑幕墙与节能分包',
    declareAmount: 18500000,
    taxAmount: 1665000,
    taxCategory: '增值税 (普通/专用)' as TaxCategory,
    filingPeriod: '2026年第二季度',
    status: '待主管复核' as FilingStatus,
    riskLevel: '预警' as RiskLevel,
    invoiceCode: '数电专票-31002688921',
    vectorSimilarity: 99.4,
    riskDescription: 'RAG 语义分析发现：发票开具抬头与银行付款账户一致，但物流现场签收单据签字存在跨期滞后（相隔42天），存在暂估挂账税务稽核偏差风险。',
    fourFlows: {
      contractMatch: true,
      invoiceMatch: true,
      paymentMatch: true,
      logisticsMatch: false,
    },
    docHighlights: ['合同总价 ¥18,500,000', '发票联税率 9%', '银企直联流水匹配度 100%', '现场电子磅单签名比对存疑']
  },
  {
    id: 'rag-bundle-02',
    docId: 'RAG-Doc://财税底账/2026-Q3/特种商砼集采电子底账.json',
    entityName: '四川恒固建材物资（高标号特种预拌商砼供应）',
    entityCategory: '主材集中采购供应',
    declareAmount: 9600000,
    taxAmount: 864000,
    taxCategory: '增值税 (普通/专用)' as TaxCategory,
    filingPeriod: '2026年第二季度',
    status: '已合规申报' as FilingStatus,
    riskLevel: '正常' as RiskLevel,
    invoiceCode: '数电专票-44032187654',
    vectorSimilarity: 98.8,
    riskDescription: 'RAG 自动质检完成：合同条款、数电发票金税抵扣、银企直联付汇凭证及现场混凝土电子磅单四流完全一致，符合即征即退进项抵扣合规标准。',
    fourFlows: {
      contractMatch: true,
      invoiceMatch: true,
      paymentMatch: true,
      logisticsMatch: true,
    },
    docHighlights: ['集采框架协议通过', '发票代码 44032187654 验真通过', '招商银行对账单一致', '物流轨迹校验闭环']
  },
  {
    id: 'rag-bundle-03',
    docId: 'RAG-Doc://跨境税务/2026-Q2/德国重型吊装设备租赁备案.pdf',
    entityName: '四川德力重工设备（特种高空重型吊装设备租赁）',
    entityCategory: '大型特种机械租赁',
    declareAmount: 24000000,
    taxAmount: 2400000,
    taxCategory: '预提所得税' as TaxCategory,
    filingPeriod: '2026年第二季度',
    status: '异常-税务稽查中' as FilingStatus,
    riskLevel: '高危' as RiskLevel,
    invoiceCode: '跨境付汇凭证-DE-99210',
    vectorSimilarity: 97.6,
    riskDescription: 'RAG 穿透稽查警报：非居民企业跨境租金涉及双边税收协定常设机构（PE）认定争议，预提所得税协定优惠税率与主管税务机关备案文件存在 5% 税率差额争议。',
    fourFlows: {
      contractMatch: true,
      invoiceMatch: false,
      paymentMatch: true,
      logisticsMatch: true,
    },
    docHighlights: ['涉税双边协定判例触发', '对外支付税务备案表缺漏项', '外汇管理局付汇申报预警', '存在滞纳金潜在风险']
  }
];

export function NewTaxRecordModal({
  isOpen,
  onClose,
  onAddRecord,
  defaultProjectName = '阿尔法一号大厦工程'
}: NewTaxRecordModalProps) {
  const [selectedBundleId, setSelectedBundleId] = useState<string>(RAG_PENDING_DOCUMENTS[0].id);
  const [isSearching, setIsSearching] = useState(false);
  const [ragSearchQuery, setRagSearchQuery] = useState('');
  const [activeTab, setActiveTab] = useState<'lake' | 'semantic'>('lake');

  if (!isOpen) return null;

  const currentDoc = RAG_PENDING_DOCUMENTS.find(d => d.id === selectedBundleId) || RAG_PENDING_DOCUMENTS[0];

  const handleSyncToLedger = () => {
    setIsSearching(true);
    setTimeout(() => {
      onAddRecord({
        entityName: currentDoc.entityName,
        entityCategory: currentDoc.entityCategory,
        declareAmount: currentDoc.declareAmount,
        taxAmount: currentDoc.taxAmount,
        taxCategory: currentDoc.taxCategory,
        filingPeriod: currentDoc.filingPeriod,
        status: currentDoc.status,
        riskLevel: currentDoc.riskLevel,
        riskDescription: currentDoc.riskDescription,
        invoiceCode: currentDoc.invoiceCode,
        ragSourceDoc: currentDoc.docId,
        vectorSimilarity: currentDoc.vectorSimilarity,
        fourFlowsCheck: { ...currentDoc.fourFlows }
      });
      setIsSearching(false);
      onClose();
    }, 400);
  };

  return (
    <div className="fixed inset-0 bg-black/75 backdrop-blur-md z-50 flex items-center justify-center p-4">
      <div className="bg-[#131b2e] border border-[#4cd7f6]/40 rounded-2xl max-w-2xl w-full p-6 shadow-2xl space-y-5 text-[13px] relative overflow-hidden">
        {/* 背景光效 */}
        <div className="absolute top-0 right-0 w-80 h-80 bg-[#4cd7f6]/5 rounded-full blur-3xl pointer-events-none"></div>

        {/* 标题栏 */}
        <div className="flex justify-between items-start pb-4 border-b border-[#444653]/30">
          <div>
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-[#03b5d3]/15 text-[#4cd7f6] border border-[#4cd7f6]/30">
                <Database className="w-5 h-5" />
              </div>
              <h3 className="text-[18px] font-bold text-[#dae2fd]">RAG 业财知识湖凭证智能检索与查账同步</h3>
            </div>
            <p className="text-[12px] text-[#8e909f] mt-1">
              本系统定位为<strong className="text-[#4cd7f6]">【智能查账与风控审计大脑】</strong>，数据无需手工录入，全量从企业 RAG 知识湖检索抽取合同、税票、资金及物流凭证，自动比对并定位涉税问题。
            </p>
          </div>
          <button 
            onClick={onClose} 
            className="text-[#8e909f] hover:text-[#dae2fd] p-1 rounded-lg hover:bg-[#222a3d] cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* 目标项目标识 */}
        <div className="bg-[#0b1326] border border-[#444653]/40 rounded-xl p-3 flex items-center justify-between">
          <div className="flex items-center gap-2 text-[#dae2fd]">
            <Building2 className="w-4 h-4 text-[#4cd7f6]" />
            <span className="text-[12px] text-[#8e909f]">查账归集目标工程：</span>
            <span className="font-bold">{defaultProjectName}</span>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-[#10B981] bg-[#10B981]/15 px-2.5 py-0.5 rounded-full border border-[#10B981]/30">
            <span className="w-1.5 h-1.5 rounded-full bg-[#10B981] animate-pulse"></span>
            RAG 向量库已就绪
          </div>
        </div>

        {/* 模式切换 */}
        <div className="flex border-b border-[#444653]/30">
          <button
            type="button"
            onClick={() => setActiveTab('lake')}
            className={`pb-2.5 px-4 font-semibold text-[13px] border-b-2 transition-colors cursor-pointer flex items-center gap-2 ${
              activeTab === 'lake'
                ? 'border-[#4cd7f6] text-[#4cd7f6]'
                : 'border-transparent text-[#8e909f] hover:text-[#dae2fd]'
            }`}
          >
            <Layers className="w-4 h-4" />
            <span>知识湖待查账凭证包 ({RAG_PENDING_DOCUMENTS.length})</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('semantic')}
            className={`pb-2.5 px-4 font-semibold text-[13px] border-b-2 transition-colors cursor-pointer flex items-center gap-2 ${
              activeTab === 'semantic'
                ? 'border-[#4cd7f6] text-[#4cd7f6]'
                : 'border-transparent text-[#8e909f] hover:text-[#dae2fd]'
            }`}
          >
            <Search className="w-4 h-4" />
            <span>RAG 语义智能检索与查账</span>
          </button>
        </div>

        {/* 标签页 1: 待同步的 RAG 凭证列表 */}
        {activeTab === 'lake' ? (
          <div className="space-y-3 max-h-[260px] overflow-y-auto pr-1">
            {RAG_PENDING_DOCUMENTS.map((doc) => {
              const isSelected = doc.id === selectedBundleId;
              return (
                <div
                  key={doc.id}
                  onClick={() => setSelectedBundleId(doc.id)}
                  className={`p-3.5 rounded-xl border transition-all cursor-pointer ${
                    isSelected
                      ? 'bg-[#171f33] border-[#4cd7f6] shadow-[0_0_15px_rgba(76,215,246,0.15)] ring-1 ring-[#4cd7f6]/50'
                      : 'bg-[#0b1326]/60 border-[#444653]/40 hover:bg-[#171f33]/60'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${
                        doc.riskLevel === '高危' ? 'bg-[#EF4444]' : doc.riskLevel === '预警' ? 'bg-[#F59E0B]' : 'bg-[#10B981]'
                      }`}></span>
                      <span className="font-bold text-[#dae2fd] text-[13px]">{doc.entityName}</span>
                      <span className="text-[11px] text-[#8e909f] bg-[#222a3d] px-2 py-0.5 rounded">
                        {doc.entityCategory}
                      </span>
                    </div>
                    <span className="text-[11px] font-mono-num text-[#4cd7f6]">
                      向量匹配度 {doc.vectorSimilarity}%
                    </span>
                  </div>

                  <div className="grid grid-cols-3 gap-2 mt-2.5 text-[12px] font-mono-num">
                    <div>
                      <span className="text-[#8e909f]">申报计税：</span>
                      <span className="font-bold text-[#dae2fd]">¥ {doc.declareAmount.toLocaleString('zh-CN')}</span>
                    </div>
                    <div>
                      <span className="text-[#8e909f]">税额：</span>
                      <span className="font-bold text-[#4cd7f6]">¥ {doc.taxAmount.toLocaleString('zh-CN')}</span>
                    </div>
                    <div>
                      <span className="text-[#8e909f]">四流质检：</span>
                      <span className={`font-semibold ${
                        doc.riskLevel === '正常' ? 'text-[#10B981]' : doc.riskLevel === '预警' ? 'text-[#F59E0B]' : 'text-[#EF4444]'
                      }`}>
                        {doc.riskLevel === '正常' ? '完全合规' : '存在差异疑点'}
                      </span>
                    </div>
                  </div>

                  <div className="mt-2 text-[11px] text-[#c4c5d5] bg-[#0b1326] p-2 rounded-lg border border-[#444653]/20 flex items-start gap-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-[#4cd7f6] shrink-0 mt-0.5" />
                    <span>{doc.riskDescription}</span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          /* 标签页 2: 语义检索查账 */
          <div className="space-y-3">
            <div className="relative">
              <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-[#8e909f]" />
              <input
                type="text"
                placeholder="输入关键字检索 RAG 底账（例如：'智慧幕墙'、'数电票 310026'、'跨境租金'）..."
                value={ragSearchQuery}
                onChange={(e) => setRagSearchQuery(e.target.value)}
                className="w-full bg-[#0b1326] border border-[#4cd7f6]/40 rounded-xl pl-10 pr-4 py-2.5 text-[#dae2fd] text-[13px] focus:outline-none focus:ring-1 focus:ring-[#4cd7f6]"
              />
            </div>
            <div className="p-3 bg-[#0b1326]/60 rounded-xl border border-[#444653]/30 text-[12px] text-[#8e909f] space-y-1.5">
              <div className="flex items-center gap-1.5 text-[#4cd7f6] font-semibold">
                <Sparkles className="w-3.5 h-3.5" />
                <span>RAG 知识湖检索模式说明</span>
              </div>
              <p>系统将自动检索金税四期电子底账库、招商银行/建设银行银企直联流水、ERP 合同台账及现场磅单系统，自动生成结构化四流校验矩阵与涉税风险报告。</p>
            </div>
          </div>
        )}

        {/* 选中的 RAG 抽取底稿摘要 */}
        <div className="bg-[#171f33] rounded-xl p-3.5 border border-[#444653]/40 space-y-2">
          <div className="flex justify-between items-center text-[12px]">
            <div className="flex items-center gap-1.5 text-[#b8c4ff] font-semibold">
              <FileText className="w-4 h-4 text-[#4cd7f6]" />
              <span>RAG 多模态溯源凭证:</span>
              <code className="text-[#4cd7f6] bg-[#0b1326] px-2 py-0.5 rounded font-mono-num text-[11px]">
                {currentDoc.docId}
              </code>
            </div>
            <span className="text-[11px] text-[#8e909f]">凭证代码: {currentDoc.invoiceCode}</span>
          </div>

          <div className="flex flex-wrap gap-2 pt-1">
            {currentDoc.docHighlights.map((item, idx) => (
              <span key={idx} className="text-[11px] bg-[#0b1326] text-[#dae2fd] px-2.5 py-1 rounded-md border border-[#444653]/40 flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3 text-[#10B981]" />
                {item}
              </span>
            ))}
          </div>
        </div>

        {/* 底部动作栏 */}
        <div className="flex items-center justify-between pt-2 border-t border-[#444653]/30">
          <div className="text-[11px] text-[#8e909f] flex items-center gap-1.5">
            <ShieldCheck className="w-4 h-4 text-[#10B981]" />
            <span>同步后自动生成防篡改数字底稿与审计追踪日志</span>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-[#222a3d] hover:bg-[#2d3449] text-[#dae2fd] rounded-xl cursor-pointer"
            >
              取消
            </button>
            <button
              type="button"
              disabled={isSearching}
              onClick={handleSyncToLedger}
              className="flex items-center gap-2 px-5 py-2 bg-[#03b5d3] hover:bg-[#03b5d3]/90 text-[#001f26] font-bold rounded-xl shadow-lg transition-all cursor-pointer disabled:opacity-50"
            >
              {isSearching ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" />
                  <span>RAG 智能解析归集中...</span>
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4" />
                  <span>确认同步并启动智能查账</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
