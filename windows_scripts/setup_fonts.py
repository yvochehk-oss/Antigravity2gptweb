"""
============================================================================
成都建工 V3.0 - 本地化 WebFont 自动校验与极速部署脚本 (setup_fonts.py)

【设计目标与架构原则】
1. 离线/本地化交付：自动检测并补齐 HarmonyOS Sans SC (Regular / Medium / Bold) 字体；
2. 0 外网 CDN 依赖：所有静态字体直接由本地 FastAPI/Uvicorn 静态目录托管服务；
3. 轻量化 Git 仓库：通过本地/国内 npmmirror 镜像源秒级提取，杜绝将超大二进制提交到 Git。
============================================================================
"""

import os
import io
import tarfile
import urllib.request

FONT_PACKAGE_URL = "https://registry.npmmirror.com/@lobehub/webfont-harmony-sans-sc/-/webfont-harmony-sans-sc-1.0.0.tgz"

TARGET_DIRECTORIES = [
    r"source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/app/static_dist/assets/fonts",
    r"source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/frontend_stitch/public/assets/fonts",
    r"source_code/0.2_RAG系统/project-rag-v1.1/app/static/fonts",
]

CORE_WEIGHTS = ["Regular", "Medium", "Bold"]


def ensure_fonts() -> bool:
    """检查各子系统本地字体文件完整性，必要时自动从国内镜像源极速下载解压。"""
    all_ready = True
    for td in TARGET_DIRECTORIES:
        for w in CORE_WEIGHTS:
            font_path = os.path.join(td, f"HarmonyOS_Sans_SC_{w}.woff2")
            if not os.path.exists(font_path) or os.path.getsize(font_path) < 10000:
                all_ready = False
                break

    if all_ready:
        print("[setup_fonts] 🟢 HarmonyOS Sans SC 本地 WebFont 已全部就绪，无需重复下载。")
        return True

    print(f"[setup_fonts] 🟡 正在从国内镜像源获取 HarmonyOS Sans SC 字体包: {FONT_PACKAGE_URL}")
    req = urllib.request.Request(FONT_PACKAGE_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read()
        
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tf:
            for m in tf.getmembers():
                if m.name.endswith(".woff2") and any(w in m.name for w in CORE_WEIGHTS):
                    fname = os.path.basename(m.name)
                    extracted = tf.extractfile(m)
                    if extracted:
                        data = extracted.read()
                        for td in TARGET_DIRECTORIES:
                            os.makedirs(td, exist_ok=True)
                            with open(os.path.join(td, fname), "wb") as out:
                                out.write(data)
        print("[setup_fonts] 🟢 HarmonyOS Sans SC 核心字体已成功部署到全部子系统静态目录！")
        return True
    except Exception as e:
        print(f"[setup_fonts] ⚠️ 获取字体包遇到异常: {e}，将回退使用系统微软雅黑/Segoe UI。")
        return False


if __name__ == "__main__":
    ensure_fonts()
