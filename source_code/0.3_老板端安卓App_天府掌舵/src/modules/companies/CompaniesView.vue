<template>
  <main class="app-page px-4 py-4 pb-8 space-y-4 max-w-[440px] mx-auto">
    <section aria-labelledby="overview-title" class="space-y-1 flex items-center justify-between">
      <div>
        <h1 id="overview-title" class="page-title text-slate-50 font-bold">项目全览 (全生态底账)</h1>
        <p class="text-[13px] leading-5 text-slate-400">穿透全盘各项目的参与单位、合同与进出金额明细。</p>
      </div>
      <DataSourceBadge :mode="dataSource" class="shrink-0" />
    </section>

    <!-- 项目全览列表 -->
    <section aria-label="项目列表" class="space-y-4">
      <article
        v-for="proj in allProjects360"
        :key="proj.project.id"
        class="surface-card rounded-2xl p-4 shadow-lg border-amber-400/25 bg-gradient-to-b from-[#162740] to-[#0d1828]"
      >
        <div class="flex items-start justify-between gap-3 border-b border-white/10 pb-3 mb-3">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <span class="rounded-md border border-amber-400/40 bg-amber-400/15 px-2 py-0.5 font-mono text-[12px] font-bold text-amber-200">
                {{ proj.project.project_code }}
              </span>
              <span
                class="rounded-md border px-2 py-0.5 text-[11px] font-medium border-emerald-400/40 bg-emerald-400/15 text-emerald-200"
              >
                {{ proj.project.status === 'ACTIVE' ? '在建' : proj.project.status }}
              </span>
            </div>
            <h3 class="mt-2 text-[17px] leading-6 font-bold text-slate-50">
              {{ proj.project.name }}
            </h3>
          </div>
        </div>

        <div class="space-y-3">
          <!-- 核心财务指标 -->
          <div class="grid grid-cols-2 gap-3">
            <div class="rounded-xl border border-white/10 bg-black/25 p-3">
              <p class="text-[11px] text-slate-400">已确认结算收入</p>
              <p class="mt-1 text-[16px] font-financial font-bold text-emerald-300">
                {{ money(proj.system_penetration.recognized_revenue) }}
              </p>
            </div>
            <div class="rounded-xl border border-white/10 bg-black/25 p-3">
              <p class="text-[11px] text-slate-400">穿透后真实成本</p>
              <p class="mt-1 text-[16px] font-financial font-bold text-amber-300">
                {{ money(proj.system_penetration.system_external_real_cost) }}
              </p>
            </div>
          </div>

          <!-- 各主体参与明细 (参与的各个单位，合同金额，进出金额等) -->
          <div class="rounded-xl border border-white/10 bg-black/40 p-3 text-[12px]">
            <h4 class="font-bold text-slate-200 border-b border-white/10 pb-2 mb-2 flex items-center justify-between">
              <span>参与单位明细 (系统内资格 + 外部供应商)</span>
              <span class="text-[10px] font-normal text-slate-400">包含合同/结算/流出金额</span>
            </h4>

            <div class="space-y-3">
              <!-- 1. 发包方 (业主) -->
              <div v-if="proj.system_penetration.revenueDetails?.length">
                <p class="text-[11px] font-bold text-emerald-400 mb-1">▶ 资金源头 (发包方)</p>
                <div v-for="(rev, idx) in proj.system_penetration.revenueDetails" :key="idx" class="flex items-center justify-between py-1.5 border-b border-white/5">
                  <div class="min-w-0 pr-2 flex-1">
                    <div class="font-medium text-slate-200 truncate">{{ rev.name }}</div>
                    <div class="text-[10px] text-slate-400">合同: {{ money(rev.contract) }}</div>
                  </div>
                  <div class="shrink-0 text-right">
                    <div class="text-[10px] text-emerald-400/80">已进账确认</div>
                    <div class="font-financial font-bold text-emerald-300">{{ money(rev.recognized) }}</div>
                  </div>
                </div>
              </div>

              <!-- 2. 系统内流转单位 -->
              <div v-if="proj.system_penetration.internalDetails?.length">
                <p class="text-[11px] font-bold text-purple-400 mt-2 mb-1">▶ 系统内参与单位</p>
                <div v-for="(item, idx) in proj.system_penetration.internalDetails" :key="idx" class="flex items-center justify-between py-1.5 border-b border-white/5">
                  <div class="min-w-0 pr-2 flex-1">
                    <div class="font-medium text-slate-200 truncate">{{ item.unit }}</div>
                    <div class="text-[10px] text-purple-300/80">{{ item.category }}</div>
                  </div>
                  <div class="shrink-0 text-right">
                    <div class="text-[10px] text-purple-400/80">内部分配流转</div>
                    <div class="font-financial font-bold text-purple-200">{{ money(item.amount) }}</div>
                  </div>
                </div>
              </div>

              <!-- 3. 外部终端供应商 (穿透成本) -->
              <div v-if="proj.system_penetration.externalDetails?.length">
                <p class="text-[11px] font-bold text-amber-400 mt-2 mb-1">▶ 系统外合格供应商 (终端成本)</p>
                <div v-for="(item, idx) in proj.system_penetration.externalDetails" :key="idx" class="flex items-center justify-between py-1.5 border-b border-white/5">
                  <div class="min-w-0 pr-2 flex-1">
                    <div class="font-medium text-slate-200 truncate">{{ item.supplier }}</div>
                    <div class="text-[10px] text-amber-300/80">{{ item.category }} (名义合同: {{ money(item.nominal) }})</div>
                  </div>
                  <div class="shrink-0 text-right">
                    <div class="text-[10px] text-amber-400/80">真实流出成本</div>
                    <div class="font-financial font-bold text-amber-300">{{ money(item.real) }}</div>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="!proj.system_penetration.revenueDetails?.length && !proj.system_penetration.internalDetails?.length && !proj.system_penetration.externalDetails?.length" class="text-center py-4 text-slate-500">
              暂无参与单位明细
            </div>
          </div>
        </div>
      </article>

      <!-- Loading state -->
      <div v-if="isLoading" class="text-center py-8">
        <div class="inline-block h-6 w-6 animate-spin rounded-full border-2 border-amber-300/30 border-t-amber-300 mb-2"></div>
        <p class="text-[13px] text-slate-400">正在加载全盘项目底账...</p>
      </div>

      <div v-if="!isLoading && !allProjects360.length" class="surface-card rounded-2xl border-dashed p-8 text-center text-[14px] text-slate-400">
        暂无项目数据，请刷新后重试。
      </div>
    </section>
  </main>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useExecutiveStore } from '../../stores/executive.store'
