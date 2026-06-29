import pytest
from unittest.mock import patch
import asyncio
import main
from main import _apply_node_reload, POLL_TIMEOUT_SECONDS
from srl_cooldown import SystemAlertState

# ==============================================================================
# TẦNG TEST NETWORK & TRANSPORT LAYER 1 BOUNDARY
# ==============================================================================

@pytest.mark.asyncio
async def test_poll_node_fallback_behavior():
    """Test Layer 1 Boundary: Khi node bị mất kết nối (gặp IP không tồn tại),
    safe_poll_node phải chủ động bẫy OSError/Timeout, trả về trạng thái unreachable.
    """
    # Sử dụng IP thuộc dải bét (TEST-NET-1 RFC 5737) để kích hoạt lỗi timeout thật
    unreachable_host = "192.0.2.1" 
    
    # Gửi qua tầng bảo vệ safe_poll_node thay vì poll_node cũ
    result = await main.safe_poll_node(unreachable_host)
    
    # Khẳng định cấu trúc phân tách lỗi vận hành chính xác
    assert result["healthz"]["status"] == "unreachable"
    assert "reason" in result["healthz"]
    assert result["memory"]["utilization"] == 0


@pytest.mark.asyncio
async def test_poll_node_data_structure():
    """Test cấu trúc dữ liệu lỗi của safe_poll_node để đảm bảo tính tương thích
    khi truyền kết quả vào tầng xử lý Functional Core (evaluate_metrics).
    """
    unreachable_host = "192.0.2.1"
    
    result = await main.safe_poll_node(unreachable_host)
    
    # Đảm bảo dù lỗi hay sống, schema data trả về luôn đồng nhất để tầng logic không bị crash
    assert "cpu" in result
    assert isinstance(result["cpu"], list)
    assert "memory" in result
    assert "temperature" in result
    assert "healthz" in result


@pytest.mark.asyncio
async def test_safe_poll_node_catches_unhandled_bug():
    """Test Layer 1 Boundary: Khi poll_node phát sinh lỗi lập trình chưa lường trước (Bug),
    safe_poll_node phải nuốt lỗi, ghi log CRITICAL và gắn nhãn 'error' cho hệ thống.
    """
    host_target = "10.0.0.99"
    error_msg = "Unexpected internal code breakdown"

    # Giả lập poll_node bị dính bug logic phần mềm và nổ thẳng RuntimeError ra ngoài
    with patch("main.poll_node", side_effect=RuntimeError(error_msg)):
        result = await main.safe_poll_node(host_target)
        
    # Khẳng định hệ thống nhận diện đây là lỗi hệ thống (status == error) chứ không phải lỗi mạng thông thường
    assert result["healthz"]["status"] == "error"
    assert error_msg in result["healthz"]["reason"]
    assert result["cpu"][0]["total"]["average-1"] == 0


# ==============================================================================
# ITEM 3: SUCCESS PATH CONTRACT — safe_poll_node passes real parse_control_output dict unchanged
# ==============================================================================

@pytest.mark.asyncio
async def test_poll_node_success_path_passthrough():
    """Item 3: Khi poll_node trả về dict cấu trúc thật (parse_control_output output),
    safe_poll_node không sửa đổi gì — pass-through hoàn toàn.
    Trước D40, poll_node trả hardcoded mock dict. Từ D40, trả parse_control_output output.
    Test này verify contract cho success path (không phải error fallback path).
    """
    real_structure = {
        "cpu": [{"index": "0", "total": {"average-1": 15, "average-5": 14, "average-15": 13}}],
        "memory": {"utilization": 28, "free": 1024},
        "temperature": {"instant": 42, "alarm-status": False},
        "healthz": {"status": "healthy"},
    }
    with patch("main.poll_node", return_value=real_structure):
        result = await main.safe_poll_node("10.0.0.1")
    assert result == real_structure


# ==============================================================================
# ITEM 2: PER-NODE TIMEOUT — _wrapped_poll outer watchdog
# ==============================================================================

@pytest.mark.asyncio
async def test_wrapped_poll_timeout_returns_fallback():
    """Item 2: Khi safe_poll_node chạy lâu hơn timeout, _wrapped_poll trả fallback timeout
    thay vì bị block mãi. Đây là outer watchdog — nằm ngoài inner asyncssh timeouts.
    """
    async def slow_node(host: str):
        await asyncio.sleep(999)  # never completes in test

    with patch("main.safe_poll_node", side_effect=slow_node):
        host, data = await main._wrapped_poll("10.0.0.1", timeout=0.05)

    assert host == "10.0.0.1"
    assert data["healthz"]["status"] == "timeout"
    assert "poll exceeded" in data["healthz"]["reason"]
    assert data["memory"]["utilization"] == 0
    assert isinstance(data["cpu"], list)


@pytest.mark.asyncio
async def test_wrapped_poll_success_path_identity_tuple():
    """Item 2: Khi safe_poll_node trả về bình thường, _wrapped_poll bảo toàn
    cả (host, data) identity tuple — host là string gốc, data là dict không đổi.
    """
    expected_data = {
        "cpu": [{"index": "0", "total": {"average-1": 10}}],
        "memory": {"utilization": 10},
        "temperature": {"instant": 30, "alarm-status": False},
        "healthz": {"status": "healthy"},
    }
    with patch("main.safe_poll_node", return_value=expected_data):
        host, data = await main._wrapped_poll("10.0.0.99")

    assert host == "10.0.0.99"
    assert data == expected_data


# ==============================================================================
# ITEM 4: _apply_node_reload PURE FUNCTION
# ==============================================================================

def test_apply_node_reload_preserves_existing_state():
    """Item 4: Node còn trong list giữ nguyên state object (identity, không phải equality).
    Node mới nhận SystemAlertState() fresh. _apply_node_reload là pure function — không side effect.
    """
    existing_state = SystemAlertState()
    old_registry = {"10.0.0.1": existing_state, "10.0.0.99": SystemAlertState()}

    new_registry = _apply_node_reload(["10.0.0.1", "10.0.0.2"], old_registry)

    assert new_registry["10.0.0.1"] is existing_state   # identity preserved
    assert "10.0.0.2" in new_registry                    # new node added
    assert isinstance(new_registry["10.0.0.2"], SystemAlertState)


def test_apply_node_reload_drops_removed_nodes():
    """Item 4: Node không còn trong new_nodes bị drop khỏi registry.
    State của node bị drop không leak vào registry mới.
    """
    old_registry = {
        "10.0.0.1": SystemAlertState(),
        "10.0.0.99": SystemAlertState(),  # will be removed
    }
    new_registry = _apply_node_reload(["10.0.0.1"], old_registry)

    assert "10.0.0.1" in new_registry
    assert "10.0.0.99" not in new_registry
    assert len(new_registry) == 1
