import json
from typing import Any
 
 
class SRLFetchError(Exception):
    """Lỗi kéo/parse control object từ thiết bị SR Linux."""
 
 
class SRLCliFetcher:
    """Kéo control object qua SSH CLI (netmiko).
 
    Gửi ĐÚNG lệnh đã tạo ra control_A.json, rồi json.loads output. KHÔNG bọc
    JSON-RPC: JSON-RPC của SR Linux là HTTP POST tới /jsonrpc, không phải SSH.
    Nếu muốn JSON-RPC, dùng requests qua HTTP, không phải netmiko (xem ghi chú).
    """
 
    CONTROL_CMD = "info from state platform control A | as json"
 
    def __init__(self, netmiko_connection: Any) -> None:
        """Nhận một kết nối netmiko đã authenticate (seam: transport được tiêm vào)."""
        self.ssh = netmiko_connection
 
    def fetch_control(self) -> dict[str, Any]:
        raw = self.ssh.send_command(self.CONTROL_CMD)
        if not raw or not raw.strip():
            raise SRLFetchError("device trả output rỗng cho lệnh control")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise SRLFetchError(f"output không phải JSON (CLI error?): {raw[:120]!r}") from e
        # '... | as json' có thể bọc object trong list 1 phần tử tùy path. Unwrap phòng thủ.
        # ENVELOPE THẬT (flat dict vs nested dưới /platform/control[A]) PHẢI verify trên device.
        if isinstance(data, list):
            if not data:
                raise SRLFetchError("device trả JSON list rỗng")
            data = data[0]
        if not isinstance(data, dict):
            raise SRLFetchError(f"control object phải là dict, nhận {type(data).__name__}")
        return data
