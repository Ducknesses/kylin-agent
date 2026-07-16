"""统一日志配置模块

提供：
  - SensitiveDataFilter：敏感信息脱敏
  - _SafeFormatter：缺失字段安全回退（trace_id 默认填充）
  - setup_logging()：幂等日志初始化（ConsoleHandler + TimedRotatingFileHandler）

本轮 L1 文件日志能力 —— 不修改业务流程，不引入第三方日志依赖。
"""
import logging
import logging.handlers
import re
import sys
from pathlib import Path
from typing import Callable, Tuple, Union
from typing import Any, Tuple


# ============================================================
# 敏感信息脱敏 Filter
# ============================================================

# 敏感 key 关键词（不区分大小写）
_SENSITIVE_KEYS: Tuple[str, ...] = (
    "authorization",
    "bearer",
    "deepseek_api_key",
    "mcp_auth_token",
    "api_token",
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
)

# 脱敏占位符
_REDACTED = "[REDACTED]"

# 匹配模式列表: (正则, 替换文本)
_SENSITIVE_PATTERNS: Tuple[Tuple[re.Pattern, Union[str, Callable[[re.Match], str]]], ...] = ()


def _build_patterns() -> Tuple[Tuple[re.Pattern, Union[str, Callable[[re.Match], str]]], ...]:
    """构建并缓存敏感信息匹配正则列表（模块级一次性构建，并发安全）"""
    global _SENSITIVE_PATTERNS
    if _SENSITIVE_PATTERNS:
        return _SENSITIVE_PATTERNS

    patterns: list[Tuple[re.Pattern, Union[str, Callable[[re.Match], str]]]] = []

    # Authorization: Bearer <value> / Authorization=<value> / Bearer <value>
    patterns.append((re.compile(r"Authorization\s*[:=]\s*Bearer\s+\S+", re.IGNORECASE),
                     f"Authorization: Bearer {_REDACTED}"))
    patterns.append((re.compile(r"Bearer\s+\S+", re.IGNORECASE),
                     f"Bearer {_REDACTED}"))

    # KEY=value 模式（URL 查询参数 / 环境变量风格）
    for key in _SENSITIVE_KEYS:
        # key=value (不区分大小写，保留原 key 大小写)
        patterns.append((
            re.compile(rf"({re.escape(key)})\s*=\s*[^\s&]+", re.IGNORECASE),
            lambda m, k=key: f"{m.group(1)}={_REDACTED}",
        ))
        # key: value (JSON 风格，双引号)
        patterns.append((
            re.compile(rf'"{re.escape(key)}"\s*:\s*"[^"]*"', re.IGNORECASE),
            f'"{key}":"{_REDACTED}"',
        ))
        # key: value (Python dict repr 风格，单引号)
        patterns.append((
            re.compile(rf"'{re.escape(key)}'\s*:\s*'[^']*'", re.IGNORECASE),
            f"'{key}':'{_REDACTED}'",
        ))

    # sk- 前缀密钥 (OpenAI/DeepSeek 风格: sk-xxxxxxxx)
    patterns.append((re.compile(r"\bsk-[a-zA-Z0-9_-]{20,}\b"), f"sk-{_REDACTED}"))



    _SENSITIVE_PATTERNS = tuple(patterns)
    return _SENSITIVE_PATTERNS


