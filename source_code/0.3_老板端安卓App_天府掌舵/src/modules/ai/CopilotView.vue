<template>
  <main class="app-page flex h-[calc(100dvh-7rem)] min-h-0 flex-col px-4 pt-4 !pb-[calc(5.5rem+var(--sab))] max-w-[440px] mx-auto">
    <div
      class="min-h-0 flex-1 overflow-y-auto overscroll-contain pr-0.5 space-y-4"
      role="log"
      aria-live="polite"
      aria-label="智策会话"
    >
      <!-- 欢迎区 -->
      <section class="surface-card rounded-2xl p-4 shadow-lg border-amber-400/25 bg-gradient-to-b from-[#162740] to-[#0d1828]" aria-labelledby="copilot-welcome-title">
        <div class="flex items-start gap-3">
          <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-amber-400 to-amber-500 text-[14px] font-black text-slate-950 shadow-[0_4px_16px_rgba(226,185,99,0.4)]">
            AI
          </div>
          <div class="min-w-0 flex-1">
            <h1 id="copilot-welcome-title" class="page-title text-slate-50 font-bold text-[18px]">
              高管经营内参，您好
            </h1>
            <p class="mt-1 text-[13px] leading-5 text-slate-300/90">
              我是您的经营智策助理。已连通真实底账、26家法人税务申报与现场证据链，随时为您提供决策支持。
            </p>
          </div>
        </div>
      </section>

      <!-- 快捷问题 -->
      <section aria-labelledby="quick-question-title" class="space-y-2">
        <div class="flex items-center justify-between gap-3">
          <h2 id="quick-question-title" class="text-[12px] font-semibold text-slate-400 uppercase tracking-wide">智能速问推荐</h2>
          <span class="text-[11px] text-slate-500">点击直接分析</span>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="question in quickQuestions"
            :key="question"
            type="button"
            :disabled="loading"
            class="min-h-10 rounded-xl border border-white/10 bg-white/5 px-3 text-left text-[12px] font-medium text-amber-200 transition-all hover:border-amber-400/50 hover:bg-amber-400/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/80 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 shadow-sm"
            @click="ask(question)"
          >
            {{ question }}
          </button>
        </div>
      </section>

      <!-- 消息列表 -->
      <div class="space-y-4 pt-1">
        <div
          v-for="(message, index) in messages"
          :key="index"
          class="flex items-start gap-2.5"
          :class="message.role === 'user' ? 'flex-row-reverse' : ''"
        >
          <div
            class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-[12px] font-bold shadow-sm"
            :class="message.role === 'user' ? 'border border-white/10 bg-[#1c2e48] text-slate-100' : 'bg-gradient-to-br from-amber-400 to-amber-500 text-slate-950'"
            aria-hidden="true"
          >
            {{ message.role === 'user' ? '您' : 'AI' }}
          </div>
          <div
            class="min-w-0 max-w-[calc(100%-3rem)] rounded-2xl p-4 shadow-md"
            :class="message.role === 'user'
              ? 'rounded-tr-sm border border-amber-400/40 bg-gradient-to-b from-[#2e2617] to-[#1e1910] text-amber-50'
              : 'rounded-tl-sm border border-white/10 bg-gradient-to-b from-[#152338] to-[#0c1626] text-slate-100'"
          >
            <div class="break-words whitespace-pre-wrap text-[14px] leading-relaxed">
              <template v-for="(segment, segIdx) in renderSegments(message)" :key="`${index}-${segIdx}`">
                <MentionChip v-if="segment.type === 'chip'" :chip="segment.chip" @drilldown="drilldown" />
                <span v-else>{{ segment.value }}</span>
              </template>
              <span
                v-if="streaming && index === messages.length - 1 && message.role === 'assistant'"
                class="ml-1 inline-block h-3.5 w-1.5 align-middle rounded-sm bg-amber-300 animate-pulse"
                aria-label="正在生成"
              />
            </div>

            <!-- AI 回复中的后续建议 pills：解析 ①②③④ 渲染为可点击按钮 -->
            <div
              v-if="message.role === 'assistant' && !streaming && followUpSuggestions(message.content).length"
              class="mt-3.5 border-t border-white/10 pt-3 space-y-2"
            >
              <p class="text-[11px] font-semibold text-amber-300/80 uppercase tracking-wide">继续深入分析</p>
              <div class="flex flex-wrap gap-2">
                <button
                  v-for="(suggestion, sIdx) in followUpSuggestions(message.content)"
                  :key="sIdx"
                  type="button"
                  :disabled="loading"
                  class="flex items-center gap-1.5 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-left text-[12px] font-medium text-amber-200 transition-all hover:border-amber-400/60 hover:bg-amber-400/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/80 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40 shadow-sm"
                  @click="ask(suggestion.text)"
                >
                  <span class="shrink-0 flex h-5 w-5 items-center justify-center rounded-full bg-amber-400/20 text-[11px] font-bold text-amber-300">{{ suggestion.index }}</span>
                  <span>{{ suggestion.text }}</span>
                </button>
              </div>
            </div>

            <div v-if="message.citations?.length" class="mt-3.5 border-t border-white/10 pt-3">
              <div class="flex items-center gap-1.5">
                <span class="inline-block h-1.5 w-1.5 rounded-full bg-amber-400" aria-hidden="true" />
                <p class="text-[11px] font-bold uppercase tracking-wider text-amber-300">底层数据证据源</p>
              </div>
              <ul class="mt-2 space-y-1.5">
                <li
                  v-for="(citation, citationIndex) in message.citations"
                  :key="`${citation.title}-${citationIndex}`"
                  class="flex items-start gap-2 text-[12px] leading-5 text-slate-300"
                >
                  <span class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400/80" aria-hidden="true" />
                  <span class="min-w-0 break-words font-medium">{{ privacyMode ? '***' : citation.title }}</span>
                </li>
              </ul>
            </div>
          </div>
        </div>

        <div v-if="loading && !streaming" class="flex items-center gap-2.5 pl-[46px] text-[13px] leading-5 text-amber-200" role="status">
          <span class="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-amber-400/30 border-t-amber-300" aria-hidden="true" />
          <span>正在穿透经营底账与四流证据…</span>
        </div>
      </div>
    </div>

    <!-- 提问输入栏 -->
    <form
      class="mt-3 flex min-h-14 shrink-0 items-center gap-2 rounded-2xl border border-white/15 bg-gradient-to-b from-[#152338] to-[#0d1728] p-1.5 shadow-[0_12px_32px_rgba(0,0,0,0.5)]"
      aria-label="向智策助手提问"
      @submit.prevent="submit"
    >
      <input
        v-model="query"
        type="text"
        inputmode="text"
        autocomplete="off"
        aria-label="问题内容"
        placeholder="向 AI 咨询项目经营、税筹或资金底账..."
        class="min-h-11 min-w-0 flex-1 bg-transparent px-3 text-[14px] leading-5 text-slate-50 placeholder:text-slate-500 focus:outline-none"
      />
      <button
        v-if="!streaming"
        type="submit"
        :disabled="!query.trim() || loading"
        class="min-h-11 shrink-0 rounded-xl bg-gradient-to-r from-amber-400 to-amber-300 px-4 text-[14px] font-bold text-slate-950 shadow-md transition-all hover:brightness-105 active:scale-95 disabled:cursor-not-allowed disabled:opacity-40"
      >
        发送
      </button>
      <button
        v-else
        type="button"
        class="min-h-11 shrink-0 rounded-xl border border-rose-400/40 bg-rose-500/20 px-3.5 text-[14px] font-bold text-rose-200 transition-colors hover:bg-rose-500/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300/80 active:scale-95"
        @click.prevent="cancel"
      >
        停止
      </button>
    </form>
  </main>
