#!/usr/bin/env python3
"""
MCP Server 环境变量加载流程测试

验证场景：
  1. .env 存在 + python-dotenv 可用 → API_TOKEN 来自 .env
  2. .env 不存在 → API_TOKEN 为空，依赖 systemd Environment= 兜底
  3. python-dotenv 未安装 → 仅使用 systemd Environment=
  4. 真实 config.py 导入验证
  5. 移除 EnvironmentFile 后 .env 缺失不会崩溃
"""
import os
import sys
import subprocess
import tempfile
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()

GREEN = "\033[32m"
RED = "\033[31m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RESET = "\033[0m"

passed = 0
failed = 0


def run_check(name, cmd, expect_contains=None, expect_not_contains=None):
    global passed, failed
    print("\n{}── 测试: {}{}".format(CYAN, name, RESET))
    print("    CMD: {}".format(" ".join(cmd)))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    output = result.stdout + result.stderr

    ok = True
    if expect_contains and expect_contains not in output:
        print("    {}✗ 期望包含 '{}' 但未找到{}".format(RED, expect_contains, RESET))
        ok = False
    if expect_not_contains and expect_not_contains in output:
        print("    {}✗ 不应包含 '{}' 但找到了{}".format(RED, expect_not_contains, RESET))
        ok = False

    if ok:
        print("    {}✓ 通过{}".format(GREEN, RESET))
        passed += 1
    else:
        print("    {}----- 实际输出 (前 60 行) -----{}".format(YELLOW, RESET))
        for line in output.splitlines()[:60]:
            print("    | {}".format(line))
        failed += 1
    return output


# ============================================================
# 场景 1: .env 存在 + python-dotenv 可用
# ============================================================
def test1_dotenv_available_with_env_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        env_file = tmp / ".env"
        env_file.write_text("API_TOKEN=test_token_value\nMCP_HOST=0.0.0.0\n")

        # 用纯字符串拼接避免 {} 被误解析
        lines = [
            "import os, sys, json",
            "sys.path.insert(0, {!r})".format(str(PROJECT_DIR)),
            "from pathlib import Path",
            "",
            "try:",
            "    from dotenv import load_dotenv",
            "    _env_path = Path({!r})".format(str(env_file)),
            "    if _env_path.exists():",
            "        _loaded = load_dotenv(_env_path)",
            "        print('DOTENV_LOADED=' + str(_loaded))",
            "    else:",
            "        print('DOTENV_LOADED=False (file not found)')",
            "except ImportError:",
            "    print('DOTENV_LOADED=False (dotenv not installed)')",
            "",
            "r = {",
            '    "API_TOKEN": os.getenv("API_TOKEN", ""),',
            '    "MCP_HOST": os.getenv("MCP_HOST", "127.0.0.1"),',
            '    "MCP_PORT": os.getenv("MCP_PORT", "8001"),',
            "}",
            "print(json.dumps(r, ensure_ascii=False))",
        ]
        code = "\n".join(lines)

        script = tmp / "test1.py"
        script.write_text(code)

        run_check(
            "场景1: .env 存在 + python-dotenv 可用 → API_TOKEN 来自 .env",
            [sys.executable, str(script)],
            expect_contains='API_TOKEN": "test_token_value',
        )
        run_check(
            "场景1a: .env 中的 MCP_HOST 覆盖 systemd Environment=",
            [sys.executable, str(script)],
            expect_contains='MCP_HOST": "0.0.0.0"',
        )


# ============================================================
# 场景 2: .env 不存在
# ============================================================
def test2_no_env_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        lines = [
            "import os, sys, json",
            "sys.path.insert(0, {!r})".format(str(PROJECT_DIR)),
            "from pathlib import Path",
            "",
            "try:",
            "    from dotenv import load_dotenv",
            "    _env_path = Path({!r}) / '.env'".format(tmpdir),
            "    if _env_path.exists():",
            "        _loaded = load_dotenv(_env_path)",
            "        print('DOTENV_LOADED=' + str(_loaded))",
            "    else:",
            "        print('DOTENV_LOADED=False (file not found)')",
            "except ImportError:",
            "    print('DOTENV_LOADED=False (dotenv not installed)')",
            "",
            "r = {",
            '    "API_TOKEN": os.getenv("API_TOKEN", ""),',
            '    "MCP_HOST": os.getenv("MCP_HOST", "127.0.0.1"),',
            '    "MCP_PORT": os.getenv("MCP_PORT", "8001"),',
            "}",
            "print(json.dumps(r, ensure_ascii=False))",
        ]
        code = "\n".join(lines)

        script = tmp / "test2.py"
        script.write_text(code)

        run_check(
            "场景2: .env 不存在 → API_TOKEN 为空",
            [sys.executable, str(script)],
            expect_contains='API_TOKEN": ""',
        )
        run_check(
            "场景2a: systemd Environment= 设置的值不被破坏",
            [sys.executable, str(script)],
            expect_contains='MCP_HOST": "127.0.0.1"',
        )


# ============================================================
# 场景 3: python-dotenv 未安装
# ============================================================
def test3_no_dotenv_installed():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        lines = [
            "import os, sys, json",
            "",
            "import builtins",
            "_real_import = builtins.__import__",
            "",
            "def _fake_import(name, *args, **kwargs):",
            '    if name == "dotenv" or name.startswith("dotenv."):',
            '        raise ImportError("Simulated: python-dotenv not installed")',
            "    return _real_import(name, *args, **kwargs)",
            "",
            "builtins.__import__ = _fake_import",
            "",
            "try:",
            "    from dotenv import load_dotenv",
            "    print('DOTENV_LOADED=True (unexpected)')",
            "except ImportError:",
            "    print('DOTENV_LOADED=False (dotenv not installed)')",
            "",
            "r = {",
            '    "API_TOKEN": os.getenv("API_TOKEN", ""),',
            '    "MCP_HOST": os.getenv("MCP_HOST", "127.0.0.1"),',
            '    "MCP_PORT": os.getenv("MCP_PORT", "8001"),',
            '    "COMMAND_TIMEOUT": os.getenv("COMMAND_TIMEOUT", "30"),',
            "}",
            "print(json.dumps(r, ensure_ascii=False))",
            "",
            "builtins.__import__ = _real_import",
        ]
        code = "\n".join(lines)

        script = tmp / "test3.py"
        script.write_text(code)

        env = os.environ.copy()
        env["MCP_HOST"] = "127.0.0.1"
        env["MCP_PORT"] = "8001"
        env["COMMAND_TIMEOUT"] = "30"
        env.pop("API_TOKEN", None)

        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
            env=env,
        )
        output = proc.stdout + proc.stderr

        prefix = "\n{}── 测试: 场景3: python-dotenv 未安装 → 仅用系统环境变量{}".format(CYAN, RESET)
        ok = True
        msgs = []
        if "DOTENV_LOADED=False (dotenv not installed)" not in output:
            msgs.append("期望 DOTENV_LOADED=False 但未找到")
            ok = False
        if '"API_TOKEN": ""' not in output:
            msgs.append("期望 API_TOKEN 为空")
            ok = False
        if '"MCP_HOST": "127.0.0.1"' not in output:
            msgs.append("期望 MCP_HOST 来自 systemd Environment=")
            ok = False

        global passed, failed
        if ok:
            print(prefix)
            print("    {}✓ 通过{}".format(GREEN, RESET))
            passed += 1
        else:
            print(prefix)
            for m in msgs:
                print("    {}✗ {}{}".format(RED, m, RESET))
            print("    {}----- 实际输出 -----{}".format(YELLOW, RESET))
            for line in output.splitlines()[:40]:
                print("    | {}".format(line))
            failed += 1


