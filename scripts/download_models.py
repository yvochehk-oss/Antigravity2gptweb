"""Model auto-downloader for Windows and offline deployment.

Downloads Spark-X2.5-4B Q4_K_M and BGE-M3 from fast mirrors.
Strictly writes to project local directory (models/), never pollutes ~/.cache.
"""
import os
import sys
import shutil
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"


def print_step(msg: str):
    print(f"\n==================================================")
    print(f"  {msg}")
    print(f"==================================================")


def download_file_with_progress(url: str, output_path: Path):
    """Download a file with streaming progress bar and resume support."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = output_path.with_suffix(output_path.suffix + ".part")
    
    if output_path.exists() and output_path.stat().st_size > 1000_000:
        size_mb = round(output_path.stat().st_size / 1024 / 1024, 1)
        print(f"[已存在] {output_path.name} ({size_mb} MB)，跳过下载。")
        return

    print(f"正在从镜像源高速下载: {output_path.name}")
    print(f"源地址: {url}")
    
    # Try using curl.exe on Windows for maximum speed and resume support
    curl_bin = shutil.which("curl")
    if curl_bin:
        ret = os.system(f'curl --fail --location --continue-at - --retry 8 --retry-delay 3 --progress-bar "{url}" -o "{part_path}"')
        if ret == 0 and part_path.exists() and part_path.stat().st_size > 1000:
            if output_path.exists():
                output_path.unlink()
            part_path.rename(output_path)
            print(f"[完成] {output_path.name} 下载成功！")
            return

    # Fallback to urllib streaming
    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    urllib.request.install_opener(opener)
    
    with urllib.request.urlopen(url) as response, open(part_path, "wb") as out_file:
        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0
        chunk_size = 1024 * 1024 * 2  # 2MB chunks
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


def download_hf_or_modelscope_repo(repo_id: str, local_dir: Path, hf_mirror: str = "https://hf-mirror.com"):
    """Download a full model repo strictly into project local directory (never into ~/.cache)."""
    local_dir.mkdir(parents=True, exist_ok=True)
    
    # Check if model files already exist locally
    if (local_dir / "config.json").exists() and (any(local_dir.glob("*.bin")) or any(local_dir.glob("*.safetensors")) or any(local_dir.glob("*.onnx"))):
        print(f"[已存在] 项目本地模型目录 {local_dir.name} 完整，跳过下载。")
        return

    print_step(f"正在下载模型至项目本地目录: {local_dir}")
    
    # Method 1: Try ModelScope snapshot_download strictly into local_dir
    try:
        os.environ["MODELSCOPE_CACHE"] = str(MODELS_DIR)
        from modelscope import snapshot_download
        print(f"使用 ModelScope 国内极速 CDN 直下至 {local_dir.name} ...")
        snapshot_download(repo_id, local_dir=str(local_dir))
        print(f"[完成] ModelScope 成功下载 {repo_id} 至本地项目目录！")
        return
    except Exception as e:
        print(f"[提示] ModelScope 尝试未成 ({e})，切换至 HF-Mirror...")

    # Method 2: Try huggingface_hub snapshot_download with hf-mirror strictly into local_dir
    try:
        os.environ["HF_ENDPOINT"] = hf_mirror
        os.environ["HF_HOME"] = str(MODELS_DIR)
        from huggingface_hub import snapshot_download
        print(f"使用 HF-Mirror ({hf_mirror}) 直下至 {local_dir.name} ...")
        snapshot_download(repo_id=repo_id, local_dir=str(local_dir), local_dir_use_symlinks=False)
        print(f"[完成] HF-Mirror 成功下载 {repo_id} 至本地项目目录！")
        return
    except Exception as e:
        print(f"[错误] HuggingFace Hub 下载失败: {e}")


def main():
    print("==================================================================")
    print("  🏗️  成都建工 V3.0 · Windows 模型全自动高速下载器")
    print("  🚀 默认本地模型: Spark-X2.5-4B Q4_K_M")
    print("==================================================================")
    
    # 1. Download Spark-X2.5-4B (GGUF Q4_K_M, ~2.6GB)
    print_step("[1/2] 检查并下载 Spark-X2.5-4B (Spark-X2.5-4B-Q4_K_M.gguf)")
    spark_url = "https://hf-mirror.com/abenzerps/Spark-X2.5-4B-GGUF/resolve/main/Spark-X2.5-4B-Q4_K_M.gguf"
    spark_fallback_url = "https://huggingface.co/abenzerps/Spark-X2.5-4B-GGUF/resolve/main/Spark-X2.5-4B-Q4_K_M.gguf"
    spark_path = MODELS_DIR / "local-llm" / "Spark-X2.5-4B-Q4_K_M.gguf"
    try:
        download_file_with_progress(spark_url, spark_path)
    except Exception:
        download_file_with_progress(spark_fallback_url, spark_path)

    # 2. Download BGE-M3 (Vector Embedding Model)
    print_step("[2/2] 检查并下载全文向量检索模型 (BAAI/bge-m3)")
    bge_m3_dir = MODELS_DIR / "bge-m3"
    download_hf_or_modelscope_repo("BAAI/bge-m3", bge_m3_dir)

    print("\n==================================================================")
    print("  🎉 全部 AI 模型已自动下载并装载完毕！")
    print("  现在可以直接双击运行 【00_一键启动全部服务.bat】！")
    print("==================================================================")


if __name__ == "__main__":
    main()
