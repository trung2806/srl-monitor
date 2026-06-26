import pytest
from main import tick
from srl_cooldown import SystemAlertState, MetricState

def test_tick_precise_recovery_after_long_time_leap():
    """
    Kịch bản 1: Bẻ cong thời gian - Nhảy cóc qua 4000 giây để phục hồi lỗi.
    Chứng minh: Lõi tính toán khoảng cách thời gian chuẩn xác mà không cần sleep thật.
    """
    # 1. Setup dữ liệu đầu vào tĩnh
    thresholds = {"cpu": 80, "memory": 25, "temperature": 75}
    cooldown_seconds = 300
    
    # Ép trạng thái quá khứ đang bị lỗi nặng găm ở mốc t=1000.0
    past_state = SystemAlertState()
    past_state.metrics["cpu"] = MetricState(
        last_status="BREACH",
        last_alert_time=1000.0
    )
    
    # Giả lập dòng thời gian nhảy vọt tới t=5000.0
    mock_current_time = 5000.0
    
    # Dữ liệu mạng giả lập: CPU đã an toàn (30% < 80%), kèm theo các metric khác ở trạng thái sạch
    mock_raw_data = {
        "cpu": [{"index": "all", "total": {"average-1": 30}}],
        "memory": {"utilization": 20},
        "temperature": {"instant": 45, "alarm-status": False}
    }
    
    # 2. Thực thi hàm lõi
    alerts, next_state = tick(
        raw_data=mock_raw_data,
        past_state=past_state,
        current_time=mock_current_time,
        thresholds=thresholds,
        cooldown_seconds=cooldown_seconds
    )
    
    # 3. Xác thực đầu ra
    assert len(alerts) == 1
    assert alerts[0]["event"] == "ALERT_RECOVERED"
    assert alerts[0]["metric"] == "cpu"
    assert alerts[0]["status"] == "OK"
    
    # Khi đã RECOVERED, bộ lưu trữ trạng thái sẽ reset last_alert_time về mốc 0.0 để sẵn sàng cho chu kỳ lỗi mới
    assert next_state.metrics["cpu"].last_status == "OK"
    assert next_state.metrics["cpu"].last_alert_time == 0.0


def test_tick_first_breach_triggers_alert():
    """
    Kịch bản 2: Vòng lặp đầu tiên - Thiết bị sạch gặp lỗi lần đầu.
    Chứng minh: State rỗng ban đầu được xử lý mượt mà và kích hoạt đúng sự kiện TRIGGERED.
    """
    # 1. Setup trạng thái sạch hoàn toàn (chưa từng có lịch sử lỗi)
    thresholds = {"cpu": 80, "memory": 25, "temperature": 75}
    cooldown_seconds = 300
    
    past_state = SystemAlertState() # metrics={} rỗng tuếch
    mock_current_time = 1000.0
    
    # Dữ liệu mạng bất ngờ vượt ngưỡng ở CPU, các metric khác vẫn an toàn
    mock_raw_data = {
        "cpu": [{"index": "all", "total": {"average-1": 95}}], # 95% > 80% -> BREACH
        "memory": {"utilization": 20},
        "temperature": {"instant": 45, "alarm-status": False}
    }
    
    # 2. Thực thi hàm lõi
    alerts, next_state = tick(
        raw_data=mock_raw_data,
        past_state=past_state,
        current_time=mock_current_time,
        thresholds=thresholds,
        cooldown_seconds=cooldown_seconds
    )
    
    # 3. Xác thực đầu ra
    assert len(alerts) == 1
    assert alerts[0]["event"] == "ALERT_TRIGGERED"
    assert alerts[0]["metric"] == "cpu"
    assert alerts[0]["status"] == "BREACH"
    
    assert next_state.metrics["cpu"].last_status == "BREACH"
    assert next_state.metrics["cpu"].last_alert_time == 1000.0
