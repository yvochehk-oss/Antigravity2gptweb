# 成都建工 AI 财税智控与 RAG 穿透研判中枢 (V2.0)

本项目为成都建工（锐宝建设）AI 财税治理与穿透研判系统 V2.0 完整工程资产库。

## 目录结构

- `source_code/`
  - `0.1_税务管理/`: 财税智控与沙盘筹划系统 (FastAPI + React 18 + TailwindCSS)
  - `0.2_RAG系统/`: ProjectRAG 凭证知识库与 Facts 研判中枢 (FastAPI + pgvector + BM25)
  - `0.3_老板端安卓App_天府掌舵/`: 移动决策大屏移动端 App (Capacitor + React 18 + Vite)
- `database/`: PostgreSQL (`projectrag`) 完整数据库转储文件 (`.sql` & `.backup`)
- `project_materials/`: 6 大工程项目全套 346 份原始增值税发票、合同等 PDF 凭证材料
- `models/`: 本地嵌入与重排模型配置（BGE-M3 / BGE-Reranker-V2-M3 / RapidOCR）
- `start_all.sh` / `stop_all.sh`: 一键全量启停脚本

## 快速启动

```bash
# 启动所有后台服务 (Tax: 8921, RAG: 8922)
./start_all.sh

# 停止所有服务
./stop_all.sh
```
