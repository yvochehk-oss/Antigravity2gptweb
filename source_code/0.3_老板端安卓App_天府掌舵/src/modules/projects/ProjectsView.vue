<template>
  <main class="projects-page min-w-0 px-4 py-4" aria-labelledby="projects-title">
    <section class="space-y-1">
      <div class="flex items-center justify-between gap-3">
        <h1 id="projects-title" class="page-title text-slate-50 font-bold">工程项目台账</h1>
        <span class="shrink-0 rounded-full border border-amber-400/40 bg-amber-400/15 px-3 py-1 font-financial text-[13px] font-bold text-amber-200 shadow-sm">
          {{ filteredProjects.length }} 个在建
        </span>
      </div>
      <p class="text-[13px] leading-5 text-slate-400">按项目编号、工程全称或区域定位，穿透施工进度与真实经营底账。</p>
    </section>

    <div class="mt-3 flex items-center justify-between gap-2">
      <div class="flex-1">
        <div
          v-if="connectionStatus === 'offline' || partialFailure"
          class="flex items-start gap-2.5 rounded-xl border border-amber-400/30 bg-[#142238] px-3.5 py-3 text-[13px] leading-5 text-amber-100/90 shadow-sm"
          role="status"
        >
          <span class="mt-1 h-2 w-2 shrink-0 rounded-full bg-amber-400" aria-hidden="true" />
          <span>{{ connectionStatus === 'offline' ? '连接暂不可用，当前保留最近可用项目数据。' : '部分经营数据仍在同步，列表保留上次可用结果。' }}</span>
        </div>
        <div
          v-else-if="loading"
          class="flex items-center gap-2.5 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3.5 py-3 text-[13px] leading-5 text-amber-200 shadow-sm"
          role="status"
          aria-live="polite"
        >
          <span class="h-2 w-2 shrink-0 animate-pulse rounded-full bg-amber-400" aria-hidden="true" />
          <span>正在同步项目经营数据…</span>
        </div>
      </div>
      <DataSourceBadge :mode="dataSource" class="shrink-0" />
    </div>

    <!-- 搜索栏 -->
    <section class="mt-4" aria-label="项目搜索">
      <div class="relative">
        <label for="project-search" class="sr-only">搜索项目</label>
        <svg class="pointer-events-none absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="m21 21-4.35-4.35m1.35-5.65a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z" />
        </svg>
        <input
          id="project-search"
          v-model="search"
          type="search"
          inputmode="search"
          autocomplete="off"
          placeholder="搜索项目名称、编号或工程地点"
          class="form-input min-h-11 w-full pl-11 pr-11 text-[14px]"
        />
        <button
          v-if="search"
          type="button"
          class="absolute right-1.5 top-1/2 -translate-y-1/2 flex h-8 w-8 items-center justify-center rounded-lg text-[18px] leading-none text-slate-400 transition hover:bg-white/10 hover:text-slate-100 focus:outline-none focus:ring-2 focus:ring-amber-400/50"
          aria-label="清除搜索"
          @click="clearSearch"
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>
    </section>

    <div
      v-if="connectionStatus === 'offline' || partialFailure"
      class="mt-3 flex items-start gap-2.5 rounded-xl border border-amber-400/30 bg-[#142238] px-3.5 py-3 text-[13px] leading-5 text-amber-100/90 shadow-sm"
      role="status"
    >
      <span class="mt-1 h-2 w-2 shrink-0 rounded-full bg-amber-400" aria-hidden="true" />
      <span>{{ connectionStatus === 'offline' ? '连接暂不可用，当前保留最近可用项目数据。' : '部分经营数据仍在同步，列表保留上次可用结果。' }}</span>
    </div>
    <div
      v-else-if="loading"
      class="mt-3 flex items-center gap-2.5 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3.5 py-3 text-[13px] leading-5 text-amber-200 shadow-sm"
      role="status"
      aria-live="polite"
    >
      <span class="h-2 w-2 shrink-0 animate-pulse rounded-full bg-amber-400" aria-hidden="true" />
      <span>正在同步项目经营数据…</span>
    </div>

    <section v-if="!canViewProjects" class="mt-4 rounded-2xl border border-rose-400/30 bg-rose-950/20 px-4 py-8 text-center" role="alert">
      <h2 class="text-[17px] leading-6 font-bold text-slate-100">暂无项目访问权限</h2>
      <p class="mt-2 text-[13px] leading-5 text-slate-400">当前高管角色尚未配置项目穿透权限，请联系系统管理员。</p>
    </section>

    <section v-else class="mt-4" aria-label="项目列表">
      <div v-if="!filteredProjects.length" class="surface-card rounded-2xl p-8 text-center">
        <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl border border-white/10 bg-white/5 text-xl text-slate-400" aria-hidden="true">⌕</div>
        <h2 class="mt-3.5 text-[17px] leading-6 font-bold text-slate-100">没有找到匹配项目</h2>
        <p class="mt-1 text-[13px] leading-5 text-slate-400">尝试使用项目编号、工程简称或施工地点搜索。</p>
        <button
          v-if="search"
          type="button"
          class="secondary-button mt-4 text-[14px]"
          @click="clearSearch"
        >
          清除搜索
        </button>
      </div>

      <div v-else class="space-y-3.5">
        <article
          v-for="project in filteredProjects"
          :key="project.id"
          class="project-card group cursor-pointer rounded-2xl p-4.5 transition-all hover:border-amber-400/50 hover:shadow-xl"
          :class="{ 'project-card--pending': detailLoadingId === project.id }"
          :aria-busy="detailLoadingId === project.id"
          @click="requestProjectDetails(project)"
        >
          <header class="flex items-start justify-between gap-3">
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                <span class="project-code rounded-md border border-amber-400/40 bg-amber-400/15 px-2 py-0.5 font-mono text-[12px] font-bold text-amber-200">{{ project.project_code || '未编号' }}</span>
                <span class="rounded-md border border-white/10 bg-white/5 px-2 py-0.5 text-[12px] font-medium text-slate-300">{{ project.tax_method || '一般计税 9%' }}</span>
              </div>
              <h2 class="project-title mt-2 text-[17px] leading-6 font-bold tracking-tight text-slate-50 transition-colors group-hover:text-amber-200">{{ project.name || '未命名项目' }}</h2>
              <p class="mt-1.5 flex min-w-0 items-center gap-1.5 text-[13px] leading-5 text-slate-400">
                <svg class="h-4 w-4 shrink-0 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 21s7-5.25 7-12a7 7 0 1 0-14 0c0 6.75 7 12 7 12Z" />
                  <circle cx="12" cy="9" r="2.25" stroke-width="1.8" />
                </svg>
                <span class="location-text min-w-0">{{ project.location || '项目地点待补充' }}</span>
              </p>
            </div>
            <span
              class="shrink-0 rounded-full border px-2.5 py-1 text-right text-[12px] font-semibold shadow-sm"
              :class="riskClass(project.risk_status)"
              :title="project.risk_status || '风险状态待补充'"
            >{{ project.risk_status || '运转稳健' }}</span>
          </header>

          <div class="mt-4 grid grid-cols-2 gap-3 rounded-xl border border-white/10 bg-black/25 p-3.5" aria-label="项目关键金额">
            <div class="min-w-0">
              <p class="text-[12px] font-medium leading-4 text-slate-400">合同签约总造价</p>
              <p class="money-value mt-1.5 font-financial text-[20px] font-bold leading-6 text-slate-100">{{ money(project.contract_amount) }}</p>
            </div>
            <div class="min-w-0 border-l border-white/10 pl-3">
              <p class="text-[12px] font-medium leading-4 text-amber-200/80">真实动态毛利</p>
              <p class="money-value mt-1.5 font-financial text-[20px] font-bold leading-6 text-amber-300">{{ money(project.real_profit) }}</p>
            </div>
          </div>

          <div class="mt-3 rounded-xl border border-white/10 bg-gradient-to-b from-[#14233a] to-[#0c1626] p-3.5">
            <div class="flex items-center justify-between gap-3 text-[13px] leading-5">
              <span class="font-medium text-slate-300">施工履约进度</span>
              <span class="shrink-0 font-financial text-[14px] font-bold text-slate-100">{{ progress(project.progress_pct) }}%</span>
            </div>
            <div class="mt-2 h-2 overflow-hidden rounded-full bg-slate-800" role="progressbar" :aria-valuenow="progress(project.progress_pct)" aria-valuemin="0" aria-valuemax="100" :aria-label="`${project.name || '项目'}施工进度`">
              <div class="h-full rounded-full bg-gradient-to-r from-amber-500 to-amber-300 transition-[width] duration-500" :style="{ width: `${progress(project.progress_pct)}%` }" />
            </div>
            <div class="mt-3 grid grid-cols-2 gap-3 border-t border-white/5 pt-2.5">
              <div>
                <p class="text-[12px] font-medium text-slate-400">项目毛利率</p>
                <p class="mt-0.5 font-financial text-[15px] font-bold text-emerald-300">{{ percent(project.gross_margin) }}</p>
              </div>
              <div class="border-l border-white/10 pl-3">
                <p class="text-[12px] font-medium text-slate-400">资金回款率</p>
                <p class="mt-0.5 font-financial text-[15px] font-bold text-sky-300">{{ percent(project.collection_rate) }}</p>
              </div>
            </div>
          </div>

          <div class="mt-3.5 flex items-center justify-between gap-3 border-t border-white/10 pt-3">
            <span class="min-w-0 text-[12px] font-medium text-slate-400">底账四流合规穿透</span>
            <button
              type="button"
              class="flex min-h-10 shrink-0 items-center justify-center gap-1.5 rounded-xl border border-amber-400/50 bg-amber-400/10 px-3.5 text-[13px] font-semibold text-amber-200 transition hover:bg-amber-400/20 focus:outline-none focus:ring-2 focus:ring-amber-400/50 disabled:cursor-wait disabled:opacity-60"
              :disabled="detailLoadingId !== null && detailLoadingId !== project.id"
              @click.stop="requestProjectDetails(project)"
            >
              <span v-if="detailLoadingId === project.id" class="h-4 w-4 animate-spin rounded-full border-2 border-amber-300/30 border-t-amber-300" aria-hidden="true" />
              <span>{{ detailLoadingId === project.id ? '正在穿透…' : '360° 穿透' }}</span>
              <span v-if="detailLoadingId !== project.id" aria-hidden="true">→</span>
            </button>
          </div>

          <p v-if="detailError?.id === project.id" class="mt-3 rounded-xl border border-rose-400/35 bg-rose-400/15 px-3.5 py-2.5 text-[13px] leading-5 text-rose-100" role="alert">
            {{ detailError.message }}
          </p>
        </article>
      </div>
    </section>
  </main>
