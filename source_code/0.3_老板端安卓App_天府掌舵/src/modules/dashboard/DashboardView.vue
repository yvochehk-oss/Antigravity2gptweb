<template>
  <main class="app-page w-full max-w-full overflow-x-clip px-4 pb-6 pt-4">
    <div class="space-y-5">
      <!-- 页面顶栏 -->
      <header class="page-heading flex items-start justify-between gap-3">
        <div class="min-w-0">
          <h2 class="page-title font-bold text-slate-50">经营大盘</h2>
          <p class="mt-0.5 text-[13px] leading-5 text-slate-400">
            {{ cockpit.as_of_date || '实时底账' }} · 核心指标全景
          </p>
        </div>

        <div class="flex shrink-0 flex-col items-end gap-1.5 pt-0.5">
          <span
            class="status-pill inline-flex min-h-7 items-center gap-1.5 rounded-full border px-2.5 text-[12px] font-semibold"
            :class="connectionPillClass"
          >
            <span class="h-2 w-2 rounded-full" :class="connectionDotClass" aria-hidden="true" />
            {{ connectionLabel }}
          </span>
          <span class="text-[12px] leading-4 text-slate-500">{{ refreshLabel }}</span>
        </div>
        <div class="shrink-0 pt-0.5">
          <DataSourceBadge :mode="dataSource" :snapshot-time="snapshotTime" />
        </div>
      </header>

      <!-- 同步/快照状态提示 -->
      <div
        v-if="ui.loading"
        class="flex min-h-11 items-center gap-2.5 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3.5 text-[13px] text-amber-100 shadow-sm"
        role="status"
        aria-live="polite"
      >
        <span class="h-2 w-2 animate-pulse rounded-full bg-amber-300" aria-hidden="true" />
        <span>正在同步经营底账，页面保留上次可用数据</span>
      </div>

      <div
        v-else-if="isStale"
        class="flex min-h-11 items-center gap-2.5 rounded-xl border border-amber-400/30 bg-[#121f33] px-3.5 text-[13px] leading-5 text-amber-100/90 shadow-sm"
        role="status"
        aria-live="polite"
      >
        <span class="h-2 w-2 rounded-full bg-amber-400" aria-hidden="true" />
        <span>当前为离线快照，数据更新时间：{{ cockpit.as_of_date || '未知' }}</span>
      </div>

      <!-- 主利润核心大金卡：高管第一视觉焦点 -->
      <section class="hero-gold-card p-5" aria-labelledby="profit-heading">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h3 id="profit-heading" class="text-[17px] font-bold leading-6 text-slate-50">本期动态真实净利润</h3>
          </div>
          <span class="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/40 bg-emerald-400/15 px-3 py-1 text-[12px] font-semibold text-emerald-300 shadow-sm">
            <span class="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-ping" aria-hidden="true" />
            稳健
          </span>
        </div>

        <div class="mt-4 flex items-baseline gap-2">
          <p class="break-words font-financial text-[34px] font-bold leading-tight tracking-tight text-amber-200 drop-shadow-[0_2px_12px_rgba(226,185,99,0.25)]">
            {{ money(cockpit.kpi?.real_profit) }}
          </p>
        </div>

        <div class="mt-5 grid grid-cols-2 gap-3 border-t border-white/10 pt-4">
          <div class="rounded-xl bg-black/20 p-2.5">
            <p class="text-[12px] font-medium text-slate-400">动态综合毛利率</p>
            <p class="mt-1 font-financial text-[22px] font-bold leading-7 text-emerald-300">{{ percent(cockpit.kpi?.gross_margin) }}</p>
          </div>
          <div class="rounded-xl bg-black/20 p-2.5">
            <p class="text-[12px] font-medium text-slate-400">核算口径标准</p>
            <p class="mt-1 text-[14px] font-semibold leading-7 text-slate-200 flex items-center gap-1.5">
              <span class="h-2 w-2 rounded-full bg-sky-400" aria-hidden="true" />
              动态真实口径
            </p>
          </div>
        </div>
      </section>

      <!-- 核心经营指标 -->
      <section class="surface-card p-4" aria-labelledby="metrics-heading">
        <div class="flex items-center justify-between gap-3 border-b border-white/10 pb-3">
          <div class="flex items-center gap-2">
            <span class="h-3.5 w-1 rounded-full bg-amber-400" aria-hidden="true" />
            <h2 id="metrics-heading" class="text-[16px] font-bold leading-6 text-slate-50">经营底账指标</h2>
          </div>
          <span class="rounded-md border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[11px] font-medium text-slate-400">单位 · 百万元</span>
        </div>

        <div class="mt-1 divide-y divide-white/10">
          <article v-for="metric in secondaryMetrics" :key="metric.label" class="flex min-h-[74px] items-center justify-between gap-3 py-3">
            <div class="min-w-0">
              <p class="metric-label text-[13px] font-medium leading-5 text-slate-300">{{ metric.label }}</p>
              <p class="mt-1 truncate text-[12px] leading-4 text-slate-400">{{ metric.note }}</p>
            </div>
            <p class="shrink-0 text-right font-financial text-[22px] font-bold leading-7" :class="metric.valueClass">{{ metric.value }}</p>
          </article>
        </div>
      </section>

      <!-- 风险提醒 -->
      <section class="space-y-3" aria-labelledby="risk-heading">
        <div class="flex items-center justify-between gap-3">
          <div class="flex min-w-0 items-center gap-2">
            <span class="h-2.5 w-2.5 shrink-0 rounded-full bg-rose-400 shadow-[0_0_8px_#f43f5e]" aria-hidden="true" />
            <h2 id="risk-heading" class="truncate text-[16px] font-bold leading-6 text-slate-50">待处置经营风险</h2>
            <span class="status-pill shrink-0 rounded-full border-rose-400/35 bg-rose-400/15 px-2.5 py-0.5 text-[12px] font-semibold text-rose-200">{{ risks.length }} 项</span>
          </div>
          <RouterLink
            :to="{ name: 'projects' }"
            class="inline-flex min-h-11 shrink-0 items-center gap-1 rounded-xl px-2 text-[13px] font-semibold text-amber-300 transition-colors hover:text-amber-200 focus:outline-none focus:ring-2 focus:ring-amber-400/50"
          >
            <span>项目台账</span>
            <span aria-hidden="true">→</span>
          </RouterLink>
        </div>

        <div v-if="risks.length" class="space-y-3">
          <button
            v-for="risk in risks"
            :key="risk.id"
            type="button"
            class="group w-full min-h-[114px] rounded-2xl border bg-gradient-to-b from-[#14233a] to-[#0d1829] p-4 text-left shadow-lg transition-all hover:border-amber-400/60 hover:shadow-xl focus:outline-none focus:ring-2 focus:ring-amber-400/50 disabled:cursor-wait disabled:opacity-70"
            :class="risk.level === 'danger' ? 'border-rose-400/35 shadow-rose-950/20' : 'border-amber-400/30 shadow-amber-950/20'"
            :disabled="riskOpeningId === risk.id"
            :aria-busy="riskOpeningId === risk.id"
            @click="openRisk(risk)"
          >
            <div class="flex items-center justify-between gap-2">
              <span
                class="status-pill min-h-6 max-w-[65%] truncate rounded-full border px-2.5 py-0.5 text-[12px] font-semibold"
                :class="risk.level === 'danger' ? 'border-rose-400/35 bg-rose-400/15 text-rose-200' : 'border-amber-400/35 bg-amber-400/15 text-amber-200'"
              >
                {{ risk.project_name }}
              </span>
              <span class="shrink-0 text-[12px] font-medium text-amber-300/80 group-hover:text-amber-200 flex items-center gap-1">
                {{ riskOpeningId === risk.id ? '打开中…' : '穿透核验 →' }}
              </span>
            </div>
            <h3 class="mt-2 text-[15px] font-bold leading-6 text-slate-100 group-hover:text-white">{{ risk.title }}</h3>
            <p class="mt-1 text-[13px] leading-5 text-slate-300">{{ risk.desc }}</p>
            <div class="mt-3 flex items-center gap-1.5 rounded-lg bg-amber-400/10 px-2.5 py-1.5 text-[12px] font-medium text-amber-200">
              <span class="font-bold">决策建议：</span>
              <span class="text-amber-100">{{ risk.action }}</span>
            </div>
          </button>
        </div>

        <div v-else class="rounded-2xl border border-white/10 bg-[#0d1829] px-4 py-6 text-center text-[13px] leading-5 text-slate-400">
          当前没有待处理风险，各工程指标运转平稳。
        </div>

        <p v-if="riskError" class="rounded-xl border border-rose-400/35 bg-rose-400/15 px-3.5 py-3 text-[13px] leading-5 text-rose-100" role="alert">
          {{ riskError }}
        </p>
      </section>

      <!-- 经营趋势图 -->
      <section class="surface-card p-4.5" aria-labelledby="trend-heading">
        <div class="flex items-start justify-between gap-3 border-b border-white/10 pb-3">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class="h-3.5 w-1 rounded-full bg-sky-400" aria-hidden="true" />
              <h2 id="trend-heading" class="text-[16px] font-bold leading-6 text-slate-50">经营趋势追踪</h2>
            </div>
            <p class="mt-0.5 text-[12px] leading-4 text-slate-400">近 6 个月 · 营收与净利对照 (百万元)</p>
          </div>
          <div class="flex shrink-0 items-center gap-3 text-[12px] font-medium text-slate-300" aria-label="图例">
            <span class="inline-flex items-center gap-1.5"><span class="h-2.5 w-2.5 rounded-sm bg-gradient-to-t from-amber-500 to-amber-300" aria-hidden="true" />营收</span>
            <span class="inline-flex items-center gap-1.5"><span class="h-2.5 w-2.5 rounded-sm bg-gradient-to-t from-emerald-500 to-emerald-300" aria-hidden="true" />净利</span>
          </div>
        </div>

        <div class="mt-6 grid min-w-0 grid-cols-6 items-end gap-2 border-b border-white/10 pb-3" role="img" aria-label="近六个月营收与净利润柱状趋势图">
          <div v-for="point in trendPoints" :key="point.month" class="flex min-w-0 flex-col items-center gap-2">
            <div class="flex h-28 w-full min-w-0 items-end justify-center gap-1">
              <span
                class="w-3 max-w-[45%] rounded-t-md rounded-b-sm bg-gradient-to-t from-amber-500 to-amber-300 shadow-sm transition-all duration-300"
                :style="{
                  height: privacyMode ? '4px' : `${point.revenueHeight}px`,
                  opacity: privacyMode ? 0.6 : 1
                }"
                :aria-label="trendLabel(point, 'revenue')"
              />
              <span
                class="w-3 max-w-[45%] rounded-t-md rounded-b-sm bg-gradient-to-t from-emerald-500 to-emerald-300 shadow-sm transition-all duration-300"
                :style="{
                  height: privacyMode ? '4px' : `${point.profitHeight}px`,
                  opacity: privacyMode ? 0.6 : 1
                }"
                :aria-label="trendLabel(point, 'profit')"
              />
            </div>
            <span class="font-financial text-[12px] font-semibold text-slate-400">{{ point.month }}</span>
          </div>
        </div>
        <div class="mt-3 flex items-center justify-between gap-2 text-[12px] leading-4 text-slate-400">
          <span>柱高自适应动态归一化</span>
          <span class="font-medium text-amber-300/80">底账实时汇算</span>
        </div>
      </section>

      <!-- ABCD 法人主体摘要 -->
      <section class="surface-card p-4.5" aria-labelledby="matrix-heading">
        <div class="flex items-center justify-between gap-3 border-b border-white/10 pb-3">
          <div class="flex items-center gap-2">
            <span class="h-3.5 w-1 rounded-full bg-emerald-400" aria-hidden="true" />
            <h2 id="matrix-heading" class="text-[16px] font-bold leading-6 text-slate-50">法人矩阵概览</h2>
          </div>
          <RouterLink
            :to="{ name: 'companies' }"
            class="inline-flex min-h-11 shrink-0 items-center gap-1 rounded-xl px-2 text-[13px] font-semibold text-amber-300 transition-colors hover:text-amber-200 focus:outline-none focus:ring-2 focus:ring-amber-400/50"
          >
            <span>26 家全景</span>
            <span aria-hidden="true">→</span>
          </RouterLink>
        </div>

        <div class="mt-4 grid grid-cols-2 gap-3">
          <article v-for="item in matrixSummary" :key="item.code" class="group min-w-0 rounded-xl border border-white/10 bg-gradient-to-b from-[#14233a] to-[#0c1626] p-3.5 shadow-md transition-all hover:border-amber-400/40">
            <div class="flex items-center justify-between gap-2">
              <span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg font-financial text-[15px] font-bold shadow-sm" :class="item.badgeClass">{{ item.code }}</span>
              <span class="rounded-full bg-white/5 px-2 py-0.5 text-[11px] font-semibold text-slate-400">{{ item.count }}</span>
            </div>
            <p class="mt-2.5 truncate text-[14px] font-semibold leading-5 text-slate-200">{{ item.title }}</p>
            <p class="mt-1.5 break-words font-financial text-[22px] font-bold leading-7" :class="item.valueClass">{{ matrixValue(item) }}</p>
            <p class="mt-1 text-[12px] leading-4 text-slate-400">{{ item.detail }}</p>
          </article>
        </div>
      </section>
    </div>
  </main>
