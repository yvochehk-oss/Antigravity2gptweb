import { useState } from 'react';
import { 
  Building, 
  Wallet, 
  CreditCard, 
  TrendingUp, 
  TrendingDown, 
  Scale, 
  AlertOctagon, 
  ShieldAlert, 
  ArrowUpRight, 
  Download, 
  CheckCircle2, 
  Layers,
  ChevronRight,
  ShieldCheck,
  AlertTriangle,
  ReceiptText
} from 'lucide-react';
import { ProjectItem, SystemSettings } from '../types';

interface DashboardViewProps {
  projects: ProjectItem[];
  onSelectProject: (projId: string) => void;
  onOpenRiskCenter: () => void;
  onOpenExportModal: () => void;
  settings?: SystemSettings;
}

export function DashboardView({ 
  projects, 
  onSelectProject, 
  onOpenRiskCenter,
  onOpenExportModal,
  settings
}: DashboardViewProps) {
  const [trendPeriod, setTrendPeriod] = useState<'month' | 'quarter'>('month');
  const [hoveredTrendIndex, setHoveredTrendIndex] = useState<number | null>(null);

  const budgetThreshold = settings?.budgetOverrunStopPayThreshold ?? 5;
  const crossRegionThreshold = settings?.crossRegionTaxThreshold ?? 5;

  // 业财综合趋势图数据 (营收、成本、毛利、税金四大维度闭环，与 KPI 卡片 100% 呼应)
  const [visibleSeries, setVisibleSeries] = useState({
    revenue: true,
    cost: true,
    profit: true,
    tax: true,
  });

  const monthlyData = [
    { label: '1月', revenue: 6.8, cost: 4.9, profit: 1.9, tax: 0.36, budget: 1.8 },
    { label: '2月', revenue: 6.2, cost: 4.5, profit: 1.7, tax: 0.33, budget: 1.8 },
    { label: '3月', revenue: 7.4, cost: 5.4, profit: 2.0, tax: 0.39, budget: 1.9 },
    { label: '4月', revenue: 7.9, cost: 5.7, profit: 2.2, tax: 0.42, budget: 1.9 },
    { label: '5月', revenue: 8.6, cost: 6.2, profit: 2.4, tax: 0.46, budget: 2.0 },
    { label: '6月', revenue: 8.1, cost: 5.9, profit: 2.2, tax: 0.43, budget: 2.0 },
    { label: '7月', revenue: 8.5, cost: 6.1, profit: 2.4, tax: 0.45, budget: 2.1 },
    { label: '8月', revenue: 8.7, cost: 6.3, profit: 2.4, tax: 0.47, budget: 2.1 },
    { label: '9月', revenue: 9.0, cost: 6.5, profit: 2.5, tax: 0.48, budget: 2.2 },
    { label: '10月', revenue: 8.3, cost: 6.0, profit: 2.3, tax: 0.44, budget: 2.2 },
    { label: '11月', revenue: 4.5, cost: 3.7, profit: 0.8, tax: 0.27, budget: 2.3 },
  ];

  const quarterlyData = [
    { label: '一季度', revenue: 20.4, cost: 14.8, profit: 5.6, tax: 1.08, budget: 5.5 },
    { label: '二季度', revenue: 24.6, cost: 17.8, profit: 6.8, tax: 1.31, budget: 5.9 },
    { label: '三季度', revenue: 26.2, cost: 18.9, profit: 7.3, tax: 1.40, budget: 6.4 },
    { label: '四季度(预)', revenue: 12.8, cost: 9.7, profit: 3.1, tax: 0.71, budget: 4.5 },
  ];

  const currentTrendData = trendPeriod === 'month' ? monthlyData : quarterlyData;
  const maxScale = trendPeriod === 'month' ? 10.0 : 30.0;

  // 动态构建精细 SVG 路径 (800x200 高精度坐标系，保证极细硬边线条与完整不截断显示)
  const getLinePath = (key: 'revenue' | 'cost' | 'profit' | 'tax' | 'budget') => {
    return 'M' + currentTrendData.map((d, i) => {
      const x = 15 + (i / (currentTrendData.length - 1)) * 770;
      const y = 175 - (d[key] / maxScale) * 155;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' L');
  };

  const getAreaPath = (key: 'revenue' | 'profit') => {
    const points = currentTrendData.map((d, i) => {
      const x = 15 + (i / (currentTrendData.length - 1)) * 770;
      const y = 175 - (d[key] / maxScale) * 155;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return `M15,175 L15,${points[0].split(',')[1]} L` + points.join(' L') + ` L785,175 Z`;
  };

  const hoveredItem = hoveredTrendIndex !== null ? currentTrendData[hoveredTrendIndex] : null;

  return (
    <div className="space-y-6">
      {/* 页面顶栏标题与快捷动作 */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-[28px] font-bold text-[#dae2fd] tracking-tight">锐宝财税智控全景驾驶舱</h2>
          <p className="text-[14px] text-[#c4c5d5] mt-1">
            实时穿透监管全量工程标段的资金流速、全周期涉税台账、成本概算执行与四流合一合规状态。
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[12px] font-mono-num text-[#4cd7f6] flex items-center gap-2 bg-[#03b5d3]/10 px-3 py-1.5 rounded-lg border border-[#4cd7f6]/20">
            <span className="w-2 h-2 rounded-full bg-[#4cd7f6] animate-ping"></span>
            实时监管中
          </span>
          <button 
            onClick={onOpenExportModal}
            className="bg-[#1e40af] hover:bg-[#1e40af]/80 text-[#dde1ff] px-4 py-2 rounded-lg text-[13px] font-semibold border-t border-[#4cd7f6]/40 transition-colors flex items-center gap-2 cursor-pointer shadow-md"
          >
            <Download className="w-4 h-4 text-[#4cd7f6]" />
            <span>导出综合简报</span>
          </button>
        </div>
      </div>

      {/* 6大关键绩效指标卡片 (分两排展示，每排3个) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* 指标 1: 全集团在建工程与重点示范标段 (方案 B) */}
        <div 
          onClick={() => onSelectProject('proj-02')}
          className="glass-panel rounded-xl p-5 glow-cyan relative overflow-hidden group hover:bg-[#222a3d]/70 transition-all flex flex-col justify-between min-h-[125px] cursor-pointer"
        >
          <div className="flex justify-between items-center mb-1.5">
            <p className="text-[13px] font-bold text-[#c4c5d5] tracking-wider">全集团在建工程</p>
            <span className="text-[11px] text-[#4cd7f6] font-semibold bg-[#03b5d3]/15 px-2 py-0.5 rounded border border-[#4cd7f6]/30 font-mono-num">
              全省覆盖 · 动态监管
            </span>
          </div>
          <div className="flex items-baseline gap-2.5">
            <p className="text-[28px] font-bold font-mono-num text-[#dae2fd]">
              142 <span className="text-[15px] font-normal text-[#8e909f]">个标段</span>
            </p>
            <span className="text-[12px] text-[#10B981] font-mono-num font-semibold flex items-center gap-0.5">
              <TrendingUp className="w-3.5 h-3.5" />
              <span>+12% 同比增长</span>
            </span>
          </div>
          <div className="mt-2 pt-2 border-t border-[#444653]/30 flex items-center justify-between text-[11px] text-[#8e909f] group-hover:text-[#4cd7f6] transition-colors">
            <span>重点穿透监管 <strong className="text-[#dae2fd]">5 大示范工程</strong> (覆盖 26 家关联企业)</span>
            <ChevronRight className="w-3.5 h-3.5 flex-shrink-0 text-[#4cd7f6] group-hover:translate-x-0.5 transition-transform" />
          </div>
        </div>

        {/* 指标 2 */}
        <div className="glass-panel rounded-xl p-5 glow-cyan relative overflow-hidden group hover:bg-[#222a3d]/70 transition-all flex flex-col justify-between min-h-[120px]">
          <div className="flex justify-between items-center mb-2">
            <p className="text-[13px] font-bold text-[#c4c5d5] tracking-wider">总营收 (本财年)</p>
            <span className="text-[11px] text-[#8e909f] font-mono-num">本年累计</span>
          </div>
          <p className="text-[28px] font-bold font-mono-num text-[#dae2fd]">¥ 84.0 <span className="text-[16px] font-normal text-[#8e909f]">亿</span></p>
          <div className="mt-2 flex items-center gap-1.5 text-[#10B981] text-[12px] font-medium font-mono-num">
            <TrendingUp className="w-4 h-4 flex-shrink-0" />
            <span>+5.4% 稳健增长</span>
          </div>
        </div>

        {/* 指标 3 */}
        <div className="glass-panel rounded-xl p-5 glow-cyan relative overflow-hidden group hover:bg-[#222a3d]/70 transition-all flex flex-col justify-between min-h-[120px]">
          <div className="flex justify-between items-center mb-2">
            <p className="text-[13px] font-bold text-[#c4c5d5] tracking-wider">发生成本</p>
            <span className="text-[11px] text-[#ffb59a] font-mono-num">止付线: +{budgetThreshold}%</span>
          </div>
          <p className="text-[28px] font-bold font-mono-num text-[#dae2fd]">¥ 61.2 <span className="text-[16px] font-normal text-[#8e909f]">亿</span></p>
          <div className="mt-2 flex items-center gap-1.5 text-[#F59E0B] text-[12px] font-medium font-mono-num">
            <TrendingUp className="w-4 h-4 flex-shrink-0" />
            <span>+2.1% 浮动 (未达+{budgetThreshold}%止付线)</span>
          </div>
        </div>

        {/* 指标 4 */}
        <div className="glass-panel rounded-xl p-5 glow-cyan relative overflow-hidden group hover:bg-[#222a3d]/70 transition-all flex flex-col justify-between min-h-[120px]">
          <div className="flex justify-between items-center mb-2">
            <p className="text-[13px] font-bold text-[#c4c5d5] tracking-wider">毛利 / 利润率</p>
            <span className="text-[#4cd7f6] text-[11px] font-semibold bg-[#03b5d3]/15 px-2 py-0.5 rounded border border-[#4cd7f6]/30 font-mono-num">
              毛利率 27.3%
            </span>
          </div>
          <p className="text-[28px] font-bold font-mono-num text-[#dae2fd]">¥ 22.8 <span className="text-[16px] font-normal text-[#8e909f]">亿</span></p>
          <div className="mt-2 flex items-center gap-1.5 text-[#4cd7f6] text-[12px] font-medium font-mono-num">
            <span>综合净利贡献率稳居优良区间</span>
          </div>
        </div>

        {/* 指标 5 */}
        <div className="glass-panel rounded-xl p-5 glow-cyan relative overflow-hidden group hover:bg-[#222a3d]/70 transition-all flex flex-col justify-between min-h-[120px]">
          <div className="flex justify-between items-center mb-2">
            <p className="text-[13px] font-bold text-[#c4c5d5] tracking-wider">当期预估应纳增值税</p>
            <span className="text-[11px] text-[#8e909f] font-mono-num">月度汇算</span>
          </div>
          <p className="text-[28px] font-bold font-mono-num text-[#dae2fd]">¥ 4.50 <span className="text-[16px] font-normal text-[#8e909f]">亿</span></p>
          <div className="mt-2 flex items-center gap-1.5 text-[#c4c5d5] text-[12px] font-mono-num">
            <span>四大主体进销项综合轧差抵扣后</span>
          </div>
        </div>

        {/* 指标 6: 高危异常预警 */}
        <div 
          onClick={onOpenRiskCenter}
          className="glass-panel rounded-xl p-5 glow-red relative overflow-hidden group hover:bg-[#EF4444]/15 transition-all cursor-pointer border border-[#EF4444]/50 flex flex-col justify-between min-h-[120px]"
        >
          <div className="flex justify-between items-center mb-2">
            <p className="text-[13px] font-bold text-[#ffb4ab] flex items-center gap-1.5 tracking-wider">
              <span className="w-2 h-2 rounded-full bg-[#EF4444] animate-ping flex-shrink-0"></span>
              未闭环高危风险
            </p>
            <span className="text-[11px] text-[#EF4444] font-semibold bg-[#EF4444]/15 px-2 py-0.5 rounded border border-[#EF4444]/30">
              待穿透处置
            </span>
          </div>
          <p className="text-[28px] font-bold font-mono-num text-[#EF4444]">14 <span className="text-[16px] font-normal text-[#ffb4ab]">项</span></p>
          <div className="mt-2 flex items-center gap-1 text-[#ffb4ab] text-[12px] font-semibold underline underline-offset-2">
            <span>立即进入风控中心介入审查</span>
            <ChevronRight className="w-4 h-4" />
          </div>
        </div>
      </div>

      {/* 数据可视化模块群 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 图表 1: 业财经营大盘综合趋势 (营收 / 成本 / 利润 / 税金) (占2列) */}
        <div className="lg:col-span-2 glass-panel rounded-xl flex flex-col min-h-[420px] overflow-hidden">
          <div className="p-3.5 sm:p-4 border-b border-[#444653]/30 bg-[#171f33]/60 flex flex-wrap justify-between items-center gap-2">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-[#4cd7f6] animate-pulse"></span>
              <h3 className="text-[15px] font-bold text-[#dae2fd]">业财经营大盘综合趋势 (营收 / 成本 / 利润 / 增值税)</h3>
            </div>

            {/* 周期切换 */}
            <div className="flex gap-1.5 bg-[#131b2e] p-1 rounded-lg border border-[#444653]/40 text-[11px]">
              <button
                onClick={() => setTrendPeriod('month')}
                className={`px-3 py-1 rounded font-medium transition-all cursor-pointer ${
                  trendPeriod === 'month' ? 'bg-[#2d3449] text-[#4cd7f6] font-bold' : 'text-[#c4c5d5] hover:text-[#dae2fd]'
                }`}
              >
                按月明细
              </button>
              <button
                onClick={() => setTrendPeriod('quarter')}
                className={`px-3 py-1 rounded font-medium transition-all cursor-pointer ${
                  trendPeriod === 'quarter' ? 'bg-[#2d3449] text-[#4cd7f6] font-bold' : 'text-[#c4c5d5] hover:text-[#dae2fd]'
                }`}
              >
                按季统筹
              </button>
            </div>
          </div>

          {/* 图例切换与线型说明栏 */}
          <div className="px-4 py-2 bg-[#131b2e]/40 border-b border-[#444653]/20 flex flex-wrap items-center justify-between gap-2 text-[11px]">
            <div className="flex flex-wrap items-center gap-2.5">
              <button
                onClick={() => setVisibleSeries(s => ({ ...s, revenue: !s.revenue }))}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg cursor-pointer transition-all ${
                  visibleSeries.revenue ? 'bg-[#10B981]/15 text-[#10B981] font-semibold border border-[#10B981]/40' : 'text-[#8e909f] line-through'
                }`}
              >
                <span className="w-4 h-0.5 bg-[#10B981] inline-block"></span>
                <span>实线：总营收 (累计¥84.0亿)</span>
              </button>

              <button
                onClick={() => setVisibleSeries(s => ({ ...s, cost: !s.cost }))}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg cursor-pointer transition-all ${
                  visibleSeries.cost ? 'bg-[#F59E0B]/15 text-[#ffa583] font-semibold border border-[#F59E0B]/40' : 'text-[#8e909f] line-through'
                }`}
              >
                <span className="w-4 border-t-2 border-dashed border-[#F59E0B] inline-block"></span>
                <span>点划线：发生成本 (累计¥61.2亿)</span>
              </button>

              <button
                onClick={() => setVisibleSeries(s => ({ ...s, profit: !s.profit }))}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg cursor-pointer transition-all ${
                  visibleSeries.profit ? 'bg-[#03b5d3]/15 text-[#4cd7f6] font-semibold border border-[#4cd7f6]/40' : 'text-[#8e909f] line-through'
                }`}
              >
                <span className="w-4 border-t-2 border-dotted border-[#4cd7f6] inline-block"></span>
                <span>虚线：实时毛利 (累计¥22.8亿)</span>
              </button>

              <button
                onClick={() => setVisibleSeries(s => ({ ...s, tax: !s.tax }))}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg cursor-pointer transition-all ${
                  visibleSeries.tax ? 'bg-[#b8c4ff]/15 text-[#b8c4ff] font-semibold border border-[#b8c4ff]/40' : 'text-[#8e909f] line-through'
                }`}
              >
                <span className="w-4 border-t-2 border-dotted border-[#b8c4ff] inline-block"></span>
                <span>细点线：预估增值税 (累计¥4.50亿)</span>
              </button>
            </div>

            <div className="flex items-center gap-1.5 text-[#8e909f] text-[10px]">
              <span className="w-4 border-t border-dashed border-[#8e909f]"></span>
              <span>预算基线</span>
            </div>
          </div>

          <div className="flex-1 p-4 relative chart-grid flex flex-col justify-between">
            {/* 图表主区域：左侧独立Y轴 + 右侧独立SVG画布与X轴 */}
            <div className="flex-1 flex gap-3 relative min-h-[240px]">
              {/* 独立 Y 轴刻度 (5个刻度均匀分布，从顶部 10.0 亿 到 底部 0 元) */}
              <div className="w-14 flex flex-col justify-between text-right text-[10px] font-mono-num text-[#8e909f] pb-7 pt-1 select-none flex-shrink-0">
                <span>¥ {trendPeriod === 'month' ? '10.0' : '30.0'} 亿</span>
                <span>¥ {trendPeriod === 'month' ? '7.5' : '22.5'} 亿</span>
                <span>¥ {trendPeriod === 'month' ? '5.0' : '15.0'} 亿</span>
                <span>¥ {trendPeriod === 'month' ? '2.5' : '7.5'} 亿</span>
                <span>0 元</span>
              </div>

              {/* 右侧主绘图画布 (viewBox 0 0 800 200，保证极细硬边线条与完整不截断显示) */}
              <div className="flex-1 relative flex flex-col justify-between">
                {/* 背景刻度网格线 */}
                <div className="absolute inset-0 pb-7 pt-1 flex flex-col justify-between pointer-events-none">
                  <div className="border-b border-[#444653]/20 w-full"></div>
                  <div className="border-b border-[#444653]/20 w-full"></div>
                  <div className="border-b border-[#444653]/20 w-full"></div>
                  <div className="border-b border-[#444653]/20 w-full"></div>
                  <div className="border-b border-[#444653]/40 w-full"></div>
                </div>

                {/* 预算警戒虚线 */}
                <div className="absolute top-[34%] left-0 right-0 border-t-2 border-dashed border-[#8e909f]/30 flex justify-end pr-2 pointer-events-none">
                  <span className="text-[9px] text-[#8e909f] bg-[#171f33]/90 px-1 rounded -translate-y-2">预算基线</span>
                </div>

                {/* SVG 多曲线图层 (高分辨率坐标系 800x200) */}
                <svg className="w-full h-[calc(100%-28px)]" viewBox="0 0 800 200" preserveAspectRatio="none">
                  <defs>
                    <linearGradient id="profitGrad" x1="0%" y1="0%" x2="0%" y2="100%">
                      <stop offset="0%" stopColor="#4cd7f6" stopOpacity="0.25" />
                      <stop offset="100%" stopColor="#1e40af" stopOpacity="0.0" />
                    </linearGradient>
                    <linearGradient id="revGrad" x1="0%" y1="0%" x2="0%" y2="100%">
                      <stop offset="0%" stopColor="#10B981" stopOpacity="0.12" />
                      <stop offset="100%" stopColor="#10B981" stopOpacity="0.0" />
                    </linearGradient>
                  </defs>

                  {/* 1. 总营收渐变与折线 (实线, 绿色, strokeWidth=1.8) */}
                  {visibleSeries.revenue && (
                    <>
                      <path d={getAreaPath('revenue')} fill="url(#revGrad)" />
                      <path d={getLinePath('revenue')} fill="none" stroke="#10B981" strokeWidth="1.8" strokeLinecap="round" />
                    </>
                  )}

                  {/* 2. 发生成本折线 (点划线 dash-dot, 琥珀橙, strokeWidth=1.8) */}
                  {visibleSeries.cost && (
                    <path d={getLinePath('cost')} fill="none" stroke="#F59E0B" strokeWidth="1.8" strokeDasharray="8,4,2,4" strokeLinecap="round" />
                  )}

                  {/* 3. 实时毛利渐变与折线 (虚线 dashed, 科技青蓝, strokeWidth=1.8) */}
                  {visibleSeries.profit && (
                    <>
                      <path d={getAreaPath('profit')} fill="url(#profitGrad)" />
                      <path d={getLinePath('profit')} fill="none" stroke="#4cd7f6" strokeWidth="1.8" strokeDasharray="6,4" strokeLinecap="round" />
                    </>
                  )}

                  {/* 4. 预估应纳增值税折线 (细点线 dotted, 紫色, strokeWidth=1.6) */}
                  {visibleSeries.tax && (
                    <path d={getLinePath('tax')} fill="none" stroke="#b8c4ff" strokeWidth="1.6" strokeDasharray="2,3" strokeLinecap="round" />
                  )}

                  {/* 悬停高亮点 */}
                  {hoveredTrendIndex !== null && (
                    <>
                      {visibleSeries.revenue && (
                        <circle
                          cx={15 + (hoveredTrendIndex / (currentTrendData.length - 1)) * 770}
                          cy={175 - (currentTrendData[hoveredTrendIndex].revenue / maxScale) * 155}
                          r="4"
                          fill="#10B981"
                          stroke="#ffffff"
                          strokeWidth="1.5"
                        />
                      )}
                      {visibleSeries.cost && (
                        <circle
                          cx={15 + (hoveredTrendIndex / (currentTrendData.length - 1)) * 770}
                          cy={175 - (currentTrendData[hoveredTrendIndex].cost / maxScale) * 155}
                          r="4"
                          fill="#F59E0B"
                          stroke="#ffffff"
                          strokeWidth="1.5"
                        />
                      )}
                      {visibleSeries.profit && (
                        <circle
                          cx={15 + (hoveredTrendIndex / (currentTrendData.length - 1)) * 770}
                          cy={175 - (currentTrendData[hoveredTrendIndex].profit / maxScale) * 155}
                          r="4"
                          fill="#4cd7f6"
                          stroke="#ffffff"
                          strokeWidth="1.5"
                          className="animate-pulse"
                        />
                      )}
                    </>
                  )}
                </svg>

                {/* 悬停 Tooltip 浮层 */}
                {hoveredItem && (
                  <div 
                    className="absolute top-2 z-20 bg-[#0b1326]/95 border border-[#4cd7f6]/40 p-2.5 rounded-xl shadow-2xl backdrop-blur-md text-[11px] font-mono-num space-y-1 pointer-events-none transition-all"
                    style={{
                      left: `${Math.min(Math.max((hoveredTrendIndex! / (currentTrendData.length - 1)) * 80, 5), 65)}%`
                    }}
                  >
                    <div className="font-bold text-[#dae2fd] border-b border-[#444653]/30 pb-1 flex justify-between gap-4">
                      <span>{hoveredItem.label} 业财汇总</span>
                      <span className="text-[#4cd7f6]">毛利率 {((hoveredItem.profit / hoveredItem.revenue) * 100).toFixed(1)}%</span>
                    </div>
                    <div className="flex justify-between gap-4 text-[#10B981]">
                      <span>总营收 (实线):</span>
                      <span className="font-bold">¥ {hoveredItem.revenue.toFixed(2)} 亿元</span>
                    </div>
                    <div className="flex justify-between gap-4 text-[#ffa583]">
                      <span>发生成本 (点划线):</span>
                      <span className="font-bold">¥ {hoveredItem.cost.toFixed(2)} 亿元</span>
                    </div>
                    <div className="flex justify-between gap-4 text-[#4cd7f6]">
                      <span>实时毛利 (虚线):</span>
                      <span className="font-bold">¥ {hoveredItem.profit.toFixed(2)} 亿元</span>
                    </div>
                    <div className="flex justify-between gap-4 text-[#b8c4ff]">
                      <span>应纳增值税 (点线):</span>
                      <span className="font-bold">¥ {hoveredItem.tax.toFixed(2)} 亿元</span>
                    </div>
                  </div>
                )}

                {/* X 轴月份标签 (右侧独立分布) */}
                <div className="flex justify-between text-[11px] font-mono-num text-[#c4c5d5] pt-1 select-none">
                  {currentTrendData.map((item, idx) => (
                    <span 
                      key={idx}
                      onMouseEnter={() => setHoveredTrendIndex(idx)}
                      onMouseLeave={() => setHoveredTrendIndex(null)}
                      className={`cursor-pointer transition-all px-1 py-0.5 rounded ${
                        hoveredTrendIndex === idx ? 'text-[#4cd7f6] bg-[#03b5d3]/15 font-bold scale-110' : 'hover:text-[#dae2fd]'
                      }`}
                    >
                      {item.label}
                    </span>
                  ))}
                </div>

                {/* 底部交互提示行 */}
                <div className="flex items-center justify-center gap-1.5 pt-2 text-[11px] text-[#8e909f] border-t border-[#444653]/20 mt-2 select-none">
                  <span className="text-[#4cd7f6]">💡 提示：</span>
                  <span>把鼠标放到{trendPeriod === 'month' ? '月份' : '季度'}上可以查看当期收支与税金详情</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* 图表 2: 六维业财合规健康雷达 */}
        <div className="glass-panel rounded-xl flex flex-col min-h-[420px] justify-between overflow-hidden">
          <div className="p-3.5 sm:p-4 border-b border-[#444653]/30 bg-[#171f33]/60 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-[#10B981] animate-ping"></span>
              <h3 className="text-[15px] font-bold text-[#dae2fd]">六维业财合规健康雷达</h3>
            </div>
            <span className="text-[11px] font-bold text-[#10B981] bg-[#10B981]/15 px-2.5 py-0.5 rounded-full border border-[#10B981]/30 flex items-center gap-1">
              <span>综合评分</span>
              <strong className="font-mono-num text-[12px]">92.5</strong>
              <span>分</span>
            </span>
          </div>

          <div className="flex-1 p-2 relative flex flex-col items-center justify-center min-h-[290px]">
            {/* 300x300 高保真六维雷达图容器 */}
            <div className="w-[290px] h-[280px] relative flex items-center justify-center select-none">
              <svg className="w-full h-full" viewBox="0 0 300 290">
                <defs>
                  {/* 雷达多边形内部渐变 */}
                  <linearGradient id="radarGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#4cd7f6" stopOpacity="0.45" />
                    <stop offset="50%" stopColor="#1e40af" stopOpacity="0.25" />
                    <stop offset="100%" stopColor="#03b5d3" stopOpacity="0.10" />
                  </linearGradient>
                  {/* 滤镜发光 */}
                  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
                    <feGaussianBlur stdDeviation="3" result="blur" />
                    <feComposite in="SourceGraphic" in2="blur" operator="over" />
                  </filter>
                </defs>

                {/* 1. 六角形同心网格 (100%, 75%, 50%, 25%) */}
                <polygon points="150,75 215,112.5 215,187.5 150,225 85,187.5 85,112.5" fill="none" stroke="#444653" strokeWidth="1" strokeOpacity="0.4" />
                <polygon points="150,93.8 198.8,121.9 198.8,178.1 150,206.3 101.2,178.1 101.2,121.9" fill="none" stroke="#444653" strokeWidth="1" strokeOpacity="0.3" />
                <polygon points="150,112.5 182.5,131.3 182.5,168.8 150,187.5 117.5,168.8 117.5,131.3" fill="none" stroke="#444653" strokeWidth="1" strokeOpacity="0.2" />
                <polygon points="150,131.3 166.3,140.6 166.3,159.4 150,168.8 133.7,159.4 133.7,140.6" fill="none" stroke="#444653" strokeWidth="1" strokeOpacity="0.15" />

                {/* 2. 中心十字与放射轴线 */}
                <line x1="150" y1="75" x2="150" y2="225" stroke="#444653" strokeWidth="1" strokeOpacity="0.35" strokeDasharray="3,3" />
                <line x1="85" y1="112.5" x2="215" y2="187.5" stroke="#444653" strokeWidth="1" strokeOpacity="0.35" strokeDasharray="3,3" />
                <line x1="85" y1="187.5" x2="215" y2="112.5" stroke="#444653" strokeWidth="1" strokeOpacity="0.35" strokeDasharray="3,3" />

                {/* 3. 动态数据多边形 (含发光轮廓) */}
                <polygon
                  points="150,78 211,114.7 208.5,183.7 150,216 94.8,181.9 88.3,114.4"
                  fill="url(#radarGrad)"
                  stroke="#4cd7f6"
                  strokeWidth="2"
                  filter="url(#glow)"
                />

                {/* 4. 顶点发光徽标 */}
                {/* 顶: 税务合规 96 */}
                <circle cx="150" cy="78" r="4" fill="#4cd7f6" stroke="#ffffff" strokeWidth="1.5" />
                {/* 右上: 四流一致 94 */}
                <circle cx="211" cy="114.7" r="4" fill="#10B981" stroke="#ffffff" strokeWidth="1.5" />
                {/* 右下: 合同履约 90 */}
                <circle cx="208.5" cy="183.7" r="4" fill="#b8c4ff" stroke="#ffffff" strokeWidth="1.5" />
                {/* 底: 成本受控 88 */}
                <circle cx="150" cy="216" r="4" fill="#ffa583" stroke="#ffffff" strokeWidth="1.5" />
                {/* 左下: 风控闭环 85 */}
                <circle cx="94.8" cy="181.9" r="4" fill="#F59E0B" stroke="#ffffff" strokeWidth="1.5" />
                {/* 左上: 资金安全 95 */}
                <circle cx="88.3" cy="114.4" r="4" fill="#4cd7f6" stroke="#ffffff" strokeWidth="1.5" />
              </svg>

              {/* 外围独立绝对定位标签胶囊 (绝不与雷达点重叠) */}
              {/* 1. 顶部：税务合规 */}
              <div className="absolute top-1 left-1/2 -translate-x-1/2 flex items-center gap-1 bg-[#131b2e]/90 px-2 py-0.5 rounded-full border border-[#4cd7f6]/40 text-[10px] text-[#4cd7f6] shadow-md">
                <span>税务合规</span>
                <strong className="text-white font-mono-num">96</strong>
              </div>

              {/* 2. 右上：四流一致 */}
              <div className="absolute top-[28%] -right-1 flex items-center gap-1 bg-[#131b2e]/90 px-2 py-0.5 rounded-full border border-[#10B981]/40 text-[10px] text-[#10B981] shadow-md">
                <span>四流一致</span>
                <strong className="text-white font-mono-num">94</strong>
              </div>

              {/* 3. 右下：合同履约 */}
              <div className="absolute bottom-[24%] -right-1 flex items-center gap-1 bg-[#131b2e]/90 px-2 py-0.5 rounded-full border border-[#b8c4ff]/40 text-[10px] text-[#b8c4ff] shadow-md">
                <span>合同履约</span>
                <strong className="text-white font-mono-num">90</strong>
              </div>

              {/* 4. 底部：成本受控 */}
              <div className="absolute bottom-1 left-1/2 -translate-x-1/2 flex items-center gap-1 bg-[#131b2e]/90 px-2 py-0.5 rounded-full border border-[#ffa583]/40 text-[10px] text-[#ffa583] shadow-md">
                <span>成本受控</span>
                <strong className="text-white font-mono-num">88</strong>
              </div>

              {/* 5. 左下：风控闭环 */}
              <div className="absolute bottom-[24%] -left-1 flex items-center gap-1 bg-[#131b2e]/90 px-2 py-0.5 rounded-full border border-[#F59E0B]/40 text-[10px] text-[#F59E0B] shadow-md">
                <span>风控闭环</span>
                <strong className="text-white font-mono-num">85</strong>
              </div>

              {/* 6. 左上：资金安全 */}
              <div className="absolute top-[28%] -left-1 flex items-center gap-1 bg-[#131b2e]/90 px-2 py-0.5 rounded-full border border-[#4cd7f6]/40 text-[10px] text-[#4cd7f6] shadow-md">
                <span>资金安全</span>
                <strong className="text-white font-mono-num">95</strong>
              </div>
            </div>
          </div>

          {/* 底部评级与健康总结栏 */}
          <div className="px-4 py-2.5 bg-[#131b2e]/60 border-t border-[#444653]/30 flex items-center justify-between text-[11px]">
            <div className="flex items-center gap-1.5">
              <span className="text-[#8e909f]">综合等级:</span>
              <span className="font-bold text-[#4cd7f6] bg-[#03b5d3]/15 px-2 py-0.5 rounded border border-[#4cd7f6]/30">甲级 · A 级优良</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[#8e909f]">短板预警:</span>
              <span className="font-bold text-[#F59E0B] bg-[#F59E0B]/15 px-2 py-0.5 rounded border border-[#F59E0B]/30">风控闭环 (85)</span>
            </div>
          </div>
        </div>
      </div>

      {/* 跨实体增值税基准对比 */}
      <div className="glass-panel rounded-xl p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-[#444653]/30">
          <div>
            <h3 className="text-[16px] font-bold text-[#dae2fd]">跨实体增值税基准对比</h3>
            <p className="text-[12px] text-[#c4c5d5] mt-0.5">直观穿透各大建设主体的进项抵扣、销项开票与当期净应纳税额</p>
          </div>
          <div className="flex items-center gap-4 text-[12px] font-medium">
            <span className="flex items-center gap-1.5 text-[#c4c5d5]">
              <span className="w-3 h-3 bg-[#2d3449] border border-[#8e909f] rounded-sm"></span> 进项税额
            </span>
            <span className="flex items-center gap-1.5 text-[#b8c4ff]">
              <span className="w-3 h-3 bg-[#1e40af]/60 border border-[#b8c4ff] rounded-sm"></span> 销项税额
            </span>
            <span className="flex items-center gap-1.5 text-[#4cd7f6]">
              <span className="w-3 h-3 bg-[#03b5d3] border border-[#4cd7f6] rounded-sm shadow-[0_0_8px_#4cd7f6]"></span> 净应缴税额
            </span>
          </div>
        </div>

        <div className="mt-6 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {/* 建筑施工企业 */}
          <div className="bg-[#131b2e]/80 rounded-xl p-4 border border-[#444653]/30 hover:border-[#4cd7f6]/40 transition-all">
            <div className="flex justify-between items-center mb-3">
              <span className="text-[13px] font-bold text-[#dae2fd]">四川锐宝建设工程（建筑施工）</span>
              <span className="text-[11px] text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded">正常</span>
            </div>
            <div className="space-y-2 text-[12px] font-mono-num">
              <div className="flex justify-between"><span className="text-[#8e909f]">进项税额:</span><span className="text-[#dae2fd]">¥ 12,000 万元</span></div>
              <div className="flex justify-between"><span className="text-[#8e909f]">销项税额:</span><span className="text-[#b8c4ff]">¥ 18,000 万元</span></div>
              <div className="flex justify-between pt-1 border-t border-[#444653]/20 font-bold"><span className="text-[#4cd7f6]">净应缴额:</span><span className="text-[#4cd7f6]">¥ 6,000 万元</span></div>
            </div>
          </div>

          {/* 商贸物资公司 */}
          <div className="bg-[#131b2e]/80 rounded-xl p-4 border border-[#444653]/30 hover:border-[#4cd7f6]/40 transition-all">
            <div className="flex justify-between items-center mb-3">
              <span className="text-[13px] font-bold text-[#dae2fd]">四川乾润和贸易（商贸物资）</span>
              <span className="text-[11px] text-[#F59E0B] bg-[#F59E0B]/15 px-2 py-0.5 rounded">进项审核中</span>
            </div>
            <div className="space-y-2 text-[12px] font-mono-num">
              <div className="flex justify-between"><span className="text-[#8e909f]">进项税额:</span><span className="text-[#dae2fd]">¥ 8,500 万元</span></div>
              <div className="flex justify-between"><span className="text-[#8e909f]">销项税额:</span><span className="text-[#b8c4ff]">¥ 11,200 万元</span></div>
              <div className="flex justify-between pt-1 border-t border-[#444653]/20 font-bold"><span className="text-[#4cd7f6]">净应缴额:</span><span className="text-[#4cd7f6]">¥ 2,700 万元</span></div>
            </div>
          </div>

          {/* 建筑劳务公司 */}
          <div className="bg-[#131b2e]/80 rounded-xl p-4 border border-[#EF4444]/40 glow-red">
            <div className="flex justify-between items-center mb-3">
              <span className="text-[13px] font-bold text-[#ffb4ab]">四川本盛劳务（建筑劳务）</span>
              <span className="text-[11px] text-[#EF4444] bg-[#EF4444]/20 px-2 py-0.5 rounded font-bold animate-pulse">退税核实</span>
            </div>
            <div className="space-y-2 text-[12px] font-mono-num">
              <div className="flex justify-between"><span className="text-[#8e909f]">进项税额:</span><span className="text-[#dae2fd]">¥ 15,200 万元</span></div>
              <div className="flex justify-between"><span className="text-[#8e909f]">销项税额:</span><span className="text-[#b8c4ff]">¥ 13,700 万元</span></div>
              <div className="flex justify-between pt-1 border-t border-[#444653]/20 font-bold"><span className="text-[#F59E0B]">留抵退税:</span><span className="text-[#F59E0B]">- ¥ 1,500 万元</span></div>
            </div>
          </div>

          {/* 机械租赁公司 */}
          <div className="bg-[#131b2e]/80 rounded-xl p-4 border border-[#444653]/30 hover:border-[#4cd7f6]/40 transition-all">
            <div className="flex justify-between items-center mb-3">
              <span className="text-[13px] font-bold text-[#dae2fd]">四川乾润和机械（机械租赁）</span>
              <span className="text-[11px] text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded">正常</span>
            </div>
            <div className="space-y-2 text-[12px] font-mono-num">
              <div className="flex justify-between"><span className="text-[#8e909f]">进项税额:</span><span className="text-[#dae2fd]">¥ 10,400 万元</span></div>
              <div className="flex justify-between"><span className="text-[#8e909f]">销项税额:</span><span className="text-[#b8c4ff]">¥ 18,900 万元</span></div>
              <div className="flex justify-between pt-1 border-t border-[#444653]/20 font-bold"><span className="text-[#4cd7f6]">净应缴额:</span><span className="text-[#4cd7f6]">¥ 8,500 万元</span></div>
            </div>
          </div>
        </div>
      </div>

      {/* 重点监控项目卡片矩阵 */}
      <div>
        <div className="flex justify-between items-center mb-4">
          <div>
            <h3 className="text-[20px] font-bold text-[#dae2fd]">重点监控工程项目</h3>
            <p className="text-[12px] text-[#c4c5d5]">穿透单体工程的预算执行深度、资金消耗率与税务合规评级</p>
          </div>
          <button 
            onClick={() => onSelectProject('proj-02')}
            className="text-[12px] font-semibold text-[#4cd7f6] hover:underline flex items-center gap-1 cursor-pointer"
          >
            <span>穿透阿尔法大厦明细</span>
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {projects.map((proj) => {
            const isAlpha = proj.id === 'proj-02';
            return (
              <div
                key={proj.id}
                className={`glass-panel rounded-xl p-5 flex flex-col justify-between gap-4 transition-all duration-200 hover:scale-[1.01] ${
                  proj.isOverBudget 
                    ? 'border-[#F59E0B]/50 shadow-[0_0_16px_rgba(245,158,11,0.1)]' 
                    : isAlpha 
                    ? 'border-[#4cd7f6]/50 shadow-[0_0_16px_rgba(76,215,246,0.15)]' 
                    : 'border-[#444653]/30'
                }`}
              >
                <div>
                  {/* 项目标头 */}
                  <div className="flex justify-between items-start gap-2">
                    <div>
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${
                          proj.isOverBudget
                            ? 'bg-[#F59E0B]/15 text-[#F59E0B] border-[#F59E0B]/30'
                            : 'bg-[#10B981]/15 text-[#10B981] border-[#10B981]/30'
                        }`}>
                          {proj.isOverBudget ? '超支预警' : '正常推进'}
                        </span>
                        <span className="text-[11px] font-mono-num text-[#8e909f]">{proj.projectCode}</span>
                      </div>
                      <h4 className="text-[17px] font-bold text-[#dae2fd]">{proj.name}</h4>
                    </div>

                    {/* 进度环 */}
                    <div className="relative w-12 h-12 flex items-center justify-center rounded-full bg-[#131b2e] border-2 border-[#444653]/40">
                      <span className="text-[11px] font-bold font-mono-num text-[#4cd7f6]">{proj.progressPercent}%</span>
                    </div>
                  </div>

                  {/* 核心指标行 */}
                  <div className="grid grid-cols-2 gap-3 my-4 py-3 border-y border-[#444653]/30 text-[12px]">
                    <div>
                      <p className="text-[11px] text-[#8e909f]">预算执行 / 批复总额</p>
                      <p className="font-bold font-mono-num text-[#dae2fd] mt-0.5">
                        ¥ {(proj.spentAmount / 100000000).toFixed(1)}亿 / {(proj.totalBudget / 100000000).toFixed(1)}亿
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] text-[#8e909f]">税务风险评级</p>
                      <p className={`font-bold mt-0.5 flex items-center gap-1 ${
                        proj.taxRiskGrade === '高危' || proj.taxRiskGrade === '中等偏高'
                          ? 'text-[#F59E0B]'
                          : 'text-[#10B981]'
                      }`}>
                        {proj.taxRiskGrade === '高危' || proj.taxRiskGrade === '中等偏高' ? (
                          <AlertTriangle className="w-3.5 h-3.5" />
                        ) : (
                          <ShieldCheck className="w-3.5 h-3.5" />
                        )}
                        {proj.taxRiskGrade}
                      </p>
                    </div>
                  </div>
                </div>

                {/* 底部团队与操作 */}
                <div className="flex justify-between items-center pt-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-[#8e909f]">负责人：</span>
                    <span className="text-[12px] font-medium text-[#dae2fd]">{proj.managerName.split(' ')[0]}</span>
                  </div>
                  <button
                    onClick={() => onSelectProject(proj.id)}
                    className={`px-3.5 py-1.5 rounded-lg text-[12px] font-semibold transition-all cursor-pointer ${
                      proj.isOverBudget
                        ? 'bg-[#F59E0B]/20 text-[#ffa583] hover:bg-[#F59E0B]/30 border border-[#F59E0B]/40'
                        : 'bg-[#1e40af]/60 hover:bg-[#1e40af] text-[#dde1ff] border border-[#4cd7f6]/30'
                    }`}
                  >
                    {proj.isOverBudget ? '紧急介入' : '项目详情'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
