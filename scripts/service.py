#!/usr/bin/env python3
"""RAG + Agent 系统服务管理脚本

用法:
    python scripts/service.py start            # 启动 API 服务（默认）
    python scripts/service.py start --ui       # 启动 API + Streamlit UI
    python scripts/service.py start --all      # 启动 API + UI + Redis
    python scripts/service.py stop             # 停止所有服务
    python scripts/service.py restart          # 重启所有服务
    python scripts/service.py status           # 查看运行状态
    python scripts/service.py logs [service]   # 查看日志

环境要求:
    - 已激活虚拟环境（或已安装依赖）
    - .env 配置文件已就绪
    - 可选: Redis 服务 (redis://localhost:6379)
"""

import os
import sys
import json
import time
import signal
import argparse
import subprocess
import webbrowser
from pathlib import Path
from datetime import datetime

# ============================================
# Windows GBK 终端兼容：强制 stdout/stderr 用 UTF-8
# ============================================
if sys.platform == "win32" and isinstance(sys.stdout, type(sys.__stdout__)):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        # Python < 3.7 不支持 reconfigure，设环境变量
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# 安全打印：遇到编码问题自动降级
_orig_print = print  # 保留原始 print 引用


def safe_print(*args, **kwargs):
    """打印时自动处理编码异常（Windows GBK 终端兼容）"""
    try:
        _orig_print(*args, **kwargs)
    except UnicodeEncodeError:
        # 降级：移除 emoji 等非 ASCII 字符
        text = " ".join(str(a) for a in args)
        text = text.encode("ascii", "replace").decode("ascii")
        _orig_print(text, **kwargs)


# 全局替换 print → safe_print（避免每处手动改）
import builtins as _builtins
_builtins.print = safe_print

# ============================================
# 路径常量
# ============================================
ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
PID_FILE = ROOT / ".service_pids.json"

# 确保日志目录存在
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ============================================
# Python 解释器探测（优先用 venv 内的）
# ============================================
_PYTHON = sys.executable
for _venv_candidate in [ROOT / "venv", ROOT / ".venv"]:
    _python_path = _venv_candidate / "Scripts" / "python.exe"
    if _python_path.is_file():
        _PYTHON = str(_python_path)
        print(f"📌 使用虚拟环境 Python: {_PYTHON}")
        break

# ============================================
# 服务配置
# ============================================
SERVICES = {
    "api": {
        "name": "API 服务",
        "cmd": [_PYTHON, "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"],
        "cwd": str(ROOT),
        "log_file": str(LOGS_DIR / "api.log"),
        "url": "http://localhost:8000",
        "health_url": "http://localhost:8000/health",
        "port": 8000,
    },
    "ui": {
        "name": "Streamlit UI",
        "cmd": [_PYTHON, "-m", "streamlit", "run", "app/ui.py", "--server.port=8501", "--server.address=0.0.0.0"],
        "cwd": str(ROOT),
        "log_file": str(LOGS_DIR / "ui.log"),
        "url": "http://localhost:8501",
        "port": 8501,
    },
    "redis": {
        "name": "Redis（可选）",
        "cmd": ["redis-server"],
        "cwd": str(ROOT),
        "log_file": str(LOGS_DIR / "redis.log"),
        "url": None,
        "port": 6379,
    },
}


# ============================================
# PID 持久化管理
# ============================================