</template>

<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useExecutiveStore } from '../../stores/executive.store'
import { useUiStore } from '../../stores/ui.store'
import { formatMoney, formatPercent } from '../../utils/formatters'
import DataSourceBadge from '../../shared/components/DataSourceBadge.vue'

const executive = useExecutiveStore()
const ui = useUiStore()
const { cockpit: rawCockpit, lastRefreshAt, partialFailure, _dataSource } = storeToRefs(executive)
const { privacyMode, loading, connectionStatus } = storeToRefs(ui)

const dataSource = computed(() => _dataSource.value || 'unavailable')
const snapshotTime = computed(() => {
  if (_dataSource.value === 'snapshot' && lastRefreshAt.value) {
    const d = new Date(lastRefreshAt.value)
    if (!Number.isNaN(d.getTime())) {
      return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
    }
  }
  return ''
})

// Return the raw cockpit when data is available; return an empty object when
// unavailable so the view does not fall back to DEFAULT_COCKPIT demo figures.
const cockpit = computed(() => {
  if (dataSource.value === 'unavailable') return {}
  return rawCockpit.value?.kpi ? rawCockpit.value : {}
})
const risks = computed(() => Array.isArray(cockpit.value.urgent_risks) ? cockpit.value.urgent_risks : [])
const riskOpeningId = ref(null)
const riskError = ref('')