class SensitiveDataFilter(logging.Filter):
    """统一敏感信息脱敏 Filter。

    覆盖：
      - str 消息
      - 参数化日志 logger.info("x=%s", value)
      - dict/list/tuple 参数转换后的文本
      - 异常消息中的敏感片段

    不修改原业务对象；脱敏失败时安全返回原始文本。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # 先格式化消息（处理 logger.info("token=%s", secret_value) 等参数化日志）
            if record.args:
                try:
                    formatted = str(record.msg) % record.args
                except (TypeError, ValueError, KeyError):
                    formatted = str(record.msg)
                record.msg = self._redact(formatted)
                # 清空 args：getMessage 已完成参数插值，msg 已替换为脱敏后的完整文本；
                # 清空是为避免后续 Handler 二次格式化和敏感值绕过
                record.args = None
            else:
                record.msg = self._redact(record.msg)
        except Exception:
            pass
        return True

    @staticmethod
    def _redact(value) -> Any:
        """对任意值进行脱敏处理"""
        if not isinstance(value, str):
            return value
        try:
            for pattern, replacement in _build_patterns():
                value = pattern.sub(replacement, value)
        except Exception:
            pass
        return value





# ============================================================
# 日志格式
# ============================================================

_LOG_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)s | trace_id=%(trace_id)s | %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 用于 console 的简单格式（不含 trace_id，开发更清晰）
_CONSOLE_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"


class _SafeFormatter(logging.Formatter):
    """安全 Formatter：对缺失的格式化字段自动填充默认值。

    解决 trace_id 等自定义字段通过 extra 传入时，
    如果不传 extra 会导致 %(trace_id)s KeyError 的问题。
    """

    _DEFAULTS = {"trace_id": "-"}

    def format(self, record: logging.LogRecord) -> str:
        for key, default in self._DEFAULTS.items():
            record.__dict__.setdefault(key, default)
        return super().format(record)


# ============================================================
# 初始化状态（幂等保护）
# ============================================================

_setup_done: bool = False


def _resolve_log_dir(log_dir: str, backend_dir: Path | None = None) -> Path:
    """解析日志目录为绝对路径。

    规则：
      - 绝对路径直接使用
      - 相对路径基于 backend 目录解析（不依赖当前工作目录）
    """
    configured_dir = Path(log_dir).expanduser()
    if configured_dir.is_absolute():
        return configured_dir.resolve()
    if backend_dir is not None:
        base_dir = Path(backend_dir).resolve()
    else:
        base_dir = Path(__file__).resolve().parents[2]
    return (base_dir / configured_dir).resolve()


def setup_logging(
    log_level: str = "INFO",
    log_to_file: bool = True,
    log_dir: str = "./logs",
    log_file: str = "backend.log",
    log_backup_count: int | str = 14,
    backend_dir: Path | None = None,
) -> logging.Logger:
    """统一日志初始化（幂等）。

    多次调用不会重复添加 Handler。

    参数:
        log_level:         日志等级，非法时回退 INFO
        log_to_file:       是否启用文件日志
        log_dir:           日志目录（相对路径以 backend 目录为基准）
        log_file:          日志文件名
        log_backup_count:  轮转保留天数，非法/负值时安全处理
        backend_dir:       backend 目录路径，None 时自动推导

    返回:
        root logger（配置完成）
    """
    global _setup_done
    if _setup_done:
        return logging.getLogger()

    # 解析日志等级
    try:
        level = getattr(logging, log_level.upper())
    except (AttributeError, TypeError):
        level = logging.INFO

    # 解析 backend 目录
    if backend_dir is None:
        backend_dir = Path(__file__).resolve().parent.parent.parent

    # 校验 LOG_FILE 非空
    if not log_file or not log_file.strip():
        log_file = "backend.log"

    # 校验 LOG_BACKUP_COUNT：接受 int / 数字字符串 / None，非法/负数回退 14
    try:
        backup_count = int(log_backup_count)
    except (ValueError, TypeError):
        backup_count = 14
    if backup_count < 0:
        backup_count = 14

    # 获取 root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 清除已有的 Handler（避免 basicConfig 残留）
    # 注意：basicConfig 在 main.py 模块级已执行，会添加一个 StreamHandler
    # 这里移除后重建，确保统一格式
    root_logger.handlers.clear()

    # ── ConsoleHandler（始终启用） ──
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_fmt = _SafeFormatter(_CONSOLE_FORMAT, datefmt=_LOG_DATE_FORMAT)
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # ── 文件日志 ──
    file_handler = None
    log_dir_path: Path | None = None
    if log_to_file:
        log_dir_path = _resolve_log_dir(log_dir, backend_dir)

        # 尝试创建日志目录
        try:
            log_dir_path.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            # fail-soft: 文件日志失败不阻止后端启动
            root_logger.warning(
                "无法创建日志目录 %s: %s。文件日志已禁用，仅输出到终端。",
                log_dir_path, e,
            )
        else:
            log_path = log_dir_path / log_file
            try:
                file_handler = logging.handlers.TimedRotatingFileHandler(
                    filename=str(log_path),
                    when="midnight",
                    interval=1,
                    backupCount=backup_count,
                    encoding="utf-8",
                    utc=False,
                    delay=True,
                )
                file_handler.setLevel(level)
                file_fmt = _SafeFormatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)
                file_handler.setFormatter(file_fmt)
                root_logger.addHandler(file_handler)
            except (OSError, PermissionError) as e:
                root_logger.warning(
                    "无法创建日志文件 %s: %s。文件日志已禁用，仅输出到终端。",
                    log_path, e,
                )

    # ── 添加 Filter ──
    # 添加到每个 Handler（而非 root logger），因为 Python logging 的
    # callHandlers() 在传播时绕过父 logger 的 filter() 调用
    sf = SensitiveDataFilter()
    console_handler.addFilter(sf)
    if file_handler:
        file_handler.addFilter(sf)

    # ── 调整 uvicorn 日志 ──
    # 将 uvicorn.error 和 uvicorn.access 的日志传播到 root，
    # 以便统一使用我们的 Handler 和 Filter
    for uvicorn_logger_name in ("uvicorn.error", "uvicorn.access", "uvicorn"):
        try:
            u_logger = logging.getLogger(uvicorn_logger_name)
            u_logger.handlers.clear()
            u_logger.propagate = True
        except Exception:
            pass

    _setup_done = True

    log_path = (
        str(log_dir_path / log_file)
        if log_to_file and file_handler is not None and log_dir_path is not None
        else "disabled"
    )

    root_logger.info(
        "日志系统初始化完成 (level=%s, file=%s)",
        log_level,
        log_path,
    )
    return root_logger


def reset_setup_state() -> None:
    """重置初始化状态（仅用于测试清理）"""
    global _setup_done
    _setup_done = False
