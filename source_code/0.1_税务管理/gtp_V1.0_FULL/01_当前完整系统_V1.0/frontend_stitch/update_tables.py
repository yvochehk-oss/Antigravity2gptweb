with open('src/components/TaxPlanningView.tsx', 'r') as f:
    content = f.read()

import re

# Update Revenue Table
revenue_old = """              <table className="w-full text-left border-collapse text-[12.5px]">
                <thead>
                  <tr className="border-b border-[#444653]/30 text-[#8e909f]">
                    <th className="py-2.5 px-3">主体类型</th>
                    <th className="py-2.5 px-3">发包方名称</th>
                    <th className="py-2.5 px-3">对应合同金额</th>
                    <th className="py-2.5 px-3 text-right">已确认结算款 (元)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#444653]/20 font-mono-num">
                  <tr className="hover:bg-[#222a3d]/40">
                    <td className="py-2.5 px-3"><span className="text-[10px] bg-[#10B981]/20 text-[#10B981] px-1.5 py-0.5 rounded">外部业主</span></td>
                    <td className="py-2.5 px-3 text-[#dae2fd]">成都交投集团 (EXT-JT)</td>
                    <td className="py-2.5 px-3 text-[#8e909f]">¥ 850,000,000</td>
                    <td className="py-2.5 px-3 text-right font-bold text-[#10B981]">¥ {(penetrationData?.recognized_revenue || currentProject.totalBudget * 0.75).toLocaleString('zh-CN')}</td>
                  </tr>
                </tbody>
              </table>"""

revenue_new = """              <table className="w-full text-left border-collapse text-[12.5px]">
                <thead>
                  <tr className="border-b border-[#444653]/30 text-[#8e909f]">
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors" onClick={() => handleSort('type')}>主体类型<SortIcon columnKey="type" /></th>
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors" onClick={() => handleSort('name')}>发包方名称<SortIcon columnKey="name" /></th>
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors text-right" onClick={() => handleSort('contract')}>对应合同金额<SortIcon columnKey="contract" /></th>
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors text-right" onClick={() => handleSort('recognized')}>已确认结算款 (元)<SortIcon columnKey="recognized" /></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#444653]/20 font-mono-num">
                  {getSortedData(penetrationData?.revenueDetails || []).map((row, idx) => (
                    <tr key={idx} className="hover:bg-[#222a3d]/40 transition-colors">
                      <td className="py-2.5 px-3"><span className="text-[10px] bg-[#10B981]/20 text-[#10B981] px-1.5 py-0.5 rounded">{row.type}</span></td>
                      <td className="py-2.5 px-3 text-[#dae2fd]">{row.name}</td>
                      <td className="py-2.5 px-3 text-[#8e909f] text-right">¥ {Math.round(row.contract).toLocaleString('zh-CN')}</td>
                      <td className="py-2.5 px-3 text-right font-bold text-[#10B981]">¥ {Math.round(row.recognized).toLocaleString('zh-CN')}</td>
                    </tr>
                  ))}
                  {(!penetrationData?.revenueDetails || penetrationData.revenueDetails.length === 0) && (
                    <tr><td colSpan={4} className="py-4 text-center text-[#8e909f]">无明细数据</td></tr>
                  )}
                </tbody>
              </table>"""

content = content.replace(revenue_old, revenue_new)