const money = value => formatMoney(value, privacyMode.value)
const percent = value => formatPercent(value, privacyMode.value)

const secondaryMetrics = computed(() => [
  {
    label: '累计确认营收',
    value: money(cockpit.value.kpi?.revenue_recognized),
    note: `签约 ${money(cockpit.value.kpi?.contract_total)}`,
    valueClass: 'text-slate-100'
  },
  {
    label: '增值税综合税负',
    value: percent(cockpit.value.kpi?.tax_burden_rate || '1.94'),
    note: `应纳税 ${money(cockpit.value.kpi?.net_tax_liability)}`,
    valueClass: 'text-amber-200'
  },
  {
    label: '资金池净头寸（30 日）',
    value: money(cockpit.value.kpi?.net_cashflow),
    note: `回款率 ${percent(cockpit.value.kpi?.collection_rate || '74.5')}`,
    valueClass: 'text-emerald-300'
  }
])

const trendPoints = computed(() => {
  const months = cockpit.value.trends?.months || ['3月', '4月', '5月', '6月', '7月', '8月']
  const revenueValues = months.map((_, index) => Number(cockpit.value.trends?.revenue?.[index]) || 0)
  const profitValues = months.map((_, index) => Number(cockpit.value.trends?.profit?.[index]) || 0)
  const revenueMax = Math.max(...revenueValues, 1)
  const profitMax = Math.max(...profitValues, 1)

  return months.map((month, index) => ({
    month,
    revenue: revenueValues[index],
    profit: profitValues[index],
    revenueHeight: Math.max(10, Math.round((revenueValues[index] / revenueMax) * 88)),
    profitHeight: Math.max(10, Math.round((profitValues[index] / profitMax) * 88))
  }))
})

