<template>
  <Transition name="project-sheet">
    <div
      v-if="project360"
      class="fixed inset-0 z-50 flex items-end bg-[#02050a]/85 backdrop-blur-sm"
      role="presentation"
      @click.self="close"
      @keydown.esc="close"
    >
      <section
        class="project-drawer mx-auto flex max-h-[calc(100dvh-20px)] w-full max-w-[440px] flex-col overflow-hidden rounded-t-[28px] border border-amber-400/30 border-b-0 bg-gradient-to-b from-[#111f33] to-[#081220] shadow-[0_-16px_48px_rgba(0,0,0,0.6)]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-drilldown-title"
      >
        <header class="shrink-0 border-b border-white/10 px-4.5 pb-4 pt-3 bg-white/[0.02]">
          <div class="mx-auto h-1.2 w-12 rounded-full bg-slate-600/70" aria-hidden="true" />
          <div class="mt-3.5 flex items-start gap-3">
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                <span class="rounded-md border border-amber-400/40 bg-amber-400/15 px-2 py-0.5 font-mono text-[12px] font-bold text-amber-200">{{ detailProject.project_code || '未编号' }}</span>
                <span v-if="detailProject.risk_status" class="rounded-full border px-2.5 py-0.5 text-[12px] font-semibold" :class="riskClass(detailProject.risk_status)">{{ detailProject.risk_status }}</span>
                <DataSourceBadge :mode="dataSource" class="shrink-0" />
              </div>
              <h2 id="project-drilldown-title" class="drawer-title mt-2 text-[18px] leading-6 font-bold tracking-tight text-slate-50">{{ detailProject.name || '项目 360° 穿透' }}</h2>
              <p class="mt-1 text-[12px] leading-4 text-slate-400">真实成本 · 进项抵扣 · 动态毛利 · 四流闭环链</p>
            </div>
            <button
              ref="closeButton"
              type="button"
              aria-label="关闭项目穿透"
              class="drawer-close-btn group shrink-0"
              @click="close"
            >
              <svg class="h-6 w-6 text-amber-200 transition-transform duration-150 group-hover:scale-110 group-hover:text-amber-100" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </header>

        <div class="min-h-0 flex-1 overflow-y-auto px-4.5 pb-[calc(28px+var(--sab))] pt-4 space-y-4">
          <template v-if="hasDetailData">
            <!-- 核心财务双指标卡 -->
            <section class="grid grid-cols-2 gap-3" aria-label="项目财务摘要">
              <article class="metric-card metric-card--gold rounded-2xl p-4 shadow-md">
                <p class="text-[12px] font-medium text-amber-200/80">动态真实净利润</p>
                <p class="metric-value mt-1.5 font-financial text-[22px] font-bold leading-6 text-amber-300">{{ money(financial.real_profit) }}</p>
                <p class="mt-1.5 text-[12px] font-semibold text-emerald-300">毛利率 {{ percent(financial.gross_margin_pct) }}</p>
              </article>
              <article class="metric-card rounded-2xl p-4 shadow-md">
                <p class="text-[12px] font-medium text-slate-400">已发生成本</p>
                <p class="metric-value mt-1.5 font-financial text-[22px] font-bold leading-6 text-slate-100">{{ money(financial.actual_cost) }}</p>
                <p class="mt-1.5 flex flex-wrap gap-x-1 text-[12px] font-medium text-slate-400"><span>EAC 预测</span><span class="font-financial font-semibold text-slate-300">{{ money(financial.eac_forecast_cost) }}</span></p>
              </article>
            </section>

            <!-- 实际成本构成 -->
            <section class="surface-card rounded-2xl p-4 shadow-md" aria-labelledby="cost-heading">
              <div class="flex items-start justify-between gap-3 border-b border-white/10 pb-3">
                <div>
                  <h3 id="cost-heading" class="text-[16px] leading-6 font-bold text-slate-50">实际成本构成拆解</h3>
                  <p class="mt-0.5 text-[12px] leading-4 text-slate-400">项目已发生金额分项权重</p>
                </div>
                <span class="shrink-0 rounded-md border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-[11px] font-semibold text-amber-200">成本穿透</span>
              </div>

              <ol v-if="costItems.length" class="mt-3.5 space-y-3.5">
                <li v-for="item in costItems" :key="item.key" class="space-y-1.5">
                  <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                      <h4 class="text-[14px] leading-5 font-semibold text-slate-200">{{ item.label }}</h4>
                      <p class="text-[11px] leading-4 text-slate-400">占实际成本 {{ item.percent }}%</p>
                    </div>
                    <span class="money-value shrink-0 text-right font-financial text-[15px] font-bold text-amber-200">{{ money(item.amount) }}</span>
                  </div>
                  <div class="h-2 overflow-hidden rounded-full bg-black/40" role="progressbar" :aria-valuenow="item.percent" aria-valuemin="0" aria-valuemax="100" :aria-label="`${item.label}成本占比`">
                    <div class="h-full rounded-full bg-gradient-to-r from-amber-500 to-amber-300 transition-[width] duration-500" :style="{ width: `${item.percent}%` }" />
                  </div>
                </li>
              </ol>
              <div v-else class="mt-4 rounded-xl border border-dashed border-white/10 px-3 py-4 text-center text-[13px] leading-5 text-slate-400">暂无成本拆分数据</div>
            </section>

            <!-- 四流一致性合规底账 -->
            <section class="surface-card rounded-2xl p-4 shadow-md" aria-labelledby="flow-heading">
              <div class="flex items-start justify-between gap-3 border-b border-white/10 pb-3">
                <div>
                  <h3 id="flow-heading" class="text-[16px] leading-6 font-bold text-slate-50">四流一致性核验</h3>
                  <p class="mt-0.5 text-[12px] leading-4 text-slate-400">合同、发票、资金与物资闭环证据链</p>
                </div>
                <span class="shrink-0 rounded-md border border-emerald-400/35 bg-emerald-400/15 px-2 py-0.5 text-[11px] font-bold text-emerald-200">100% 闭环</span>
              </div>
              <ol class="mt-3.5 space-y-2.5">
                <li v-for="(flow, index) in flows" :key="flow.title" class="flex gap-3 rounded-xl border border-white/10 bg-gradient-to-b from-[#14233a] to-[#0c1626] p-3 shadow-sm">
                  <span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-amber-400/40 bg-amber-400/15 font-financial text-[13px] font-bold text-amber-200">{{ index + 1 }}</span>
                  <div class="min-w-0">
                    <h4 class="text-[14px] leading-5 font-bold" :class="flow.color">{{ flow.title }}</h4>
                    <p class="mt-0.5 text-[12px] leading-4 text-slate-300">{{ flow.detail }}</p>
                  </div>
                </li>
              </ol>
            </section>

            <!-- 关联凭证档案 -->
            <section class="surface-card rounded-2xl p-4 shadow-md" aria-labelledby="evidence-heading">
              <div class="flex items-start justify-between gap-3 border-b border-white/10 pb-3">
                <div>
                  <h3 id="evidence-heading" class="text-[16px] leading-6 font-bold text-slate-50">底层关联原始凭证</h3>
                  <p class="mt-0.5 text-[12px] leading-4 text-slate-400">用于佐证经营底账与四流结论的材料</p>
                </div>
                <span class="shrink-0 rounded-md bg-white/5 px-2 py-0.5 text-[11px] font-medium text-slate-400">{{ documents.length ? `${documents.length} 份档案` : '暂无' }}</span>
              </div>
              <ul v-if="documents.length" class="mt-3.5 space-y-2">
                <li v-for="doc in documents" :key="doc.id || doc.filename" class="flex min-w-0 items-center gap-3 rounded-xl border border-white/10 bg-gradient-to-b from-[#14233a] to-[#0c1626] p-3 transition-colors hover:border-amber-400/40">
                  <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/15 bg-black/30 font-mono text-[11px] font-bold text-amber-300" aria-hidden="true">DOC</span>
                  <div class="min-w-0 flex-1">
                    <p class="break-words text-[14px] leading-5 font-semibold text-slate-100">{{ doc.filename || '未命名凭证' }}</p>
                    <p class="mt-0.5 text-[12px] leading-4 text-slate-400">{{ doc.document_type || '未分类凭证' }}</p>
                  </div>
                </li>
              </ul>
              <div v-else class="mt-4 rounded-xl border border-dashed border-white/10 px-3 py-4 text-center text-[13px] leading-5 text-slate-400">当前项目未返回关联凭证</div>
            </section>
          </template>

          <div v-else class="rounded-2xl border border-amber-400/30 bg-[#18170f] px-4 py-8 text-center" role="status">
            <h3 class="text-[17px] leading-6 font-bold text-slate-100">项目详情暂不可用</h3>
            <p class="mt-2 text-[13px] leading-5 text-slate-400">服务未返回完整穿透数据，请关闭后重试。</p>
          </div>
        </div>
      </section>
    </div>
  </Transition>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useExecutiveStore } from '../../../stores/executive.store'
