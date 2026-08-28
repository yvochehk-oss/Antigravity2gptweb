"""Static startup contract tests that do not touch PostgreSQL or model files."""

import ast
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

TESTS_DIR = Path(__file__).resolve().parent
RAG_DIR = TESTS_DIR.parent
V2_ROOT = RAG_DIR.parents[2]
STARTUP_SCRIPT = V2_ROOT / "scripts" / "runtime" / "start_services_impl.sh"


def _run_jwt_contract(*, tax_env: str, rag_env: str, jwt_override: str | None = None):
    """Run only the startup JWT contract against isolated dotenv files."""
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        tax_dir = root / "source_code" / "0.1_税务管理" / "gtp_V1.0_FULL" / "01_当前完整系统_V1.0" / "chengdu_construction_tax_system_v1_0"
        rag_dir = root / "source_code" / "0.2_RAG系统" / "project-rag-v1.1"
        tax_dir.mkdir(parents=True)
        rag_dir.mkdir(parents=True)
        (tax_dir / ".env").write_text(tax_env, encoding="utf-8")
        (rag_dir / ".env").write_text(rag_env, encoding="utf-8")
        shutil.copytree(V2_ROOT / "scripts", root / "scripts")
        script = root / "start_all.sh"
        shutil.copy2(V2_ROOT / "start_all.sh", script)
        script.chmod(0o755)
        environment = os.environ.copy()
        environment["START_ALL_VALIDATE_JWT_ONLY"] = "1"
        if jwt_override is None:
            environment.pop("JWT_SECRET_KEY", None)
        else:
            environment["JWT_SECRET_KEY"] = jwt_override
        return subprocess.run(
            ["bash", str(script)],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )


def test_launchers_are_postgresql_only_and_do_not_reload_or_auto_seed():
    launchers = (
        V2_ROOT / "start_all.sh",
        V2_ROOT / "stop_all.sh",
        RAG_DIR / "run.sh",
        RAG_DIR / "run_mac.command",
    )
    for launcher in launchers:
        source = launcher.read_text(encoding="utf-8")
        assert "--reload" not in source
        assert "2>/dev/null || true" not in source
    assert "app.seed" not in (V2_ROOT / "start_all.sh").read_text(encoding="utf-8")


def test_rag_direct_launchers_fail_closed_and_accept_file_injected_shared_key():
    rag_launcher = (RAG_DIR / "run.sh").read_text(encoding="utf-8")
    mac_launcher = (RAG_DIR / "run_mac.command").read_text(encoding="utf-8")
    root_launcher = STARTUP_SCRIPT.read_text(encoding="utf-8")

    assert "validate_shared_key_config" in rag_launcher
    assert "RAG_SHARED_API_KEY_FILE" in rag_launcher
    assert '"$SCRIPT_DIR/run.sh" &' in mac_launcher
    assert "resolve_shared_service_key" in root_launcher
    assert "RAG_SHARED_API_KEY_FILE" in root_launcher


def test_rag_lifespan_removes_demo_write_and_fails_on_model_prewarm():
    wiring = (RAG_DIR / "app" / "wiring.py").read_text(encoding="utf-8")
    seed = (RAG_DIR / "app" / "seed.py").read_text(encoding="utf-8")
    assert "YB-DEMO-001" not in wiring
    assert "YB-DEMO-001" not in seed
    assert "raise_on_error=True" in wiring
    assert "PROJECT_RAG_SEED_PROJECT_CODE" in seed
    assert "PROJECT_RAG_SEED_CONTRACT_AMOUNT" in seed


def test_main_compatibility_timestamp_helper_remains_available():
    from app.main import now
    assert callable(now)


def test_root_launcher_uses_owned_pids_and_explicit_effective_ports():
    launcher = STARTUP_SCRIPT.read_text(encoding="utf-8")
    assert 'start_tax "$EFFECTIVE_TAX_PORT"' in launcher
    assert 'start_rag "$EFFECTIVE_RAG_PORT"' in launcher
    assert 'exec nohup "$TAX_DIR/.venv/bin/python" -m uvicorn' in launcher
    assert "remove_owned_pid_file" in launcher
    assert 'rm -f "$RAG_PID_FILE" "$TAX_PID_FILE"' not in launcher


