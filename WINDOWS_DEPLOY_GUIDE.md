# 成都建工 V2.0 财税事实智能中台系统 · Windows 部署与极速启动指南

> **目标设备硬件适配评估**：
> * **CPU**：Intel Core i3-9100F @ 3.60GHz（4核4线程，具备 **AVX2 / FMA3** 硬件矢量加速指令集）
> * **内存（RAM）**：**16.0 GB**（完全满足系统全组件运行，剩余可用内存约 8.5GB+）
> * **显卡（GPU）**：NVIDIA GeForce GT 730（Kepler/Fermi 架构，1~2G 显存，不支持现代 CUDA 12+）
> * **运行架构决策**：**全系统采用纯 CPU AVX2 硬件加速模式**。本地大模型服务使用预置的 Windows x64 专用二进制 `llama-server.exe`，免装 CUDA，免配庞大显卡驱动，即开即用、零报错、零风扇噪音。

---

## 目录
1. [系统架构与端口一览](#1-系统架构与端口一览)
2. [部署前准备（三项基础软件）](#2-部署前准备三项基础软件)
3. [三步快速部署流程](#3-三步快速部署流程)
   - [第一步：一键还原数据库](#第一步一键还原数据库)
   - [第二步：一键初始化 Python 与 Node 依赖](#第二步一键初始化-python-与-node-依赖)
   - [第三步：一键双击启动全系统](#第三步一键双击启动全系统)
4. [各子模块独立启动说明](#4-各子模块独立启动说明)
5. [常见问题与优化建议](#5-常见问题与优化建议)

---

## 1. 系统架构与端口一览

系统包含 4 个核心服务，在 Windows 上均已封装为一键批处理脚本：

| 服务名称 | 端口 | 核心职责 | 脚本文件 |
| :--- | :--- | :--- | :--- |
| **01. 本地大模型服务** | `8930` | 运行 `Ling-3.0-tiny` (7.9B MoE，每次激活 1.3B)，CPU 毫秒级推理 | `01_启动本地LLM服务_Windows.bat` |
| **02. RAG 事实中台** | `8922` | BGE-M3 向量检索、MinerU 解析、单据快速抽取、事实中台 | `02_启动RAG事实中台_Windows.bat` |
| **03. 税务管理后端** | `8921` | 税务台账、四流合规复核、财税确定性计算、RAG 同步 | `03_启动税务系统后端_Windows.bat` |
| **04. 老板端 Web** | `5173` | 老板端移动驾驶舱、工程驾驶舱 UI 交互界面 | `04_启动老板端Web_Windows.bat` |

---

## 2. 部署前准备（三项基础软件）

在 Windows 电脑上，请确保安装以下 3 个标准工具：

### (1) Python 3.14 (Free-Threaded 64位) 或 Python 3.11+
* **下载地址**：[Python 官方 Windows 安装包](https://www.python.org/downloads/windows/) 或通过 `pip install uv` 自动管理
* **⚠️ 安装要点**：安装第一步必须**勾选 `Add Python to PATH`**（将 Python 添加到系统环境变量）。
* **自动化说明**：在 Windows 运行 `setup_environment_windows.bat` 时，`uv` 会自动为 RAG 系统与税务系统拉取 **Python 3.14** 运行内核，确保 4 并发 Worker 无 GIL 全速运行。

### (2) PostgreSQL 15 或 16 + pgvector
* **下载地址**：[PostgreSQL Windows 官方安装包](https://www.enterprisedb.com/downloads/postgres-postgresql-downloads)
* **向量扩展**：安装后在 pgAdmin 或命令行执行 `CREATE EXTENSION IF NOT EXISTS vector;`。

### (3) Node.js 18+ (LTS)
* **下载地址**：[Node.js 官方 LTS 安装包](https://nodejs.org/zh-cn/download)
* 用于运行老板端 Web 驾驶舱页面。

---

## 3. 三步快速部署流程

将整个 `073_成都建工/V2.0` 项目文件夹复制到 Windows 电脑（例如 `D:\073_成都建工\V2.0`）。

### 第一步：一键还原数据库
双击运行：
👉 `database/restore_database_windows.bat`
* 输入您的 PostgreSQL 用户名（默认 `postgres`）与端口；
* 脚本会自动创建 `projectrag` 数据库、启用 `pgvector` 扩展，并一键导入 `projectrag_backup_full_20260827.dump` 中的全部工程、合同、单据与财税真实数据。

### 第二步：一键初始化 Python 与 Node 依赖
双击运行：
👉 `setup_environment_windows.bat`
* 脚本会自动使用 `uv` 极速搭建 RAG 系统与税务系统的 Python 虚拟环境；
* 自动安装前端 Node 依赖并完成静态构建。

### 第三步：一键双击启动全系统
双击运行根目录下的：
👉 **`00_一键启动成都建工系统_Windows.bat`**

* 自动按顺序拉起 4 个窗口（大模型 8930 ➔ RAG 8922 ➔ 税务 8921 ➔ 前端 5173）；
* 自动在默认浏览器中打开老板端驾驶舱：`http://127.0.0.1:5173`。

---

## 4. 各子模块独立启动与停止

如果需要单独调试某个服务：
* 启动大模型：双击 `01_启动本地LLM服务_Windows.bat`
* 启动 RAG 中台：双击 `02_启动RAG事实中台_Windows.bat`
* 启动税务后端：双击 `03_启动税务系统后端_Windows.bat`
* 启动前端界面：双击 `04_启动老板端Web_Windows.bat`
* **停止所有服务**：双击 **`99_停止全部服务_Windows.bat`**

---

## 5. 针对 i3-9100F + GT 730 的专属调优说明

1. **为什么不需要配置显卡 CUDA？**
   * GT 730（Kepler架构，1~2GB显存）属于入门亮机卡，显存极小且不支持现代深度学习 CUDA 12。
   * 本系统已集成的 `llama-server.exe` 纯 CPU 运行时能够直接利用 i3-9100F 的 **AVX2 矢量指令** 进行 4 核心并行计算，单次抽取响应时间在毫秒至 1 秒级别，效率极高且完全不占显卡显存。

2. **内存占用分布（16GB 完全充裕）**：
   * `Ling-3.0-tiny` 大模型：约 **4.9 GB**
   * BGE-M3 向量检索模型：约 **1.2 GB**
   * 后端服务与数据库：约 **0.8 GB**
   * **总内存占用**：约 **6.9 GB**，Windows 操作系统与浏览器剩余超过 **9 GB** 内存，运行极其丝滑。

3. **极低资源备选方案**：
   * 若电脑同时运行大型 CAD/BIM 软件导致内存紧张，可随时将 `01_启动本地LLM服务_Windows.bat` 中的模型改为 `Qwen3.5-2B-Q4_K_M.gguf`，内存占用将从 4.9GB 骤降至 **1.2GB**。
