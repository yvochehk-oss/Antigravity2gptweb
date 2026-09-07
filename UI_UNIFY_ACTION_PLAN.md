# V3.0 UI 统一整改行动方案

**版本：** v1.0
**制定日期：** 2026-09-03
**状态：** 待用户批准后启动

---

## 一、当前阶段与前置条件

### 当前阶段判定

本项目英文黑话清理工作仍在进行中，**尚未达到 UI 整改启动条件**。根据用户指令，UI 统一工作必须满足以下前置条件才能开始：

| 前置条件 | 说明 | 负责方 |
|---|---|---|
| 英文黑话 100% 清理完毕 | 零英文黑话强制规则执行完毕，用户确认关闭 | 用户 / 当前对话 |
| 所有未提交修改已提交至 main | 当前分支待提交的 FVAT 修复与前端调整全部 commit + push | 用户 / 当前对话 |

### 阶段分界线

```
[当前] 英文黑话清理 → commit → push → main
                ↓
[阶段 0] UI 实施前准备 → 新建 feature/v3-ui-unify 分支
                ↓
[阶段 1] Tax 视觉样板 → Luna Max
                ↓
[阶段 2] RAG 视觉样板 → Luna Max
                ↓
[阶段 3] Boss 视觉样板 → Luna Max
                ↓
[阶段 4] 全页面推广 → 三系统串行推进
                ↓
[阶段 5] 测试与验收 → test_fixer → final_reviewer
                ↓
[交付] 合并至 main / 同步到镜像分支
```

---

## 二、阶段 0：UI 实施前准备（人工执行）

### 0.1 当前未提交内容处理

**执行人：** 用户或当前对话

**操作步骤：**

1. 在当前分支 `feature/v3-database-core` 上执行：
   ```bash
   git status
   ```
2. 确认未提交内容性质：
   - 若为 FVAT 后端修复 → 正常 commit，push
   - 若为英文黑话清理 → 正常 commit，push
   - 若混合 → 拆分为独立 commit
3. 将所有已验证修改 commit 并 push 至 `origin/feature/v3-database-core`
4. 确认 main 分支已同步最新代码：
   ```bash
   git checkout main
   git pull origin main
   ```

### 0.2 创建 UI 实施专用分支

**执行人：** 用户或当前对话

**操作步骤：**

```bash
# 从 main 最新切出新分支
git checkout -b feature/v3-ui-unify

# 推送到远程（建立远程分支）
git push -u origin feature/v3-ui-unify
```

**分支命名：** `feature/v3-ui-unify`
**分支基线：** `main` 最新 commit（SHA 由实际执行时确认）
**分支用途：** 仅承载 UI 统一整改，不混入 FVAT 后端修复或其他业务改动

### 0.3 下载并准备 HarmonyOS Sans SC 字体

**执行人：** 用户

**说明：** 方案第五节要求将 HarmonyOS Sans SC（鸿蒙字体）作为本地静态资源引入，实现零 CDN 依赖。字体文件需由用户从合法渠道下载商用授权版本。

**字体准备规范：**

| 项 | 规范 |
|---|---|
| 字体名称 | HarmonyOS Sans SC（Regular / Bold / Medium） |
| 存放路径 | `frontend_stitch/public/fonts/harmony-sans-sc/` |
| 文件格式 | `.woff2`（主推，体积最小）、备 `.woff` + `.ttf` |
| 字体文件命名 | `HarmonyOS_Sans_SC-Regular.woff2`、`HarmonyOS_Sans_SC-Bold.woff2` 等 |
| 授权确认 | 用户自行确保来源为开源商用授权（华为已开源商用） |
| 体积控制 | 单字重 .woff2 建议 ≤ 500KB，三字重合计 ≤ 1.5MB |

**字体引入方式：**

在 `frontend_stitch/src/index.css` 中新增本地字体声明（由 Luna Max 在阶段 1 执行）：

```css
/* 本地静态字体：HarmonyOS Sans SC */
@font-face {
  font-family: "HarmonyOS Sans SC";
  src: url("/assets/fonts/harmony-sans-sc/HarmonyOS_Sans_SC-Regular.woff2") format("woff2");
  font-weight: 400;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "HarmonyOS Sans SC";
  src: url("/assets/fonts/harmony-sans-sc/HarmonyOS_Sans_SC-Bold.woff2") format("woff2");
  font-weight: 700;
  font-style: normal;
  font-display: swap;
}

/* 更新回退链：HarmonyOS Sans SC 作为首选 */
--font-ui: "HarmonyOS Sans SC", "Microsoft YaHei UI",
           "Microsoft YaHei", "PingFang SC", "Segoe UI", Arial, sans-serif;
```