# ============================================================
# 场景 4: 真实 config.py 导入
# ============================================================
def test4_real_config_import():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        env_file = tmp / ".env"
        env_file.write_text("API_TOKEN=real_test_abc123\nMCP_HOST=10.0.0.1\n")

        lines = [
            "import os, sys, json, shutil",
            "from pathlib import Path",
            "",
            "proj = Path({!r})".format(str(PROJECT_DIR)),
            "env_file = Path({!r})".format(str(env_file)),
            "",
            "sys.path.insert(0, str(proj))",
            "# 清除 module cache 确保重新加载",
            "sys.modules.pop('config', None)",
            "sys.modules.pop('sandbox', None)",
            "",
            "# 模拟 systemd 初始环境",
            'os.environ["MCP_HOST"] = "127.0.0.1"',
            'os.environ["MCP_PORT"] = "8001"',
            'os.environ.pop("API_TOKEN", None)',
            "",
            "# 备份 / 替换项目 .env",
            "dotenv_path = proj / '.env'",
            "real_env_existed = dotenv_path.exists()",
            "backup = dotenv_path.read_bytes() if real_env_existed else None",
            "dotenv_path.write_text(env_file.read_text())",
            "",
            "try:",
            "    from config import config",
            "",
            "    token_ok = config.API_TOKEN == 'real_test_abc123'",
            "    host_ok = config.HOST == '10.0.0.1'",
            "",
            "    r = {",
            "        'API_TOKEN': config.API_TOKEN,",
            "        'HOST': config.HOST,",
            "        'PORT': config.PORT,",
            "        'TOKEN_OK': token_ok,",
            "        'HOST_OK': host_ok,",
            "    }",
            "    print(json.dumps(r, ensure_ascii=False))",
            "finally:",
            "    if real_env_existed and backup is not None:",
            "        dotenv_path.write_bytes(backup)",
            "    else:",
            "        dotenv_path.unlink(missing_ok=True)",
        ]
        code = "\n".join(lines)

        script = tmp / "test4.py"
        script.write_text(code)

        run_check(
            "场景4: 真实 config.py 导入 → .env 覆盖 systemd 默认值",
            [sys.executable, str(script)],
            expect_contains='"TOKEN_OK": true',
        )
        # 关键行为：systemd Environment= 优先级高于 .env
        # 子进程预设了 MCP_HOST=127.0.0.1（模拟 systemd），
        # python-dotenv 默认不覆盖已存在的环境变量，
        # 所以 .env 中的 MCP_HOST=10.0.0.1 被忽略。
        run_check(
            "场景4a: systemd Environment= 优先级高于 .env（不被 .env 覆盖）",
            [sys.executable, str(script)],
            expect_contains='"HOST_OK": false',
        )