const matrixSummary = [
  { code: 'A', title: '施工总承包', count: '11 家', value: 85.4, kind: 'percent', detail: '产值贡献', valueClass: 'text-amber-300', badgeClass: 'bg-amber-400/20 text-amber-300 border border-amber-400/35' },
  { code: 'B', title: '物资商贸', count: '10 家', value: 12_880_000, kind: 'money', detail: '进项抵扣池', valueClass: 'text-sky-300', badgeClass: 'bg-sky-400/20 text-sky-300 border border-sky-400/35' },
  { code: 'C', title: '建筑劳务', count: '2 家', value: 100, kind: 'percent', detail: '用工合规', valueClass: 'text-emerald-300', badgeClass: 'bg-emerald-400/20 text-emerald-300 border border-emerald-400/35' },
  { code: 'D', title: '机械租赁', count: '3 家', value: 88.5, kind: 'percent', detail: '设备出租率', valueClass: 'text-violet-300', badgeClass: 'bg-violet-400/20 text-violet-300 border border-violet-400/35' }
]

const connectionLabel = computed(() => {
  if (loading.value || connectionStatus.value === 'connecting') return '同步中'
  if (connectionStatus.value === 'offline' || partialFailure.value) return '快照'
  return '在线'
})

const connectionPillClass = computed(() => {
  if (connectionStatus.value === 'offline' || partialFailure.value) return 'border-amber-400/35 bg-amber-400/10 text-amber-200'
  if (loading.value || connectionStatus.value === 'connecting') return 'border-sky-400/35 bg-sky-400/10 text-sky-200'
  return 'border-emerald-400/35 bg-emerald-400/10 text-emerald-200'
})