import { useUiStore } from '../../../stores/ui.store'
import { formatMoney } from '../../../utils/formatters'
import DataSourceBadge from '../../../shared/components/DataSourceBadge.vue'

const executive = useExecutiveStore()
const ui = useUiStore()
const { activeProject360: project360, _dataSource } = storeToRefs(executive)
const { privacyMode } = storeToRefs(ui)

const dataSource = computed(() => _dataSource.value || 'unavailable')

const closeButton = ref(null)
let previousBodyOverflow = ''
let bodyLocked = false

const detailProject = computed(() => project360.value?.project || {})
const financial = computed(() => project360.value?.financial_penetration || {})
const documents = computed(() => Array.isArray(project360.value?.document_list) ? project360.value.document_list.slice(0, 8) : [])
const costItems = computed(() => Object.entries(project360.value?.cost_breakdown || {}).map(([key, value]) => ({
  key,
  label: costLabel(key),
  amount: value?.amount,
  percent: safePercent(value?.pct)
})))
const hasDetailData = computed(() => Boolean(project360.value?.financial_penetration || project360.value?.cost_breakdown || project360.value?.document_list))

const flows = [
  { title: '合同流', detail: '约定 9% 建筑服务税率与节点工程款结算', color: 'text-amber-200' },
  { title: '发票流', detail: '13%/9% 进销项增值税专票 100% 勾选认证', color: 'text-sky-300' },
  { title: '资金流', detail: '银行对公账户回单与交易流水三方一致', color: 'text-emerald-300' },
  { title: '物资流', detail: '智能地磅单、出入库单据与现场影像全闭环', color: 'text-violet-300' }
]

