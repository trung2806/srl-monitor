import json
import time
import logging
import sys
import signal
import asyncio
import asyncssh
import ipaddress
from typing import Dict, Any, List, Tuple

# Import tầng xử lý logic từ các module lõi
from srl_monitor import evaluate_metrics
from srl_cooldown import SystemAlertState, process_all_cooldowns

# Cấu hình hệ thống Log ghi song song ra Console và File
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("srl_monitor.log", encoding="utf-8")
    ]
)


# ==============================================================================
# 1. TẦNG CONFIGURATION LOADER (FAIL-FAST)
# ==============================================================================
def load_thresholds(filepath: str) -> Dict[str, int]:
    """Nạp và kiểm tra tính hợp lệ của file cấu hình ngưỡng giám sát."""
    with open(filepath, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    if not isinstance(config, dict):
        raise TypeError(f"Cấu hình gốc phải là một dictionary. Nhận được: {type(config).__name__}")
        
    required_keys = {"cpu", "memory", "temperature"}
    missing_keys = required_keys - config.keys()
    if missing_keys:
        raise ValueError(f"File cấu hình thiếu các key bắt buộc: {', '.join(sorted(missing_keys))}")
        
    validated_thresholds: Dict[str, int] = {}
    for key in required_keys:
        val = config[key]
        if not isinstance(val, int) or isinstance(val, bool):
            raise TypeError(f"Ngưỡng của '{key}' phải là số nguyên. Nhận được: {type(val).__name__}")
        if val <= 0:
            raise ValueError(f"Ngưỡng của '{key}' phải lớn hơn 0. Nhận được: {val}")
        validated_thresholds[key] = val
        
    return validated_thresholds


def load_nodes(filepath: str) -> List[str]:
    """Nạp và kiểm tra nghiêm ngặt danh sách IP thiết bị đầu vào từ file ngoài."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise TypeError(f"nodes.json phải là list. Nhận được: {type(data).__name__}")

    if not data:
        raise ValueError("Danh sách node không được rỗng.")

    for i, node in enumerate(data):
        if not isinstance(node, str):
            raise TypeError(f"Phần tử [{i}] phải là str. Nhận được: {type(node).__name__}")
            
        try:
            ipaddress.ip_address(node)
        except ValueError:
            raise ValueError(f"IP không hợp lệ tại [{i}]: '{node}'")

    seen = set()
    for node in data:
        if node in seen:
            raise ValueError(f"IP trùng lặp: '{node}'")
        seen.add(node)

    return data


# ==============================================================================
# 2. TẦNG TRANSPORT NETWORKING (IO BOUNDARY)
# ==============================================================================
async def poll_node(host: str, username: str = "admin", password: str = "admin") -> Dict[str, Any]:
    """Mở kết nối SSH bất đồng bộ, chạy lệnh lấy dữ liệu thô (Raw Fetcher)."""
    logging.info(f"🔌 [SSH] Đang kết nối tới node: {host}...")
    async with asyncssh.connect(
        host=host, username=username, password=password, known_hosts=None, connect_timeout=3
    ) as conn:
        logging.info(f"🚀 [SSH] Kết nối thành công! Đang chạy lệnh trên node: {host}...")
        result = await conn.run("show version", timeout=3)
        logging.info(f"📄 [SSH] Nhận được output thô dài {len(result.stdout)} ký tự từ {host}")
        return {
            "cpu": [{"index": "all", "total": {"average-1": 15}}], 
            "memory": {"utilization": 22},
            "temperature": {"instant": 41, "alarm-status": False},
            "healthz": {"status": "healthy"}
        }


async def safe_poll_node(host: str) -> Dict[str, Any]:
    """🧱 LAYER 1 ERROR BOUNDARY: Vách ngăn Transport cách ly hoàn toàn lỗi mạng."""
    try:
        return await poll_node(host)
    except (asyncssh.Error, OSError) as net_err:
        logging.error(f"❌ [SSH] Node {host} unreachable: {net_err}")
        return {
            "cpu": [{"index": "all", "total": {"average-1": 0}}], 
            "memory": {"utilization": 0},
            "temperature": {"instant": 0, "alarm-status": False},
            "healthz": {"status": "unreachable", "reason": str(net_err)}
        }
    except Exception as bug:
        logging.critical(f"💥 [BUG] safe_poll_node({host}): {bug}", exc_info=True)
        return {
            "cpu": [{"index": "all", "total": {"average-1": 0}}], 
            "memory": {"utilization": 0},
            "temperature": {"instant": 0, "alarm-status": False},
            "healthz": {"status": "error", "reason": str(bug)}
        }


# ==============================================================================
# 3. TẦNG OUTPUT CẢNH BÁO
# ==============================================================================
def emit_alert(host: str, alert: Dict[str, Any]) -> None:
    logging.info(f"🚨 [{host}] [{alert['event']}] Metric '{alert['metric']}' is {alert['status']}. Reason: {alert['reason']}")


# ==============================================================================
# 4. THE FUNCTIONAL CORE (PURE LOGIC)
# ==============================================================================
def tick(
    raw_data: Dict[str, Any], 
    past_state: SystemAlertState, 
    current_time: float, 
    thresholds: Dict[str, int], 
    cooldown_seconds: int
) -> Tuple[List[Dict[str, Any]], SystemAlertState]:
    analysis_result = evaluate_metrics(raw_data, thresholds)
    return process_all_cooldowns(
        analysis_result=analysis_result,
        current_state=past_state,
        current_time=current_time,
        cooldown_seconds=cooldown_seconds
    )


# ==============================================================================
# 5. THE IMPERATIVE SHELL (ASYNC EVENT LOOP)
# ==============================================================================
async def main_loop(
    interval_seconds: int = 2, 
    cooldown_seconds: int = 1, 
    config_path: str = "thresholds.json",
    nodes_path: str = "nodes.json"
):
    """Vòng lặp chính điều phối xử lý theo mô hình Reactive (Xong node nào, xử lý ngay node đó)."""
    logging.info("🚀 Khởi động hệ thống giám sát SR Linux Monitor Fleet (Day 39)...")
    
    _stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda: _stop_event.set())
    loop.add_signal_handler(signal.SIGINT, lambda: _stop_event.set())

    try:
        current_thresholds = load_thresholds(config_path)
        nodes = load_nodes(nodes_path)
        logging.info(f"⚙️ Nạp cấu hình thành công từ '{config_path}': {current_thresholds}")
        logging.info(f"🖥️ Nạp danh sách fleet động thành công từ '{nodes_path}': {nodes}")
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError) as err:
        logging.critical(f"💥 KHÔNG THỂ KHỞI ĐỘNG DAEMON: Lỗi cấu hình hoặc danh sách thiết bị: {err}")
        raise

    # [Tối ưu YAGNI] Định nghĩa wrapper ngoài vòng lặp while để tránh tạo function object liên tục
    async def _wrapped_poll(host_str: str) -> Tuple[str, Dict[str, Any]]:
        data = await safe_poll_node(host_str)
        return host_str, data

    state_registry: Dict[str, SystemAlertState] = {host: SystemAlertState() for host in nodes}

    try:
        while not _stop_event.is_set():
            logging.info(f"--- 🔄 Bắt đầu chu kỳ quét mới trên toàn bộ {len(nodes)} nodes ---")
            
            # Fan-out: Khởi tạo danh sách các Task chạy ngầm song song độc lập
            tasks = [asyncio.create_task(_wrapped_poll(host)) for host in nodes]
            
            # Sử dụng vòng lặp thường lướt qua iterator đồng bộ của as_completed
            for fut in asyncio.as_completed(tasks):
                host, raw_data = await fut
                
                # Reactive Timestamping: Đo thời gian độc lập ngay khi nhận được dữ liệu
                now = time.time()
                
                # 🧱 LAYER 2 ERROR BOUNDARY: Cách ly lỗi logic xử lý của từng node riêng biệt
                try:
                    alerts, next_state = tick(
                        raw_data=raw_data,
                        past_state=state_registry[host],
                        current_time=now,
                        thresholds=current_thresholds,
                        cooldown_seconds=cooldown_seconds
                    )
                    
                    state_registry[host] = next_state
                    for alert in alerts:
                        emit_alert(host, alert)
                        
                except Exception as bug:
                    logging.critical(f"💥 [LOGIC BUG] Lỗi xử lý dữ liệu cho node {host}: {bug}", exc_info=True)
            
            # Chờ đến chu kỳ quét tiếp theo hoặc thoát ra nếu nhận tín hiệu kết thúc
            try:
                await asyncio.wait_for(_stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass
                
    except asyncio.CancelledError:
        logging.warning("⚠️ Vòng lặp chính nhận tín hiệu hủy tác vụ.")
    finally:
        _stop_event.set()
        logging.info("🛑 SR Linux Monitor daemon stopped cleanly. Exit code 0.")


# ==============================================================================
# 6. BACKWARD COMPATIBILITY SHIMS
# ==============================================================================
DEFAULT_THRESHOLDS = {"cpu": 80, "memory": 25, "temperature": 75}


# ==============================================================================
# 7. ENTRYPOINT
# ==============================================================================
if __name__ == "__main__":
    target_config = sys.argv[1] if len(sys.argv) > 1 else "thresholds.json"
    target_nodes = sys.argv[2] if len(sys.argv) > 2 else "nodes.json"
    try:
        asyncio.run(main_loop(config_path=target_config, nodes_path=target_nodes))
    except Exception:
        sys.exit(1)