import { useAuthStore } from '../../stores/auth.store'
import { useUiStore } from '../../stores/ui.store'
import { getProject360 } from '../../api/projects.api'
import { formatMoney } from '../../utils/formatters'
import DataSourceBadge from '../../shared/components/DataSourceBadge.vue'

const executive = useExecutiveStore()
const auth = useAuthStore()
const ui = useUiStore()
const { projects, _dataSource } = storeToRefs(executive)
const { privacyMode } = storeToRefs(ui)

const dataSource = computed(() => _dataSource.value || 'unavailable')
const isLoading = ref(true)
const allProjects360 = ref([])

onMounted(async () => {
  isLoading.value = true
  try {
    const rawProjects = projects.value || []
    if (!rawProjects.length) {
      await executive.refresh()
    }

    // Fetch 360 detail for all projects concurrently
    const promises = (projects.value || []).map(p =>
      getProject360(ui.serverBaseUrl, p.id, auth.session?.accessToken)
    )
    const results = await Promise.allSettled(promises)

    const loadedData = []
    for (const res of results) {
      if (res.status === 'fulfilled' && res.value && res.value.status === 'success') {
        loadedData.push(res.value)
      }
    }
    allProjects360.value = loadedData
  } catch (err) {
    console.error("Failed to fetch project 360 overviews:", err)
  } finally {
    isLoading.value = false
  }
})

const money = value => formatMoney(value, privacyMode.value)
</script>
