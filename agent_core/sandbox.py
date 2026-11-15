import sys
import subprocess
from pathlib import Path

def run_python_script(script_code: str, work_dir: Path, timeout: int = 120) -> dict:
    script_path = work_dir / "exec_step.py"
    script_path.write_text(script_code, encoding="utf-8")

    try:
        proc = subprocess.run(
            [sys.executable, str(script_path)],  # Использует python из активного .venv
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:]
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": f"Timeout ({timeout}s) expired."}
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


def run_bash(command: str, work_dir: Path, timeout: int = 60) -> dict:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:]
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": f"Timeout ({timeout}s) expired."}