</template>

<script setup>
import { ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useCopilotStore } from '../../stores/copilot.store'
import { useExecutiveStore } from '../../stores/executive.store'
import { useUiStore } from '../../stores/ui.store'
import { detectProjectMentions, buildMentionSegments } from '../../utils/mentions'
import MentionChip from '../../shared/components/MentionChip.vue'

const copilot = useCopilotStore()
const executive = useExecutiveStore()
const ui = useUiStore()
const { messages, loading, streaming } = storeToRefs(copilot)
const { privacyMode } = storeToRefs(ui)
const quickQuestions = copilot.quickQuestions
const { projects } = storeToRefs(executive)
const query = ref('')

const segmentCache = new Map()
watch(projects, () => segmentCache.clear(), { deep: true })
watch(privacyMode, () => segmentCache.clear())

/**
 * Parse circled-number follow-up suggestions from AI response text.
 * Matches ①-⑩ followed by suggestion text (2–60 chars), stopping at the
 * next circled digit or newline.  Returns array of { index, text }.
 */
const CIRCLED_DIGITS = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩']
const CIRCLED_PATTERN = new RegExp(
  `[${CIRCLED_DIGITS.join('')}][^${CIRCLED_DIGITS.join('')}\n]{2,60}`,
  'g'
)

function followUpSuggestions(content) {
  if (!content) return []
  const matches = content.match(CIRCLED_PATTERN) || []
  return matches.map(m => {
    const idx = m[0]
    const text = m.slice(1).replace(/[；;。，,、\s]+$/, '').trim()
    return { index: idx, text }
  }).filter(s => s.text.length > 1)
}

/**
 * Replace numeric sequences in AI assistant content with "***" when privacy mode is active.
 */
function maskContent(text) {
  if (!privacyMode.value || !text) return text
  return text.replace(/\b\d+(?:\.\d+)?(?:[万亿]?元?|[%％]?)?\b/g, '***')
}

function renderSegments(message) {
  if (message.role !== 'assistant') {
    const raw = message.content || ''
    return [{ type: 'text', value: privacyMode.value ? maskContent(raw) : raw }]
  }
  const cached = segmentCache.get(message)
  if (cached) return cached
  const chips = detectProjectMentions(message.content || '', projects.value)
  const segments = buildMentionSegments(message.content || '', chips)
  segmentCache.set(message, segments)
  return segments
}

async function drilldown(chip) {
  if (chip.kind !== 'project') return
  try {
    await executive.openProjectByCode(chip.code)
  } catch (error) {
    console.warn('drilldown failed', error)
  }
}

async function ask(question) {
  if (loading.value) return
  segmentCache.clear()
  await copilot.ask(question)
}

async function submit() {
  const question = query.value.trim()
  if (!question || loading.value) return
  query.value = ''
  await ask(question)
}

function cancel() {
  copilot.cancel()
}
</script>