</template>

<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useExecutiveStore } from '../../stores/executive.store'
import { PERMISSIONS, useAuthStore } from '../../stores/auth.store'
import { useUiStore } from '../../stores/ui.store'
import { formatMoney, formatPercent } from '../../utils/formatters'
import DataSourceBadge from '../../shared/components/DataSourceBadge.vue'

const executive = useExecutiveStore()
const auth = useAuthStore()
const ui = useUiStore()
const { projects: rawProjects, partialFailure, _dataSource } = storeToRefs(executive)
const { privacyMode, connectionStatus, loading } = storeToRefs(ui)

const dataSource = computed(() => _dataSource.value || 'unavailable')

const search = ref('')
const detailLoadingId = ref(null)
const detailError = ref(null)

const canViewProjects = computed(() => auth.can(PERMISSIONS.VIEW_PROJECTS))
const allProjects = computed(() => {
  if (dataSource.value === 'unavailable') return []
  const raw = rawProjects.value
  return Array.isArray(raw) && raw.length ? raw : []
})

const filteredProjects = computed(() => {
  const query = search.value.trim().toLowerCase()
  if (!query) return allProjects.value
  return allProjects.value.filter(project => [project.name, project.project_code, project.location, project.owner].some(value => String(value || '').toLowerCase().includes(query)))
})

