"""Единая точка запуска: backend + frontend одной командой."""

import subprocess
import sys
import os
import time
import threading
import json
import urllib.request


def stream_output(proc, prefix):
    """Читает stdout процесса в отдельном потоке и печатает с префиксом."""
    try:
        for line in proc.stdout:
            text = line.rstrip()
            if text:
                print(f"{prefix:12s} {text}")
    except (ValueError, OSError):
        # Пайп закрыт — процесс завершился
        pass


def run_smoke_check(frontend_port, backend_port):
    """Verify the isolated ETH HTTP surfaces after child processes start."""
    backend_url = f"http://127.0.0.1:{backend_port}/"
    diagnostics_url = f"http://127.0.0.1:{backend_port}/api/research/diagnostics"
    snapshot_url = f"http://127.0.0.1:{backend_port}/api/snapshot"
    deribit_url = f"http://127.0.0.1:{backend_port}/api/research/deribit-smoke-test"
    frontend_url = f"http://127.0.0.1:{frontend_port}/"
    last_error = None
    for _ in range(20):
        try:
            with urllib.request.urlopen(backend_url, timeout=2) as response:
                backend = json.load(response)
            with urllib.request.urlopen(diagnostics_url, timeout=5) as response:
                diagnostics = json.load(response)
            with urllib.request.urlopen(frontend_url, timeout=2) as response:
                frontend_html = response.read().decode("utf-8")

            snapshot = None
            for _ in range(20):
                with urllib.request.urlopen(snapshot_url, timeout=5) as response:
                    candidate = json.load(response)
                if candidate.get("status") == "ok" and candidate.get("data", {}).get("spot", 0) > 0:
                    snapshot = candidate
                    break
                time.sleep(1)
            if snapshot is None:
                raise RuntimeError("ETH snapshot did not become ready")

            with urllib.request.urlopen(deribit_url, timeout=20) as response:
                deribit = json.load(response)
            sample_names = deribit.get("sample_instrument_names") or []
            result = {
                "backend_service": backend.get("service"),
                "backend_status": backend.get("status"),
                "frontend_has_eth_title": "ETH OPTIONS DASHBOARD" in frontend_html,
                "code_version": diagnostics.get("code_version"),
                "engine_patch_version": diagnostics.get("engine_patch_version"),
                "spot": snapshot["data"]["spot"],
                "deribit_instruments": deribit.get("raw_instruments_count", 0),
                "deribit_samples_are_eth": bool(sample_names)
                and all(str(name).startswith("ETH-") for name in sample_names),
                "frontend_url": frontend_url,
                "backend_url": backend_url,
            }
            if (
                result["backend_service"] != "ETH Options Dashboard"
                or result["backend_status"] != "running"
                or not result["frontend_has_eth_title"]
                or result["code_version"] != "eth_fork_2026_08_14_v1_from_btc_v70"
                or result["spot"] <= 0
                or result["deribit_instruments"] <= 0
                or not result["deribit_samples_are_eth"]
            ):
                raise RuntimeError(f"unexpected ETH smoke response: {result}")
            print(f"[SMOKE] {json.dumps(result, sort_keys=True)}")
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"ETH smoke check failed: {last_error}")


