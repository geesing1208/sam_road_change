"""Cancellable JSON subprocess boundary for potentially large input packages."""
import json
import subprocess
from threading import Event


def run_package_process(command, request, environment, cancelled=None):
    environment = {**environment, 'PYTHONIOENCODING': 'utf-8'}
    cancelled = cancelled if cancelled is not None else Event()
    if cancelled.is_set():
        raise ValueError('已取消数据包操作，现有配置保持不变')
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                          env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        payload = json.dumps(request, ensure_ascii=False)
        try:
            while True:
                if cancelled.is_set():
                    raise ValueError('已取消数据包操作，现有配置保持不变')
                try:
                    stdout, stderr = process.communicate(input=payload, timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    payload = None
            if cancelled.is_set():
                raise ValueError('已取消数据包操作，现有配置保持不变')
            if process.returncode:
                raise ValueError((stderr or stdout)[-2500:])
            return json.loads(stdout)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
