import pytest
import json
from main import load_thresholds

def test_load_thresholds_happy_path(tmp_path):
    """Branch 1: File hợp lệ, chứa đủ key, value > 0 và lọc bỏ các key thừa."""
    config_data = {
        "cpu": 80,
        "memory": 30,
        "temperature": 75,
        "extraneous_metric": 999
    }
    config_file = tmp_path / "valid_config.json"
    config_file.write_text(json.dumps(config_data), encoding="utf-8")
    
    result = load_thresholds(str(config_file))
    
    assert result == {"cpu": 80, "memory": 30, "temperature": 75}
    assert "extraneous_metric" not in result


def test_load_thresholds_file_not_found():
    """Branch 2: Lỗi không tìm thấy file (FileNotFoundError)."""
    with pytest.raises(FileNotFoundError):
        load_thresholds("non_existent_file_path_xyz.json")


def test_load_thresholds_invalid_json(tmp_path):
    """Branch 3: File có tồn tại nhưng sai cú pháp định dạng (JSONDecodeError)."""
    config_file = tmp_path / "broken_syntax.json"
    config_file.write_text("{ cpu: 80, memory: missing_quotes }", encoding="utf-8")
    
    with pytest.raises(json.JSONDecodeError):
        load_thresholds(str(config_file))


def test_load_thresholds_invalid_type_root_or_value(tmp_path):
    """Branch 4: Sai kiểu dữ liệu gốc (không phải dict) hoặc sai kiểu value (không phải int)."""
    # Case A: Root list thay vì dict
    file_list = tmp_path / "root_list.json"
    file_list.write_text(json.dumps([{"cpu": 80}]), encoding="utf-8")
    with pytest.raises(TypeError, match="phải là một dictionary"):
        load_thresholds(str(file_list))
        
    # Case B: Value là string
    file_str_val = tmp_path / "string_value.json"
    file_str_val.write_text(json.dumps({"cpu": "eighty", "memory": 30, "temperature": 75}), encoding="utf-8")
    with pytest.raises(TypeError, match="phải là số nguyên"):
        load_thresholds(str(file_str_val))

    # Case C: Value là boolean (isinstance check)
    file_bool_val = tmp_path / "bool_value.json"
    file_bool_val.write_text(json.dumps({"cpu": 80, "memory": True, "temperature": 75}), encoding="utf-8")
    with pytest.raises(TypeError, match="phải là số nguyên"):
        load_thresholds(str(file_bool_val))


def test_load_thresholds_invalid_value_or_missing_keys(tmp_path):
    """Branch 5: Thiếu key bắt buộc hoặc giá trị vi phạm ràng buộc <= 0."""
    # Case A: Thiếu key temperature
    file_missing = tmp_path / "missing_key.json"
    file_missing.write_text(json.dumps({"cpu": 80, "memory": 25}), encoding="utf-8")
    with pytest.raises(ValueError, match="thiếu các key bắt buộc"):
        load_thresholds(str(file_missing))
        
    # Case B: Ngưỡng âm
    file_negative = tmp_path / "negative_value.json"
    file_negative.write_text(json.dumps({"cpu": 80, "memory": 25, "temperature": -10}), encoding="utf-8")
    with pytest.raises(ValueError, match="phải lớn hơn 0"):
        load_thresholds(str(file_negative))
