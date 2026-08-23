import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { getCockpitSummary } from '../api/dashboard.api'
import { getCompanyMatrix } from '../api/companies.api'
import { getProject360, getProjects } from '../api/projects.api'
import { readSnapshot, saveSnapshot } from '../offline/snapshot'
import { PERMISSIONS, useAuthStore } from './auth.store'
import { useUiStore } from './ui.store'

export const DEFAULT_COCKPIT = {
  as_of_date: '2026-08-21',
  kpi: {
    real_profit: 39300000,
    revenue_recognized: 182000000,
    contract_total: 818000000,
    tax_burden_rate: '1.94',
    net_tax_liability: 3530000,
    net_cashflow: 52400000,
    collection_rate: '74.5',
    gross_margin: '21.6'
  },
  urgent_risks: [
    {
      id: 1,
      project_code: 'YB-DEMO-001',
      project_name: '宜宾示范工业项目',
      title: '四流合一发票与资金流向偏离预警',
      desc: '跨省预缴税款 63 万凭据核销延迟，劳务分包发票抵扣池存在 12 万元差额。',
      action: '穿透税局核销凭据',
      level: 'danger'
    },
    {
      id: 2,
      project_code: 'CD-TF-001',
      project_name: '成都天府二期大厦',
      title: '钢筋进项抵扣池集中到票',
      desc: '本月由玖硕商贸集采 1800 吨螺纹钢，已完成 13% 进项专票 100% 勾选核销。',
      action: '查看金税抵扣证明',
      level: 'warning'
    }
  ],
  trends: {
    months: ['3月', '4月', '5月', '6月', '7月', '8月'],
    revenue: [22, 28, 32, 29, 35, 36],
    profit: [4.5, 5.8, 6.9, 6.2, 7.8, 8.1]
  }
}

export const DEFAULT_PROJECTS = [
  {
    id: 1,
    project_code: 'YB-DEMO-001',
    name: '宜宾示范工业项目',
    tax_method: '一般计税 (9%)',
    risk_status: '中危 (四流偏离)',
    contract_amount: 145000000,
    real_profit: 8700000,
    gross_margin: 22.6,
    collection_rate: 82.5,
    progress_pct: 68,
    location: '四川省宜宾市三江新区临港开发区'
  },
  {
    id: 2,
    project_code: 'CD-TF-001',
    name: '成都天府国际金融中心二期大厦工程',
    tax_method: '一般计税 (9%)',
    risk_status: '低危 (正常推进)',
    contract_amount: 120000000,
    real_profit: 9500000,
    gross_margin: 22.62,
    collection_rate: 78.0,
    progress_pct: 75,
    location: '成都市天府新区兴隆湖总部基地二期'
  },
  {
    id: 3,
    project_code: 'CQ-KS-003',
    name: '重庆江北跨省施工综合体项目',
    tax_method: '异地施工预缴 (2%)',
    risk_status: '关注 (预缴核销)',
    contract_amount: 210000000,
    real_profit: 16800000,
    gross_margin: 20.0,
    collection_rate: 72.0,
    progress_pct: 82,
    location: '重庆市江北区金融城核心标段'
  },
  {
    id: 4,
    project_code: 'CD-GX-004',
    name: '成都高新区生物医药创新产业园',
    tax_method: '一般计税 (9%)',
    risk_status: '低危 (正常推进)',
    contract_amount: 98000000,
    real_profit: 7200000,
    gross_margin: 21.8,
    collection_rate: 90.0,
    progress_pct: 90,
    location: '成都市高新西区生物城中路'
  },
  {
    id: 5,
    project_code: 'CD-WH-005',
    name: '成都武侯智慧物流港项目',
    tax_method: '简易计税 (3%)',
    risk_status: '低危 (正常推进)',
    contract_amount: 160000000,
    real_profit: 11200000,
    gross_margin: 18.5,
    collection_rate: 65.0,
    progress_pct: 45,
    location: '成都市武侯区太平寺路'
  },
  {
    id: 6,
    project_code: 'CD-JN-006',
    name: '成都金牛区城市更新旧改一期',
    tax_method: '一般计税 (9%)',
    risk_status: '低危 (正常推进)',
    contract_amount: 85000000,
    real_profit: 6100000,
    gross_margin: 22.0,
    collection_rate: 55.0,
    progress_pct: 30,
    location: '成都市金牛区蓉北商贸大道'
  }
]

