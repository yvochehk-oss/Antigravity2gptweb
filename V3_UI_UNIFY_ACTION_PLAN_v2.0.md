# V3.0 UI 统一整改行动方案（v2.0）

**版本：** v2.0  
**制定日期：** 2026-09-03  
**状态：** 可执行  
**正式基线：** `main @ 630e56a`  
**适用范围：** Tax 前端、RAG Web、Boss 移动端  
**核心目标：** 统一视觉设计系统、保持七大板块固定排版契约、持续零英文黑话、实现三端一致的商业化 UI 体验

---

# 一、当前基线与已完成事项

## 1.1 正式代码基线

当前主线已完成合并并推送：

```text
Repository : yvochehk-oss/chengdu-construction-tax-system-v2.0
Branch     : main
HEAD       : 630e56a
Commit     : feat(frontend): bundle localized static_dist and polish sidebar model health tooltip
```

本轮 UI 统一整改必须从该主线基线切出独立分支，不再以 `feature/v3-database-core` 为默认起点。

## 1.2 已完成并关闭的前置工作

以下事项已完成，不再作为本方案启动前置条件：

- ✅ 英文黑话清理：FINAL PASS / CLOSED
- ✅ 七大业务板块统一标题排版：FINAL PASS / CLOSED
- ✅ `data-page-title` / `data-page-controls` 守门规则已建立
- ✅ `UnifiedPageLayout.ui.test.ts` 已纳入测试
- ✅ `npm run test:api`：40 / 40 PASS
- ✅ `npm run test:ui`：68 / 68 PASS
- ✅ 前端总计：108 / 108 PASS
- ✅ `npm run build`：PASS
- ✅ Chrome 七大业务板块视觉终验：PASS
- ✅ 用户界面英文黑话扫描：0
- ✅ `static_dist` 已包含当前本地化产物

因此，本方案从 **UI 视觉统一实施准备** 直接开始。

---

# 二、项目级硬约束

本方案执行期间，必须继续遵守项目根本约束文件 `AGENTS.md` 中已生效的规则。

## 2.1 七大业务板块固定排版铁律

七大业务板块：

1. 集团经营总览
2. 法人经营画像
3. 法人法定税务
4. 项目工程库
5. 智能财税决策中心
6. 风控中心
7. 合规审计

必须持续满足：

```text
页面最上方
┌──────────────────────────────┐
│ data-page-title              │
│ 图标 + 主标题 + 副标题       │
└──────────────────────────────┘

下一独立区域
┌──────────────────────────────┐
│ data-page-controls           │
│ 筛选 / 操作 / 标签 / 切换    │
└──────────────────────────────┘
```

强制规则：

- `data-page-title` 必须独立存在
- `data-page-controls` 有需要时必须独立存在
- title 不得嵌套 controls
- controls 不得嵌套 title
- title 必须位于 controls 之前
- 禁止为了视觉方便重新把标题与筛选器放回同一 Flex 行
- `UnifiedPageLayout.ui.test.ts` 必须持续 PASS

## 2.2 零英文黑话持续守门

英文黑话清理已经完成，因此本轮 UI 工作的规则不是“以后再汉化”，而是：

> **任何视觉重构都不得新增、恢复或重新暴露用户可见英文黑话。**

底层 TypeScript 字段、API 枚举、数据库代码可保持原值，但不能直接显示给用户。

以下类型的词不得以原始英文直接出现在业务界面：

```text
Projection
VAT
CIT
READY
DEGRADED
UNAVAILABLE
NOT AVAILABLE
SCENARIO
SIMULATION
NOT FILING BASIS
FACT-BASED
Run
LEGAL_ENTITY_PROJECTION
LEGAL_ENTITY_STATUTORY
PROJECT_BOUNDARY
INPUT_VAT_DEDUCTIBILITY_NEEDS_REVIEW
```

必须继续通过中文业务语义层展示。

## 2.3 禁改业务边界

本轮 UI 统一整改原则上属于表现层改造。

默认禁止：

- 修改后端 `.py` 业务逻辑
- 修改 `middleware.py`
- 修改数据库 Schema / Migration
- 修改 `.env`
- 修改 Secret
- 改变 API URL 与请求协议
- 改变 Formal VAT 契约
- 改变领域状态流
- 用假数据掩盖不可用状态
- skip / 删除失败测试
- 降低既有测试标准

---

# 三、总体实施路线

