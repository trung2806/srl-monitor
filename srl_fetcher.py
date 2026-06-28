import json
from typing import Dict, Any

# Định nghĩa tập trung Command để dùng chung cho cả Sync và Async
CONTROL_CMD = "info from state platform control A | as json"


class SRLFetchError(Exception):
    """Lỗi tầng vận chuyển: Xảy ra khi không thể kết nối hoặc giao tiếp mạng với thiết bị."""
    pass


def parse_control_output(raw_stdout: str) -> Dict[str, Any]:
    """Pure Function: Nhận chuỗi thô từ CLI, làm sạch và trả về dict cấu trúc chuẩn.
    Sử dụng hoàn toàn Standard Library Exceptions.
    """
    if not raw_stdout or not raw_stdout.strip():
        raise ValueError("Dữ liệu xuất từ CLI rỗng.")

    # Có thể bắn ra json.JSONDecodeError nếu chuỗi không phải JSON hợp lệ
    data = json.loads(raw_stdout)

    # Xử lý unwrap nếu SR Linux bọc dict trong một list 1 phần tử
    if isinstance(data, list):
        if len(data) == 0:
            raise ValueError("Mảng JSON từ thiết bị trả về bị rỗng.")
        data = data[0]

    if not isinstance(data, dict):
        raise TypeError(f"Cấu trúc JSON sau khi unwrap phải là dict. Nhận được: {type(data).__name__}")

    return data


class SRLCliFetcher:
    CONTROL_CMD = CONTROL_CMD

    def __init__(self, session):
        self.session = session

    def fetch_control(self) -> Dict[str, Any]:
        """Tầng vận chuyển đồng bộ (Netmiko) cho các script cũ."""
        try:
            raw_output = self.session.send_command(self.CONTROL_CMD)
        except Exception as net_err:
            # Lỗi kết nối vật lý vẫn giữ nguyên bọc SRLFetchError nghiệp vụ
            raise SRLFetchError(f"Lỗi kết nối mạng CLI: {net_err}") from net_err

        # Lỗi dữ liệu bên trong hàm parse sẽ tự nhiên bubble up (ValueError/TypeError...)
        return parse_control_output(raw_output)
