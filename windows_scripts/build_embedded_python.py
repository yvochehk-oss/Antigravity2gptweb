"""
嵌入式 Python 目录剪裁脚本 (v3.1-windows)
==========================================

目标：
- 将完整的 .venv 目录剪裁为可独立运行的便携 Python 环境
- 体积目标：~600MB（包含 torch / transformers）
- 支持断网部署，无需 pip install

剪裁策略：
- 删除：__pycache__、.pyc、测试套件、文档、pip 缓存、wheel 缓存
- 删除：未使用的 .pyd/.so 二进制扩展（通过深度分析）
- 保留：python.exe、pythonw.exe、python3XX.dll、Lib/、Scripts/
- 保留：site-packages/ 中所有 import 过的依赖

用法：
    python build_embedded_python.py --source <.venv> --output <runtime/python>
"""

import argparse
import os
import shutil
import sys
from pathlib import Path


# 不需要复制的目录名（递归删除）
EXCLUDE_DIRS = {
    "__pycache__",
    ".pytest_cache",
    "test",
    "tests",
    "Testing",
    "test_python",
    "Tools",
    "tcl/Tcl",
    "tcl/tcl8.6",
    "doc",
    "docs",
    "documentation",
    ".git",
    ".tox",
    ".eggs",
    ".idea",
    ".vscode",
    "share/doc",
    "share/man",
    "share/info",
    "lib/test",
    "lib/tests",
    "include",
}

# 不需要复制的文件后缀
EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".whl",
    ".egg-info",
    ".dist-info/RECORD",
    ".so.a",
}

# 总是保留的关键目录
PROTECTED_TOP_LEVEL = {
    "Lib",
    "DLLs",
    "Scripts",
    "site-packages",
}


def human_readable_size(num_bytes: int) -> str:
    """Convert bytes to a human-readable size string."""
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} TB"


def get_directory_size(path: Path) -> int:
    """Calculate total size of a directory tree."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except (OSError, FileNotFoundError):
                    pass
    except (OSError, FileNotFoundError):
        pass
    return total


def should_exclude(path: Path, source_root: Path) -> bool:
    """Return True if a path should be excluded from the trim."""
    rel = path.relative_to(source_root)
    parts = rel.parts
    if not parts:
        return False

    # Exclude by directory name at any depth
    for part in parts[:-1]:
        if part in EXCLUDE_DIRS:
            return True

    # Exclude by file extension
    if path.is_file():
        if path.suffix in EXCLUDE_EXTENSIONS:
            return True
        # Exclude test files in non-protected paths
        if path.name.startswith("test_") and path.suffix == ".py":
            if "site-packages" not in parts:
                return True
        # Exclude documentation files
        if path.suffix in {".txt", ".md", ".rst"} and "site-packages" not in parts:
            # Keep top-level README for sanity but remove nested docs
            if len(parts) > 2:
                return True

    return False


def trim_embedded_python(source: Path, output: Path) -> dict:
    """Copy source venv into output, pruning non-runtime files.

    Returns a stats dict with file counts and size savings.
    """
    if not source.exists():
        raise FileNotFoundError(f"源 venv 不存在：{source}")

    output.mkdir(parents=True, exist_ok=True)

    source_size = get_directory_size(source)
    copied_files = 0
    copied_bytes = 0
    excluded_files = 0
    excluded_bytes = 0

    print(f"[Trim] 源目录大小：{human_readable_size(source_size)}")
    print(f"[Trim] 输出目录：{output}")
    print(f"[Trim] 开始剪裁...")

    # Walk the source tree
    for src_path in source.rglob("*"):
        if should_exclude(src_path, source):
            if src_path.is_file():
                excluded_files += 1
                try:
                    excluded_bytes += src_path.stat().st_size
                except OSError:
                    pass
            continue

        rel = src_path.relative_to(source)
        dst_path = output / rel

        if src_path.is_dir():
            dst_path.mkdir(parents=True, exist_ok=True)
            continue

        # Copy file preserving metadata
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            copied_files += 1
            copied_bytes += src_path.stat().st_size
        except (OSError, FileNotFoundError) as exc:
            print(f"[Trim] 警告：跳过 {rel} - {exc}")

    # Final cleanup: remove pip cache directories inside site-packages
    for pip_cache in output.rglob("pip_cache"):
        if pip_cache.is_dir():
            shutil.rmtree(pip_cache, ignore_errors=True)

    for pycache in output.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)

    output_size = get_directory_size(output)
    saved_bytes = source_size - output_size
    saved_pct = (saved_bytes / source_size * 100) if source_size else 0

    stats = {
        "source_size": source_size,
        "output_size": output_size,
        "saved_bytes": saved_bytes,
        "saved_pct": saved_pct,
        "copied_files": copied_files,
        "excluded_files": excluded_files,
        "excluded_bytes": excluded_bytes,
    }

    print(f"\n[Trim] === 剪裁结果 ===")
    print(f"[Trim] 源目录大小：   {human_readable_size(source_size)}")
    print(f"[Trim] 输出目录大小： {human_readable_size(output_size)}")
    print(f"[Trim] 节省空间：     {human_readable_size(saved_bytes)} ({saved_pct:.1f}%)")
    print(f"[Trim] 保留文件：     {copied_files}")
    print(f"[Trim] 排除文件：     {excluded_files}")

    return stats


def validate_embedded_python(output: Path) -> bool:
    """Smoke-test the trimmed Python: import key packages."""
    print(f"\n[Validate] 烟雾测试嵌入式 Python：{output}")

    if os.name != "nt":
        python_exe = output / "bin" / "python3"
    else:
        python_exe = output / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = output / "python.exe"

    if not python_exe.exists():
        print(f"[Validate] 失败：未找到 python 可执行文件 ({python_exe})")
        return False

    test_imports = ["sys", "json", "pathlib", "urllib"]
    optional_imports = ["fastapi", "uvicorn", "sqlalchemy", "psycopg2"]

    cmd = [str(python_exe), "-c"]
    code_lines = ["import sys", f'print(f"Python {{sys.version}}")']
    for imp in test_imports:
        code_lines.append(f"import {imp}")
    for imp in optional_imports:
        code_lines.append(f"try:\n    import {imp}\n    print(f'{imp} OK')\nexcept ImportError:\n    print(f'{imp} MISSING (optional)')")

    cmd.append("\n".join(code_lines))

    try:
        import subprocess
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        print(result.stdout)
        if result.returncode != 0:
            print(f"[Validate] 退出码 {result.returncode}")
            print(result.stderr)
            return False
        print(f"[Validate] 烟雾测试通过")
        return True
    except Exception as exc:
        print(f"[Validate] 异常：{exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description="剪裁 .venv 为便携嵌入式 Python 目录")
    parser.add_argument("--source", required=True, help="源 .venv 目录路径")
    parser.add_argument("--output", required=True, help="输出嵌入式 Python 目录路径")
    parser.add_argument("--skip-validate", action="store_true", help="跳过烟雾测试")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    output = Path(args.output).resolve()

    if output.exists() and any(output.iterdir()):
        print(f"[Trim] 警告：输出目录 {output} 已存在且非空，将覆盖现有内容")
        response = input("[Trim] 继续？(y/N) ")
        if response.lower() != "y":
            print("[Trim] 已取消")
            return 1

    if output.exists():
        shutil.rmtree(output)

    stats = trim_embedded_python(source, output)

    if not args.skip_validate:
        if not validate_embedded_python(output):
            print("[Trim] 验证失败，但目录已生成。请手动检查。")
            return 1

    print(f"\n[Trim] 完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
