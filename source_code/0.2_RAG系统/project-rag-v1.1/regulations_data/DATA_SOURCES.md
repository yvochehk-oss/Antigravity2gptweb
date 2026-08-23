# 法规数据源指南

> 补充爬虫无法获取的权威法规全文，推荐以下合法数据源。
> 针对建筑施工、劳务、租赁、商贸四类企业的财税法规。

---

## 一、专业法律数据库（推荐采购）

### 1. 北大法宝（PKULaw）⭐⭐⭐⭐⭐

**最推荐**，拥有正规 MCP API，适合 RAG 系统集成。

| 项目 | 详情 |
|------|------|
| 官网 | https://mcp.pkulaw.com |
| 数据量 | 580万+ 法规、1.7亿+ 案例 |
| 覆盖 | 中央/地方各级法规，实时更新 |
| **API** | `apim-gateway.pkulaw.com`（MCP 协议） |
| **CLI 工具** | `npm install -g @pkulaw/mcp-cli` |
| 定价（尝鲜价） | 关键词检索 0.019元/次；语义检索 0.095元/次 |
| 免费额度 | 注册送 900 次（9个服务各100次） |
| 企业版 | 大规模次数包 + 私有化部署 + SLA |
| **适合场景** | 正式生产系统的法规检索，精确引注 |

**Cursor MCP 配置（已写入 settings.json，重启后生效）：**

```json
// ~/Library/Application Support/Cursor/User/settings.json
"cline.mcpServers": {
  "pkulaw-law-search-service": {
    "url": "https://apim-gateway.pkulaw.com/mcp-law-search-service",
    "headers": { "Authorization": "Bearer ${PKULAW_MCP_TOKEN}" }
  },
  "pkulaw-law": {
    "url": "https://apim-gateway.pkulaw.com/mcp-law",
    "headers": { "Authorization": "Bearer ${PKULAW_MCP_TOKEN}" }
  },
  "pkulaw-fatiao": {
    "url": "https://apim-gateway.pkulaw.com/mcp-fatiao",
    "headers": { "Authorization": "Bearer ${PKULAW_MCP_TOKEN}" }
  }
  // ... 共 10 个服务（见 settings.json）
}
```

