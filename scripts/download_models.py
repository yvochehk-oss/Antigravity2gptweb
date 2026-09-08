"""Windows AI dependency downloader for offline deployment.

Installs a Spark-X2.5-compatible llama.cpp CPU runtime, Spark-X2.5-4B Q4_K_M,
and BGE-M3 into the project-local models/ directory. Nothing is written to
~/.cache.
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
LOCAL_LLM_DIR = MODELS_DIR / "local-llm"

# Spark2_5 support landed in llama.cpp b10828. Pin the first official Windows
# CPU x64 release that contains that architecture so deployments are reproducible.
LLAMA_CPP_MIN_BUILD = 10828
LLAMA_CPP_TAG = "b10828"
LLAMA_RUNTIME_ARCHIVE = f"llama-{LLAMA_CPP_TAG}-bin-win-cpu-x64.zip"
LLAMA_RUNTIME_URL = (
    f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_TAG}/"
    f"{LLAMA_RUNTIME_ARCHIVE}"
)
LLAMA_RUNTIME_SHA256 = "221ef3c3d4f649e426708942fbb9dd369bc7ab7aa0153543323195459522b8cc"
LLAMA_RUNTIME_DIR = LOCAL_LLM_DIR / "runtime-win-cpu-x64"


def print_step(msg: str):
    print("\n==================================================")
    print(f"  {msg}")
    print("==================================================")


def download_file_with_progress(url: str, output_path: Path):
    """Download a file with streaming progress and resume support."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = output_path.with_suffix(output_path.suffix + ".part")

    if output_path.exists() and output_path.stat().st_size > 1000_000:
        size_mb = round(output_path.stat().st_size / 1024 / 1024, 1)
        print(f"[已存在] {output_path.name} ({size_mb} MB)，跳过下载。")
        return

    print(f"正在从镜像源高速下载: {output_path.name}")
    print(f"源地址: {url}")

    # Prefer curl.exe on Windows for speed, redirects, retries and resume.
    curl_bin = shutil.which("curl")
    if curl_bin:
        ret = os.system(
            f'curl --fail --location --continue-at - --retry 8 --retry-delay 3 '
            f'--progress-bar "{url}" -o "{part_path}"'
        )
        if ret == 0 and part_path.exists() and part_path.stat().st_size > 1000:
            if output_path.exists():
                output_path.unlink()
            part_path.rename(output_path)
            print(f"[完成] {output_path.name} 下载成功！")
            return

    # Fallback to urllib streaming.
    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    urllib.request.install_opener(opener)

    with urllib.request.urlopen(url) as response, open(part_path, "wb") as out_file:
        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0
        chunk_size = 1024 * 1024 * 2
        while True:
            buffer = response.read(chunk_size)
            if not buffer:
                break
            downloaded += len(buffer)
            out_file.write(buffer)
            if total_size > 0:
                pct = int(downloaded / total_size * 100)
                mb_down = round(downloaded / 1024 / 1024, 1)
                mb_tot = round(total_size / 1024 / 1024, 1)
                sys.stdout.write(f"\r  下载进度: {pct}% ({mb_down}MB / {mb_tot}MB)")
                sys.stdout.flush()
    print()
    if output_path.exists():
        output_path.unlink()
    part_path.rename(output_path)
    print(f"[完成] {output_path.name} 下载成功！")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024 * 4), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_llama_build(server_bin: Path) -> int | None:
    """Return llama.cpp numeric build when the local Windows runtime can run."""
    if os.name != "nt" or not server_bin.exists():
        return None
    try:
        result = subprocess.run(
            [str(server_bin), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    version_text = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"\bbuild\s+(\d+)\b", version_text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def install_llama_runtime():
    """Install an official Windows CPU x64 llama.cpp runtime with Spark2_5 support."""
    server_bin = LLAMA_RUNTIME_DIR / "llama-server.exe"
    current_build = detect_llama_build(server_bin)
    if current_build is not None and current_build >= LLAMA_CPP_MIN_BUILD:
        print(
            f"[已兼容] llama.cpp build {current_build} >= {LLAMA_CPP_MIN_BUILD}，"
            "无需升级 runtime。"
        )
        return

    if current_build is None:
        print("[提示] 未检测到可识别的 llama.cpp Windows runtime，准备安装。")
    else:
        print(
            f"[升级] 当前 llama.cpp build {current_build} 不支持 Spark2_5；"
            f"升级至 {LLAMA_CPP_TAG}。"
        )

    LOCAL_LLM_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = LOCAL_LLM_DIR / LLAMA_RUNTIME_ARCHIVE

    if archive_path.exists():
        actual_hash = sha256_file(archive_path)
        if actual_hash.lower() != LLAMA_RUNTIME_SHA256.lower():
            print("[警告] 已存在的 llama.cpp ZIP 校验失败，删除后重新下载。")
            archive_path.unlink()

    download_file_with_progress(LLAMA_RUNTIME_URL, archive_path)
    actual_hash = sha256_file(archive_path)
    if actual_hash.lower() != LLAMA_RUNTIME_SHA256.lower():
        archive_path.unlink(missing_ok=True)
        raise RuntimeError(
            "llama.cpp runtime SHA-256 校验失败；已删除可疑下载文件。"
        )

    extract_dir = LOCAL_LLM_DIR / ".runtime-win-cpu-x64-extract"
    new_runtime_dir = LOCAL_LLM_DIR / ".runtime-win-cpu-x64-new"
    backup_dir = LOCAL_LLM_DIR / ".runtime-win-cpu-x64-backup"
    for path in (extract_dir, new_runtime_dir, backup_dir):
        if path.exists():
            shutil.rmtree(path)

    try:
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extract_dir)

        server_candidates = list(extract_dir.rglob("llama-server.exe"))
        if len(server_candidates) != 1:
            raise RuntimeError(
                f"llama.cpp ZIP 结构异常：找到 {len(server_candidates)} 个 llama-server.exe。"
            )

        payload_dir = server_candidates[0].parent
        shutil.copytree(payload_dir, new_runtime_dir)
        (new_runtime_dir / ".llama-build").write_text(
            f"{LLAMA_CPP_TAG}\n", encoding="utf-8"
        )

        # Swap whole runtime directories so EXE/DLL versions can never be mixed.
        if LLAMA_RUNTIME_DIR.exists():
            try:
                LLAMA_RUNTIME_DIR.rename(backup_dir)
            except PermissionError as exc:
                raise RuntimeError(
                    "旧 llama-server.exe 正在运行或被占用。请先停止本地大模型服务后重试。"
                ) from exc

        try:
            new_runtime_dir.rename(LLAMA_RUNTIME_DIR)
        except Exception:
            if backup_dir.exists() and not LLAMA_RUNTIME_DIR.exists():
                backup_dir.rename(LLAMA_RUNTIME_DIR)
            raise

        if backup_dir.exists():
            shutil.rmtree(backup_dir)
    finally:
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        if new_runtime_dir.exists():
            shutil.rmtree(new_runtime_dir)

    installed_build = detect_llama_build(LLAMA_RUNTIME_DIR / "llama-server.exe")
    if installed_build is not None and installed_build < LLAMA_CPP_MIN_BUILD:
        raise RuntimeError(
            f"安装后的 llama.cpp build {installed_build} 仍低于 {LLAMA_CPP_MIN_BUILD}。"
        )

    print(
        f"[完成] llama.cpp {LLAMA_CPP_TAG} Windows CPU x64 runtime 已安装，"
        "支持 Spark2_5。"
    )


def download_hf_or_modelscope_repo(
    repo_id: str,
    local_dir: Path,
    hf_mirror: str = "https://hf-mirror.com",
):
    """Download a full model repo strictly into the project-local directory."""
    local_dir.mkdir(parents=True, exist_ok=True)

    if (local_dir / "config.json").exists() and (
        any(local_dir.glob("*.bin"))
        or any(local_dir.glob("*.safetensors"))
        or any(local_dir.glob("*.onnx"))
    ):
        print(f"[已存在] 项目本地模型目录 {local_dir.name} 完整，跳过下载。")
        return

    print_step(f"正在下载模型至项目本地目录: {local_dir}")

    try:
        os.environ["MODELSCOPE_CACHE"] = str(MODELS_DIR)
        from modelscope import snapshot_download

        print(f"使用 ModelScope 国内极速 CDN 直下至 {local_dir.name} ...")
        snapshot_download(repo_id, local_dir=str(local_dir))
        print(f"[完成] ModelScope 成功下载 {repo_id} 至本地项目目录！")
        return
    except Exception as exc:
        print(f"[提示] ModelScope 尝试未成 ({exc})，切换至 HF-Mirror...")

    try:
        os.environ["HF_ENDPOINT"] = hf_mirror
        os.environ["HF_HOME"] = str(MODELS_DIR)
        from huggingface_hub import snapshot_download

        print(f"使用 HF-Mirror ({hf_mirror}) 直下至 {local_dir.name} ...")
        snapshot_download(
            repo_id=repo_id,
            local_dir=str(local_dir),
            local_dir_use_symlinks=False,
        )
        print(f"[完成] HF-Mirror 成功下载 {repo_id} 至本地项目目录！")
        return
    except Exception as exc:
        print(f"[错误] HuggingFace Hub 下载失败: {exc}")


def parse_args():
    parser = argparse.ArgumentParser(description="成都建工 Windows AI 依赖下载器")
    parser.add_argument(
        "--runtime-only",
        action="store_true",
        help="仅安装/升级支持 Spark2_5 的 llama.cpp Windows CPU x64 runtime",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    print("==================================================================")
    print("  🏗️  成都建工 V3.0 · Windows AI 依赖全自动下载器")
    print("  🚀 默认本地模型: Spark-X2.5-4B Q4_K_M")
    print(f"  🧩 llama.cpp 最低兼容版本: {LLAMA_CPP_TAG} / build {LLAMA_CPP_MIN_BUILD}")
    print("==================================================================")

    print_step("[1/3] 检查并安装 Spark2_5 兼容 llama.cpp Windows CPU runtime")
    install_llama_runtime()
    if args.runtime_only:
        return

    print_step("[2/3] 检查并下载 Spark-X2.5-4B (Spark-X2.5-4B-Q4_K_M.gguf)")
    spark_url = (
        "https://hf-mirror.com/abenzerps/Spark-X2.5-4B-GGUF/resolve/main/"
        "Spark-X2.5-4B-Q4_K_M.gguf"
    )
    spark_fallback_url = (
        "https://huggingface.co/abenzerps/Spark-X2.5-4B-GGUF/resolve/main/"
        "Spark-X2.5-4B-Q4_K_M.gguf"
    )
    spark_path = LOCAL_LLM_DIR / "Spark-X2.5-4B-Q4_K_M.gguf"
    try:
        download_file_with_progress(spark_url, spark_path)
    except Exception:
        download_file_with_progress(spark_fallback_url, spark_path)

    print_step("[3/3] 检查并下载全文向量检索模型 (BAAI/bge-m3)")
    bge_m3_dir = MODELS_DIR / "bge-m3"
    download_hf_or_modelscope_repo("BAAI/bge-m3", bge_m3_dir)

    print("\n==================================================================")
    print("  🎉 llama.cpp runtime 与 AI 模型依赖已准备完毕！")
    print("  现在可以直接双击运行 【00_一键启动全部服务.bat】！")
    print("==================================================================")


if __name__ == "__main__":
    main()