**字体就绪后通知 Luna Max：**
用户将字体文件放入正确路径后，告知 Luna Max "字体文件已就位，可以开始构建"。

### 0.4 阶段 0 验收清单

- [ ] 所有未提交内容已 commit + push 至 main
- [ ] `feature/v3-ui-unify` 分支已创建并推送至 origin
- [ ] `frontend_stitch/public/fonts/harmony-sans-sc/` 目录已就位（字体文件放入后）
- [ ] 英文黑话清理已由用户确认关闭（进入阶段 1 前的门控）

---

## 三、阶段 1：Tax 视觉样板（Luna Max）

**文件所有权：** `frontend_stitch/src/`（不含任何 `.py`、`.env`、数据库文件）
**验收截图节点：** 侧栏 / 顶部栏 / 驾驶舱 / 设计令牌

### 1.1 目标

完成 Tax 前端侧栏、顶部栏、财税驾驶舱和全局设计令牌的统一改造，构建视觉样板并提供截图供用户确认。

### 1.2 范围（精确）

**必须修改的文件（前端）：**
- `frontend_stitch/src/index.css` —— 全局 design tokens（颜色 / 字号 / 间距 / 圆角）
- `frontend_stitch/src/components/Sidebar.tsx` —— 侧栏视觉统一
- `frontend_stitch/src/components/Header.tsx` —— 顶部栏视觉统一
- `frontend_stitch/src/components/DashboardView.tsx` —— 驾驶舱视觉统一
- `frontend_stitch/src/components/SettingsModal.tsx` —— 设置弹窗（若涉及颜色）

**必须保持不动的文件：**
- `frontend_stitch/src/App.tsx` —— 逻辑结构不变，仅视觉调整
- `frontend_stitch/src/api.ts`、`frontend_stitch/src/legalEntityApi.ts` —— 接口层不变
- `frontend_stitch/src/types.ts` —— 类型不变
- 所有后端文件（`app/*.py`、`middleware.py`）
- 所有数据库迁移和 Schema 文件

### 1.3 设计令牌规范（Tax 视觉样板必须遵循）

#### 颜色令牌（新增至 `:root`）

```css
:root {
  /* === 核心设计令牌（Tax + RAG + Boss 统一） === */

  /* 页面背景 */
  --color-bg:         #0b1326;   /* 深海蓝黑 */
  --color-surface:     #1a2540;   /* 卡片表面（深） */
  --color-surface-2:   #1e2d4d;   /* 卡片表面（浅） */
  --color-border:      #2a3a5c;   /* 边框线 */

  /* 主品牌色（单一清亮蓝） */
  --color-brand:       #3b82f6;
  --color-brand-hover: #2563eb;
  --color-brand-muted: rgba(59, 130, 246, 0.15);

  /* 文字 */
  --color-text-primary:   #e8eef7;
  --color-text-secondary: #8899b4;
  --color-text-muted:    #5a6a82;

  /* 语义状态色（仅用于真实状态） */
  --color-success: #22c55e;   /* 绿色：真实成功状态 */
  --color-warning: #f59e0b;   /* 琥珀色：风险提醒 */
  --color-danger:  #ef4444;   /* 红色：错误 / 高风险 */
  --color-info:    #3b82f6;   /* 蓝色：信息 / 默认选中 */

  /* 渐变与特效（大幅减少） */
  --shadow-card: 0 1px 3px rgba(0,0,0,0.3);
  --shadow-elevated: 0 4px 12px rgba(0,0,0,0.4);

  /* === 五级字号 === */
  --font-xs:   12px;  /* 辅助说明、状态标签 */
  --font-sm:   14px;  /* 桌面正文、表格、表单 */
  --font-base: 16px;  /* 卡片标题、重要控件 */
  --font-lg:   20px;  /* 页面标题 */
  --font-kpi:  28px;  /* 关键金额与 KPI（例外） */

  /* 三档数字字号（仅用于金额/KPI 卡片内） */
  --font-num-sm:  20px;
  --font-num-base: 28px;
  --font-num-hero: 48px;

  /* === 间距基准（4px） === */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-6: 24px;
  --space-8: 32px;

  /* === 圆角 === */
  --radius-sm: 6px;
  --radius-base: 8px;
  --radius-lg: 12px;

  /* === 侧栏与顶部栏尺寸 === */
  --sidebar-width: 192px;
  --header-height: 56px;
}
```