const connectionDotClass = computed(() => {
  if (connectionStatus.value === 'offline' || partialFailure.value) return 'bg-amber-400'
  if (loading.value || connectionStatus.value === 'connecting') return 'bg-sky-400 animate-ping'
  return 'bg-emerald-400'
})

const isStale = computed(() => connectionStatus.value === 'offline' || partialFailure.value)

const refreshLabel = computed(() => {
  if (!lastRefreshAt.value) return '等待首次同步'
  const date = new Date(lastRefreshAt.value)
  if (Number.isNaN(date.getTime())) return '更新时间未知'
  return `更新于 ${date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
})

function matrixValue(item) {
  return item.kind === 'money' ? money(item.value) : percent(item.value)
}

function trendLabel(point, kind) {
  const label = kind === 'revenue' ? '营收' : '净利'
  if (privacyMode.value) return `${point.month}${label} 已隐藏`
  return `${point.month}${label} ${point[kind]} 百万元`
}

async function openRisk(risk) {
  if (riskOpeningId.value !== null) return
  riskOpeningId.value = risk.id
  riskError.value = ''
  try {
    await executive.openProjectByCode(risk.project_code)
  } catch (error) {
    riskError.value = error instanceof Error ? error.message : '项目证据链暂时无法打开，请稍后重试。'
  } finally {
    riskOpeningId.value = null
  }
}
</script>
