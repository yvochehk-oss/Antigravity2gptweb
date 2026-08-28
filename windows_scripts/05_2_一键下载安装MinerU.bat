﻿@echo off
@chcp 65001 >nul
title 成都建工 V2.0 - 安装与下载 MinerU (PDF解析组件)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V2.0 - MinerU 3.4 极速安装与模型下载器
echo   将自动配置独立的 Python 3.12 环境以保证兼容性，并自动下载所需模型。
echo   模型将严格保存在项目本地的 models 文件夹，不会占用 C 盘系统缓存。
echo ==============================================================================
echo.

set "UV_BIN=%SCRIPT_DIR%tools\uv.exe"
set "MINERU_VENV_DIR=%SCRIPT_DIR%..\source_code\0.2_RAG系统\project-rag-v1.1\.mineru-venv"
set "MINERU_PY=%MINERU_VENV_DIR%\Scripts\python.exe"

if not exist "%UV_BIN%" (
    echo [错误] 找不到 uv.exe，请确保解压了工具包。
    pause
    exit /b 1
)

echo [1/3] 正在使用 uv 准备 MinerU 专用虚拟环境 (Python 3.12) ...
"%UV_BIN%" venv "%MINERU_VENV_DIR%" --python 3.12
if errorlevel 1 (
    echo [错误] 创建虚拟环境失败，请重试。
    pause
    exit /b 1
)

echo.
echo [2/3] 正在安装 MinerU 及相关依赖 (使用阿里云镜像) ...
"%UV_BIN%" pip install --python "%MINERU_VENV_DIR%" -i https://mirrors.aliyun.com/pypi/simple/ "mineru>=3.4.5"
if errorlevel 1 (
    echo [警告] MinerU 安装过程可能遇到警告，将继续尝试...
)

echo.
echo [3/3] 正在启动 MinerU 模型自动下载器 (包含 PDF 布局解析、公式识别等模型) ...
echo.
:: 强制将缓存定向到当前项目的 models 目录
set "MODELSCOPE_CACHE=%SCRIPT_DIR%..\models\mineru_weights"
set "HF_HOME=%SCRIPT_DIR%..\models\mineru_weights"
set "MINERU_MODEL_SOURCE=modelscope"

if exist "%MINERU_VENV_DIR%\Scripts\mineru-models-download.exe" (
    "%MINERU_VENV_DIR%\Scripts\mineru-models-download.exe" --source modelscope --model_type all
) else (
    echo [提示] 未在默认位置找到下载命令，尝试使用 python 模块启动...
    "%MINERU_PY%" -c "import os; os.environ['MINERU_MODEL_SOURCE']='modelscope'; import sys; sys.argv=['mineru-models-download', '--source', 'modelscope', '--model_type', 'all']; from mineru.cli.models_download import download_models; download_models()"
)

echo.
echo ==============================================================================
echo   MinerU 安装与模型下载流程执行完毕！
echo ==============================================================================
echo.
pause
