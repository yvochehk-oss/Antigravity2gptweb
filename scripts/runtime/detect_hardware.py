#!/usr/bin/env python3
"""Hardware adaptive detection and self-healing profile persistence.

The JSON profile is the canonical hardware decision record. `.env.hardware` is a
shell projection generated from the JSON profile. Both files are validated on
startup and rebuilt atomically when either file is missing, stale, corrupted, or
no longer matches the current hardware fingerprint.
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
ENV_HARDWARE_PATH = os.path.join(PROJECT_DIR, ".env.hardware")
PROFILE_JSON_PATH = os.path.join(PROJECT_DIR, ".hardware_profile.json")
PROFILE_VERSION = "2.1"
MIN_CUDA_VRAM_MB = 2048

_REQUIRED_PROFILE_KEYS = {
    "HARDWARE_CONFIGURED",
    "PROFILE_VERSION",
    "HARDWARE_FINGERPRINT",
    "PROFILE_MODE",
    "CPU_CORES",
    "TOTAL_RAM_GB",
    "AVX2_SUPPORTED",
    "LOCAL_LLM_GPU_LAYERS",
    "LOCAL_LLM_SLOTS",
    "OCR_VL_SLOTS",
    "OCR_VL_DEVICE",
    "PADDLE_OCR_BACKEND",
    "SYSTEM_OS",
    "ARCH",
}


def detect_cpu_cores() -> int:
    if psutil:
        cores = psutil.cpu_count(logical=False)
        if cores:
            return int(cores)
    # os.cpu_count() is logical-core count on many platforms. It is still a
    # usable fallback, but unknown/invalid values are kept conservative.
    cores = os.cpu_count()
    return int(cores) if cores and cores > 0 else 1


def detect_total_ram_gb() -> float:
    if psutil:
        mem = psutil.virtual_memory()
        return round(mem.total / (1024**3), 2)

    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=2).strip()
            return round(int(out) / (1024**3), 2)
        if system == "Linux":
            meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
            for line in meminfo.splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024**2), 2)
        if system == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return round(stat.ullTotalPhys / (1024**3), 2)
    except Exception:
        pass

    # Unknown RAM must fail toward the conservative LOW_CPU policy instead of
    # silently pretending the host has 16 GiB.
    return 0.0


def detect_avx2_support() -> str:
    arch = platform.machine().lower()
    if arch in ("arm64", "aarch64"):
        return "not_applicable"

    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.leaf7_features"], timeout=2
            ).decode("utf-8", errors="ignore")
            return "true" if "AVX2" in out.upper() else "false"
        if system == "Linux":
            out = subprocess.check_output(["lscpu"], timeout=2).decode("utf-8", errors="ignore")
            return "true" if "avx2" in out.lower() else "false"
        if system == "Windows":
            # Windows has no stable stdlib-only AVX2 probe. Keep the state
            # explicit rather than fail-open to true.
            return "unknown"
    except Exception:
        return "unknown"
    return "unknown"


def detect_gpu_capability() -> dict:
    system = platform.system()
    arch = platform.machine().lower()
    is_apple_silicon = system == "Darwin" and arch in ("arm64", "aarch64")
    cuda_supported = False
    vram_mb = 0

    if system != "Darwin":
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total",
                    "--format=csv,noheader,nounits",
                ],
                timeout=3,
            ).decode("utf-8", errors="ignore")
            values = [int(line.strip().split()[0]) for line in out.splitlines() if line.strip()]
            vram_mb = max(values) if values else 0
            cuda_supported = vram_mb >= MIN_CUDA_VRAM_MB
        except Exception:
            cuda_supported = False
            vram_mb = 0

    return {
        "is_apple_silicon": is_apple_silicon,
        "metal_supported": is_apple_silicon,
        "cuda_supported": cuda_supported,
        "vram_mb": vram_mb,
        "gpu_memory_type": "unified" if is_apple_silicon else "discrete" if vram_mb else "none",
    }


def collect_hardware_facts() -> dict:
    return {
        "system": platform.system(),
        "arch": platform.machine(),
        "cpu_cores": detect_cpu_cores(),
        "ram_gb": detect_total_ram_gb(),
        "avx2": detect_avx2_support(),
        "gpu": detect_gpu_capability(),
    }


def compute_hardware_fingerprint(facts: dict | None = None) -> str:
    facts = facts or collect_hardware_facts()
    gpu = facts["gpu"]
    fp_str = ":".join(
        [
            str(facts["system"]),
            str(facts["arch"]),
            str(facts["cpu_cores"]),
            str(facts["ram_gb"]),
            str(facts["avx2"]),
            str(gpu.get("is_apple_silicon")),
            str(gpu.get("metal_supported")),
            str(gpu.get("cuda_supported")),
            str(gpu.get("vram_mb", 0)),
        ]
    )
    return hashlib.sha256(fp_str.encode("utf-8")).hexdigest()[:16]


def _render_env(profile: dict) -> str:
    lines = [f"# Auto-generated hardware adaptation configuration v{PROFILE_VERSION}"]
    for key, value in profile.items():
        if isinstance(value, bool):
            value = "true" if value else "false"
        text = str(value).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'export {key}="{text}"')
    return "\n".join(lines) + "\n"


def _atomic_write_text(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _profile_shape_valid(profile: dict) -> bool:
    if not isinstance(profile, dict) or not _REQUIRED_PROFILE_KEYS.issubset(profile):
        return False
    if profile.get("HARDWARE_CONFIGURED") != "true":
        return False
    if profile.get("PROFILE_VERSION") != PROFILE_VERSION:
        return False
    if profile.get("PROFILE_MODE") not in {"LOW_CPU", "HIGH_CPU_SAFE", "HIGH_GPU"}:
        return False
    if profile.get("OCR_VL_DEVICE") not in {"cpu", "gpu"}:
        return False
    if not isinstance(profile.get("LOCAL_LLM_SLOTS"), int) or profile["LOCAL_LLM_SLOTS"] < 1:
        return False
    if not isinstance(profile.get("OCR_VL_SLOTS"), int) or profile["OCR_VL_SLOTS"] < 1:
        return False
    return True


def _load_profile() -> dict | None:
    try:
        with open(PROFILE_JSON_PATH, "r", encoding="utf-8") as handle:
            profile = json.load(handle)
        return profile if _profile_shape_valid(profile) else None
    except Exception:
        return None


def validate_cached_profile(facts: dict | None = None) -> bool:
    if not os.path.exists(ENV_HARDWARE_PATH) or not os.path.exists(PROFILE_JSON_PATH):
        return False
    profile = _load_profile()
    if not profile:
        return False
    try:
        expected_fp = compute_hardware_fingerprint(facts)
        if profile.get("HARDWARE_FINGERPRINT") != expected_fp:
            return False
        env_content = Path(ENV_HARDWARE_PATH).read_text(encoding="utf-8")
        if env_content != _render_env(profile):
            return False
        return True
    except Exception:
        return False


def _build_profile(facts: dict) -> dict:
    cores = int(facts["cpu_cores"])
    ram_gb = float(facts["ram_gb"])
    gpu_info = facts["gpu"]
    is_low_spec = (ram_gb < 16.0) or (cores <= 4)
    has_supported_gpu = bool(gpu_info.get("is_apple_silicon") or gpu_info.get("cuda_supported"))

    if is_low_spec:
        profile_mode = "LOW_CPU"
        gpu_layers = 0
        llm_slots = 2
        ocr_slots = 1
        ocr_device = "cpu"
        ocr_backend = "cpu"
    elif has_supported_gpu:
        profile_mode = "HIGH_GPU"
        gpu_layers = 99
        llm_slots = 4
        ocr_slots = 2
        ocr_device = "gpu"
        ocr_backend = "metal" if gpu_info.get("is_apple_silicon") else "cuda"
    else:
        profile_mode = "HIGH_CPU_SAFE"
        gpu_layers = 0
        llm_slots = 4
        ocr_slots = 1
        ocr_device = "cpu"
        ocr_backend = "cpu"

    return {
        "HARDWARE_CONFIGURED": "true",
        "PROFILE_VERSION": PROFILE_VERSION,
        "HARDWARE_FINGERPRINT": compute_hardware_fingerprint(facts),
        "PROFILE_MODE": profile_mode,
        "CPU_CORES": cores,
        "TOTAL_RAM_GB": ram_gb,
        "AVX2_SUPPORTED": facts["avx2"],
        "GPU_VRAM_MB": int(gpu_info.get("vram_mb", 0)),
        "GPU_MEMORY_TYPE": gpu_info.get("gpu_memory_type", "none"),
        "LOCAL_LLM_GPU_LAYERS": gpu_layers,
        "LOCAL_LLM_SLOTS": llm_slots,
        "OCR_VL_SLOTS": ocr_slots,
        "OCR_VL_DEVICE": ocr_device,
        "PADDLE_OCR_BACKEND": ocr_backend,
        "SYSTEM_OS": facts["system"],
        "ARCH": facts["arch"],
    }


def _persist_profile(profile: dict) -> None:
    # JSON is canonical; env is a deterministic projection. Atomic replacement
    # prevents partial writes from being sourced on the next startup.
    json_text = json.dumps(profile, indent=2, ensure_ascii=False) + "\n"
    env_text = _render_env(profile)
    _atomic_write_text(PROFILE_JSON_PATH, json_text)
    _atomic_write_text(ENV_HARDWARE_PATH, env_text)


def run_hardware_probe(force: bool = False) -> dict:
    facts = collect_hardware_facts()
    if not force and validate_cached_profile(facts):
        profile = _load_profile()
        if profile is not None:
            return profile

    profile = _build_profile(facts)
    _persist_profile(profile)
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardware Adaptive Probe v2.1")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-detection even if persisted config exists",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate JSON profile, env projection, and current hardware fingerprint",
    )
    args = parser.parse_args()

    if args.validate:
        print("VALID" if validate_cached_profile() else "INVALID")
        return 0 if validate_cached_profile() else 1

    result = run_hardware_probe(force=args.force)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
