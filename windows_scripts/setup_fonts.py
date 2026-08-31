import urllib.request, tarfile, os, io

def ensure_fonts():
    url = "https://registry.npmmirror.com/@lobehub/webfont-harmony-sans-sc/-/webfont-harmony-sans-sc-1.0.0.tgz"
    target_dirs = [
        r"source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/app/static_dist/assets/fonts",
        r"source_code/0.2_RAG系统/project-rag-v1.1/app/static/fonts",
    ]
    # Check if already installed
    all_exist = True
    for td in target_dirs:
        for w in ["Regular", "Medium", "Bold"]:
            if not os.path.exists(os.path.join(td, f"HarmonyOS_Sans_SC_{w}.woff2")):
                all_exist = False
    if all_exist:
        return
    print("Fetching HarmonyOS Sans SC local webfont from npmmirror...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=15)
    tf = tarfile.open(fileobj=io.BytesIO(resp.read()), mode="r:gz")
    for m in tf.getmembers():
        if m.name.endswith(".woff2") and any(w in m.name for w in ["Regular", "Medium", "Bold"]):
            fname = os.path.basename(m.name)
            extracted = tf.extractfile(m)
            if extracted:
                data = extracted.read()
                for td in target_dirs:
                    os.makedirs(td, exist_ok=True)
                    with open(os.path.join(td, fname), "wb") as out:
                        out.write(data)
    print("HarmonyOS Sans SC webfont installed locally!")

if __name__ == "__main__":
    ensure_fonts()