def main():
    project_root = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.join(project_root, "backend")
    frontend_dir = os.path.join(project_root, "frontend")
    runtime_dir = os.path.join(project_root, ".runtime")
    pid_file = os.path.join(runtime_dir, "eth-stack.json")
    backend_port = int(os.environ.get("MOS_BACKEND_PORT", "8101"))
    frontend_port = int(os.environ.get("MOS_FRONTEND_PORT", "5174"))
    os.environ.setdefault("MOS_BACKEND_HOST", "127.0.0.1")
    os.environ["MOS_BACKEND_PORT"] = str(backend_port)
    os.environ["MOS_FRONTEND_PORT"] = str(frontend_port)

    procs = []
    option_flow_enabled = os.environ.get("MOS_OPTION_TRADE_FLOW_ENABLED", "1").lower() not in {
        "0", "false", "no", "off"
    }
    open_browser = os.environ.get("MOS_OPEN_BROWSER", "1").lower() not in {
        "0", "false", "no", "off"
    }
    max_runtime_seconds = float(os.environ.get("MOS_MAX_RUNTIME_SECONDS", "0"))
    smoke_check = os.environ.get("MOS_SMOKE_CHECK", "0").lower() in {
        "1", "true", "yes", "on"
    }
    orchestrator_started_at = time.monotonic()

    try:
        # 1. Запускаем backend (FastAPI + uvicorn)
        print("[BACKEND] Starting backend (FastAPI)...")
        backend_proc = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=backend_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
        )
        procs.append(("backend", backend_proc))

        # 2. Запускаем frontend (Vite dev server)
        print("[FRONTEND] Starting frontend (Vite)...")
        vite_cli = os.path.join(frontend_dir, "node_modules", "vite", "bin", "vite.js")
        if not os.path.exists(vite_cli):
            raise RuntimeError(
                "Frontend dependencies are missing. Run npm.cmd install in frontend first."
            )
        frontend_proc = subprocess.Popen(
            ["node", vite_cli],
            cwd=frontend_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
        )
        procs.append(("frontend", frontend_proc))

        # 3. Запускаем Future Label Worker (Market Memory)
        print("[WORKER] Starting future label worker...")
        worker_proc = subprocess.Popen(
            [sys.executable, "-m", "workers.future_label_worker"],
            cwd=backend_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
        )
        procs.append(("worker", worker_proc))

        # 4. Отдельный read-only observer публичных опционных сделок.
        # Он пишет только option_trade_flow.db и не участвует в формулах MOS.
        if option_flow_enabled:
            print("[OPTION FLOW] Starting public option trade-flow observer...")
            option_flow_proc = subprocess.Popen(
                [sys.executable, "-m", "workers.option_trade_flow_worker"],
                cwd=backend_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
            )
            procs.append(("option-flow", option_flow_proc))

        os.makedirs(runtime_dir, exist_ok=True)
        with open(pid_file, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "project_root": project_root,
                    "orchestrator_pid": os.getpid(),
                    "started_at": time.time(),
                    "children": [
                        {"name": name, "pid": proc.pid}
                        for name, proc in procs
                    ],
                },
                handle,
                indent=2,
            )

        # Запускаем потоки чтения логов (daemon=True — умрут вместе с главным процессом)
        for name, proc in procs:
            t = threading.Thread(target=stream_output, args=(proc, f"[{name}]"), daemon=True)
            t.start()

        # Ждем 4 секунды, чтобы оба сервера успели проинициализироваться
        time.sleep(4)

        for name, proc in procs:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"{name} exited during startup with code {proc.returncode}"
                )

        if smoke_check:
            run_smoke_check(frontend_port, backend_port)
            return

        # Открываем браузер
        if open_browser:
            try:
                import webbrowser
                webbrowser.open(f"http://localhost:{frontend_port}")
            except Exception as e:
                print(f"[SYSTEM] Could not auto-open browser: {e}")

        print()
        print("=" * 50)
        print("  ETH OPTIONS DASHBOARD")
        print("  ---------------------")
        print(f"  Frontend: http://localhost:{frontend_port}")
        print(f"  Backend:  http://localhost:{backend_port}")
        print(f"  API docs: http://localhost:{backend_port}/docs")
        print("=" * 50)
        print()
        print("Press Ctrl+C to stop both servers.")
        print()

        # Главный цикл: ждём, пока один из процессов не упадёт или пользователь не нажмет Ctrl+C
        while True:
            for name, proc in procs:
                if proc.poll() is not None:
                    print(f"\n[WARNING] {name} exited with code {proc.returncode}")
                    raise KeyboardInterrupt  # Корректно завершаем всё
            if (
                max_runtime_seconds > 0
                and time.monotonic() - orchestrator_started_at >= max_runtime_seconds
            ):
                print("\n[SMOKE] Configured runtime elapsed; stopping ETH stack.")
                break
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n[STOP] Stopping...")
    finally:
        for name, proc in procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
                print(f"  [OK] {name} stopped")
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                print(f"  [ERROR] {name} killed")
        try:
            os.remove(pid_file)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
