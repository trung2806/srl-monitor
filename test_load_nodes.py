import json
import pytest
import main

def test_load_nodes_happy_path(tmp_path):
    """Kịch bản lý tưởng: Nạp danh sách IP IPv4/IPv6 hợp lệ, không trùng lặp."""
    file = tmp_path / "nodes_valid.json"
    valid_data = ["192.168.1.1", "10.0.0.254", "2001:db8::1"]
    file.write_text(json.dumps(valid_data), encoding="utf-8")
    
    loaded = main.load_nodes(str(file))
    assert loaded == valid_data


def test_load_nodes_file_not_found():
    """Kịch bản lỗi vận hành: File cấu hình không tồn tại trên ổ đĩa."""
    with pytest.raises(FileNotFoundError):
        main.load_nodes("non_existent_file_path_xyz.json")


def test_load_nodes_invalid_json(tmp_path):
    """Kịch bản lỗi cú pháp: File bị hỏng cấu trúc cú pháp JSON băm nát."""
    file = tmp_path / "nodes_broken.json"
    file.write_text("[ '192.168.1.1', incomplete json... ", encoding="utf-8")
    
    with pytest.raises(json.JSONDecodeError):
        main.load_nodes(str(file))


def test_load_nodes_not_a_list(tmp_path):
    """Kịch bản sai cấu trúc gốc: File JSON trả về Object dict thay vì Array list."""
    file = tmp_path / "nodes_dict.json"
    file.write_text(json.dumps({"node1": "192.168.1.1"}), encoding="utf-8")
    
    with pytest.raises(TypeError) as exc_info:
        main.load_nodes(str(file))
    assert "phải là list" in str(exc_info.value)


def test_load_nodes_empty_list(tmp_path):
    """Kịch bản tập rỗng: Danh sách rỗng không có thiết bị nào để giám sát (Vô nghĩa)."""
    file = tmp_path / "nodes_empty.json"
    file.write_text(json.dumps([]), encoding="utf-8")
    
    with pytest.raises(ValueError) as exc_info:
        main.load_nodes(str(file))
    assert "không được rỗng" in str(exc_info.value)


def test_load_nodes_non_string_element(tmp_path):
    """Kịch bản sai kiểu dữ liệu phần tử: Có số nguyên lọt vào danh sách chuỗi ký tự."""
    file = tmp_path / "nodes_wrong_type.json"
    file.write_text(json.dumps(["192.168.1.1", 12345, "192.168.1.2"]), encoding="utf-8")
    
    with pytest.raises(TypeError) as exc_info:
        main.load_nodes(str(file))
    assert "phải là str" in str(exc_info.value)


def test_load_nodes_invalid_ip(tmp_path):
    """Kịch bản sai định dạng mạng: Chuỗi chữ chứa định dạng IP không hợp lệ hoặc rác."""
    file = tmp_path / "nodes_invalid_ip.json"
    file.write_text(json.dumps(["192.168.1.1", "999.999.999.999", "192.168.1.2"]), encoding="utf-8")
    
    with pytest.raises(ValueError) as exc_info:
        main.load_nodes(str(file))
    assert "IP không hợp lệ" in str(exc_info.value)


def test_load_nodes_duplicate_ip(tmp_path):
    """Kịch bản trùng lặp dữ liệu: Một thực thể mạng xuất hiện hai lần gây xung đột Registry Key."""
    file = tmp_path / "nodes_dup.json"
    file.write_text(json.dumps(["192.168.1.1", "192.168.1.2", "192.168.1.1"]), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        main.load_nodes(str(file))
    assert "trùng lặp" in str(exc_info.value)


def test_load_nodes_invalid_ip_at_first_position(tmp_path):
    """Regression D42: IP không hợp lệ ở vị trí [0] phải bị bắt, không bị bỏ qua.
    Bug cũ (indentation sai): chỉ validate phần tử cuối — nếu cuối hợp lệ, đầu lọt qua.
    """
    file = tmp_path / "nodes.json"
    file.write_text(json.dumps(["not-an-ip", "192.168.1.1"]), encoding="utf-8")
    with pytest.raises(ValueError) as exc_info:
        main.load_nodes(str(file))
    assert "IP không hợp lệ" in str(exc_info.value)
    assert "[0]" in str(exc_info.value)
