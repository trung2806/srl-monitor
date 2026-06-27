import json
import pytest
import asyncio
from srl_cooldown import SystemAlertState
from main import load_thresholds, poll_node, tick

# ==============================================================================
# 1. TẦNG KIỂM THỬ ĐỒNG BỘ (SYNC TESTS)
# ==============================================================================

def test_load_thresholds_success(tmp_path):
    config_file = tmp_path / "valid_thresholds.json"
    valid_data = {"cpu": 80, "memory": 90, "temperature": 75}
    config_file.write_text(json.dumps(valid_data))
    
    thresholds = load_thresholds(str(config_file))
    assert thresholds == valid_data


def test_load_thresholds_missing_keys(tmp_path):
    config_file = tmp_path / "missing_keys.json"
    invalid_data = {"cpu": 80, "memory": 90}
    config_file.write_text(json.dumps(invalid_data))
    
    with pytest.raises(ValueError, match="File cấu hình thiếu các key bắt buộc"):
        load_thresholds(str(config_file))


def test_load_thresholds_invalid_types(tmp_path):
    config_file = tmp_path / "invalid_types.json"
    invalid_data = {"cpu": "high", "memory": 90, "temperature": 75}
    config_file.write_text(json.dumps(invalid_data))
    
    with pytest.raises(TypeError, match="Ngưỡng của 'cpu' phải là số nguyên"):
        load_thresholds(str(config_file))


def test_tick_pure_logic_normal():
    """Kiểm tra Functional Core (tick) với dữ liệu an toàn, không sinh alert."""
    thresholds = {"cpu": 80, "memory": 90, "temperature": 75}
    past_state = SystemAlertState()
    
    raw_data = {
        "cpu": [{"index": "all", "total": {"average-1": 15}}],
        "memory": {"utilization": 40},
        "temperature": {"instant": 35, "alarm-status": False}
    }
    
    alerts, next_state = tick(
        raw_data=raw_data,
        past_state=past_state,
        current_time=1000.0,
        thresholds=thresholds,
        cooldown_seconds=10
    )
    
    # 1. Đảm bảo không có alert nào được phát ra
    assert len(alerts) == 0
    
    # 2. Đảm bảo cả 3 metrics đều được theo dõi và ghi nhận trạng thái 'OK' an toàn
    for metric in ["cpu", "memory", "temperature"]:
        assert metric in next_state.metrics
        assert next_state.metrics[metric].last_status == "OK"


# ==============================================================================
# 2. TẦNG KIỂM THỬ BẤT ĐỒNG BỘ (ASYNC TESTS)
# ==============================================================================

@pytest.mark.asyncio
async def test_poll_node_fallback_behavior():
    """Test xem poll_node() có hoạt động đúng như một Error Boundary khi gặp IP không tồn tại."""
    result = await poll_node(host="192.0.2.1")  
    
    assert isinstance(result, dict)
    assert result["healthz"]["status"] == "unreachable"
    assert "reason" in result["healthz"]
    assert result["cpu"][0]["total"]["average-1"] == 0
    assert result["memory"]["utilization"] == 0


@pytest.mark.asyncio
async def test_poll_node_data_structure():
    """Test cấu trúc dữ liệu trả về của poll_node để đảm bảo tính tương thích với evaluate_metrics."""
    result = await poll_node(host="192.0.2.1")
    
    assert "cpu" in result
    assert "memory" in result
    assert "temperature" in result
    assert isinstance(result["cpu"], list)
    assert isinstance(result["memory"], dict)
    assert isinstance(result["temperature"], dict)