def load_pids():
    """从 JSON 文件加载记录的 PID"""
    if PID_FILE.exists():
        try:
            return json.loads(PID_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            return {}
    return {}


def save_pids(pids: dict):
    """将 PID 记录写入 JSON 文件"""
    PID_FILE.write_text(
        json.dumps(pids, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def is_pid_alive(pid: int) -> bool:
    """检查 PID 是否存活（跨平台）"""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x100000, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            # fallback: 用 tasklist
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in result.stdout
    else:
        # Linux / macOS
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def kill_process(pid: int, timeout: int = 10) -> bool:
    """尝试优雅终止进程，超时后强制杀死"""
    if not is_pid_alive(pid):
        return True

    try:
        if sys.platform == "win32":
            # Windows: 先 try CTRL_BREAK / TerminateProcess
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=False, timeout=5)
            return True
        else:
            os.kill(pid, signal.SIGTERM)
            # 等待进程退出
            for _ in range(timeout):
                if not is_pid_alive(pid):
                    return True
                time.sleep(1)
            # 超时后强制杀死
            os.kill(pid, signal.SIGKILL)
            return True
    except Exception:
        return False


def find_pid_by_port(port: int) -> int | None:
    """通过 netstat 找到监听某端口的实际 PID（比跟踪 subprocess PID 更可靠）"""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=10,
            )
            # netstat -ano 输出: Proto LocalAddr ForeignAddr State PID
            # 例: TCP 0.0.0.0:8000 0.0.0.0:0 LISTENING 27920
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 5:
                    local_addr = parts[1]  # 如 "0.0.0.0:8000"
                    state = parts[3]        # "LISTENING"
                    pid_str = parts[4]      # "27920"
                    if local_addr.endswith(f":{port}") and state == "LISTENING" and pid_str.isdigit():
                        # netstat 输出可能过时，需确认 PID 真实存活
                        candidate = int(pid_str)
                        if is_pid_alive(candidate):
                            return candidate
        else:
            result = subprocess.run(
                ["ss", "-tlnp", f"sport = :{port}"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTEN" in line:
                    import re
                    m = re.search(r'pid=(\d+)', line)
                    if m:
                        return int(m.group(1))
    except Exception:
        pass
    return None


# ============================================
# 进程启动
# ============================================

def start_service(key: str, wait_health: bool = True) -> bool:
    """启动一个服务，返回是否成功"""
    svc = SERVICES[key]
    pids = load_pids()

    # 检查是否已在运行（优先用端口查）
    port = svc.get("port")
    if port:
        port_pid = find_pid_by_port(port)
        if port_pid:
            # PID 匹配记录 → 已经是我们的在跑
            if key in pids and pids[key] == port_pid:
                print(f"  ⏩ {svc['name']} 已在运行 (PID {port_pid})，跳过")
                return True

            # 端口被别的进程占了 -> 可能是旧版残存，尝试释放
            print(f"  ⚠️  端口 {port} 被其他进程占用 (PID {port_pid})，尝试释放...")

            kill_process(port_pid)
            time.sleep(2)

            # 验证端口是否释放
            if find_pid_by_port(port):
                print(f"  ❌ 无法释放端口 {port}（PID {port_pid} 拒绝终止）")
                print(f"     请手动运行: taskkill /F /PID {port_pid}")
                return False
            print(f"  ✅ 端口 {port} 已释放")
    elif key in pids:
        old_pid = pids[key]
        if is_pid_alive(old_pid):
            print(f"  ⏩ {svc['name']} 已在运行 (PID {old_pid})，跳过")
            return True
        else:
            print(f"  ⚠️  {svc['name']} 记录的 PID {old_pid} 已不存在，重新启动")

    log_file = svc["log_file"]
    print(f"  🚀 启动 {svc['name']}...", end="", flush=True)

    try:
        with open(log_file, "a", encoding="utf-8") as f:
            timestamp = f"\n--- {'='*50}\n"
            timestamp += f"--- 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            timestamp += f"--- 工作目录: {svc['cwd']}\n"
            timestamp += f"{'='*50}\n"
            f.write(timestamp)

            proc = subprocess.Popen(
                svc["cmd"],
                cwd=svc["cwd"],
                stdout=f,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )

        pids[key] = proc.pid
        save_pids(pids)
        print(f" PID {proc.pid}")

        # 等端口就绪后，用 netstat 找到实际监听的 PID（比 subprocess PID 更可靠）
        time.sleep(3)
        port = svc.get("port")
        if port:
            real_pid = find_pid_by_port(port)
            if real_pid and real_pid != proc.pid:
                pids[key] = real_pid
                save_pids(pids)

        # 检查进程存活
        tracked_pid = pids[key]
        if not is_pid_alive(tracked_pid):
            print(f"  ❌ {svc['name']} 启动后异常退出，查看日志: {log_file}")
            tail_log(log_file, n=10)
            return False

        # 健康检查（仅 API 服务）
        if wait_health and svc.get("health_url"):
            if not wait_for_health(svc["health_url"], timeout=120):
                print(f"  ⚠️  {svc['name']} 进程已启动但尚未就绪（超时），请稍后手动检查")
                return False
            print(f"  ✅ {svc['name']} 就绪 → {svc['url']}")
        elif svc.get("url"):
            print(f"  ✅ {svc['name']} 已启动 → {svc['url']}")

        return True

    except FileNotFoundError:
        # 非核心服务（Redis）找不到可执行文件 → 温和提示
        if key == "redis":
            print()
            print(f"  ⚠️  未检测到 Redis")
            print(f"     （多轮对话记忆需要 Redis：docker run -d -p 6379:6379 redis:7-alpine）")
            return False
        print(f"\n  ❌ 找不到可执行文件，请确认依赖已安装:")
        print(f"     {' '.join(svc['cmd'])}")
        return False
    except Exception as e:
        print(f"\n  ❌ 启动失败: {e}")
        return False


def wait_for_health(url: str, timeout: int = 120, interval: int = 3) -> bool:
    """等待服务健康检查通过"""
    import urllib.request
    import urllib.error

    print(f"     等待健康检查...", end="", flush=True)
    start_ts = time.time()

    while time.time() - start_ts < timeout:
        try:
            resp = urllib.request.urlopen(url, timeout=5)
            if resp.status == 200:
                print(f" 耗时 {int(time.time() - start_ts)}s")
                return True
        except (urllib.error.URLError, ConnectionRefusedError, TimeoutError):
            pass

        time.sleep(interval)
        print(".", end="", flush=True)

    print(f" 超时（>{timeout}s）")
    return False


# ============================================
# 进程停止
# ============================================

def stop_service(key: str) -> bool:
    """停止一个服务"""
    svc = SERVICES[key]
    pids = load_pids()

    # 优先用端口查实际 PID
    port = svc.get("port")
    port_pid = find_pid_by_port(port) if port else None

    if port_pid:
        pid = port_pid
    elif key in pids:
        pid = pids[key]
    else:
        print(f"  ⏩ {svc['name']} 未在运行")
        return True

    print(f"  🛑 停止 {svc['name']} (PID {pid})...", end="", flush=True)

    success = kill_process(pid)
    if success:
        pids.pop(key, None)
        save_pids(pids)
        print(" ✅ 已停止")
    else:
        print(" ❌ 停止失败（可能需要手动 kill）")

    return success


# ============================================
# 命令实现
# ============================================

def cmd_start(args):
    """启动服务"""
    print("🔧 启动 RAG + Agent 系统...\n")

    # 先检查 .env
    if not (ROOT / ".env").exists():
        print("❌ 未找到 .env 文件！请先创建（可复制 .env.example）")
        return 1

    # 预检：HuggingFace 可达性（国内需镜像）
    _check_hf_reachable()

    # 确定要启动的服务列表
    if args.all:
        keys = ["api", "ui", "redis"]
    elif args.ui:
        keys = ["api", "ui"]
    else:
        keys = ["api"]

    # Redis 是可选的，启动失败不阻断
    errors = []
    for key in keys:
        ok = start_service(key, wait_health=(key == "api"))
        if not ok:
            errors.append(key)

    print()
    if not errors:
        print("✅ 所有服务已启动！")
    else:
        started = [k for k in keys if k not in errors]
        if started:
            print("✅ 核心服务已启动，部分可选服务未就绪：")
        for k in errors:
            if k == "redis":
                print("   ⚠️  Redis 未启动（多轮对话记忆不可用）")
                print("       Windows 下可通过以下方式安装 Redis：")
                print("         docker run -d -p 6379:6379 redis:7-alpine")
                print("         或安装 Memurai: https://www.memurai.com")
            else:
                print(f"   ❌ {SERVICES[k]['name']} 启动失败")

    if "api" in keys and "api" not in errors:
        print(f"   📡 API:      http://localhost:8000")
        print(f"   📖 文档:     http://localhost:8000/docs")
        print(f"   🩺 健康检查:  http://localhost:8000/health")
    if "ui" in keys and "ui" not in errors:
        print(f"   🖥️  UI:       http://localhost:8501")
    if "redis" in keys and "redis" not in errors:
        print(f"   📦 Redis:    redis://localhost:6379")

    # 自动打开浏览器
    if args.open and "ui" in keys and "ui" not in errors:
        webbrowser.open("http://localhost:8501")
    elif args.open and "api" in keys and "api" not in errors:
        webbrowser.open("http://localhost:8000/docs")

    if errors:
        # 只有核心服务（API/UI）失败才算真错误
        core_errors = [k for k in errors if k != "redis"]
        if core_errors:
            print("\n⚠️  部分核心服务启动异常，请查看日志排查")
            return 1
        print("\n💡 提示: Redis 对多轮对话记忆可选，不影响基础 RAG 问答")

    return 0


def cmd_stop(args):
    """停止所有服务"""
    print("🛑 停止 RAG + Agent 系统...\n")

    all_ok = True
    # 按逆序停止（UI → API → Redis）
    order = ["ui", "api", "redis"]
    stopped_any = False
    for key in order:
        port = SERVICES[key].get("port")
        if port and find_pid_by_port(port):
            stopped_any = True
            ok = stop_service(key)
            if not ok:
                all_ok = False
        elif key in load_pids():
            stopped_any = True
            ok = stop_service(key)
            if not ok:
                all_ok = False

    if not stopped_any:
        print("  ℹ️  没有正在运行的服务")

    print()
    print("✅ 所有服务已停止" if all_ok else "⚠️  部分服务停止异常")
    return 0 if all_ok else 1


def cmd_restart(args):
    """重启所有服务"""
    print("🔄 重启 RAG + Agent 系统...\n")
    # 先停止，再启动
    pids = load_pids()
    if pids:
        cmd_stop(args)
        print()

    # 传递 --ui / --all 参数
    args.start = True
    return cmd_start(args)


def cmd_status(args):
    """查看服务运行状态"""
    print("📊 服务状态\n")
    pids = load_pids()
    header = f"{'服务':<20} {'状态':<12} {'PID':<8} {'端口':<10} {'运行时间':<20}"
    print(header)
    print("-" * len(header))

    has_any = False
    for key in ["api", "ui", "redis"]:
        svc = SERVICES[key]

        # 优先用端口查实际 PID（比 PID 文件更可靠）
        port = svc.get("port")
        port_pid = find_pid_by_port(port) if port else None

        if port_pid:
            status = "✅ 运行中"
            pid_str = str(port_pid)
            runtime = _get_process_runtime(port_pid)
            # 更新记录的 PID
            if key not in pids or pids[key] != port_pid:
                pids[key] = port_pid
                save_pids(pids)
        elif key in pids:
            pid = pids[key]
            alive = is_pid_alive(pid)
            if alive:
                status = "✅ 运行中"
                pid_str = str(pid)
                runtime = _get_process_runtime(pid)
            else:
                status = "❌ 已停止（PID 残留）"
                pid_str = f"{pid} (失效)"
                runtime = "—"
        else:
            status = "⏹️  未启动"
            pid_str = "—"
            runtime = "—"

        port_display = str(port) if port else "—"
        if status != "⏹️  未启动":
            has_any = True
        print(f"{svc['name']:<20} {status:<12} {pid_str:<8} {port_display:<10} {runtime:<20}")

    if not has_any:
        print("\nℹ️  没有正在运行的服务。使用以下命令启动：")
        print("   python scripts/service.py start       # 仅 API")
        print("   python scripts/service.py start --ui  # API + UI")
        print("   python scripts/service.py start --all # API + UI + Redis")
    print()

    # 显示日志文件大小
    print("📁 日志文件：")
    for key in ["api", "ui", "redis"]:
        log = SERVICES[key]["log_file"]
        log_path = Path(log)
        if log_path.exists():
            size = log_path.stat().st_size
            size_str = _format_size(size)
            print(f"   {key}.log  {size_str:>8}")
        else:
            print(f"   {key}.log   — (无)")

    return 0


def cmd_logs(args):
    """查看服务日志"""
    service = args.service or "api"

    if service not in SERVICES:
        print(f"❌ 未知服务: {service}，可选: {', '.join(SERVICES.keys())}")
        return 1

    log_file = SERVICES[service]["log_file"]
    tail_log(log_file, n=args.lines, follow=args.follow)

    return 0


# ============================================
# 辅助函数
# ============================================

def tail_log(filepath: str, n: int = 50, follow: bool = False):
    """取日志尾部 N 行（兼容 Linux tail -n 效果）"""
    path = Path(filepath)
    if not path.exists():
        print(f"日志文件不存在: {filepath}")
        return

    if follow:
        # 实时 tail -f
        if sys.platform == "win32":
            # Windows 用 Python 实现简易 tail -f
            try:
                with open(path, "r", encoding="utf-8") as f:
                    # 跳到末尾
                    lines = f.readlines()
                    if lines:
                        print("".join(lines[-n:]), end="")
                    print(f"\n📡 正在实时监听 {filepath} (Ctrl+C 退出)...\n")
                    while True:
                        line = f.readline()
                        if line:
                            print(line.rstrip())
                        else:
                            time.sleep(0.1)
            except KeyboardInterrupt:
                pass
        else:
            subprocess.run(["tail", "-f", "-n", str(n), filepath], check=False)
    else:
        # 静态打印尾部 N 行
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            tail = lines[-n:] if len(lines) > n else lines
            if lines:
                print("".join(tail), end="")
            else:
                print("(空日志)")
        except Exception as e:
            print(f"读取日志失败: {e}")


def _get_process_runtime(pid: int) -> str:
    """获取进程运行时间（简化实现）"""
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CreationDate", "/format:value"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "CreationDate" in line:
                    dt_str = line.split("=")[1].strip()
                    # WMIC 格式: YYYYMMDDHHMMSS.mmmmmms+ZZZ
                    created = datetime.strptime(dt_str[:14], "%Y%m%d%H%M%S")
                    elapsed = datetime.now() - created
                    return _format_duration(elapsed)
        except Exception:
            pass
    else:
        try:
            import psutil
            proc = psutil.Process(pid)
            created = datetime.fromtimestamp(proc.create_time())
            elapsed = datetime.now() - created
            return _format_duration(elapsed)
        except (ImportError, psutil.NoSuchProcess):
            pass
    return "—"


def _format_duration(td) -> str:
    """格式化 timedelta 为可读字符串"""
    total_seconds = int(td.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    elif total_seconds < 3600:
        return f"{total_seconds // 60}m {total_seconds % 60}s"
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}h {minutes}m"


def _format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f}MB"
    else:
        return f"{size_bytes / 1024 / 1024 / 1024:.1f}GB"


# ============================================
# HuggingFace 网络预检（国内镜像）
# ============================================
_HF_MIRROR = "https://hf-mirror.com"


def _check_hf_reachable():
    """检测 huggingface.co 是否可达，不可达时自动切到 hf-mirror.com"""
    import urllib.request

    # 如果用户已手动设了 HF_ENDPOINT，尊重用户选择
    if os.environ.get("HF_ENDPOINT"):
        return

    try:
        urllib.request.urlopen("https://huggingface.co", timeout=5)
        # 可达，无需切换
        return
    except Exception:
        pass

    # 不可达，尝试镜像
    print("  🌐 huggingface.co 不可达，自动切换至国内镜像...")
    os.environ["HF_ENDPOINT"] = _HF_MIRROR

    # 验证镜像是否可用
    try:
        urllib.request.urlopen(f"{_HF_MIRROR}/BAAI/bge-m3", timeout=10)
        print(f"     ✅ 镜像 {_HF_MIRROR} 可用，模型将从镜像下载")
    except Exception:
        print(f"     ⚠️  镜像也不可达！请手动下载模型或检查网络：")
        print(f"        1. 设置代理: set HTTPS_PROXY=http://your-proxy:port")
        print(f"        2. 或设置镜像: set HF_ENDPOINT={_HF_MIRROR}")
        print(f"        3. 或手动下载模型到本地缓存")


# ============================================
# CLI 入口
# ============================================

def main():
    parser = argparse.ArgumentParser(
        description="RAG + Agent 系统 — 服务管理脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/service.py start             启动 API 服务
  python scripts/service.py start --ui        启动 API + UI
  python scripts/service.py start --all       启动全部（含 Redis）
  python scripts/service.py stop              停止所有服务
  python scripts/service.py restart --ui      重启 API + UI
  python scripts/service.py status            查看运行状态
  python scripts/service.py logs api          查看 API 日志
  python scripts/service.py logs ui --follow  实时查看 UI 日志
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # start
    p_start = subparsers.add_parser("start", help="启动服务")
    p_start.add_argument("--ui", action="store_true", help="同时启动 Streamlit UI")
    p_start.add_argument("--all", action="store_true", help="启动全部服务（含 Redis）")
    p_start.add_argument("--open", "-o", action="store_true", help="启动后自动打开浏览器")
    p_start.set_defaults(func=cmd_start)

    # stop
    p_stop = subparsers.add_parser("stop", help="停止所有服务")
    p_stop.set_defaults(func=cmd_stop)

    # restart
    p_restart = subparsers.add_parser("restart", help="重启所有服务")
    p_restart.add_argument("--ui", action="store_true", help="同时启动 Streamlit UI")
    p_restart.add_argument("--all", action="store_true", help="启动全部服务（含 Redis）")
    p_restart.add_argument("--open", "-o", action="store_true", help="启动后自动打开浏览器")
    p_restart.set_defaults(func=cmd_restart)

    # status
    p_status = subparsers.add_parser("status", help="查看服务状态")
    p_status.set_defaults(func=cmd_status)

    # logs
    p_logs = subparsers.add_parser("logs", help="查看服务日志")
    p_logs.add_argument("service", nargs="?", default="api",
                        choices=list(SERVICES.keys()),
                        help="服务名称 (默认: api)")
    p_logs.add_argument("--lines", "-n", type=int, default=50, help="显示行数")
    p_logs.add_argument("--follow", "-f", action="store_true", help="实时监听日志")
    p_logs.set_defaults(func=cmd_logs)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
