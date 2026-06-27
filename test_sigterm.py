import json
import pytest
import asyncio
import signal
import os
from main import main_loop

@pytest.mark.asyncio
async def test_main_loop_clean_shutdown_via_signal(tmp_path):
    """Kiểm tra toàn bộ vòng lặp chính thoát sạch sẽ khi nhận tín hiệu từ OS (SIGINT)."""
    
    # 1. Chuẩn bị file cấu hình tạm thời để vượt qua tầng Fail-Fast Loader
    config_file = tmp_path / "thresholds.json"
    config_file.write_text(json.dumps({"cpu": 80, "memory": 90, "temperature": 75}))
    
    # 2. Kích hoạt main_loop chạy nền như một asyncio.Task độc lập
    loop_task = asyncio.create_task(
        main_loop(interval_seconds=1, cooldown_seconds=1, config_path=str(config_file))
    )
    
    # Chờ một khoảng thời gian cực ngắn để Event Loop kịp đăng ký signal handler
    await asyncio.sleep(0.1)
    
    # 3. Giả lập OS bắn tín hiệu SIGINT (tương đương Ctrl+C) trực tiếp vào PID hiện tại
    os.kill(os.getpid(), signal.SIGINT)
    
    # 4. Kiểm tra xem loop_task có tự giác đóng và hoàn thành một cách êm đẹp không
    try:
        await asyncio.wait_for(loop_task, timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail("❌ Khủng hoảng: main_loop bị treo cứng, không chịu thoát sau khi nhận tín hiệu!")
        
    # Khẳng định task đã hoàn thành (done) thành công mà không raise exception ngầm nào ra ngoài
    assert loop_task.done()