const money = value => formatMoney(value, privacyMode.value)
const percent = value => value === undefined || value === null ? '—' : formatPercent(value, privacyMode.value)
const progress = value => Math.max(0, Math.min(100, Number(value) || 0))

function riskClass(status) {
  const value = String(status || '')
  if (value.includes('中危') || value.includes('高危')) return 'border-rose-400/35 bg-rose-400/15 text-rose-200'
  if (value.includes('关注') || value.includes('预警')) return 'border-amber-400/35 bg-amber-400/15 text-amber-200'
  return 'border-emerald-400/35 bg-emerald-400/15 text-emerald-200'
}

function clearSearch() {
  search.value = ''
}

async function requestProjectDetails(project) {
  if (!canViewProjects.value || detailLoadingId.value !== null || project?.id === undefined || project?.id === null) return
  detailError.value = null
  detailLoadingId.value = project.id
  try {
    await executive.openProject(project.id)
  } catch (error) {
    detailError.value = {
      id: project.id,
      message: error?.message || '项目详情暂时无法加载，请稍后重试。'
    }
  } finally {
    if (detailLoadingId.value === project.id) detailLoadingId.value = null
  }
}
</script>

<style scoped>
.projects-page {
  width: 100%;
  max-width: 440px;
  margin: 0 auto;
}

.project-card {
  background: linear-gradient(180deg, rgba(20, 36, 59, 0.8) 0%, rgba(13, 24, 40, 0.95) 100%);
  border: 1px solid rgba(85, 115, 155, 0.35);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
}

.project-card:hover {
  border-color: rgba(226, 185, 99, 0.5);
}

.project-card--pending {
  border-color: rgba(226, 185, 99, 0.7);
  box-shadow: 0 0 16px rgba(226, 185, 99, 0.2);
}

.project-title,
.location-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.money-value {
  overflow-wrap: anywhere;
}

@media (prefers-reduced-motion: reduce) {
  .project-card,
  .project-card * {
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
  }
}
</style>