```text
正式基线
main @ 630e56a
108 / 108 PASS
零英文黑话 CLOSED
七大标题统一 CLOSED
        │
        ▼
阶段 UI-0
实施准备 / 新建 feature/v3-ui-unify
字体准备 / 基线截图 / 设计令牌方案锁定
        │
        ▼
阶段 UI-1
Tax 视觉样板
Design Tokens + Sidebar + Header + Dashboard
        │
        ▼
人工视觉确认
        │
        ▼
阶段 UI-2
Tax 七大页面与通用组件全推广
        │
        ▼
阶段 UI-3
RAG 样板 + 全页面推广
        │
        ▼
阶段 UI-4
Boss 样板 + 全页面推广
        │
        ▼
阶段 UI-5
三系统全量测试 / Chrome / 响应式 / static_dist
        │
        ▼
最终验收
        │
        ▼
PR / merge main
        │
        ▼
同步 v3.0-macos / v3.0-windows
```

---

# 四、阶段 UI-0：实施前准备

## 4.1 创建专用分支

从最新主线切出：

```bash
git checkout main
git pull --ff-only origin main

git checkout -b feature/v3-ui-unify
git push -u origin feature/v3-ui-unify
```

分支用途：

> 仅承载 UI Design System、样式统一、响应式改造、字体和静态产物同步。

禁止混入：

- FVAT 后端修复
- 数据库迁移
- 新业务功能
- 新 API
- 领域模型重构

## 4.2 保存正式 UI 基线截图

开始改造前保存：

- 集团经营总览
- 法人经营画像
- 法人法定税务
- 项目工程库
- 智能财税决策中心
- 风控中心
- 合规审计

建议分辨率：

```text
1366 × 768
1440 × 900
1920 × 1080
```

目的：

- 视觉前后对比
- 防止功能区域在 UI 重构后丢失
- 验证标题位置不回退

## 4.3 HarmonyOS Sans SC 字体准备

### 目录规范

统一使用：

```text
frontend_stitch/public/fonts/harmony-sans-sc/
```

建议文件：

```text
HarmonyOS_Sans_SC-Regular.woff2
HarmonyOS_Sans_SC-Medium.woff2
HarmonyOS_Sans_SC-Bold.woff2
```

实际使用前必须以字体发行包内 LICENSE 为准确认授权。

### CSS 路径

因为字体位于 `public/fonts/...`，Vite 构建后访问路径应使用：

```css
url("/fonts/harmony-sans-sc/HarmonyOS_Sans_SC-Regular.woff2")
```

不得写成：

```css
/assets/fonts/...
```

除非字体目录真的改成 `public/assets/fonts/...`。

### 字重建议

第一阶段优先：

- 400 Regular
- 500/600 Medium

700 Bold 按实际需要加载。

不将“单字重 ≤ 500KB”设为硬性门槛。中文字体体积应以实际产物为准，可后续做子集化优化。

## 4.4 UI-0 验收清单

- [ ] main 基线确认：630e56a
- [ ] `feature/v3-ui-unify` 已创建
- [ ] baseline screenshots 已保存
- [ ] 字体目录建立
- [ ] 字体授权文件已确认
- [ ] 字体 URL 方案确认
- [ ] 设计 Token 命名方案锁定
- [ ] 108 / 108 测试在新分支基线上仍可通过

---

# 五、阶段 UI-1：Tax 视觉样板

## 5.1 目标

先只建立一套可靠的 Tax 视觉样板，不立即重构所有业务页。

首批范围：

- 全局 Design Tokens
- 侧边栏
- 顶部栏
- 集团经营总览

用户确认视觉方向后再推广。

## 5.2 首批文件范围

允许修改：

```text
frontend_stitch/src/index.css
frontend_stitch/src/components/Sidebar.tsx
frontend_stitch/src/components/Header.tsx
frontend_stitch/src/components/DashboardView.tsx
frontend_stitch/src/components/SettingsModal.tsx
```

相关 UI 测试允许同步调整。

### App.tsx 特殊规则

`App.tsx` 不是绝对禁止修改，但仅允许：

- className
- 纯视觉 wrapper
- 非业务语义布局容器

禁止修改：

- API 调用
- useEffect
- 数据加载逻辑
- 状态生命周期
- tab / routing 语义
- Formal VAT rebuild 流程
- domain error isolation

## 5.3 Design Tokens

统一写入 `:root`。

