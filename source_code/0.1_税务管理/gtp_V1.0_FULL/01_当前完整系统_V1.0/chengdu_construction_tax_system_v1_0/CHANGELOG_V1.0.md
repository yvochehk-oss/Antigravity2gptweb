# V1.0 Changelog

## 当前整改口径（2026-08）

- 运行时法人边界已迁移到 canonical Entity 主数据：A01-A11、B01-B10、C01-C02、D01-D03，共 26 家实际单位。
- 其中 25 家为独立法人；A04（四川屹明汇建设工程有限公司重庆分公司）为非独立法人，`parent_entity_code=A03`。
- A/B/C/D 仅保留为 `business_role` 业务角色枚举；任何表单、接口和确定性计算都必须使用完整的实际单位代码。
- 外部交易方使用 `ExternalParty` 的真实名称和外部方代码；无法唯一识别的交易方保持 unresolved/unknown external party，不能用占位主体代替。

## V1.0 性质：V0.2 的完整快照副本

**V1.0 不是独立的功能版本**，而是 V0.2 在归档时的完整快照：

| 字段 | 值 |
|---|---|
| V1.0 包路径 | `gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/` |
| 来源版本 | V0.2（`gtp_V0.2_FULL/01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/`） |
| 代码差异 | **0**（`diff -r` 逐字节一致） |
| 功能改动 | **0**（完全继承 V0.2 的全部功能） |
| Changelog 性质 | 仅说明快照来源与用途 |

## 为什么需要 V1.0 这个名字

V1.0 在本项目目录树中承担以下角色：

1. **快照存档** —— 在不同时刻冻结 V0.2 的完整状态，便于复现 / 调试历史问题
2. **回滚后备** —— 一旦 V0.2 包出现意外损坏，可从 V1.0 包快速恢复
3. **离线分发** —— 与 V0.2 包同时对外分发，下游可直接引用同一份说明
4. **结构对齐** —— 项目目录命名遵循 `gtp_V{主版本}_FULL` 模式，V1.0 作为首版快照承担命名起始位

## V0.2 → V1.0 的实际改动（登录系统 + 管理者大屏）

> 2026-08-18 本次检查会话新增。

### 新增功能

#### 1. 用户认证与角色权限

| 改动 | 文件 | 说明 |
|---|---|---|
| 新增 `User` 模型 | `app/models.py` | username / password_hash / role(manager/operator) / display_name |
| PBKDF2-SHA256 密码哈希 | `app/auth.py` | `hash_password()` / `verify_password()` |
| HMAC 签名 Session Cookie | `app/auth.py` | 8 小时有效，HttpOnly，防篡改 |
| 认证中间件 | `app/middleware.py` | 未登录 → `/login`，API 未登录 → 401 |
| 角色依赖注入 | `app/dependencies.py` | `require_role()` / `admin_only()` |
| 登录/登出路由 | `app/routers/auth.py` | GET/POST `/login`，POST `/logout` |
| 默认种子用户 | `app/seed.py` | admin (密码 `888888`) + operator (密码 `888888`)；生产环境通过 `INITIAL_ADMIN_PASSWORD` 环境变量注入强密码 |

#### 2. 管理者专属大屏

| 改动 | 文件 | 说明 |
|---|---|---|
| 管理者路由 | `app/routers/manager.py` | `/manager/dashboard` / `/manager/project-list` / `/manager/project/{pid}` |
| 总览大屏 | `app/templates/manager_dashboard.html` | KPI 卡片 + 项目网格 + 实际法人税务汇总 |
| 项目列表 | `app/templates/manager_project_list.html` | 项目卡片网格 |
| 项目详情 + AI 问答 | `app/templates/manager_project.html` | 完整经济指标 + 税务台账 + 成本明细 + AI 自由问答 |
| AI 问答接口 | `app/routers/manager.py` | POST `/manager/project/{pid}/ask` → 调用 `call_endpoint()` |

#### 3. 全局 UI 改造

| 改动 | 文件 | 说明 |
|---|---|---|
| 登录状态栏 | `app/templates/base.html` | 顶栏显示用户名 + 角色标签 + 退出按钮 |
| 角色导航过滤 | `app/templates/base.html` | manager 只能看总览/项目/审计，operator 可见全部现有功能 |
| 模板全局上下文 | `app/templates.py` | context_processor 注入 `current_user/role/is_manager/is_operator` |

### 路由清单变更

| 变化 | 路由 |
|---|---|
| **新增（7 个）** | `/login` GET/POST, `/logout` POST, `/manager/dashboard`, `/manager/project-list`, `/manager/project/{pid}`, `/manager/project/{pid}/ask` |
| **受保护（全部）** | 所有 HTML 页面路由均需登录；API 路由未登录返回 401 |
| **公开（不变）** | `/login`, `/logout`, `/docs`, `/redoc`, `/openapi.json`, `/healthz` |

## 与 V0.2 包的关系

```
gtp_V0.2_FULL/01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2/   ← 主版本（功能开发在此进行）
gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/   ← 快照副本（不写新代码）
```

**重要**：所有新功能、Bug 修复、性能优化请在 **V0.2 包** 内进行。V1.0 包**只读快照**，修改会被下次同步覆盖。

## 后续版本命名约定

按本目录命名规则，未来版本将依次：

- `gtp_V1.0_FULL` ← 当前快照（V0.2 镜像）
- `gtp_V1.1_FULL` ← 第一个独立功能版本（待启动）
- `gtp_V1.2_FULL` ← ...
- `gtp_V2.0_FULL` ← 重大架构升级

> 详细 V0.2 自身演进参见 `CHANGELOG_V02.md`（本包内）。