def test_root_launcher_injects_rag_local_fallback_only_after_local_health():
    launcher = STARTUP_SCRIPT.read_text(encoding="utf-8")
    local_start = launcher.index("if local_llm_is_enabled; then")
    fallback_call = launcher.index("\nconfigure_rag_local_llm_fallback\n", local_start)
    rag_start = launcher.index('start_rag "$EFFECTIVE_RAG_PORT"', fallback_call)

    assert "configure_rag_local_llm_fallback() {" in launcher
    assert "export RAG_LLM_LOCAL_BASE_URL" in launcher
    assert "export RAG_LLM_LOCAL_MODEL" in launcher
    assert "unset RAG_LLM_LOCAL_BASE_URL RAG_LLM_LOCAL_MODEL RAG_LLM_LOCAL_TIMEOUT_SECONDS" in launcher
    assert launcher.index("wait_for_local_llm", local_start) < fallback_call
    assert fallback_call < rag_start


def test_shared_jwt_secret_contract_accepts_matching_dotenv_values():
    secret = "shared-jwt-secret-for-startup-contract-32"
    result = _run_jwt_contract(
        tax_env=f"JWT_SECRET_KEY={secret}\n",
        rag_env=f"JWT_SECRET_KEY={secret}\n",
    )
    assert result.returncode == 0, result.stderr
    assert secret not in result.stdout + result.stderr


def test_shared_jwt_secret_contract_rejects_mismatch_without_leaking_values():
    tax_secret = "tax-secret-that-must-not-be-printed-32"
    rag_secret = "rag-secret-that-must-not-be-printed-32"
    result = _run_jwt_contract(
        tax_env=f"JWT_SECRET_KEY={tax_secret}\n",
        rag_env=f"JWT_SECRET_KEY={rag_secret}\n",
    )
    assert result.returncode != 0
    assert "不一致" in result.stderr
    assert tax_secret not in result.stdout + result.stderr
    assert rag_secret not in result.stdout + result.stderr


def test_shared_jwt_secret_contract_rejects_missing_value_without_leaking_other_value():
    configured_secret = "configured-secret-that-must-not-be-printed-32"
    result = _run_jwt_contract(
        tax_env=f"JWT_SECRET_KEY={configured_secret}\n",
        rag_env="# JWT_SECRET_KEY intentionally absent\n",
    )
    assert result.returncode != 0
    assert "JWT_SECRET_KEY 未配置" in result.stderr
    assert configured_secret not in result.stdout + result.stderr


def test_shared_jwt_secret_contract_rejects_short_value_without_leaking_it():
    short_secret = "too-short"
    result = _run_jwt_contract(
        tax_env=f"JWT_SECRET_KEY={short_secret}\n",
        rag_env=f"JWT_SECRET_KEY={short_secret}\n",
    )
    assert result.returncode != 0
    assert "长度不足" in result.stderr
    assert short_secret not in result.stdout + result.stderr


def test_external_jwt_override_wins_over_both_dotenv_values():
    override = "external-shared-secret-that-must-not-be-printed-32"
    result = _run_jwt_contract(
        tax_env="JWT_SECRET_KEY=tax-dotenv-value-that-is-not-used-32\n",
        rag_env="JWT_SECRET_KEY=rag-dotenv-value-that-is-not-used-32\n",
        jwt_override=override,
    )
    assert result.returncode == 0, result.stderr
    assert override not in result.stdout + result.stderr


def test_fastapi_worker_does_not_replace_uvicorn_signal_handlers():
    wiring = (RAG_DIR / "app" / "wiring.py").read_text(encoding="utf-8")
    jobs = (RAG_DIR / "app" / "services" / "jobs.py").read_text(encoding="utf-8")

    wiring_tree = ast.parse(wiring)
    worker_calls = [
        node
        for node in ast.walk(wiring_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "start_worker"
    ]
    assert any(
        any(
            keyword.arg == "install_signal_handlers"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
            for keyword in call.keywords
        )
        for call in worker_calls
    ), "FastAPI lifespan must disable worker signal-handler installation"

    jobs_tree = ast.parse(jobs)
    start_worker = next(
        node
        for node in jobs_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "start_worker"
    )
    keyword_defaults = {
        argument.arg: default
        for argument, default in zip(start_worker.args.kwonlyargs, start_worker.args.kw_defaults)
    }
    assert "install_signal_handlers" in keyword_defaults
    assert "concurrency" in keyword_defaults
    assert isinstance(keyword_defaults["install_signal_handlers"], ast.Constant)
    assert keyword_defaults["install_signal_handlers"].value is True
    assert isinstance(keyword_defaults["concurrency"], ast.Constant)
    assert keyword_defaults["concurrency"].value is None

    # The call that installs process signal handlers must remain guarded by
    # the opt-in flag.  This is the source-level counterpart to the wiring
    # assertion above and prevents the worker from replacing Uvicorn's
    # handlers when FastAPI starts it.
    assert any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "install_signal_handlers"
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_install_signal_handlers"
            for child in ast.walk(node)
        )
        for node in ast.walk(start_worker)
    )
