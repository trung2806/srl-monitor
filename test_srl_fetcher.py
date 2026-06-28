import pytest
import json
from unittest.mock import MagicMock
from srl_fetcher import SRLCliFetcher, parse_control_output, SRLFetchError, CONTROL_CMD


# ==============================================================================
# TẦNG KIỂM THỬ 1: PURE FUNCTION LAYER (parse_control_output)
# ==============================================================================
def test_parse_control_output_happy_path():
    raw = '{"memory": {"utilization": 22}}'
    assert parse_control_output(raw) == {"memory": {"utilization": 22}}


def test_parse_control_output_unwrap_list():
    raw = '[{"memory": {"utilization": 22}}]'
    assert parse_control_output(raw) == {"memory": {"utilization": 22}}


def test_parse_control_output_empty_or_whitespace():
    with pytest.raises(ValueError) as exc_info:
        parse_control_output("   ")
    assert "Dữ liệu xuất từ CLI rỗng" in str(exc_info.value)


def test_parse_control_output_invalid_json():
    with pytest.raises(json.JSONDecodeError):
        parse_control_output("{broken json")


def test_parse_control_output_empty_list():
    with pytest.raises(ValueError) as exc_info:
        parse_control_output("[]")
    assert "Mảng JSON từ thiết bị trả về bị rỗng" in str(exc_info.value)


def test_parse_control_output_not_a_dict():
    with pytest.raises(TypeError) as exc_info:
        parse_control_output('"just a string string"')
    assert "phải là dict" in str(exc_info.value)


def test_parse_control_output_on_real_fixture():
    """Verify parser xử lý đúng JSON thật từ device — dùng fixture control_A.json."""
    raw = open("control_A.json", encoding="utf-8").read()
    result = parse_control_output(raw)
    assert result["healthz"]["status"] == "healthy"
    assert isinstance(result["cpu"], list)
    assert result["cpu"][0]["index"] == "all"
    assert "utilization" in result["memory"]
    assert "instant" in result["temperature"]


# ==============================================================================
# TẦNG KIỂM THỬ 2: TRANSPORT & FETCHER LAYER (SRLCliFetcher)
# ==============================================================================
def test_fetch_control_sends_correct_command():
    """Verify SRLCliFetcher bắn chính xác chuỗi command đã được định nghĩa tập trung."""
    mock_session = MagicMock()
    mock_session.send_command.return_value = '{"healthz": {"status": "healthy"}}'
    
    SRLCliFetcher(mock_session).fetch_control()
    mock_session.send_command.assert_called_once_with(CONTROL_CMD)


def test_fetch_control_network_failure_raises_srl_fetch_error():
    """Lỗi kết nối vật lý (SSH Timeout/Drop) phải được dịch thành SRLFetchError."""
    mock_session = MagicMock()
    mock_session.send_command.side_effect = Exception("Netmiko SSH Timeout Connection")
    fetcher = SRLCliFetcher(mock_session)
    
    with pytest.raises(SRLFetchError) as exc_info:
        fetcher.fetch_control()
    assert "Lỗi kết nối mạng CLI" in str(exc_info.value)


def test_fetch_control_bad_data_raises_stdlib_exception():
    """Lỗi dữ liệu sai cấu trúc không được bọc SRLFetchError, trả thẳng lỗi stdlib gốc."""
    mock_session = MagicMock()
    mock_session.send_command.return_value = "{invalid json string"
    fetcher = SRLCliFetcher(mock_session)
    
    with pytest.raises(json.JSONDecodeError):
        fetcher.fetch_control()