#### 颜色禁则

- ❌ 删除所有重复的青色（`#06b6d4` 等）、蓝色（`#3b82f6`/`#60a5fa`/`#93c5fd` 多重变体）、紫色（`#8b5cf6` 等）变体
- ❌ 不使用紫色作为普通功能色
- ❌ 禁止在普通按钮、链接、选中状态使用金色（`#f59e0b` 仅保留给风险警告）
- ❌ 禁止新增 `text-[#xxx]`、`bg-[#xxx]` 等硬编码行内颜色（设计令牌之外）
- ✅ 所有颜色通过 CSS 变量引用，不在 JSX 中直接写十六进制颜色

#### 字号禁则

- ❌ 删除散布的 9px、10.5px、11.5px、13.5px、17px、19px、26px、34px 等特殊字号
- ✅ 严格使用五级字号 + 三档数字字号
- ✅ 金额数字启用 `font-variant-numeric: tabular-nums`（已有，保持）

#### 效果禁则

- ❌ 删除所有 `.glass-panel`（玻璃拟态）
- ❌ 删除所有 `.glow-cyan`、`.glow-amber`（霓虹发光）
- ❌ 删除所有 `.animate-radar-sweep`（雷达扫描动效）
- ✅ 保留 `.animate-slow-pulse`（低频呼吸动效，用于加载状态）
- ✅ 保留 `.chart-grid`（图表网格背景）
- ✅ 保留 `.scrollbar-hide`（隐藏滚动条）

#### 字体禁则

- ❌ 不引入任何 Google Fonts CDN 链接
- ✅ macOS 优先 PingFang SC，Windows 回退 YaHei，字体回退链已通过本地字体声明增强

#### 零英文黑话（本次阶段 1 不涉及文案）

- 视觉样板阶段只处理颜色、字号、间距、布局
- 文案零英文黑话转换在阶段 4 全页面推广时统一执行
- 组件中已有的英文文案（如 `Statutory VAT`、`Projection`）暂时保持，由独立任务处理

### 1.4 测试要求

**阶段 1 完成后必须通过：**

```bash
cd frontend_stitch

# 1. 语法与类型检查
npm run lint

# 2. TypeScript 编译检查
npm run lint   # tsc --noEmit 已含于 lint

# 3. 单元测试（UI 组件测试）
npm run test:ui

# 4. 构建
npm run build

# 5. 验收截图（人工）
#    提供以下截图：
#    - 侧栏（Sidebar）截图：含展开态与折叠态
#    - 顶部栏（Header）截图
#    - 财税驾驶舱（DashboardView）截图
#    - 设计令牌 CSS 变量截图（DevTools Elements 面板）
```

### 1.5 阶段 1 交付物

1. 修改后的 `index.css`（含完整 design tokens）
2. 修改后的 `Sidebar.tsx`
3. 修改后的 `Header.tsx`
4. 修改后的 `DashboardView.tsx`
5. `npm run build` 成功的 `dist/`
6. 四张验收截图（侧栏 × 2、顶部栏、驾驶舱、tokens）
7. 变更说明文档（每文件改动摘要）

---

## 四、阶段 2：RAG 视觉样板（Luna Max）

**文件所有权：** `app/templates/*.html` + `app/static/css/`（若存在）
**验收截图节点：** 登录页 / 基础框架 / 统一 CSS

### 2.1 目标

将 RAG 模板中大量行内字号和颜色收敛到统一 CSS，完成登录页和基础 Dashboard 框架的视觉统一。

### 2.2 范围（精确）

**必须修改的文件：**
- `app/templates/base.html` —— 提取内联 CSS 为统一 design tokens
- `app/templates/login.html` —— 登录页视觉
- `app/templates/home.html` 或主要 dashboard 模板

**必须保持不动：**
- 所有 `*.py` 后端文件
- `middleware.py`
- 数据库模型与迁移
- URL 路由与 JavaScript 请求逻辑

### 2.3 设计令牌（复用阶段 1 规范）

RAG 的 CSS 令牌应与 Tax 保持完全一致的颜色、字号、间距定义，仅在同一 `:root` 作用域内声明。

**禁止新增颜色：**
- 复用 `--color-brand`、`--color-text-primary` 等令牌
- 不引入紫色、金色、霓虹青色作为功能色

### 2.4 测试要求