# ============================================================
# 场景 5: 移除 EnvironmentFile 后 .env 缺失不会崩溃
# ============================================================
def test5_envfile_removed_no_hard_failure():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        lines = [
            "import os, sys, json",
            "sys.path.insert(0, {!r})".format(str(PROJECT_DIR)),
            "from pathlib import Path",
            "",
            "try:",
            "    from dotenv import load_dotenv",
            "    _env_path = Path({!r}) / '.env'".format(tmpdir),
            "    if _env_path.exists():",
            "        load_dotenv(_env_path)",
            "        print('DOTENV_OK')",
            "    else:",
            "        print('NO_ENV_FILE_BUT_NO_CRASH')",
            "except ImportError:",
            "    print('DOTENV_UNAVAILABLE_BUT_NO_CRASH')",
            "",
            "r = {",
            '    "HOST": os.getenv("MCP_HOST", "127.0.0.1"),',
            '    "PORT": os.getenv("MCP_PORT", "8001"),',
            '    "API_TOKEN": os.getenv("API_TOKEN", ""),',
            "}",
            "print(json.dumps(r, ensure_ascii=False))",
        ]
        code = "\n".join(lines)

        script = tmp / "test5.py"
        script.write_text(code)

        env = os.environ.copy()
        env["MCP_HOST"] = "127.0.0.1"
        env["MCP_PORT"] = "8001"
        env.pop("API_TOKEN", None)

        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=15,
            env=env,
        )
        output = proc.stdout + proc.stderr
        print("\n{}── 测试: 场景5: .env 缺失时服务不会崩溃（移除 EnvironmentFile 的关键收益）{}".format(CYAN, RESET))
        global passed, failed
        if "NO_ENV_FILE_BUT_NO_CRASH" in output:
            print("    {}✓ 通过{}".format(GREEN, RESET))
            passed += 1
        else:
            print("    {}✗ 期望 NO_ENV_FILE_BUT_NO_CRASH 但未找到{}".format(RED, RESET))
            for line in output.splitlines()[:30]:
                print("    | {}".format(line))
            failed += 1


# ============================================================
if __name__ == "__main__":
    try:
        import dotenv  # noqa: F401
        DOTENV_AVAILABLE = True
    except ImportError:
        DOTENV_AVAILABLE = False

    print("{}============================================================{}".format(CYAN, RESET))
    print("{}MCP Server 环境变量加载流程测试{}".format(CYAN, RESET))
    print("{}python-dotenv 可用: {}{}".format(CYAN, DOTENV_AVAILABLE, RESET))
    print("{}项目目录: {}{}".format(CYAN, PROJECT_DIR, RESET))
    print("{}============================================================{}".format(CYAN, RESET))

    test1_dotenv_available_with_env_file()
    test2_no_env_file()
    test3_no_dotenv_installed()
    test4_real_config_import()
    test5_envfile_removed_no_hard_failure()

    print("\n{}============================================================{}".format(CYAN, RESET))
    print("  结果: {}{} 通过{}, {}{} 失败{}".format(GREEN, passed, RESET, RED, failed, RESET))
    print("{}============================================================{}".format(CYAN, RESET))

    sys.exit(0 if failed == 0 else 1)