export const DEFAULT_COMPANIES = {
  matrix: {
    A: {
      title: 'A 类 · 施工总承包主体 (11家)',
      desc: '核心施工总承包法人与独立核算分公司，贡献 85.4% 建筑总产值。',
      entities: [
        { id: 1, entity_code: 'A01', name: '中镌（湖北）建筑有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '郭让', registered_capital: '2000万', uscc: '91420105MA49M***', note: '郭让个人往来396000 · 李朝欣往来400000' },
        { id: 2, entity_code: 'A02', name: '四川锐宝建设工程有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '洪凯', registered_capital: '5000万', uscc: '91510100MA6C***', note: '核心总包资质主体' },
        { id: 3, entity_code: 'A03', name: '成都帆亿建筑工程有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '李朝欣', registered_capital: '3000万', uscc: '91510107MA6D***', note: '房屋建筑总承包壹级' },
        { id: 4, entity_code: 'A04', name: '四川中恒腾鸣建筑工程有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '谢润林', registered_capital: '1000万', uscc: '91510107MA6E***', note: '谢润林个人往来297440' },
        { id: 5, entity_code: 'A05', name: '成都天府建工第七工程分公司', legal_entity: 0, risk_status: '合规正常', legal_representative: '王建国', registered_capital: '分支机构', uscc: '91510100MA6F***', note: '天府国际金融中心二期主力承建' },
        { id: 6, entity_code: 'A06', name: '成都建工第八工程分公司', legal_entity: 0, risk_status: '合规正常', legal_representative: '陈志强', registered_capital: '分支机构', uscc: '91510100MA6G***', note: '宜宾示范工业项目承建分部' },
        { id: 7, entity_code: 'A07', name: '四川鼎固建筑工程有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '张立新', registered_capital: '2000万', uscc: '91510100MA6H***', note: '市政公用工程总承包' },
        { id: 8, entity_code: 'A08', name: '成都筑城建设有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '周鹏', registered_capital: '1500万', uscc: '91510100MA6J***', note: '钢结构与幕墙专业承包' },
        { id: 9, entity_code: 'A09', name: '四川蜀道路桥工程有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '赵刚', registered_capital: '3000万', uscc: '91510100MA6K***', note: '公路与路基工程承包' },
        { id: 10, entity_code: 'A10', name: '成都天府华西建设分公司', legal_entity: 0, risk_status: '合规正常', legal_representative: '孙明', registered_capital: '分支机构', uscc: '91510100MA6L***', note: '高新生物城标段施工' },
        { id: 11, entity_code: 'A11', name: '湖北楚天建工工程部', legal_entity: 0, risk_status: '合规正常', legal_representative: '刘强', registered_capital: '分支机构', uscc: '91420100MA6M***', note: '跨省华中区域业务部' }
      ]
    },
    B: {
      title: 'B 类 · 物资商贸集采主体 (10家)',
      desc: '物资集中采购与供应链商贸平台，构建 ¥1288万 进项税额抵扣池。',
      entities: [
        { id: 12, entity_code: 'B01', name: '四川乾润和贸易有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '张燕', registered_capital: '2000万', uscc: '91510106MA6N***', note: '张燕个人往来2828870 · 钢筋集采核心' },
        { id: 13, entity_code: 'B02', name: '成都玖硕商贸有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '黄建军', registered_capital: '1000万', uscc: '91510100MA6P***', note: '水泥沙石集中供应链' },
        { id: 14, entity_code: 'B03', name: '南宁市青秀区浩宇建材经营部', legal_entity: 0, risk_status: '合规正常', legal_representative: '廖浩宇', registered_capital: '个体工商户', uscc: '92450103MA5N***', note: '辅材与五金配件配送' },
        { id: 15, entity_code: 'B04', name: '成都宏泰物资有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '吴华', registered_capital: '1500万', uscc: '91510100MA6Q***', note: '铝模与爬架耗材供应' },
        { id: 16, entity_code: 'B05', name: '四川恒利物资贸易有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '徐磊', registered_capital: '800万', uscc: '91510100MA6R***', note: '商品混凝土集采与地磅结算' },
        { id: 17, entity_code: 'B06', name: '成都鑫源建材实业有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '郑伟', registered_capital: '1200万', uscc: '91510100MA6S***', note: '电缆与机电安装耗材' },
        { id: 18, entity_code: 'B07', name: '四川万盛钢材经销部', legal_entity: 0, risk_status: '合规正常', legal_representative: '冯勇', registered_capital: '分支机构', uscc: '91510100MA6T***', note: '型钢与特种钢直供' },
        { id: 19, entity_code: 'B08', name: '成都天府建材配送中心', legal_entity: 0, risk_status: '合规正常', legal_representative: '邓波', registered_capital: '分支机构', uscc: '91510100MA6U***', note: '园区最后一公里配送' },
        { id: 20, entity_code: 'B09', name: '四川润泽水泥商贸公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '唐敏', registered_capital: '600万', uscc: '91510100MA6V***', note: '高标号水泥专供' },
        { id: 21, entity_code: 'B10', name: '湖北宏达建材贸易有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '曹俊', registered_capital: '1000万', uscc: '91420100MA6W***', note: '华中物资联调仓' }
      ]
    },
    C: {
      title: 'C 类 · 建筑劳务分包主体 (2家)',
      desc: '专业建筑劳务分包主体，100% 接入四川省农民工工资实名制监管专户与完税闭环。',
      entities: [
        { id: 22, entity_code: 'C01', name: '四川本盛建筑劳务有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '李本盛', registered_capital: '500万', uscc: '91510100MA6X***', note: '模板工与砌筑工实名用工' },
        { id: 23, entity_code: 'C02', name: '成都灏琅建筑劳务有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '王灏琅', registered_capital: '500万', uscc: '91510100MA6Y***', note: '钢筋工与混凝土浇筑劳务' }
      ]
    },
    D: {
      title: 'D 类 · 机械租赁与特种设备 (3家)',
      desc: '塔吊、施工升降机与大型工程机械租赁平台，综合出租率达 88.5%。',
      entities: [
        { id: 24, entity_code: 'D01', name: '四川乾润和机械设备租赁有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '张泽明', registered_capital: '200万', uscc: '91510106MA6Z***', note: '洪凯个人往来27645 · 塔吊及大型机械' },
        { id: 25, entity_code: 'D02', name: '成都天府重型机械租赁有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '罗永强', registered_capital: '500万', uscc: '91510100MA70***', note: '旋挖钻机与特种起重机' },
        { id: 26, entity_code: 'D03', name: '四川蜀安机械吊装有限公司', legal_entity: 1, risk_status: '合规正常', legal_representative: '朱海', registered_capital: '300万', uscc: '91510100MA71***', note: '施工升降机与吊装维保' }
      ]
    }
  }
}

export function generateDefaultProject360(projectId) {
  const all = Array.isArray(DEFAULT_PROJECTS) ? DEFAULT_PROJECTS : []
  const project = all.find(p => p.id === Number(projectId) || p.project_code === String(projectId)) || all[0] || {
    id: 1,
    project_code: 'CD-TF-001',
    name: '成都天府国际金融中心二期大厦工程',
    tax_method: '一般计税 (9%)',
    risk_status: '低危 (正常推进)',
    contract_amount: 120000000,
    real_profit: 9500000,
    gross_margin: 22.62,
    location: '成都市天府新区兴隆湖总部基地二期'
  }
  const contract = project.contract_amount || 120000000
  const profit = project.real_profit || 9500000
  const cost = contract - profit

  return {
    project: {
      id: project.id,
      project_code: project.project_code,
      name: project.name,
      tax_method: project.tax_method,
      risk_status: project.risk_status,
      location: project.location
    },
    financial_penetration: {
      contract_amount: contract,
      real_profit: profit,
      gross_margin_pct: project.gross_margin || 22.62,
      actual_cost: Math.round(cost * 0.72),
      eac_forecast_cost: Math.round(cost)
    },
    cost_breakdown: {
      materials: { label: '材料采购', amount: Math.round(cost * 0.58), pct: 58 },
      labor: { label: '建筑劳务分包', amount: Math.round(cost * 0.28), pct: 28 },
      equipment: { label: '机械与塔吊租赁', amount: Math.round(cost * 0.14), pct: 14 }
    },
    document_list: [
      { id: 1, filename: `${project.name || '工程'}施工总承包主合同.pdf`, document_type: '总包主合同' },
      { id: 2, filename: '金税四期增值税专用发票进项清单.xlsx', document_type: '金税发票抵扣链' },
      { id: 3, filename: '四川省建筑劳务工资专户对公流水.pdf', document_type: '银行资金对公回单' },
      { id: 4, filename: '现场智能地磅过磅称重与物资入库联单.pdf', document_type: '物资出入库闭环' }
    ]
  }
}

/** Returns true when the backend explicitly marks the response as demo data. */
function isDemoData(data) {
  return data?._meta?.data_source === 'demo'
}

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
   *  'demo'       — default mock data (e.g. DEFAULT_COCKPIT kpi values)
   *  'snapshot'   — loaded from offline snapshot
   *  'unavailable' — no data loaded and no snapshot available
   */
  const _dataSource = ref('unavailable')

  const urgentRiskCount = computed(() => (cockpit.value?.urgent_risks || DEFAULT_COCKPIT.urgent_risks).length)

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