RAG 为服务端模板，无前端单元测试框架。验收以构建成功 + 截图为准：

```bash
# 1. Python 语法检查（确保模板语法正确）
cd chengdu_construction_tax_system_v1_0
python -m py_compile app/templates/base.html  # Jinja2 语法检查（间接）

# 2. 启动后端服务验证（手动）
#    uvicorn app.main:app --reload
#    浏览器访问 http://localhost:8921
#    截图：登录页 + 主页 + 任一业务页

# 3. 验收截图（人工）
#    - 登录页截图
#    - 主页 / Dashboard 截图
#    - base.html 设计令牌变量截图（浏览器 DevTools）
```

### 2.5 阶段 2 交付物

1. 抽取后的 `base.html`（含统一 design tokens）
2. 更新后的 `login.html` 和 `home.html`
3. 变更说明文档
4. 验收截图（登录页、主页）

---

## 五、阶段 3：Boss 视觉样板（Luna Max）

**文件所有权：** Boss 前端源码（含 Android 去 Google 构建配置）
**验收截图节点：** 登录页 / 高管驾驶舱 / 底部导航

### 5.1 目标

完成 Boss 移动端登录页、驾驶舱和底部导航的统一视觉改造，移除不必要的金色、渐变和玻璃效果。

### 5.2 范围（精确）

**必须修改的文件：**
- Boss 前端组件（`src/` 下的 Vue 文件）
- `index.html`（移除 Google Fonts 声明）
- `manifest.json`（移除 Google Fonts 声明）
- Android 构建配置（如需修改，仅限"去 Google 依赖"方向）

**必须保持不动：**
- Capacitor 配置（除非涉及 Google Services 插件移除）
- 权限逻辑与离线状态逻辑
- 后端 API 路由与认证逻辑

### 5.3 移动端特殊规范

| 项 | 规范 |
|---|---|
| 触控目标 | ≥ 44px |
| 安全区域 | 支持 `env(safe-area-inset-*)` |
| 底部导航 | 固定在屏幕底部，支持 iPhone刘海屏 |
| 宽度适配 | 360px / 390px / 430px 三档无溢出 |
| 字号 | 正文 15px（比桌面大 1px，适应移动阅读） |

### 5.4 测试要求

```bash
# 1. Boss 单元测试
npm run test

# 2. Lint
npm run lint

# 3. Build
npm run build

# 4. Android 构建（可选，如有变更）
#    ./gradlew assembleDebug

# 5. 验收截图（人工）
#    - 登录页（360px / 390px / 430px 各一张）
#    - 高管驾驶舱（390px 为主）
#    - 底部导航截图
```

### 5.5 阶段 3 交付物

1. 修改后的 Boss 前端文件
2. `dist/` 构建产物
3. 变更说明文档
4. 验收截图（3 档宽度 × 3 页面）

---

## 六、阶段 4：全页面推广

**三系统串行推进，每系统完成后独立测试与人工检查，不同时修改重叠文件。**

### 6.1 Tax 全页面推广

**范围：** 阶段 1 视觉样板确认通过后，将 design tokens 推广至剩余 7 个业务视图：

1. `EntityCorporateView.tsx` —— 法人经营画像
2. `ProjectRepositoryView.tsx` —— 项目工程库
3. `ProjectDetailView.tsx` —— 项目详情
4. `TaxLedgerView.tsx` —— 税务台账
5. `AiDecisionCenterView.tsx` —— AI 财税决策中心
6. `RiskCenterView.tsx` —— 风控预警中心
7. `AuditView.tsx` —— 合规审计追溯
8. `DataStatusCard.tsx` —— 数据状态卡片（通用组件）
9. `AiAssistantDrawer.tsx` —— AI 助手抽屉
10. `NewTaxRecordModal.tsx` / `ExportReportModal.tsx` —— 弹窗组件

**推进策略：** 每次 commit 覆盖 1~2 个视图，提供截图，人工确认后再推进下一个。

### 6.2 RAG 全页面推广

**范围：** 将统一 design tokens 推广至剩余模板：

1. `project.html` —— 项目页
2. `risks.html` —— 风险页
3. `audit.html` —— 审计页
4. `ai_review.html` / `ai_review_detail.html` —— AI 审核
5. `ai_models.html` / `ai_prompts.html` —— AI 模型管理
6. `matching.html` —— 匹配页
7. `tasks.html` —— 任务页
8. `health_check.html` / `health_check_detail.html` —— 健康检查
9. `manage.html` / `manager_dashboard.html` / `manager_project*.html` —— 管理后台

