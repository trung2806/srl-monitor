import pytest
from srl_cooldown import SystemAlertState, process_all_cooldowns

def test_cooldown_alert_lifecycle_without_sleep():
    """Kiểm thử trọn vẹn vòng đời dịch chuyển trạng thái Alert theo đúng Semantics Day 34."""
    state = SystemAlertState()
    t = 1000.0  # Bắt đầu dòng thời gian giả lập tại giây thứ 1000

    # 1. First Breach: Chuyển sang lỗi -> Bắn alert ngay tắp lự
    mock_analysis_breach = {"metrics": {"cpu": {"status": "BREACH"}}}
    alerts, state = process_all_cooldowns(mock_analysis_breach, state, current_time=t)
    assert len(alerts) == 1
    assert alerts[0]["event"] == "ALERT_TRIGGERED"
    assert state.get_metric("cpu").last_status == "BREACH"
    assert state.get_metric("cpu").last_alert_time == 1000.0

    # 2. Repeat trong Cooldown: Trôi qua 150s (vẫn < 300s Cooldown) -> Im lặng
    t += 150.0  # t = 1150.0
    alerts, state = process_all_cooldowns(mock_analysis_breach, state, current_time=t)
    assert len(alerts) == 0
    assert state.get_metric("cpu").last_alert_time == 1000.0  # Mốc ghi nhận cũ phải đứng yên

    # 3. Repeat sau Cooldown: Trôi thêm 151s nữa (tổng 301s > 300s) -> Phải nhắc lại lỗi
    t += 151.0  # t = 1301.0
    alerts, state = process_all_cooldowns(mock_analysis_breach, state, current_time=t)
    assert len(alerts) == 1
    assert alerts[0]["event"] == "ALERT_REPEATED"
    assert state.get_metric("cpu").last_alert_time == 1301.0  # Cập nhật mốc giờ mới

    # 4. Recovery: Lỗi được xử lý, trạng thái về OK -> Bắn alert khép lại chu kỳ
    t += 10.0  # t = 1311.0
    mock_analysis_ok = {"metrics": {"cpu": {"status": "OK"}}}
    alerts, state = process_all_cooldowns(mock_analysis_ok, state, current_time=t)
    assert len(alerts) == 1
    assert alerts[0]["event"] == "ALERT_RECOVERED"
    assert state.get_metric("cpu").last_status == "OK"
    assert state.get_metric("cpu").last_alert_time == 0.0

    # 5. OK duy trì liên tục: Sang chu kỳ tiếp theo vẫn OK -> Tuyệt đối không spam alert rác
    t += 10.0  # t = 1321.0
    alerts, state = process_all_cooldowns(mock_analysis_ok, state, current_time=t)
    assert len(alerts) == 0


def test_independent_metrics_cooldown():
    """Bảo đảm 4 mục tiêu giám sát (cpu/memory/temperature/healthz) độc lập, không dẫm đạp state."""
    state = SystemAlertState()
    
    mock_mixed_analysis = {
        "metrics": {
            "cpu": {"status": "BREACH"},
            "memory": {"status": "OK"},
            "temperature": {"status": "OK"},
            "healthz": {"status": "OK"}
        }
    }
    
    alerts, state = process_all_cooldowns(mock_mixed_analysis, state, current_time=5000.0)
    
    # Chỉ duy nhất CPU kích hoạt alert
    assert len(alerts) == 1
    assert alerts[0]["metric"] == "cpu"
    
    # State Registry bóc tách rạch ròi từng ngăn ô nhớ
    assert state.get_metric("cpu").last_status == "BREACH"
    assert state.get_metric("memory").last_status == "OK"
    assert state.get_metric("temperature").last_status == "OK"
    assert state.get_metric("healthz").last_status == "OK"