```css
:root {
  /* 页面结构 */
  --color-bg: #0b1326;
  --color-surface: #1a2540;
  --color-surface-2: #1e2d4d;
  --color-border: #2a3a5c;

  /* 品牌 */
  --color-brand: #3b82f6;
  --color-brand-hover: #2563eb;
  --color-brand-muted: rgba(59, 130, 246, 0.15);

  /* 文字 */
  --color-text-primary: #e8eef7;
  --color-text-secondary: #8899b4;
  --color-text-muted: #5a6a82;

  /* 真实语义状态 */
  --color-success: #22c55e;
  --color-warning: #f59e0b;
  --color-danger: #ef4444;
  --color-info: #3b82f6;

  /* 阴影 */
  --shadow-card: 0 1px 3px rgba(0, 0, 0, 0.3);
  --shadow-elevated: 0 4px 12px rgba(0, 0, 0, 0.4);

  /* 桌面五级字号 */
  --font-xs: 12px;
  --font-sm: 14px;
  --font-base: 16px;
  --font-lg: 20px;
  --font-kpi: 28px;

  /* 数字 */
  --font-num-sm: 20px;
  --font-num-base: 28px;
  --font-num-hero: 48px;

  /* 间距 */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-6: 24px;
  --space-8: 32px;

  /* 圆角 */
  --radius-sm: 6px;
  --radius-base: 8px;
  --radius-lg: 12px;

  /* Shell */
  --sidebar-width: 192px;
  --header-height: 56px;

  /* 字体 */
  --font-ui:
    "HarmonyOS Sans SC",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Segoe UI",
    Arial,
    sans-serif;
}
```

## 5.4 语义 Utility 层

不建议把：

```text
text-[#4cd7f6]
```

简单替换成：

```text
text-[var(--color-brand)]
```

更推荐建立语义 utility：

```css
.text-primary { color: var(--color-text-primary); }
.text-secondary { color: var(--color-text-secondary); }
.text-muted { color: var(--color-text-muted); }
.text-brand { color: var(--color-brand); }

.bg-page { background: var(--color-bg); }
.bg-surface { background: var(--color-surface); }
.bg-surface-2 { background: var(--color-surface-2); }

.border-default { border-color: var(--color-border); }

.status-success { color: var(--color-success); }
.status-warning { color: var(--color-warning); }
.status-danger { color: var(--color-danger); }
```

这样 JSX 保持业务语义化，而不是继续散落颜色定义。

## 5.5 颜色规则

### 禁止

- JSX 新增 `text-[#xxxxxx]`
- JSX 新增 `bg-[#xxxxxx]`
- JSX 新增 `border-[#xxxxxx]`
- 紫色作为普通功能色
- 金色作为普通按钮或普通选中状态
- 多种近似青色/蓝色重复承担同一功能

### 允许

仅真实状态使用：

```text
绿色：成功
橙色：警告
红色：失败 / 高风险
蓝色：品牌 / 信息 / 默认选中
```

## 5.6 玻璃拟态与发光效果迁移策略

不采用“立即删除 `.glass-panel`”的一刀切方法。

正确流程：

### 第一步

把 `.glass-panel` 的视觉表现重定义为普通 Surface：

```css
.glass-panel {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-card);
  backdrop-filter: none;
}
```

### 第二步

新增：

```css
.surface-card { ... }
```

新代码逐步使用 `.surface-card`。

### 第三步

待所有组件迁移后，再删除 `.glass-panel`。

同理：

```text
.glow-cyan
.glow-amber
.animate-radar-sweep
```

应逐步退役，不应机械删除导致布局或状态提示损坏。

## 5.7 字号规则

桌面统一五级正文体系：

```text
12 / 14 / 16 / 20 / 28
```

金额 KPI 允许：

```text
20 / 28 / 48
```

禁止新增：

```text
9
10.5
11.5
13.5
17
19
26
34
```

除非经过明确设计评审。

金额保持：

```css
font-variant-numeric: tabular-nums;
```

## 5.8 阶段 UI-1 测试

```bash
cd frontend_stitch

npm run lint
npm run test:api
npm run test:ui
npm run build
```

必须至少保持：

```text
API：40 / 40
UI ：68 / 68
总计：108 / 108
```

### 人工截图

- Sidebar 展开
- Sidebar 折叠
- Header
- Dashboard 1366
- Dashboard 1440
- Dashboard 1920

用户确认后进入 UI-2。

---

# 六、阶段 UI-2：Tax 全页面推广

## 6.1 推广范围

按批次推进：

### Batch A

```text
EntityCorporateView.tsx
TaxLedgerView.tsx
DataStatusCard.tsx
```

### Batch B

