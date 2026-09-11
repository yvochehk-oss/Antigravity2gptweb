# 腾讯云 TokenHub 配置指南

## 概述

系统使用腾讯云 TokenHub 的 DeepSeek V4 Flash 模型作为云端 AI。配置过程只需 4 步，全程个人用户即可完成。

---

## 四步完成配置

### 第一步：注册用户

1. 访问 https://console.cloud.tencent.com/tokenhub/open-management
2. 点击「注册/登录」
3. 选择**个人用户**，支持微信或手机号注册
4. 无需企业认证，无需实名认证

### 第二步：开通服务

1. 登录后进入 TokenHub 控制台
2. 找到「开通服务」或「立即开通」按钮
3. 按提示完成服务开通（个人用户可直接开通）

### 第三步：领取免费模型额度

1. 进入控制台后，找到「免费额度」或「新用户礼包」
2. 点击「立即领取」
3. 领取成功后即可使用免费 Token

### 第四步：建立 API 并配置到系统

1. 在控制台进入「API 密钥管理」
2. 创建新的 API Key（或使用已有的）
3. 将 API Key 填入系统配置

---

## 系统预配置

系统已预置 Base URL 和模型名称，您需要填入自己的 API Key：

| 配置项 | 值 |
|--------|-----|
| API Key | `sk-YOUR_OWN_API_KEY`（请填入您在 TokenHub 获取的 Key） |
| Base URL | `https://tokenhub.tencentmaas.com/v1` |
| 模型 | `deepseek-v4-flash-202605` |

> **重要**：API Key 是您的私人凭证，请勿使用示例 Key 或与他人分享。
> 若需配置，请修改项目根目录 `.env` 文件中的 `DEEPSEEK_API_KEY`。

---

## 离线兜底

当 TokenHub 额度用尽或网络不可达时，系统自动切换到本地离线模型：

| 兜底模型 | 地址 |
|----------|------|
| Spark-X2.5-4B | `http://127.0.0.1:8931/v1/chat/completions` |
