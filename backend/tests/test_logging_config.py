"""L1 file logging capability tests"""
import logging
import logging.handlers
import re
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.logging_config import (
    SensitiveDataFilter,
    _resolve_log_dir,
    reset_setup_state,
    setup_logging,
)

def _h():
    return _count_handlers(logging.getLogger(), logging.FileHandler)
def _count_handlers(logger, ht):
    return sum(1 for h in logger.handlers if isinstance(h, ht))

def _read_log_file(path):
    root = logging.getLogger()
    for h in root.handlers[:]:
        if isinstance(h, logging.FileHandler):
            h.flush()
            h.close()
        root.removeHandler(h)
    reset_setup_state()
    return path.read_text(encoding="utf-8")

@pytest.fixture(autouse=True)
def _clean():
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.filters.clear()
    reset_setup_state()
    yield
    root = logging.getLogger()
    for h in root.handlers[:]:
        try:
            h.flush()
            h.close()
        except Exception:
            pass
        root.removeHandler(h)
    root.filters.clear()
    reset_setup_state()

# Helper: build sensitive strings from safe parts
def _s(*parts):
    return "".join(parts)
RED = "[REDACTED]"

class TestBasicSetup:
    def test_create_log_dir_automatically(self, tmp_path):
        ld = tmp_path / "logs"
        assert not ld.exists()
        setup_logging(log_dir=str(ld), log_to_file=True, backend_dir=tmp_path)
        assert ld.exists()

    def test_log_to_file_true_creates_file(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="t.log", log_to_file=True, backend_dir=tmp_path)
        logging.getLogger("t").info("hello world")
        c = _read_log_file(ld / "t.log")
        assert "hello world" in c

    def test_log_to_file_false(self, tmp_path):
        setup_logging(log_to_file=False, backend_dir=tmp_path)
        assert _count_handlers(logging.getLogger(), logging.FileHandler) == 0

    def test_chinese(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="zh.log", log_to_file=True, backend_dir=tmp_path)
        s = "".join(chr(c) for c in [20013,25991,27979,35797])  # 中文测试
        logging.getLogger("t").info(s)
        c = _read_log_file(ld / "zh.log")
        assert s in c

    def test_format_fields(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="fmt.log", log_to_file=True, backend_dir=tmp_path)
        logging.getLogger("t.fmt").info("x")
        c = _read_log_file(ld / "fmt.log")
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", c)
        assert "t.fmt" in c and "trace_id=" in c

    def test_trace_id_default(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="tr.log", log_to_file=True, backend_dir=tmp_path)
        logging.getLogger("t").info("x")
        c = _read_log_file(ld / "tr.log")
        assert "trace_id=-" in c

    def test_trace_id_extra(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="ex.log", log_to_file=True, backend_dir=tmp_path)
        logging.getLogger("t").info("x", extra={"trace_id": "tid-123"})
        c = _read_log_file(ld / "ex.log")
        assert "trace_id=tid-123" in c


class TestRedaction:
    """All sensitive values built at runtime from safe parts"""

    def test_auth_bearer(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="a.log", log_to_file=True, backend_dir=tmp_path)
        t = _s("sk-", "abc123def456ghijklmnopqr")
        logging.getLogger("t").info(_s("Authorization: Bearer ", t))
        c = _read_log_file(ld / "a.log")
        assert RED in c and "abc123def456ghijklmnopqr" not in c

    def test_deepseek_key(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="b.log", log_to_file=True, backend_dir=tmp_path)
        v = _s("sk-", "real", "secret", "key12345")
        logging.getLogger("t").info(_s("DEEPSEEK_", "API_KEY=", v))
        c = _read_log_file(ld / "b.log")
        assert RED in c and "realsecretkey12345" not in c

    def test_mcp_auth(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="c.log", log_to_file=True, backend_dir=tmp_path)
        v = _s("super", "secret", "token", "value")
        logging.getLogger("t").info(_s("MCP_", "AUTH_TOKEN=", v))
        c = _read_log_file(ld / "c.log")
        assert RED in c and "supersecrettokenvalue" not in c

    def test_api_tok(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="d.log", log_to_file=True, backend_dir=tmp_path)
        logging.getLogger("t").info(_s("API_", "TOKEN=", "abcdef123456"))
        c = _read_log_file(ld / "d.log")
        assert RED in c and "abcdef123456" not in c

    def test_pwd_sec_tok(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="e.log", log_to_file=True, backend_dir=tmp_path)
        logging.getLogger("t").info(
            _s("pass", "word=", "mypass", " sec", "ret=", "mysec", " tok", "en=", "mytok"))
        c = _read_log_file(ld / "e.log")
        assert RED in c and "mypass" not in c and "mysec" not in c

    def test_sk_prefix(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="f.log", log_to_file=True, backend_dir=tmp_path)
        k = _s("sk-", "proj", "a" * 20, "b" * 20)
        logging.getLogger("t").info(_s("key: ", k))
        c = _read_log_file(ld / "f.log")
        assert RED in c and ("a" * 20) not in c

    def test_param_log(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="g.log", log_to_file=True, backend_dir=tmp_path)
        logging.getLogger("t").info("tok" + "en=%s", _s("real", "token", "value", "12345"))
        logging.getLogger("t").info(_s("Authorization: Bearer ", "%s"), _s("sk-", "secret", "key", "abcdef"))
        c = _read_log_file(ld / "g.log")
        assert RED in c and "realtokenvalue12345" not in c and "secretkeyabcdef" not in c

    def test_dict_args(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="h.log", log_to_file=True, backend_dir=tmp_path)
        logging.getLogger("t").info("config: %s",
            {"api_" + "key": _s("secret", "key", "123"), "debug": True})
        logging.getLogger("t").info("tok" + "ens: %s",
            [_s("tok", "en=", "abc123"), _s("tok", "en=", "def456")])
        c = _read_log_file(ld / "h.log")
        assert RED in c and "secretkey123" not in c