### 6.3 Boss 全页面推广

**范围：** 将视觉改造推广至 Boss 剩余页面：

1. 项目列表页
2. 公司经营页
3. AI 助手页
4. 设置页

### 6.4 static_dist 同步要求

| 阶段 | Tax static_dist 同步 | RAG static_dist 同步 | Boss 同步 |
|---|---|---|---|
| 阶段 1 完成后 | ✅ 同步 Tax dist | — | — |
| 阶段 2 完成后 | ✅ 同步 | ✅ RAG 模板更新 | — |
| 阶段 3 完成后 | ✅ 同步 | ✅ 同步 | ✅ Boss 更新 |
| 阶段 4 每批次 | ✅ 同步 | ✅ 同步 | ✅ 同步 |

**同步命令（073 成都建工）：**
```bash
# Tax 前端构建产物同步到后端静态目录
cp -r frontend_stitch/dist/* chengdu_construction_tax_system_v1_0/app/static_dist/

# 确认 middleware.py 已包含 /assets 和 /ui 放行
#（按项目规则，PUBLIC_PREFIXES 已配置）
```

### 6.5 文案零英文黑话

**注意：** 英文黑话清理在阶段 4 推广时统一执行，不在本行动方案范围内，由独立任务处理。

---

## 七、阶段 5：测试与验收

### 7.1 test_fixer 独立回归测试

**执行方：** `test_fixer`（Luna Max 独立角色）

**测试项：**

```bash
# Tax
cd frontend_stitch
npm run lint
npm run test:api
npm run test:ui
npm run build

# RAG（手动启动服务验证）
# uvicorn app.main:app --reload --port 8921
# pytest tests/ -v

# Boss
cd boss_app
npm run test
npm run lint
npm run build
```

**检查项：**
- 功能回归（页面可正常加载，数据可正常读取）
- 构建成功（无编译错误、无类型错误）
- 响应式布局（桌面 1366×768 / 1440×900 / 1920×1080）
- Boss 移动端（360 / 390 / 430px）
- Google 依赖清理验证（`dist/` 中无 Google Fonts 请求）
- 禁改文件完整性（后端 `.py`、`middleware.py` 未被修改）

### 7.2 final_reviewer 只读验收

**执行方：** `final_reviewer`（Sol Medium Read Only）

**验收判定：**

| 判定 | 含义 |
|---|---|
| **PASS** | 全部验收标准满足 |
| **PASS WITH WARNING** | 存在已知局限（如 Windows 字体渲染需实机确认），不影响交付 |
| **FAIL** | 存在阻断问题，需要 Luna Max 返工 |

### 7.3 完整验收标准清单

- [ ] 三套系统使用同一套颜色、字体、字号、间距、状态规则
- [ ] 核心字号不超过五级（三档数字字号仅用于金额/KPI 卡片）
- [ ] 页面不再随意新增颜色（所有颜色通过 CSS 变量引用）
- [ ] 桌面 1366×768 / 1440×900 / 1920×1080 可用
- [ ] Boss 360×800 / 390×844 / 430×932 无溢出
- [ ] Tax lint + 类型检查 + 构建通过
- [ ] Boss 单元测试 + lint + build 通过
- [ ] 构建产物无 Google 网络请求（dist/ 中无 Google Fonts 引用）
- [ ] 后端 `.py`、`middleware.py` 零修改
- [ ] 数据库 Schema 与 Migration 零修改
- [ ] `static_dist` 已同步最新 `dist/`
- [ ] 侧栏宽度 192px / 顶部栏高度 56px 已固定
- [ ] 玻璃拟态、霓虹发光效果已清除
- [ ] 触摸目标 ≥ 44px（Boss）

---

## 八、交付与合并

### 8.1 合并流程

```
feature/v3-ui-unify（阶段 1~5 完成，验收 PASS）
        ↓
PR 合并至 main
        ↓
feature/v3-ui-unify 已合并后，同步到镜像分支（用户操作）
        ↓
方案交付完成
```

### 8.2 同步到镜像分支

验收通过后，同步到 `v3.0-macos` 和 `v3.0-windows`：

```bash
# 方式一：从 main 合并（推荐，UI 改动已在 main）
git checkout v3.0-macos
git merge main
git push origin v3.0-macos

git checkout v3.0-windows
git merge main
git push origin v3.0-windows

# 方式二：cherry-pick UI 相关 commit（如需精确控制）
# git cherry-pick <ui-commit-sha-1> <ui-commit-sha-2> ...
```