```text
ProjectRepositoryView.tsx
ProjectDetailView.tsx
```

### Batch C

```text
AiDecisionCenterView.tsx
AiReviewView.tsx
TaxPlanningView.tsx
AiAssistantDrawer.tsx
```

### Batch D

```text
RiskCenterView.tsx
AuditView.tsx
EntityVatLineageDrawer.tsx
```

### Batch E

```text
NewTaxRecordModal.tsx
ExportReportModal.tsx
SettingsModal.tsx
其他通用弹窗
```

## 6.2 每批次强制要求

每批次必须：

1. 仅处理 1~3 个业务视图
2. 不改 API
3. 不改业务状态流
4. 不破坏中文展示
5. 不破坏 `data-page-title`
6. 不破坏 `data-page-controls`
7. 更新对应 UI tests
8. 运行：

```bash
npm run lint
npm run test:api
npm run test:ui
npm run build
```

9. 提供截图
10. 用户确认后再推进下一批

## 6.3 七大页面统一布局验收

重点检查：

- 左边距一致
- 页面标题顶部基线一致
- 图标尺寸一致
- 主标题字号一致
- 副标题间距一致
- controls 卡片顶部间距一致
- 宽度变化时标题不位移
- controls 堆叠不会推动标题
- 空态 / 错误态 / READY 状态标题位置完全一致

---

# 七、阶段 UI-3：RAG 样板与全推广

## 7.1 目标

将 RAG Web 模板与 Tax Design System 统一：

- 同品牌蓝
- 同 Surface
- 同圆角
- 同状态色
- 同字体
- 同桌面字号语义层级

不是简单复制 JSX 样式，而是复用同一套设计语义。

## 7.2 首批样板

建议：

```text
app/templates/base.html
app/templates/login.html
app/templates/home.html
```

如果有独立 CSS：

```text
app/static/css/
```

优先把 Design Tokens 提取至统一 CSS 文件，而不是继续把大量 style 放在 HTML。

## 7.3 RAG 模板验证方法

原方案中的：

```bash
python -m py_compile app/templates/base.html
```

禁止使用，因为 HTML / Jinja2 不是 Python 文件。

### Python 代码检查

```bash
python -m compileall app
```

### Jinja 模板加载检查

可增加脚本：

```python
from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader("app/templates"))

for template in [
    "base.html",
    "login.html",
    "home.html",
]:
    env.get_template(template)
```

### 更推荐

通过已有服务测试或 TestClient 请求真实路由：

```text
GET login
GET home
GET 任一业务页
```

确认：

- HTTP 正常
- 模板成功渲染
- 无 Jinja UndefinedError
- 无静态资源 404

## 7.4 全推广范围

包括但不限于：

```text
project.html
risks.html
audit.html
ai_review.html
ai_review_detail.html
ai_models.html
ai_prompts.html
matching.html
tasks.html
health_check.html
health_check_detail.html
manage.html
manager_dashboard.html
manager_project*.html
```

每批 2~4 个模板。

## 7.5 RAG 验收

- pytest 全绿
- `python -m compileall app`
- 模板加载测试
- 服务实际启动
- Chrome 截图
- 1366 / 1440 / 1920 无明显布局错误
- 无 Google Fonts 网络请求
- 无新增英文黑话
- 后端业务逻辑零修改

---

# 八、阶段 UI-4：Boss 移动端统一

## 8.1 目标

Boss 应与 Tax / RAG 共用同一个视觉语言，但不强制使用完全相同的字号数值。

统一的是：

- 品牌色
- Surface 层级
- 状态颜色
- 圆角语义
- 字体族
- 间距规则
- 图标风格

移动端允许独立字号值。

## 8.2 移动端字号

建议：

```text
辅助：12px
正文：15px
卡片标题：17px
页面标题：22px
关键数字：28 / 36 / 44px
```

保持与桌面相同的语义层级，但不强求桌面 14px 直接复制到手机。

## 8.3 移动端硬指标

```text
触控目标 ≥ 44px
```

支持：

```css
env(safe-area-inset-top)
env(safe-area-inset-bottom)
env(safe-area-inset-left)
env(safe-area-inset-right)
```

必须验证：

```text
360 × 800
390 × 844
430 × 932
```

## 8.4 去 Google 依赖

检查：

- `index.html`
- manifest
- CSS
- dist
- Android WebView 资源

禁止：

```text
fonts.googleapis.com
fonts.gstatic.com
Google Fonts import
```

如存在 Google Services SDK，应区分：

