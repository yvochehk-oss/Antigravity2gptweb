import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { getCockpitSummary } from '../api/dashboard.api'
import { getCompanyMatrix } from '../api/companies.api'
import { getProject360, getProjects } from '../api/projects.api'
import { readSnapshot, saveSnapshot } from '../offline/snapshot'
import { PERMISSIONS, useAuthStore } from './auth.store'
import { useUiStore } from './ui.store'

export const useExecutiveStore = defineStore('executive', () => {
  const cockpit = ref({})
  const projects = ref([])
  const companies = ref({})
  const activeProject360 = ref(null)
  const lastRefreshAt = ref(null)
  const partialFailure = ref(false)

  /**
   * Tracks where the current data came from:
   *  'live'       — returned from a successful API call
   *  'demo'       — explicitly marked demo data from the backend
   *  'snapshot'   — loaded from offline snapshot
   *  'unavailable' — no data loaded and no snapshot available
   */
  const _dataSource = ref('unavailable')

  const urgentRiskCount = computed(() => (Array.isArray(cockpit.value?.urgent_risks) ? cockpit.value.urgent_risks : []).length)

  async function restoreSnapshots() {
    const auth = useAuthStore()
    const uid = auth.session?.user?.id ?? null
    const r = auth.role?.value ?? auth.session?.user?.role ?? null
    const prev = await readSnapshot('cockpit', null, uid, r)
    const prevProjects = await readSnapshot('projects', null, uid, r)
    const prevCompanies = await readSnapshot('companies', null, uid, r)
    if (prev || prevProjects?.length || prevCompanies?.matrix) {
      _dataSource.value = 'snapshot'
    }
    cockpit.value = prev || {}
    projects.value = prevProjects || []
    companies.value = prevCompanies || {}
  }

  async function refresh() {
    const ui = useUiStore()
    const auth = useAuthStore()
    const accessToken = auth.session?.accessToken
    const uid = auth.session?.user?.id ?? null
    const r = auth.role?.value ?? auth.session?.user?.role ?? null
    ui.loading = true
    ui.connectionStatus = 'connecting'
    partialFailure.value = false

    const fetches = []

    if (auth.can(PERMISSIONS.VIEW_COCKPIT)) {
      fetches.push({ key: 'cockpit', promise: getCockpitSummary(ui.serverBaseUrl, accessToken) })
    }
    if (auth.can(PERMISSIONS.VIEW_PROJECTS)) {
      fetches.push({ key: 'projects', promise: getProjects(ui.serverBaseUrl, accessToken) })
    }
    if (auth.can(PERMISSIONS.VIEW_COMPANIES)) {
      fetches.push({ key: 'companies', promise: getCompanyMatrix(ui.serverBaseUrl, accessToken) })
    }

    if (fetches.length === 0) {
      ui.connectionStatus = 'connected'
      ui.loading = false
      return
    }

    const results = await Promise.allSettled(fetches.map(f => f.promise))
    const resultsMap = {}
    fetches.forEach((f, i) => { resultsMap[f.key] = results[i] })

    const hasFailure = results.some(result => result.status === 'rejected')
    const hasSuccess = results.some(result => result.status === 'fulfilled')
    const allFailed = !hasSuccess

    const SLOTS = [
      { key: 'cockpit', state: cockpit, fallback: {} },
      { key: 'projects', state: projects, fallback: [] },
      { key: 'companies', state: companies, fallback: {} }
    ]
    for (const { key, state, fallback } of SLOTS) {
      const result = resultsMap[key]
      if (!result) continue
      if (result.status === 'fulfilled') {
        state.value = result.value || fallback
        if (ui.offlineCacheEnabled) await saveSnapshot(key, state.value, uid, r)
      } else if (ui.offlineCacheEnabled) {
        state.value = await readSnapshot(key, state.value, uid, r)
        partialFailure.value = true
      }
    }

    // Derive _dataSource from the backend-supplied _meta.data_source flag.
    // Priority: unavailable (all failed, no snapshot) → demo → live.
    // Unknown source without an explicit backend flag is treated as unavailable.
    if (allFailed) {
      _dataSource.value = cockpit.value?.kpi ? 'snapshot' : 'unavailable'
    } else if (cockpit.value?._meta?.data_source === 'demo') {
      _dataSource.value = 'demo'
    } else if (cockpit.value?._meta?.data_source === 'live') {
      _dataSource.value = 'live'
    } else {
      // Backend did not provide _meta.data_source — treat as unavailable rather
      // than optimistically assuming live to avoid displaying real figures
      // when the source is unknown or the response is malformed.
      _dataSource.value = 'unavailable'
    }

    ui.connectionStatus = hasFailure ? 'offline' : 'connected'
    ui.loading = false
    lastRefreshAt.value = new Date().toISOString()

    if (allFailed) throw results.find(result => result.status === 'rejected')?.reason
  }

  async function openProject(projectId) {
    const ui = useUiStore()
    const auth = useAuthStore()
    if (!auth.can(PERMISSIONS.VIEW_PROJECTS)) {
      throw new Error('当前角色无权访问项目详情')
    }
    activeProject360.value = await getProject360(ui.serverBaseUrl, projectId, auth.session?.accessToken)
  }

  async function openProjectByCode(projectCode) {
    const project = projects.value.find(item => item.project_code === projectCode)
    if (project) await openProject(project.id)
  }

  function closeProject() {
    activeProject360.value = null
  }

  return {
    cockpit,
    projects,
    companies,
    activeProject360,
    lastRefreshAt,
    urgentRiskCount,
    partialFailure,
    _dataSource,
    refresh,
    restoreSnapshots,
    openProject,
    openProjectByCode,
    closeProject
  }
})
