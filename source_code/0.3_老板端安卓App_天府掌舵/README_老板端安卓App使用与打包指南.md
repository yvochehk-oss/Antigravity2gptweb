# 成都建工·天府掌舵（老板端独立 Android App）使用与打包全指南

本子目录是专为集团高管层（董事长、总经理、CFO）打造的**独立运行移动端决策驾驶舱与 AI 智策系统**。

---

## 📱 核心功能板块

1. **📊 集团经营与财税大盘（首页）**：
   * 集团总签约造价（¥6.23 亿）、累计确认营收（¥1.825 亿）、动态真实净利润（¥3,930 万，毛利率 21.53%）；
   * 增值税综合税负率（1.94%）、销项与进项抵扣池对账；
   * 资金池 30 天净头寸（+¥1,750 万）、工程款回款率（74.5%）；
   * 6 个月动态产值与利润走势图、Top 2 紧急风险预警卡片。

2. **🏗️ 重点项目 360° 穿透分析**：
   * 6 大工程项目穿透看板（宜宾示范工业项目、天府二期金融中心、成渝跨江特大桥、广元利州生态治理、成都高新微电网、青海格尔木）；
   * 真实成本精细构成（材料 60% / 劳务 25% / 机械 10% / 其它 5%）；
   * EAC 完工预测成本与毛利空间；
   * **四流一致性穿透闭环**（合同流、发票流、资金流、物资地磅流）与 188 份 PDF 凭证查验。

3. **🏢 集团 26 家法人全景透视**：
   * ABCD 四类业务主体矩阵透视：
     * **A 类（施工总包/分包 11家）**：85.4% 产值贡献；
     * **B 类（物资商贸 10家）**：¥1,288 万钢筋建材进项抵扣池；
     * **C 类（建筑劳务 2家）**：100% 用工合规与劳务资金闭环；
     * **D 类（设备租赁 3家）**：88.5% 设备出租率。
   * 统一社会信用代码、法定代表人、注册资本、内部关联往来备注。

4. **🤖 董事长 AI 智策助手（Executive AI Copilot）**：
   * 随时随地语音/文字向 AI 提问经营、税务、利润与风控指标；
   * 结构化高管决策内参生成，附带证据链溯源与权威事实支撑。

5. **⚙️ Cloudflare 远程安全穿透与离线支持**：
   * 支持通过 Cloudflare 免费安全隧道（HTTPS 端到端加密），在户外 4G/5G 手机网络秒级直连本地计算机数据；
   * 支持离线数据快照，飞机/地下室无信号时照常浏览；
   * 支持高管隐私打码模式与指纹识别。

> 状态说明：以上为产品目标与演示范围。V2 分支正在把界面、API、状态管理与离线能力分层；真实登录、RBAC、设备绑定、生物识别、推送、正式数据接口和证据链服务仍须后端联调与安全验收，不能把演示数据视为生产数据。

---

## 🚀 极速使用与体验方式

### 方式一：手机浏览器 / 局域网即时体验（免装包）

1. 在当前目录下运行：
   * **Mac / Linux**：`./一键启动移动端服务.sh`
   * **Windows**：双击 `一键启动移动端服务_Windows.bat`
2. 打开手机浏览器（与电脑连接同一 Wi-Fi），直接访问终端提示的手机地址（例如 `http://192.168.1.100:5173`），即可全屏流畅使用！

---

### 方式二：远程 Cloudflare 安全穿透（出差/4G/5G 移动网络直连）

1. 在电脑端运行：
   * **Mac / Linux**：`./start_cloudflare_tunnel.sh`
   * **Windows**：双击 `start_cloudflare_tunnel_windows.bat`
2. 终端会自动生成一个独一无二的公网安全 HTTPS 链接（例如 `https://xxxx.trycloudflare.com`）；
3. 在手机 App 底部的「穿透设置」中填入该链接并点击保存，老板在任何城市、任何地点均可直连本地数据！

---

### 方式三：编译 Android APK 安装包（直接发给老板手机安装）

1. 如需生成仅供内部演示的调试包，先在未纳入 Git 的 `.env.local` 中设置 `VITE_ENABLE_LOCAL_DEMO=true`；正式构建必须保持关闭并接入真实统一认证。
2. 在当前目录下运行：

   ```bash
   ./build_apk.sh
   ```

3. 编译成功后，调试安装包位于：
   `dist_apk/ChengduConstruction_Boss_v1.0.0-debug.apk`
4. 调试包仅用于开发者和受控内部演示，不能作为正式版本发给管理层长期使用。

---

## V2 打包与安全说明

上述原始快速打包流程生成的是可安装的调试包，不是正式发布包。运行前需安装 Node.js、JDK 21 与 Android SDK。调试构建允许明文 HTTP，便于局域网联调；Release 构建拒绝明文流量。本地角色登录仅在 Vite 开发模式或显式设置 `VITE_ENABLE_LOCAL_DEMO=true` 时启用；正式包未接入统一认证时会拒绝进入。临时 `trycloudflare.com` 地址也只适合演示；正式环境应使用固定域名、Cloudflare Access 或等效身份认证，并限制源站访问。

---

## V2 目录与配置

- `src/api/`：统一 API 客户端与业务接口；
- `src/stores/`：认证、驾驶舱、AI 与 UI 状态；
- `src/offline/`：离线快照边界；
- `android/`：Capacitor Android 原生工程；
- `capacitor.config.json`：应用标识、Web 资源目录与 WebView 安全基线；
- `docs/V2_改造清单.md`：当前完成项和上线前待办；
- `docs/Android_安全边界.md`：网络、备份、签名与敏感数据边界。

应用 ID 为 `cn.cdjg.executive.app`，Web 产物目录为 `dist`。Capacitor 使用本地 HTTPS scheme，禁止混合内容，Release 不输出 Capacitor 日志且不开放 WebView 调试。

---

## 正式 Release 打包

先构建并同步 Web 资源：

```bash
npm ci
npm run build
npx cap sync android
```

Release 签名通过下列环境变量注入，仓库不得保存 keystore 或口令：

- `CDJG_RELEASE_STORE_FILE`：keystore 的绝对路径；
- `CDJG_RELEASE_STORE_PASSWORD`；
- `CDJG_RELEASE_KEY_ALIAS`；
- `CDJG_RELEASE_KEY_PASSWORD`。

变量齐全后执行：

```bash
cd android
./gradlew clean bundleRelease
```

AAB 输出到 `android/app/build/outputs/bundle/release/app-release.aab`。如需受控内部分发 APK，可执行 `./gradlew assembleRelease`，输出目录为 `android/app/build/outputs/apk/release/`。

若未配置签名变量，Gradle 会明确提示并生成未签名产物；未签名产物不能作为正式版本分发。发布前还应提升 `versionCode`/`versionName`，在隔离环境保管签名材料，并留存 SHA-256 摘要、构建日志与版本标签。

---

## 发布前最低验收

1. `npm run build`、`npx cap sync android`、`./gradlew test lintRelease assembleRelease` 全部通过；
2. 在真机验证登录失效、无网、弱网、Token 过期、系统返回键和隐私模式；
3. Release 抓包确认只访问批准的 HTTPS 域名，HTTP 请求应失败；
4. 确认应用数据不会进入云备份或换机迁移；
5. 确认 APK/AAB 已使用正式证书签名，且证书指纹与发布记录一致；
6. 脱敏检查日志、崩溃报告、离线快照与 AI 请求，不得出现口令、Token、身份证号、银行账号或完整财税底账。