> 字体/CDN 依赖 ≠ Firebase / Google Services 功能依赖

只有用户明确要求移除 Google Services 时才修改 Android 插件或 Capacitor 配置。

## 8.5 Boss 测试

```bash
npm run test
npm run lint
npm run build
```

如果 Android 构建文件发生变化：

```bash
./gradlew assembleDebug
```

---

# 九、static_dist 与构建产物同步

## 9.1 原则

源码与 8921 实际服务静态产物必须同步。

禁止出现：

```text
src 已更新
但 static_dist 仍是旧版
```

## 9.2 Tax 同步

构建：

```bash
cd frontend_stitch
npm run build
```

同步：

```bash
rm -rf ../chengdu_construction_tax_system_v1_0/app/static_dist/*
cp -R dist/* ../chengdu_construction_tax_system_v1_0/app/static_dist/
```

实际相对路径必须按仓库真实目录确认。

## 9.3 建议脚本化

建议新增类似：

```text
scripts/sync_static_dist.sh
```

或 package script：

```json
{
  "scripts": {
    "build:deploy": "vite build && <sync-command>"
  }
}
```

目标：

- 避免手工漏同步
- 避免 Chrome 实测命中旧 bundle
- 保证 main 包含可部署静态产物

---

# 十、测试与验收矩阵

## 10.1 Tax

每阶段必须执行：

```bash
npm run lint
npm run test:api
npm run test:ui
npm run build
```

当前不可降低基准：

```text
40 / 40 API
68 / 68 UI
108 / 108 Total
```

最终：

- 8921 静态部署
- Chrome 七大页面扫描
- 1366 × 768
- 1440 × 900
- 1920 × 1080
- 英文黑话扫描 = 0
- 页面标题位移问题 = 0

## 10.2 RAG

```bash
python -m compileall app
pytest -v
```

加：

- Jinja 模板加载
- 服务启动
- 关键页面 HTTP
- 静态资源 404 检查
- Chrome 页面检查

## 10.3 Boss

```bash
npm run test
npm run lint
npm run build
```

可选：

```bash
./gradlew assembleDebug
```

响应式：

```text
360
390
430
```

---

# 十一、视觉验收标准

## 11.1 Design System

- [ ] 三端使用统一品牌蓝
- [ ] 三端使用统一 Surface 逻辑
- [ ] 同类状态使用同一颜色语义
- [ ] 普通功能不使用警告金色
- [ ] 普通功能不使用紫色
- [ ] JSX 不新增硬编码十六进制颜色
- [ ] 新组件必须使用语义 utility / design token
- [ ] 玻璃拟态已退役
- [ ] 霓虹发光已大幅消除
- [ ] 动效只保留有业务意义的低频反馈

## 11.2 字体

- [ ] 无 Google Fonts
- [ ] HarmonyOS Sans SC 本地加载成功
- [ ] Windows 使用字体显示平滑
- [ ] macOS 正常回退
- [ ] font-display: swap
- [ ] 字体 404 = 0

## 11.3 Tax 七大页面

- [ ] 7/7 `data-page-title` 存在
- [ ] controls 独立
- [ ] 标题不随窗口宽度移动
- [ ] loading / empty / degraded / unavailable 标题位置一致
- [ ] 中文业务文案无回退
- [ ] 英文黑话扫描 = 0

## 11.4 响应式

Desktop：

- [ ] 1366×768
- [ ] 1440×900
- [ ] 1920×1080

Boss：

- [ ] 360×800
- [ ] 390×844
- [ ] 430×932

---

# 十二、禁止事项

以下任一情况发生，应立即停止并回滚：

| 禁止项 | 原因 |
|---|---|
| 修改后端核心 `.py` 业务逻辑 | UI 项目越界 |
| 修改 `middleware.py` | 认证安全边界 |
| 修改 Schema / Migration | 数据完整性 |
| 修改 `.env` / Secret | 安全合规 |
| 恢复 Google Fonts | 违反本地资源要求 |
| 删除或 skip 测试 | 降低验收标准 |
| 用静态假数据掩盖接口异常 | 破坏事实可信性 |
| 恢复英文黑话 | 违反 AGENTS.md |
| 破坏 `data-page-title` | 违反七页面布局契约 |
| 在 JSX 新增硬编码色 | 破坏 Design Token |
| 修改 Formal VAT 数据契约 | 超出 UI 范围 |
| 为视觉需求修改领域模型 | 架构越界 |

---

# 十三、Commit 策略

禁止一次性把全部三系统塞入一个巨大提交。