# Update Internal Table
internal_old = """              <table className="w-full text-left border-collapse text-[12.5px]">
                <thead>
                  <tr className="border-b border-[#444653]/30 text-[#8e909f]">
                    <th className="py-2.5 px-3">流转节点</th>
                    <th className="py-2.5 px-3">系统内单位</th>
                    <th className="py-2.5 px-3">业务类别</th>
                    <th className="py-2.5 px-3 text-right">内部开票流转额 (元)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#444653]/20 font-mono-num">
                  <tr className="hover:bg-[#222a3d]/40">
                    <td className="py-2.5 px-3 text-[#8e909f]">总包 → 专包</td>
                    <td className="py-2.5 px-3 text-[#dae2fd]">A08 锐宝建设</td>
                    <td className="py-2.5 px-3 text-[#a78bfa]">内部总包分流</td>
                    <td className="py-2.5 px-3 text-right font-bold text-[#dae2fd]">¥ {((penetrationData?.internal_trade_volume || currentProject.spentAmount * 0.65) * 0.4).toLocaleString('zh-CN')}</td>
                  </tr>
                  <tr className="hover:bg-[#222a3d]/40">
                    <td className="py-2.5 px-3 text-[#8e909f]">总包 → 物资</td>
                    <td className="py-2.5 px-3 text-[#dae2fd]">B01 乾润和贸易</td>
                    <td className="py-2.5 px-3 text-[#a78bfa]">材料集采代购</td>
                    <td className="py-2.5 px-3 text-right font-bold text-[#dae2fd]">¥ {((penetrationData?.internal_trade_volume || currentProject.spentAmount * 0.65) * 0.35).toLocaleString('zh-CN')}</td>
                  </tr>
                  <tr className="hover:bg-[#222a3d]/40">
                    <td className="py-2.5 px-3 text-[#8e909f]">总包 → 劳务</td>
                    <td className="py-2.5 px-3 text-[#dae2fd]">C01 四川本盛劳务</td>
                    <td className="py-2.5 px-3 text-[#a78bfa]">劳务大包</td>
                    <td className="py-2.5 px-3 text-right font-bold text-[#dae2fd]">¥ {((penetrationData?.internal_trade_volume || currentProject.spentAmount * 0.65) * 0.25).toLocaleString('zh-CN')}</td>
                  </tr>
                </tbody>
              </table>"""

internal_new = """              <table className="w-full text-left border-collapse text-[12.5px]">
                <thead>
                  <tr className="border-b border-[#444653]/30 text-[#8e909f]">
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors" onClick={() => handleSort('node')}>流转节点<SortIcon columnKey="node" /></th>
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors" onClick={() => handleSort('unit')}>系统内单位<SortIcon columnKey="unit" /></th>
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors" onClick={() => handleSort('category')}>业务类别<SortIcon columnKey="category" /></th>
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors text-right" onClick={() => handleSort('amount')}>内部开票流转额 (元)<SortIcon columnKey="amount" /></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#444653]/20 font-mono-num">
                  {getSortedData(penetrationData?.internalDetails || []).map((row, idx) => (
                    <tr key={idx} className="hover:bg-[#222a3d]/40 transition-colors">
                      <td className="py-2.5 px-3 text-[#8e909f]">{row.node}</td>
                      <td className="py-2.5 px-3 text-[#dae2fd]">{row.unit}</td>
                      <td className="py-2.5 px-3 text-[#a78bfa]">{row.category}</td>
                      <td className="py-2.5 px-3 text-right font-bold text-[#dae2fd]">¥ {Math.round(row.amount).toLocaleString('zh-CN')}</td>
                    </tr>
                  ))}
                  {(!penetrationData?.internalDetails || penetrationData.internalDetails.length === 0) && (
                    <tr><td colSpan={4} className="py-4 text-center text-[#8e909f]">无明细数据</td></tr>
                  )}
                </tbody>
              </table>"""

content = content.replace(internal_old, internal_new)