const labels = {
  materials: '材料采购',
  labor: '建筑劳务分包',
  equipment: '机械与塔吊租赁'
}

const money = value => formatMoney(value, privacyMode.value)
const percent = value => privacyMode.value ? '***' : `${value ?? 0}%`

function safePercent(value) {
  return Math.max(0, Math.min(100, Number(value) || 0))
}

function costLabel(key) {
  return labels[key] || '安全文明及其他'
}

function riskClass(status) {
  const value = String(status || '')
  if (value.includes('中危') || value.includes('高危')) return 'border-rose-400/35 bg-rose-400/15 text-rose-200'
  if (value.includes('关注') || value.includes('预警')) return 'border-amber-400/35 bg-amber-400/15 text-amber-200'
  return 'border-emerald-400/35 bg-emerald-400/15 text-emerald-200'
}

function close() {
  executive.closeProject()
}

function syncScrollLock(isOpen) {
  if (typeof document === 'undefined') return
  if (isOpen && !bodyLocked) {
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    bodyLocked = true
    nextTick(() => closeButton.value?.focus())
  } else if (!isOpen && bodyLocked) {
    document.body.style.overflow = previousBodyOverflow
    bodyLocked = false
  }
}

watch(() => project360.value, value => syncScrollLock(Boolean(value)), { immediate: true })

onBeforeUnmount(() => syncScrollLock(false))
</script>

<style scoped>
.project-drawer {
  overscroll-behavior: contain;
}

.drawer-close-btn {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  min-width: 44px;
  min-height: 44px;
  border-radius: 12px;
  border: 1.5px solid rgba(226, 185, 99, 0.65);
  background: linear-gradient(135deg, rgba(34, 55, 84, 0.95) 0%, rgba(14, 25, 42, 0.98) 100%);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.45), 0 0 12px rgba(226, 185, 99, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.18);
  cursor: pointer;
  touch-action: manipulation;
  transition: all 180ms cubic-bezier(0.16, 1, 0.3, 1);
}

.drawer-close-btn:hover {
  border-color: rgba(226, 185, 99, 0.95);
  background: linear-gradient(135deg, rgba(46, 74, 112, 1) 0%, rgba(20, 36, 60, 1) 100%);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.6), 0 0 18px rgba(226, 185, 99, 0.35);
}

.drawer-close-btn:active {
  transform: scale(0.92);
}

.drawer-close-btn:focus-visible {
  outline: 2px solid #fbbf24;
  outline-offset: 2px;
}

.metric-card {
  min-width: 0;
  background: linear-gradient(180deg, rgba(20, 36, 59, 0.8) 0%, rgba(13, 24, 40, 0.95) 100%);
  border: 1px solid rgba(85, 115, 155, 0.35);
}

.metric-card--gold {
  border-color: rgba(226, 185, 99, 0.45);
  background: linear-gradient(165deg, rgba(32, 48, 72, 0.9) 0%, rgba(17, 28, 46, 0.95) 100%);
}

.drawer-title,
.metric-value,
.money-value {
  overflow-wrap: anywhere;
}

.project-sheet-enter-active,
.project-sheet-leave-active {
  transition: opacity 180ms ease;
}

.project-sheet-enter-active .project-drawer,
.project-sheet-leave-active .project-drawer {
  transition: transform 180ms ease;
}

.project-sheet-enter-from,
.project-sheet-leave-to {
  opacity: 0;
}

.project-sheet-enter-from .project-drawer,
.project-sheet-leave-to .project-drawer {
  transform: translateY(100%);
}

@media (prefers-reduced-motion: reduce) {
  .project-sheet-enter-active,
  .project-sheet-leave-active,
  .project-sheet-enter-active .project-drawer,
  .project-sheet-leave-active .project-drawer {
    transition-duration: 0.01ms;
  }
}
</style>
