import json
import os
import signal
import asyncio
import pytest
from unittest.mock import patch, MagicMock
import main


# Dữ liệu mẫu hợp lệ cho evaluate_metrics — tất cả dưới ngưỡng, không trigger alert
_HEALTHY_DATA = {
    "cpu": [{"index": "0", "total": {"average-1": 10, "average-5": 9, "average-15": 8}}],
    "memory": {"utilization": 10},
    "temperature": {"instant": 30, "alarm-status": False},
    "healthz": {"status": "healthy"},
}


# ==============================================================================
# ITEM 4 — SIGHUP INTEGRATION: fleet hot-reload giữa các chu kỳ
# ==============================================================================

@pytest.mark.asyncio
async def test_main_loop_sighup_triggers_fleet_reload(tmp_path):
    """Item 4: Khi nhận SIGHUP, main_loop gọi load_nodes lần thứ hai ở đầu chu kỳ tiếp theo.
    Verify bằng cách đếm số lần load_nodes được gọi: startup (1) + SIGHUP (1) = >=2.
    Reload check xảy ra ở đầu mỗi chu kỳ (không interrupt sleep), nên dùng interval_seconds nhỏ.
    """
    config_file = tmp_path / "thresholds.json"
    config_file.write_text(json.dumps({"cpu": 80, "memory": 90, "temperature": 75}))
    nodes_file = tmp_path / "nodes.json"
    nodes_file.write_text(json.dumps(["10.0.0.1"]))

    load_nodes_call_count = 0
    original_load_nodes = main.load_nodes

    def counting_load_nodes(path: str):
        nonlocal load_nodes_call_count
        load_nodes_call_count += 1
        return original_load_nodes(path)

    async def fast_poll(host: str, timeout: float = main.POLL_TIMEOUT_SECONDS):
        return host, _HEALTHY_DATA

    with patch("main.load_nodes", side_effect=counting_load_nodes), \
         patch("main._wrapped_poll", side_effect=fast_poll):
        loop_task = asyncio.create_task(
            main.main_loop(
                interval_seconds=0,
                cooldown_seconds=1,
                config_path=str(config_file),
                nodes_path=str(nodes_file),
            )
        )

        # Đợi main_loop startup + ít nhất 1 chu kỳ hoàn thành
        await asyncio.sleep(0.05)

        # Gửi SIGHUP → _reload_event.set()
        os.kill(os.getpid(), signal.SIGHUP)

        # Đợi đủ thời gian để chu kỳ tiếp theo chạy qua reload check
        await asyncio.sleep(0.1)

        # Dừng daemon
        os.kill(os.getpid(), signal.SIGINT)
        try:
            await asyncio.wait_for(loop_task, timeout=2.0)
        except asyncio.TimeoutError:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)
            pytest.fail("main_loop hung sau SIGINT — có thể reload block vòng lặp")

    # load_nodes phải được gọi ít nhất 2 lần: lần 1 startup, lần 2+ trên SIGHUP
    assert load_nodes_call_count >= 2, (
        f"load_nodes chỉ được gọi {load_nodes_call_count} lần — SIGHUP reload không kích hoạt"
    )


# ==============================================================================
# ITEM 1 — ORPHAN CANCEL + ITEM 2 INTEGRATION: shutdown kịp thời khi node chậm
# ==============================================================================

@pytest.mark.asyncio
async def test_main_loop_exits_promptly_despite_slow_nodes(tmp_path):
    """Item 1 + 2: Với node cực chậm, main_loop vẫn thoát trong thời gian poll_timeout
    (không bị kẹt 999s). Cơ chế: outer watchdog (asyncio.wait_for) timeout safe_poll_node,
    sau đó _stop_event check break vòng lặp, finally cancel orphaned tasks.
    """
    config_file = tmp_path / "thresholds.json"
    config_file.write_text(json.dumps({"cpu": 80, "memory": 90, "temperature": 75}))
    nodes_file = tmp_path / "nodes.json"
    nodes_file.write_text(json.dumps(["10.0.0.1"]))

    poll_started = asyncio.Event()

    async def slow_safe_poll(host: str):
        poll_started.set()
        await asyncio.sleep(999)  # sẽ bị cancel bởi asyncio.wait_for trong _wrapped_poll

    with patch("main.safe_poll_node", side_effect=slow_safe_poll):
        import time as _time
        start = _time.monotonic()

        loop_task = asyncio.create_task(
            main.main_loop(
                interval_seconds=60,     # sleep dài — verify không bị kẹt ở đây
                cooldown_seconds=1,
                config_path=str(config_file),
                nodes_path=str(nodes_file),
                poll_timeout=0.1,        # outer timeout nhỏ để test nhanh
            )
        )

        # Đợi đến khi slow_safe_poll thực sự đã bắt đầu (không chỉ task chưa schedule)
        await asyncio.wait_for(poll_started.wait(), timeout=2.0)

        # Gửi SIGINT khi node đang chờ
        os.kill(os.getpid(), signal.SIGINT)

        try:
            await asyncio.wait_for(loop_task, timeout=5.0)
        except asyncio.TimeoutError:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)
            pytest.fail(
                "main_loop hung sau SIGINT — outer watchdog hoặc orphan cancel không hoạt động"
            )

        elapsed = _time.monotonic() - start

    assert loop_task.done()
    # Tổng thời gian: poll_timeout(0.1) + asyncio overhead << 5s
    # interval_seconds=60 không được đợi vì _stop_event đã set
    assert elapsed < 5.0
