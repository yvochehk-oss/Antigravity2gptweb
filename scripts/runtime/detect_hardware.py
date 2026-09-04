#!/usr/bin/env python3
"""Hardware Adaptive Detection & Profile Persistence Module (Enhanced v2.0).

Detects CPU physical cores, total system memory, AVX2 support, and GPU VRAM capability.
Persists configuration to `.env.hardware` and `.hardware_profile.json` so full probes
are skipped on subsequent startups unless `--force` is specified.
"""

import sys
import os
import json
import platform
import argparse
import subprocess

try:
    import psutil
except ImportError:
    psutil = None

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
ENV_HARDWARE_PATH = os.path.join(PROJECT_DIR, ".env.hardware")
PROFILE_JSON_PATH = os.path.join(PROJECT_DIR, ".hardware_profile.json")
PROFILE_VERSION = "2.0"

def detect_cpu_cores() -> int:
    if psutil:
        cores = psutil.cpu_count(logical=False)
        if cores:
            return cores
    return os.cpu_count() or 4

def detect_total_ram_gb() -> float:
    if psutil:
        mem = psutil.virtual_memory()
        return round(mem.total / (1024 ** 3), 2)
    if platform.system() == "Darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()
            return round(int(out) / (1024 ** 3), 2)
        except Exception:
            pass
    return 16.0

def detect_avx2_support() -> str:
    arch = platform.machine().lower()
    if arch in ("arm64", "aarch64"):
        return "not_applicable"
    if platform.system() == "Darwin":
        # x86 Macs
        return "true"
    try:
        out = subprocess.check_output(["lscpu"]).decode("utf-8")
        return "true" if "avx2" in out.lower() else "false"
    except Exception:
        return "true"

def detect_gpu_capability() -> dict:
    is_mac = platform.system() == "Darwin"
    arch = platform.machine().lower()
    is_apple_silicon = is_mac and arch in ("arm64", "aarch64")
    
    cuda_supported = False
    vram_mb = 0
    
    if not is_mac:
        # Check NVIDIA CUDA via nvidia-smi
        try:
            out = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"], timeout=3).decode("utf-8")
            vram_mb = int(out.strip().split()[0])
            cuda_supported = vram_mb >= 2048
        except Exception:
            cuda_supported = False

    return {
        "is_apple_silicon": is_apple_silicon,
        "metal_supported": is_mac,
        "cuda_supported": cuda_supported,
        "vram_mb": vram_mb
    }

def run_hardware_probe(force=False) -> dict:
    if not force and os.path.exists(ENV_HARDWARE_PATH) and os.path.exists(PROFILE_JSON_PATH):
        try:
            with open(PROFILE_JSON_PATH, "r", encoding="utf-8") as f:
                profile = json.load(f)
                if profile.get("HARDWARE_CONFIGURED") == "true" and profile.get("PROFILE_VERSION") == PROFILE_VERSION:
                    return profile
        except Exception:
            pass

    cores = detect_cpu_cores()
    ram_gb = detect_total_ram_gb()
    avx2_status = detect_avx2_support()
    gpu_info = detect_gpu_capability()

    is_low_spec = (ram_gb < 16.0) or (cores <= 4)
    
    if is_low_spec:
        profile_mode = "LOW_CPU"
        gpu_layers = 0
        llm_slots = 2
        ocr_backend = "cpu"
    else:
        profile_mode = "HIGH_PERFORMANCE"
        if gpu_info["is_apple_silicon"]:
            gpu_layers = 99
            llm_slots = 4
            ocr_backend = "metal"
        elif gpu_info["cuda_supported"]:
            gpu_layers = 99
            llm_slots = 4
            ocr_backend = "cuda"
        else:
            gpu_layers = 0
            llm_slots = 4
            ocr_backend = "cpu"

    profile = {
        "HARDWARE_CONFIGURED": "true",
        "PROFILE_VERSION": PROFILE_VERSION,
        "PROFILE_MODE": profile_mode,
        "CPU_CORES": cores,
        "TOTAL_RAM_GB": ram_gb,
        "AVX2_SUPPORTED": avx2_status,
        "LOCAL_LLM_GPU_LAYERS": gpu_layers,
        "LOCAL_LLM_SLOTS": llm_slots,
        "PADDLE_OCR_BACKEND": ocr_backend,
        "SYSTEM_OS": platform.system(),
        "ARCH": platform.machine()
    }

    with open(ENV_HARDWARE_PATH, "w", encoding="utf-8") as f:
        f.write("# Auto-generated hardware adaptation configuration v2.0\n")
        for k, v in profile.items():
            f.write(f"export {k}=\"{v}\"\n")

    with open(PROFILE_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)

    return profile

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hardware Adaptive Probe v2.0")
    parser.add_argument("--force", action="store_true", help="Force re-detection even if persisted config exists")
    args = parser.parse_args()

    result = run_hardware_probe(force=args.force)
    print(json.dumps(result, indent=2, ensure_ascii=False))