### 8.3 交付文档

每次阶段交付须提供：

1. **变更文件清单**（每阶段）
2. **验收截图**（人工截图 + 标注）
3. **测试报告**（lint / typecheck / unit tests / build）
4. **禁改文件完整性确认**（后端 / middleware 未被修改）

最终交付提供：

1. 全阶段变更文件汇总
2. 全部验收截图
3. 全部测试报告
4. static_dist 最终同步确认
5. 镜像分支同步完成确认

---

## 九、禁止事项（铁律）

以下行为在任何阶段均被禁止，违反者立即停止并回滚：

| 禁止项 | 原因 |
|---|---|
| 修改任何 `.py` 后端文件 | 业务逻辑安全边界 |
| 修改 `middleware.py` | 认证授权安全边界 |
| 修改数据库 Schema / Migration | 数据完整性保障 |
| 修改 `.env` 或写入任何 Secret | 安全合规 |
| 引入任何 Google Fonts CDN 链接 | 违反零 Google 依赖要求 |
| 删除或 skip 失败的测试 | 测试标准不可降低 |
| 用硬编码或假数据掩盖不可用状态 | 数据真实性保障 |
| 在 JSX 中直接写 `text-[#xxx]`、`bg-[#xxx]` | 强制使用 CSS 变量令牌 |
| 修改 `App.tsx` 逻辑结构 | 仅允许视觉调整 |

---

## 十、Agent 职责与委派规范

| 角色 | 模型 | 职责 |
|---|---|---|
| 主 Agent | GPT-5.6 Sol Medium | 项目管理、任务委派、进度跟踪、质量复核 |
| 实现 Agent | GPT-5.6 Luna Max | 所有实现工作（前端 / RAG / Boss / 测试） |
| Grok CLI | grok 1.0.5（仅辅助） | 只读代码审查、低风险简单写任务（需主 Agent 明确授权） |
| test_fixer | GPT-5.6 Luna Max | 独立回归测试 |
| final_reviewer | GPT-5.6 Sol Medium（Read Only） | 最终只读验收 |

**委派模板（每次必须包含）：**
- 目标文件所有权（明确列出文件名）
- 范围与验收标准
- 使用 Luna Max 的理由
- 测试要求

---

## 十一、阶段依赖关系

```
阶段 0（准备） ──────────────────────────────────────────────┐
  └─ 当前修改 commit + push                                    │
  └─ 新建 feature/v3-ui-unify 分支                              │
  └─ 下载字体到 public/fonts/                                  │
                                                              ↓
阶段 1（Tax 样板） ── 验收通过 ── 阶段 2（RAG 样板） ── 验收通过 ── 阶段 3（Boss 样板）
                                                              │
                                                              ↓
                              阶段 4（Tax 全推广）──验收──RAG 全推广──验收──Boss 全推广
                                                              │
                                                              ↓
                                                    阶段 5（测试 + 验收）
                                                              │
                                                              ↓
                                                    合并至 main / 同步镜像分支
```

---

## 十二、当前状态与下一步

### 当前状态

| 项 | 状态 |
|---|---|
| 英文黑话清理 | 🔄 进行中（未完成） |
| 当前分支未提交内容 | ⏳ 待处理 |
| feature/v3-ui-unify 分支 | ⏳ 待创建 |
| HarmonyOS Sans SC 字体 | ⏳ 待下载并放入 `public/fonts/harmony-sans-sc/` |
| Tax 视觉样板 | ⏳ 等待阶段 0 完成 |
| RAG 视觉样板 | ⏳ 等待 Tax 样板确认 |
| Boss 视觉样板 | ⏳ 等待 RAG 样板确认 |
| 全页面推广 | ⏳ 等待 Boss 样板确认 |
| 最终测试与验收 | ⏳ 等待推广完成 |

### 下一步（用户操作）

1. **立即：** 完成英文黑话清理，确认关闭
2. **立即：** 将当前未提交内容 commit + push 至 main
3. **字体准备：** 下载 HarmonyOS Sans SC（商用开源版），放入 `frontend_stitch/public/fonts/harmony-sans-sc/`
4. **通知主 Agent：** "阶段 0 已完成，可以开始 UI 实施"，主 Agent 随即执行 `git checkout -b feature/v3-ui-unify` 并启动 Luna Max 阶段 1

---

**方案版本：** v1.0
**下次更新：** 用户确认阶段 0 完成并提供字体文件后
