"""Единая точка запуска: backend + frontend одной командой."""

import subprocess
import sys
import os
import time
import threading


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


def main():
    project_root = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.join(project_root, "backend")
    frontend_dir = os.path.join(project_root, "frontend")

    procs = []
    option_flow_enabled = os.environ.get("MOS_OPTION_TRADE_FLOW_ENABLED", "1").lower() not in {
        "0", "false", "no", "off"
    }

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
        frontend_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=frontend_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            shell=True,
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

        # Запускаем потоки чтения логов (daemon=True — умрут вместе с главным процессом)
        for name, proc in procs:
            t = threading.Thread(target=stream_output, args=(proc, f"[{name}]"), daemon=True)
            t.start()

        # Ждем 4 секунды, чтобы оба сервера успели проинициализироваться
        time.sleep(4)

        # Открываем браузер
        try:
            import webbrowser
            webbrowser.open("http://localhost:5173")
        except Exception as e:
            print(f"[SYSTEM] Could not auto-open browser: {e}")

        print()
        print("=" * 50)
        print("  BTC OPTIONS DASHBOARD")
        print("  ---------------------")
        print("  Frontend: http://localhost:5173")
        print("  Backend:  http://localhost:8005")
        print("  API docs: http://localhost:8005/docs")
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


if __name__ == "__main__":
    main()
