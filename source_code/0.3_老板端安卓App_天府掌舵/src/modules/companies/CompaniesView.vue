<template>
  <main class="app-page px-4 py-4 pb-8 space-y-4 max-w-[440px] mx-auto">
    <section aria-labelledby="companies-title" class="space-y-1 flex items-center justify-between">
      <div>
        <h1 id="companies-title" class="page-title text-slate-50 font-bold">法人全景矩阵</h1>
        <p class="text-[13px] leading-5 text-slate-400">穿透 26 家法人及分支机构的底账风险与法定代表人穿透图谱。</p>
      </div>
      <DataSourceBadge :mode="dataSource" class="shrink-0" />
    </section>

    <!-- ABCD 分类段落选择器：豪华药丸形切换器 -->
    <div
      class="grid grid-cols-4 gap-1.5 rounded-2xl border border-white/10 bg-black/40 p-1.5 shadow-inner"
      role="tablist"
      aria-label="法人主体分类"
    >
      <button
        v-for="role in ['A', 'B', 'C', 'D']"
        :key="role"
        type="button"
        role="tab"
        :aria-selected="selectedRole === role"
        class="min-h-12 rounded-xl px-1 text-center transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/80"
        :class="selectedRole === role
          ? 'bg-gradient-to-r from-amber-400 to-amber-300 text-slate-950 font-bold shadow-[0_4px_16px_rgba(226,185,99,0.35)]'
          : 'text-slate-400 hover:bg-white/5 hover:text-slate-100 font-medium'"
        @click="selectedRole = role"
      >
        <span class="block text-[14px] leading-tight">{{ role }} 类</span>
        <span class="block text-[11px] leading-tight mt-0.5 opacity-80 font-normal">主体</span>
      </button>
    </div>

    <!-- 当前分组概览卡 -->
    <section
      aria-labelledby="company-group-title"
      class="surface-card rounded-2xl p-4 shadow-lg border-amber-400/25 bg-gradient-to-b from-[#162740] to-[#0d1828]"
    >
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0 flex-1">
          <p class="text-[12px] font-semibold text-amber-300 uppercase tracking-wide">当前主体分组</p>
          <h2 id="company-group-title" class="mt-1 break-words text-[17px] leading-6 font-bold text-slate-50">
            {{ currentGroup?.title || `${selectedRole} 类主体` }}
          </h2>
        </div>
        <span class="shrink-0 rounded-full border border-amber-400/40 bg-amber-400/15 px-3 py-1 font-financial text-[13px] font-bold text-amber-200 shadow-sm">
          {{ companyCount }} 家
        </span>
      </div>
      <p class="mt-2.5 break-words text-[13px] leading-5 text-slate-300/90 border-t border-white/5 pt-2.5">
        {{ currentGroup?.desc || '法人经营矩阵' }}
      </p>
    </section>

    <!-- 法人主体列表 -->
    <section aria-label="法人主体列表" aria-live="polite" class="space-y-3.5">
      <article
        v-for="company in currentGroup?.entities || []"
        :key="company.id"
        class="surface-card rounded-2xl p-4 shadow-md transition-all hover:border-amber-400/40"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="rounded-md border border-amber-400/40 bg-amber-400/15 px-2 py-0.5 font-mono text-[12px] font-bold text-amber-200">
                {{ company.entity_code }}
              </span>
              <span
                class="rounded-md border px-2 py-0.5 text-[12px] font-medium"
                :class="company.legal_entity
                  ? 'border-emerald-400/40 bg-emerald-400/15 text-emerald-200'
                  : 'border-slate-500/40 bg-slate-500/15 text-slate-300'"
              >
                {{ company.legal_entity ? '独立法人' : '分支机构' }}
              </span>
            </div>
            <h3 class="mt-2.5 break-words text-[16px] leading-6 font-bold text-slate-50">
              {{ company.name }}
            </h3>
          </div>
          <span
            class="shrink-0 rounded-full border border-emerald-400/35 bg-emerald-400/15 px-2.5 py-1 text-[12px] font-semibold text-emerald-200 shadow-sm"
          >
            {{ company.risk_status }}
          </span>
        </div>

        <dl class="mt-3.5 divide-y divide-white/10 rounded-xl border border-white/10 bg-black/25">
          <div class="flex items-center justify-between gap-4 px-3.5 py-2.5">
            <dt class="shrink-0 text-[12px] font-medium text-slate-400">法定代表人</dt>
            <dd class="min-w-0 break-words text-right text-[14px] font-semibold text-slate-100">
              {{ masked(company.legal_representative) }}
            </dd>
          </div>
          <div class="flex items-center justify-between gap-4 px-3.5 py-2.5">
            <dt class="shrink-0 text-[12px] font-medium text-slate-400">注册资本</dt>
            <dd class="min-w-0 break-words text-right font-financial text-[15px] font-bold text-amber-300">
              {{ masked(company.registered_capital) }}
            </dd>
          </div>
        </dl>

        <div class="mt-2.5 rounded-xl border border-white/10 bg-black/20 px-3.5 py-2.5">
          <p class="text-[11px] font-medium text-slate-400">统一社会信用代码</p>
          <p class="mt-0.5 break-all font-mono text-[13px] leading-5 font-semibold text-slate-200">
            {{ masked(company.uscc) }}
          </p>
        </div>

        <div v-if="company.note" class="mt-2.5 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3.5 py-2.5">
          <p class="text-[12px] font-semibold text-amber-200">关联说明</p>
          <p class="mt-0.5 break-words text-[12px] leading-5 text-slate-200">
            {{ company.note.replace(/\n/g, ' · ') }}
          </p>
        </div>
      </article>

      <div v-if="!currentGroup?.entities?.length" class="surface-card rounded-2xl border-dashed p-8 text-center text-[14px] text-slate-400">
        暂无法人主体数据，请联网刷新后重试。
      </div>
    </section>
  </main>
</template>

<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { DEFAULT_COMPANIES, useExecutiveStore } from '../../stores/executive.store'
import { useUiStore } from '../../stores/ui.store'
import DataSourceBadge from '../../shared/components/DataSourceBadge.vue'

const selectedRole = ref('A')
const { companies, _dataSource } = storeToRefs(useExecutiveStore())
const { privacyMode } = storeToRefs(useUiStore())
const dataSource = computed(() => _dataSource.value || 'unavailable')
const currentGroup = computed(() => companies.value?.matrix?.[selectedRole.value] || DEFAULT_COMPANIES.matrix[selectedRole.value])
const companyCount = computed(() => currentGroup.value?.entities?.length || 0)
const masked = value => privacyMode.value ? '***' : (value || '—')
</script>