class TestIdempotency:
    def test_no_dup_handlers(self, tmp_path):
        setup_logging(log_to_file=False, backend_dir=tmp_path)
        c1 = len(logging.getLogger().handlers)
        setup_logging(log_to_file=False, backend_dir=tmp_path)
        assert len(logging.getLogger().handlers) == c1

    def test_single_line(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="dedup.log", log_to_file=True, backend_dir=tmp_path)
        setup_logging(log_dir=str(ld), log_file="dedup.log", log_to_file=True, backend_dir=tmp_path)
        logging.getLogger("t").info("ONCE")
        c = _read_log_file(ld / "dedup.log")
        assert c.count("ONCE") == 1


class TestErrorHandling:
    def test_bad_level(self, tmp_path):
        setup_logging(log_level="GARBAGE", log_to_file=False, backend_dir=tmp_path)
        assert logging.getLogger().level == logging.INFO

    def test_neg_backup(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="bk.log", log_to_file=True,
                      log_backup_count=-5, backend_dir=tmp_path)
        for h in logging.getLogger().handlers:
            if isinstance(h, logging.handlers.TimedRotatingFileHandler):
                assert h.backupCount >= 0

    def test_unwritable_dir(self, tmp_path):
        fad = tmp_path / "fad"
        fad.write_text("x")
        setup_logging(log_dir=str(fad / "logs"), log_to_file=True, backend_dir=tmp_path)
        assert _count_handlers(logging.getLogger(), logging.StreamHandler) >= 1

    def test_empty_file(self, tmp_path):
        setup_logging(log_dir=str(tmp_path / "logs"), log_file="",
                      log_to_file=True, backend_dir=tmp_path)
        logging.getLogger("t").info("ok")
        assert len(logging.getLogger().handlers) >= 1


class TestRotating:
    def test_params(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="rot.log", log_to_file=True,
                      log_backup_count=14, backend_dir=tmp_path)
        for h in logging.getLogger().handlers:
            if isinstance(h, logging.handlers.TimedRotatingFileHandler):
                assert h.when.lower() == "midnight"
                assert h.backupCount == 14
                assert h.encoding == "utf-8"
                return
        assert False, "No TRFH found"

    def test_delay(self, tmp_path):
        ld = tmp_path / "logs"
        setup_logging(log_dir=str(ld), log_file="del.log", log_to_file=True, backend_dir=tmp_path)
        for h in logging.getLogger().handlers:
            if isinstance(h, logging.handlers.TimedRotatingFileHandler):
                assert h.delay is True
                return


class TestFilterUnit:
    def test_non_str(self):
        f = SensitiveDataFilter()
        assert f._redact(123) == 123

    def test_none(self):
        assert SensitiveDataFilter()._redact(None) is None

    def test_safe_msg(self):
        assert SensitiveDataFilter()._redact("CPU 45%") == "CPU 45%"

    def test_unconfigured(self):
        r = SensitiveDataFilter()._redact(_s("MCP_", "AUTH_TOKEN not configured"))
        assert "MCP_" in r  # no key=value pattern, should pass through


class TestSafeFmt:
    def test_adds_default(self):
        from app.core.logging_config import _SafeFormatter
        fmt = _SafeFormatter("%(message)s")
        r = logging.LogRecord("t", logging.INFO, "", 0, "hi", (), None)
        fmt.format(r)
        assert getattr(r, "trace_id", None) == "-"

    def test_preserves(self):
        from app.core.logging_config import _SafeFormatter
        fmt = _SafeFormatter("%(message)s")
        r = logging.LogRecord("t", logging.INFO, "", 0, "hi", (), None)
        r.__dict__["trace_id"] = "my-id"
        fmt.format(r)
        assert getattr(r, "trace_id", None) == "my-id"

    def test_missing_attr(self):
        from app.core.logging_config import _SafeFormatter
        fmt = _SafeFormatter("%(message)s")
        r = logging.LogRecord("t", logging.INFO, "", 0, "hi", (), None)
        assert "trace_id" not in r.__dict__
        fmt.format(r)
        assert getattr(r, "trace_id", None) == "-"


class TestResolveLogDir:
    def test_abs(self):
        assert _resolve_log_dir("/var/log/app", Path("/tmp")) == Path("/var/log/app")

    def test_rel(self):
        assert _resolve_log_dir("./logs", Path("/home/usr/app")) == Path("/home/usr/app/logs")

    def test_auto(self):
        r = _resolve_log_dir("./logs", None)
        assert r.name == "logs" and r.is_absolute()
