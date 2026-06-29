import json
import os
import signal
import asyncio
import pytest
from dataclasses import asdict
from unittest.mock import patch
import main
from main import DaemonStats, STATS

# Dữ liệu healthy dùng chung — không trigger alert, không gây nhiễu alert_count
_HEALTHY_DATA = {
    "cpu": [{"index": "0", "total": {"average-1": 10, "average-5": 9, "average-15": 8}}],
    "memory": {"utilization": 10},
    "temperature": {"instant": 30, "alarm-status": False},
    "healthz": {"status": "healthy"},
}


@pytest.fixture(autouse=True)
def reset_stats():
    """Reset module-level STATS trước mỗi test để tránh ô nhiễm inter-test."""
    main.STATS.cycle_count = 0
    main.STATS.orphaned_cancel_count = 0
    main.STATS.sighup_count = 0
    main.STATS.timeout_node_count = 0
    main.STATS.alert_count = 0
    yield


# ==============================================================================
# UNIT: DaemonStats dataclass
# ==============================================================================

def test_daemon_stats_defaults():
    s = DaemonStats()
    assert s.cycle_count == 0
    assert s.orphaned_cancel_count == 0
    assert s.sighup_count == 0
    assert s.timeout_node_count == 0
    assert s.alert_count == 0


def test_daemon_stats_asdict_json_serializable():
    """asdict() phải trả về dict với đúng 5 keys, tất cả JSON-serializable."""
    s = DaemonStats(cycle_count=3, sighup_count=1, timeout_node_count=2, alert_count=5, orphaned_cancel_count=0)
    d = asdict(s)
    assert set(d.keys()) == {"cycle_count", "orphaned_cancel_count", "sighup_count", "timeout_node_count", "alert_count"}
    serialized = json.dumps(d)
    assert '"cycle_count": 3' in serialized
    assert '"sighup_count": 1' in serialized


# ==============================================================================
# UNIT: timeout_node_count instrumentation trong _wrapped_poll
# ==============================================================================

@pytest.mark.asyncio
async def test_wrapped_poll_timeout_increments_timeout_count():
    """Khi safe_poll_node vượt quá timeout, STATS.timeout_node_count phải tăng đúng 1."""
    before = main.STATS.timeout_node_count

    async def slow_poll(host: str):
        await asyncio.sleep(999)

    with patch("main.safe_poll_node", side_effect=slow_poll):
        host, data = await main._wrapped_poll("10.0.0.1", timeout=0.05)

    assert main.STATS.timeout_node_count == before + 1
    assert data["healthz"]["status"] == "timeout"


@pytest.mark.asyncio
async def test_wrapped_poll_success_does_not_increment_timeout_count():
    """Safe_poll_node phản hồi đúng hạn thì timeout_node_count KHÔNG tăng."""
    before = main.STATS.timeout_node_count

    async def fast_poll(host: str):
        return _HEALTHY_DATA

    with patch("main.safe_poll_node", side_effect=fast_poll):
        await main._wrapped_poll("10.0.0.1", timeout=5.0)

    assert main.STATS.timeout_node_count == before


# ==============================================================================
# INTEGRATION: cycle_count + sighup_count trong main_loop
# ==============================================================================

@pytest.mark.asyncio
async def test_main_loop_cycle_count_increments(tmp_path):
    """Sau khi main_loop chạy ít nhất 1 chu kỳ, STATS.cycle_count phải > 0."""
    config_file = tmp_path / "thresholds.json"
    config_file.write_text(json.dumps({"cpu": 80, "memory": 90, "temperature": 75}))
    nodes_file = tmp_path / "nodes.json"
    nodes_file.write_text(json.dumps(["10.0.0.1"]))

    async def fast_poll(host, timeout=main.POLL_TIMEOUT_SECONDS):
        return host, _HEALTHY_DATA

    with patch("main._wrapped_poll", side_effect=fast_poll):
        task = asyncio.create_task(main.main_loop(
            interval_seconds=0,
            cooldown_seconds=1,
            config_path=str(config_file),
            nodes_path=str(nodes_file),
        ))
        await asyncio.sleep(0.1)
        os.kill(os.getpid(), signal.SIGINT)
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert main.STATS.cycle_count >= 1


@pytest.mark.asyncio
async def test_main_loop_sighup_increments_sighup_count(tmp_path):
    """Mỗi lần nhận SIGHUP, STATS.sighup_count phải tăng đúng 1."""
    config_file = tmp_path / "thresholds.json"
    config_file.write_text(json.dumps({"cpu": 80, "memory": 90, "temperature": 75}))
    nodes_file = tmp_path / "nodes.json"
    nodes_file.write_text(json.dumps(["10.0.0.1"]))

    async def fast_poll(host, timeout=main.POLL_TIMEOUT_SECONDS):
        return host, _HEALTHY_DATA

    with patch("main._wrapped_poll", side_effect=fast_poll):
        task = asyncio.create_task(main.main_loop(
            interval_seconds=0,
            cooldown_seconds=1,
            config_path=str(config_file),
            nodes_path=str(nodes_file),
        ))
        await asyncio.sleep(0.05)
        os.kill(os.getpid(), signal.SIGHUP)
        await asyncio.sleep(0.1)
        os.kill(os.getpid(), signal.SIGINT)
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert main.STATS.sighup_count >= 1


# ==============================================================================
# INTEGRATION: SIGUSR1 không crash daemon
# ==============================================================================

@pytest.mark.asyncio
async def test_sigusr1_does_not_stop_daemon(tmp_path):
    """SIGUSR1 phải dump stats vào log nhưng KHÔNG dừng daemon — daemon tiếp tục chạy."""
    config_file = tmp_path / "thresholds.json"
    config_file.write_text(json.dumps({"cpu": 80, "memory": 90, "temperature": 75}))
    nodes_file = tmp_path / "nodes.json"
    nodes_file.write_text(json.dumps(["10.0.0.1"]))

    async def fast_poll(host, timeout=main.POLL_TIMEOUT_SECONDS):
        return host, _HEALTHY_DATA

    with patch("main._wrapped_poll", side_effect=fast_poll):
        task = asyncio.create_task(main.main_loop(
            interval_seconds=0,
            cooldown_seconds=1,
            config_path=str(config_file),
            nodes_path=str(nodes_file),
        ))

        await asyncio.sleep(0.05)
        cycle_before = main.STATS.cycle_count

        # SIGUSR1: không được dừng daemon
        os.kill(os.getpid(), signal.SIGUSR1)
        await asyncio.sleep(0.1)

        # Daemon vẫn chạy → cycle_count tiếp tục tăng
        assert not task.done(), "Daemon bị dừng bởi SIGUSR1 — sai, SIGUSR1 chỉ dump stats"
        assert main.STATS.cycle_count > cycle_before, "Daemon không tiếp tục chạy sau SIGUSR1"

        os.kill(os.getpid(), signal.SIGINT)
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