# Update External Table
external_old = """              <table className="w-full text-left border-collapse text-[12.5px]">
                <thead>
                  <tr className="border-b border-[#444653]/30 text-[#8e909f]">
                    <th className="py-2.5 px-3">成本类别</th>
                    <th className="py-2.5 px-3">外部终端供应商</th>
                    <th className="py-2.5 px-3 text-right">名义采购额 (含税)</th>
                    <th className="py-2.5 px-3 text-right">真实流出成本 (元)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#444653]/20 font-mono-num">
                  <tr className="hover:bg-[#222a3d]/40">
                    <td className="py-2.5 px-3"><span className="text-[10px] bg-[#f59e0b]/20 text-[#f59e0b] px-1.5 py-0.5 rounded">外部材料</span></td>
                    <td className="py-2.5 px-3 text-[#dae2fd]">海螺水泥 (四川直销处)</td>
                    <td className="py-2.5 px-3 text-[#8e909f] text-right">¥ {((penetrationData?.system_external_cost || currentProject.spentAmount * 0.78) * 0.5 * 1.13).toLocaleString('zh-CN')}</td>
                    <td className="py-2.5 px-3 text-right font-bold text-[#f59e0b]">¥ {((penetrationData?.system_external_cost || currentProject.spentAmount * 0.78) * 0.5).toLocaleString('zh-CN')}</td>
                  </tr>
                  <tr className="hover:bg-[#222a3d]/40">
                    <td className="py-2.5 px-3"><span className="text-[10px] bg-[#f59e0b]/20 text-[#f59e0b] px-1.5 py-0.5 rounded">外部设备</span></td>
                    <td className="py-2.5 px-3 text-[#dae2fd]">三一重工租赁站</td>
                    <td className="py-2.5 px-3 text-[#8e909f] text-right">¥ {((penetrationData?.system_external_cost || currentProject.spentAmount * 0.78) * 0.3 * 1.09).toLocaleString('zh-CN')}</td>
                    <td className="py-2.5 px-3 text-right font-bold text-[#f59e0b]">¥ {((penetrationData?.system_external_cost || currentProject.spentAmount * 0.78) * 0.3).toLocaleString('zh-CN')}</td>
                  </tr>
                  <tr className="hover:bg-[#222a3d]/40">
                    <td className="py-2.5 px-3"><span className="text-[10px] bg-[#f59e0b]/20 text-[#f59e0b] px-1.5 py-0.5 rounded">外部劳务</span></td>
                    <td className="py-2.5 px-3 text-[#dae2fd]">四川蜀匠特种劳务队</td>
                    <td className="py-2.5 px-3 text-[#8e909f] text-right">¥ {((penetrationData?.system_external_cost || currentProject.spentAmount * 0.78) * 0.2 * 1.03).toLocaleString('zh-CN')}</td>
                    <td className="py-2.5 px-3 text-right font-bold text-[#f59e0b]">¥ {((penetrationData?.system_external_cost || currentProject.spentAmount * 0.78) * 0.2).toLocaleString('zh-CN')}</td>
                  </tr>
                </tbody>
              </table>"""

external_new = """              <table className="w-full text-left border-collapse text-[12.5px]">
                <thead>
                  <tr className="border-b border-[#444653]/30 text-[#8e909f]">
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors" onClick={() => handleSort('category')}>成本类别<SortIcon columnKey="category" /></th>
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors" onClick={() => handleSort('supplier')}>外部终端供应商<SortIcon columnKey="supplier" /></th>
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors text-right" onClick={() => handleSort('nominal')}>名义采购额 (含税)<SortIcon columnKey="nominal" /></th>
                    <th className="py-2.5 px-3 cursor-pointer hover:text-[#dae2fd] transition-colors text-right" onClick={() => handleSort('real')}>真实流出成本 (元)<SortIcon columnKey="real" /></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#444653]/20 font-mono-num">
                  {getSortedData(penetrationData?.externalDetails || []).map((row, idx) => (
                    <tr key={idx} className="hover:bg-[#222a3d]/40 transition-colors">
                      <td className="py-2.5 px-3"><span className="text-[10px] bg-[#f59e0b]/20 text-[#f59e0b] px-1.5 py-0.5 rounded">{row.category}</span></td>
                      <td className="py-2.5 px-3 text-[#dae2fd]">{row.supplier}</td>
                      <td className="py-2.5 px-3 text-[#8e909f] text-right">¥ {Math.round(row.nominal).toLocaleString('zh-CN')}</td>
                      <td className="py-2.5 px-3 text-right font-bold text-[#f59e0b]">¥ {Math.round(row.real).toLocaleString('zh-CN')}</td>
                    </tr>
                  ))}
                  {(!penetrationData?.externalDetails || penetrationData.externalDetails.length === 0) && (
                    <tr><td colSpan={4} className="py-4 text-center text-[#8e909f]">无明细数据</td></tr>
                  )}
                </tbody>
              </table>"""

content = content.replace(external_old, external_new)

with open('src/components/TaxPlanningView.tsx', 'w') as f:
    f.write(content)
