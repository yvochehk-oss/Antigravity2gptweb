import { ref } from 'vue'
import { defineStore } from 'pinia'
import { askExecutiveCopilot, streamExecutiveCopilot } from '../api/ai.api'
import { PERMISSIONS, useAuthStore } from './auth.store'
import { useUiStore } from './ui.store'

export const useCopilotStore = defineStore('copilot', () => {
  const messages = ref([])
  const loading = ref(false)
  const streaming = ref(false)
  const activeProject = ref(null) // { id, name }

  const quickQuestions = [
    '天府二期真实利润率是多少？',
    '全盘进项发票抵扣池缺口多大？',
    '重庆跨省施工预缴63万凭证核销情况',
    '生成高管月度经营内参简报'
  ]

  /** AbortController for the in-flight stream, if any. */
  let activeAbort = null

  function cancel() {
    if (activeAbort) {
      activeAbort.abort()
      activeAbort = null
    }
    streaming.value = false
    loading.value = false
  }

  function resetToGroup() {
    activeProject.value = null
  }

  function setProject(project) {
    if (!project) {
      activeProject.value = null
    } else {
      activeProject.value = { id: project.id, name: project.name || project.project_code }
    }
  }

  async function ask(message, options = {}) {
    const { stream = true } = options
    const question = String(message || '').trim()
    const auth = useAuthStore()
    if (!question || loading.value) return
    if (!auth.can(PERMISSIONS.USE_COPILOT)) {
      messages.value.push({ role: 'assistant', content: '⚠️ 当前角色无权使用智策引擎' })
      return
    }

    // 若问题明确要求看集团/全盘/重置，清空当前项目聚焦
    const wantsGroup = /集团|全盘|全集团|所有项目|全部项目|切换至集团|回到集团/.test(question)
    if (wantsGroup) {
      activeProject.value = null
    }

    const targetProjectId = options.projectId !== undefined
      ? options.projectId
      : (wantsGroup ? null : activeProject.value?.id)

    messages.value.push({ role: 'user', content: question })
    loading.value = true

    // Placeholder bubble the stream fills in.
    const bubbleIndex = messages.value.length
    messages.value.push({ role: 'assistant', content: '', citations: [] })

    const ui = useUiStore()
    const accessToken = auth.session?.accessToken

    if (stream) {
      streaming.value = true
      activeAbort = new AbortController()
      let dataSource = null
      try {
        let reply = ''
        let citations = []
        for await (const event of streamExecutiveCopilot(ui.serverBaseUrl, question, accessToken, activeAbort.signal, targetProjectId)) {
          if (event.type === 'delta') {
            reply += event.data
            messages.value[bubbleIndex] = { ...messages.value[bubbleIndex], content: reply, citations }
          } else if (event.type === 'citations') {
            citations = event.data
            messages.value[bubbleIndex] = { ...messages.value[bubbleIndex], citations }
          } else if (event.type === 'meta') {
            dataSource = event.data?.data_source
            if (event.data?.project_id) {
              activeProject.value = {
                id: event.data.project_id,
                name: event.data.project_name || '当前项目'
              }
            } else if (event.data?.project_id === null && wantsGroup) {
              activeProject.value = null
            }
          } else if (event.type === 'error') {
            if (!reply) {
              messages.value[bubbleIndex] = {
                role: 'assistant',
                content: 'AI 服务暂时不可用，请稍后再试。',
                citations: []
              }
            }
            break
          }
        }
      } catch {
        messages.value[bubbleIndex] = {
          role: 'assistant',
          content: 'AI 服务暂时不可用，请稍后再试。',
          citations: []
        }
      } finally {
        streaming.value = false
        loading.value = false
        activeAbort = null
      }
      return
    }

    // Non-streaming fallback
    try {
      const response = await askExecutiveCopilot(ui.serverBaseUrl, question, accessToken, targetProjectId)
      if (response?.project_id) {
        activeProject.value = { id: response.project_id, name: response.project_name || '当前项目' }
      }
      messages.value[bubbleIndex] = {
        role: 'assistant',
        content: response?.reply || '智策引擎未返回正文。',
        citations: response?.citations || []
      }
    } catch {
      messages.value[bubbleIndex] = {
        role: 'assistant',
        content: 'AI 服务暂时不可用，请稍后再试。',
        citations: []
      }
    } finally {
      loading.value = false
    }
  }

  function clear() {
    cancel()
    messages.value = []
    activeProject.value = null
  }

  return {
    messages,
    loading,
    streaming,
    quickQuestions,
    activeProject,
    ask,
    cancel,
    clear,
    setProject,
    resetToGroup
  }
})
