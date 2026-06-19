import json
import logging
from pathlib import Path

import pytest

from orb_bot import logconf


@pytest.fixture(autouse=True)
def _cleanup_root_handlers():
    """Teardown: close and remove all root handlers after each test.

    Prevents a stale file handler pointing at a deleted tmp_path from
    contaminating subsequent tests or capsys output.
    """
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        # Remove first, then flush/close defensively: a console handler bound to
        # capsys's captured stdout (already closed by the time teardown runs)
        # raises "I/O operation on closed file" on flush/close — must not error.
        root.removeHandler(h)
        try:
            h.flush()
            h.close()
        except (ValueError, OSError):
            pass


def test_configure_logging_returns_bound_logger_with_run_id(tmp_path, capsys):
    log = logconf.configure_logging("INFO", str(tmp_path), "2026-06-19-AAPL")
    log.info("startup", mode="PAPER")
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "expected at least one console JSON line"
    rec = json.loads(out[-1])
    assert rec["event"] == "startup"
    assert rec["mode"] == "PAPER"
    assert rec["run_id"] == "2026-06-19-AAPL"
    assert rec["level"] == "info"


def test_configure_logging_writes_rotating_file(tmp_path):
    log = logconf.configure_logging("DEBUG", str(tmp_path), "2026-06-19-AAPL")
    log.warning("data_warning", iex_partial=True, bars_present=12)
    for h in logging.getLogger().handlers:
        h.flush()
    files = list(Path(tmp_path).glob("orb_*.log"))
    assert len(files) == 1, f"expected one rotating log file, got {files}"
    line = files[0].read_text().strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["event"] == "data_warning"
    assert rec["bars_present"] == 12
    assert rec["run_id"] == "2026-06-19-AAPL"


def test_configure_logging_respects_level(tmp_path, capsys):
    log = logconf.configure_logging("WARNING", str(tmp_path), "rid")
    log.info("should_be_dropped")
    log.error("should_appear")
    out = capsys.readouterr().out
    assert "should_be_dropped" not in out
    assert "should_appear" in out


def test_configure_logging_rejects_unknown_level(tmp_path):
    with pytest.raises(ValueError, match="unknown log level"):
        logconf.configure_logging("VERBOSE", str(tmp_path), "rid")