> ⚠️ Token 已硬编码在 settings.json 中。请定期到 [北大法宝控制台](https://mcp.pkulaw.com) 检查用量，避免超额。

**API 工具一览：**

| 工具 | 用途 | 适合 RAG 吗 |
|------|------|-------------|
| `law-keyword` | 法规关键词检索 | ✅ 可批量查询元数据 |
| `law-semantic` | 法规语义检索（向量） | ✅ 直接对接 embedding |
| `fatiao` | 精准法条查找 | ✅ 条款级精确命中 |
| `law-item-keyword` | 法规详情（正文） | ✅ 获取完整法规文本 |
| `citation_validator` | 引用核验 | ✅ 生成答案时校验条款 |
| `statute_freshness` | 法条时效核查 | ✅ 检查是否仍有效 |

**建议用法：**
- 用 `law-keyword` + 地区/行业过滤 → 筛选候选法规
- 用 `law-semantic` → 语义补召回（与 PostgreSQL FTS 并行）
- 用 `fatiao` + `law-item-keyword` → 获取精确条款正文
- 用 `citation_validator` → 答案输出前核验引用有效性

---

### 2. 元典开放平台（ChineseLaw）⭐⭐⭐⭐

**免费额度最大**，有语义检索向量 API，适合研究和原型验证。

| 项目 | 详情 |
|------|------|
| 官网 | https://open.chineselaw.com |
| 定价 | 注册送免费额度；详细价格需联系 |
| **核心 API** | 见下方 |

**API 接口：**

| 接口 | 端点 | 说明 |
|------|------|------|
| 法规关键词检索 | `POST /open/rh_fg_search` | 关键词 → 法规列表 |
| 法条关键词检索 | `POST /open/rh_ft_search` | 关键词 → 条款内容 |
| **法规语义检索** | `POST /open/law_vector_search` | 自然语言 → 法条（向量） |
| 法规详情 | `GET /open/rh_fg_detail` | 法规ID → 完整正文 |
| 发布部门过滤 | `fbbm` 参数 | 如 `四川省财政厅` |
| 地域过滤 | `dy` 参数 | 四川、重庆 等 |
| 时效性过滤 | `sixiao` 参数 | 有效/失效/已被修订 |

**请求示例（法条检索）：**

```json
POST https://open.chineselaw.com/open/rh_ft_search
{
  "keyword": "甲供工程 简易计税",
  "dy": "四川",
  "xljb_1": "行政法规",
  "top_k": 20
}
```

**适用场景：** 免费语义检索，适合早期研究和 POC 阶段。

---

### 3. 威科先行（Wolters Kluwer）⭐⭐⭐⭐

适合企业用户，**财税专题**有深度实务解读。

| 项目 | 详情 |
|------|------|
| 官网 | https://law.wkinfo.com.cn |
| 数据量 | 260万+ 法规（1949年至今） |
| 特色 | 法规配套**专家解读**、**实务指南**、**文书模板** |
| 批量导出 | 支持类案检索报告批量下载（需机构账号） |
| 采购方式 | 联系销售（企业订阅制），无公开 API |
| **适合场景** | 需要专家解读版本作为答案补充 |

**与 RAG 对接方式：**
威科无公开 API，但支持手动批量导出 PDF/Word，再由系统处理入库。
适合补充权威解读文档（与法规原文配合给 LLM 参考）。

---

### 4. 法律之星 ⭐⭐⭐

| 项目 | 详情 |
|------|------|
| 官网 | https://www.law-star.com |
| 数据量 | 每日 200-300 篇法规更新 |
| 覆盖 | 中央+地方各级法规 |
| **API** | `POST /open/rh_fg_search` 等（结构化接口） |
| **特色** | 区域/行业/主题法规包，支持个性化定制 |
| 接入方式 | 联系商务（wangzhan@law-star.com） |

**适合：** 采购区域/行业法规包，数据质量较高。

---

## 二、免费数据源（可脚本化）

### 5. 国家法律法规数据库（NPC）⭐⭐⭐⭐⭐

**国家级权威来源**，免费，法规可下载。

| 项目 | 详情 |
|------|------|
| 官网 | https://flk.npc.gov.cn |
| 数据量 | 法律 275+、行政法规 609+、地方性法规 16000+、司法解释 637+ |
| 格式 | WPS 文字版 + PDF 公报原版 |
| 覆盖 | 宪法、法律、行政法规、地方性法规、司法解释 |
| **API** | `https://flk.npc.gov.cn/api/`（有社区封装） |
| 版权 | 政府公开数据，可免费使用 |

**API 接口：**

| 用途 | 端点 |
|------|------|
| 法规列表 | `GET https://flk.npc.gov.cn/api/` |
| 法规详情 | `GET https://flk.npc.gov.cn/api/detail?id=xxx` |
| 文件下载 | `GET https://wb.flk.npc.gov.cn/` + 相对路径 |

**已有开源工具（推荐）：**

```bash
# cnlaw - 国家法律法规数据库 CLI
npm install -g cnlaw
cnlaw search "甲供工程 简易计税"
cnlaw download --id xxx
```

GitHub: `github.com/cnlaw/` 系列工具

---

### 6. 财政部政策库（szs.mof.gov.cn）⭐⭐⭐⭐

已验证可用，是**最可靠的财税法规来源**。

| 项目 | 详情 |
|------|------|
| 官网 | `https://szs.mof.gov.cn/zhengcefabu/` |
| 数据量 | 财政部/税务总局 2016 年至今政策文件 |
| 格式 | HTML（TRS_Editor 正文格式） |
| 可靠性 | ✅ 最高，官方来源 |
| **缺点** | 部分旧 URL 已 404（网站改版） |

**当前可用的 URL 规律：**
- 新文件：`szs.mof.gov.cn/zhengcefabu/YYYYMM/tYYYYMMDD_nnnnn.htm`
- PDF：`szs.mof.gov.cn/zhengcefabu/YYYYMM/P0YYYYMMDDnnnnnnn.pdf`

> 建议用搜索找当前可用 URL（见下方案例脚本）。

---

### 7. 国家税务总局省级税务局 ⭐⭐⭐

| 来源 | 可用性 | 说明 |
|------|--------|------|
| 内蒙古税务局 | ✅ 稳定 | 政策解读转载 |
| 贝云社区（beiyun.cn） | ✅ 稳定 | 地方规定转载 |
| 中国会计网（canet.com.cn） | ✅ 稳定 | 地方规定转载 |
| 四川省税务局 | ⚠️ SSL 不稳定 | 建议用其他镜像 |
| 成都市税务局 | ⚠️ SSL 不稳定 | 建议用 beiyun.cn |

---

## 三、补充建议清单

对于你的四类企业，以下法规目前无法通过爬虫获取（JS 渲染/无 API），**建议通过北大法宝或政府采购补充**：

| 缺失法规 | 优先级 | 获取渠道 |
|----------|--------|----------|
| 四川省建筑劳务管理办法 | 高 | 北大法宝 + 四川省政府官网 |
| 成都市建筑市场主体信用评价规定 | 高 | 成都市住建局官网 |
| 劳务派遣暂行规定（人社保部令22号） | 高 | NPC 数据库（免费） |
| 农民工工资支付暂行条例（国务院令724号） | 高 | NPC 数据库（免费） |
| 设备租赁增值税税目注释（2026年更新版） | 中 | 北大法宝 |
| 建筑工人实名制管理办法（住建部） | 中 | 住建部官网 |
| 小微企业普惠性税收减免政策（延续至2027） | 中 | 北大法宝/元典 |

---

## 四、推荐的 RAG 系统集成路径

```
第一步：自动抓取（已完成）
    16 条核心法规  → regulations_data/  ✅

第二步：补充权威全文
    ├─ 北大法宝 MCP API
    │   → 批量检索：建筑/劳务/租赁/增值税/四川/成都
    │   → 获取：全文 + 专家解读
    │   → 写入：regulations/regulation_articles 表
    │
    └─ NPC 数据库（免费补充）
        → 补充：上位法依据（增值税法全文等）

第三步：更新 RegulationsFetcher（整合北大法宝）
    → 新增 provider: "pkulaw" / "npc"
    → 自动调用 MCP API 替代手动 URL 维护
```

---

## 五、北大法宝 MCP 快速接入（示例脚本）

```python
# regulations_data/scripts/providers/pkulaw_client.py
"""
北大法宝 MCP API 客户端

安装：npm install -g @pkulaw/mcp-cli
认证：pkulaw-mcp init --authorization "Bearer YOUR_TOKEN"
"""

import httpx
import json
from pathlib import Path

PKULAW_API = "https://apim-gateway.pkulaw.com/mcp-law-search-service"
TOKEN = "YOUR_ACCESS_TOKEN"  # 从北大法宝官网获取

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}


def search_laws(keyword: str, region: str = "四川", industry: str = "建筑") -> list:
    """关键词检索法规（返回元数据列表）。"""
    payload = {
        "keyword": keyword,
        "region": region,
        "industry": industry,
        "page_size": 20,
    }
    resp = httpx.post(PKULAW_API, headers=HEADERS, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("result", [])


def semantic_search(query: str, top_k: int = 10) -> list:
    """语义检索法条（适合补召回）。"""
    payload = {"query": query, "top_k": top_k}
    resp = httpx.post(
        "https://apim-gateway.pkulaw.com/mcp-law-semantic-service",
        headers=HEADERS, json=payload, timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("result", [])


def get_law_detail(law_id: str) -> dict:
    """获取法规正文。"""
    resp = httpx.get(
        f"https://apim-gateway.pkulaw.com/mcp-law-detail/{law_id}",
        headers=HEADERS, timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


# 使用示例
if __name__ == "__main__":
    results = search_laws("建筑服务 增值税 简易计税")
    for r in results[:5]:
        print(f"[{r['id']}] {r['title']} — {r['doc_no']}")
```

---

## 六、NPC 数据库快速补充（免费）

```python
import httpx, re

NPC_LIST = "https://flk.npc.gov.cn/api/search?wd={keyword}&type=regulation"
NPC_DETAIL = "https://flk.npc.gov.cn/api/detail?id={law_id}"

def search_npc(keyword: str) -> list:
    url = NPC_LIST.format(keyword=keyword)
    r = httpx.get(url, timeout=10)
    return r.json().get("result", [])

def download_npc_pdf(law_id: str, out_dir: Path):
    detail = httpx.get(NPC_DETAIL.format(law_id=law_id), timeout=15).json()
    pdf_url = "https://wb.flk.npc.gov.cn" + detail["data"]["pdf"]
    r = httpx.get(pdf_url, timeout=30)
    out = out_dir / f"{law_id}.pdf"
    out.write_bytes(r.content)
    return out
```

---

## 七、总结推荐

| 场景 | 推荐数据源 | 理由 |
|------|-----------|------|
| **立即可用** | 财政部政策库（已抓取） | 免费，官方，稳定 |
| **正式生产** | 北大法宝 MCP API | 权威，实时，覆盖全，API 友好 |
| **研发/POC** | 元典开放平台 | 免费额度大，语义检索 |
| **补充地方规定** | NPC 数据库 + 省级税务局镜像 | 免费，权威 |
| **专家解读** | 威科先行 | 实务补充（非必需） |

**第一步建议：**
去 [北大法宝官网](https://mcp.pkulaw.com) 注册账号，申请免费试用 900 次 API 额度，先跑通「建筑服务/增值税/四川」场景的完整检索链路。