推荐：

```text
Commit 1
chore(ui): add shared tax design tokens

Commit 2
style(frontend): unify sidebar and header

Commit 3
style(frontend): apply dashboard visual baseline

Commit 4
style(frontend): unify legal entity and statutory tax views

Commit 5
style(frontend): unify project views

Commit 6
style(frontend): unify decision and risk workspaces

Commit 7
style(rag): apply shared web design system

Commit 8
style(boss): apply mobile executive design system

Commit 9
build(frontend): sync localized static distribution
```

每个 commit 必须：

- 单一职责
- 可独立回滚
- 通过对应测试
- 不混业务逻辑

---

# 十四、Agent 协同规范

## 14.1 主控制面

职责：

- 锁定范围
- 分配批次
- 检查 diff
- 验收测试
- 防止架构越界

## 14.2 实现 Agent

职责：

- UI 实现
- Design Token 迁移
- 样式重构
- 对应测试修复

禁止自行改业务边界。

## 14.3 test_fixer

职责：

- 独立执行全量测试
- 不通过降测试标准来修复
- 只修真实回归

## 14.4 final_reviewer

只读验收：

```text
PASS
PASS WITH WARNING
FAIL
```

不得边审边改。

---

# 十五、阶段门控

## UI-1 → UI-2

必须：

- Tax 108 / 108
- lint PASS
- build PASS
- Sidebar / Header / Dashboard 用户确认

## UI-2 → UI-3

必须：

- Tax 全页面 token 化完成
- 7/7 标题守门 PASS
- 英文黑话 = 0
- Chrome 三分辨率无阻断问题

## UI-3 → UI-4

必须：

- RAG 样板 + 全推广完成
- pytest PASS
- 模板加载 PASS
- Chrome PASS

## UI-4 → UI-5

必须：

- Boss test/lint/build PASS
- 三档手机宽度 PASS

## UI-5 → main

必须：

- 三端测试 PASS
- `static_dist` 同步
- 无 Google Fonts
- 无后端越界修改
- final_reviewer = PASS

---

# 十六、最终交付

## 16.1 合并流程

```text
feature/v3-ui-unify
        ↓
最终测试
        ↓
final_reviewer PASS
        ↓
PR
        ↓
merge main
```

## 16.2 镜像分支

main 合并后：

```bash
git checkout v3.0-macos
git merge main
git push origin v3.0-macos

git checkout v3.0-windows
git merge main
git push origin v3.0-windows
```

优先 merge main，而不是大量 cherry-pick。

## 16.3 最终交付物

1. 全阶段变更文件清单
2. Design Token 文档
3. Tax 七大页面截图
4. RAG 页面截图
5. Boss 三宽度截图
6. Tax 108 / 108 测试报告
7. RAG 测试报告
8. Boss 测试报告
9. build 报告
10. `static_dist` 同步确认
11. Google 依赖扫描结果
12. 后端 / DB 零越界确认
13. main merge commit
14. macOS / Windows 镜像同步结果

---

# 十七、当前状态与下一步

## 当前状态

| 项目 | 状态 |
|---|---|
| main 主线同步 | ✅ 已完成 |
| main 基线 | ✅ `630e56a` |
| 英文黑话清理 | ✅ FINAL PASS / CLOSED |
| 七大板块标题统一 | ✅ FINAL PASS / CLOSED |
| Tax 测试 | ✅ 108 / 108 PASS |
| Tax build | ✅ PASS |
| Chrome 七模块 | ✅ PASS |
| `feature/v3-ui-unify` | ⏳ 待创建 |
| HarmonyOS Sans SC | ⏳ 待准备 |
| Tax 视觉样板 | ⏳ 待启动 |
| RAG UI 统一 | ⏳ 待启动 |
| Boss UI 统一 | ⏳ 待启动 |

## 下一步

直接执行：

```bash
git checkout main
git pull --ff-only origin main
git checkout -b feature/v3-ui-unify
git push -u origin feature/v3-ui-unify
```

然后：

1. 准备 HarmonyOS Sans SC 本地字体
2. 保存七大页面 baseline screenshots
3. 启动阶段 UI-1
4. 先只改：
   - `index.css`
   - `Sidebar.tsx`
   - `Header.tsx`
   - `DashboardView.tsx`
5. 完成后运行 108 项全量测试和 build
6. 用户视觉确认后再开始全页面推广

---

**方案版本：** v2.0  
**正式基线：** `main @ 630e56a`  
**状态：** READY FOR EXECUTION
