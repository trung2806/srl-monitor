import json
import time
import logging
import sys
import signal
import asyncio
import asyncssh
from typing import Dict, Any, List, Tuple

# Import tầng phân tích và trạng thái từ cấu trúc của các bài học trước
from srl_monitor import evaluate_metrics
from srl_cooldown import SystemAlertState, process_all_cooldowns

# Cấu hình logging ghi song song ra cả Console stdout và file log để tracking lịch sử
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("srl_monitor.log", encoding="utf-8")
    ]
)


# ==============================================================================
# 1. TẦNG CONFIGURATION LOADER (FAIL-FAST) - [Day 37]
# ==============================================================================
def load_thresholds(filepath: str) -> Dict[str, int]:
    """Nạp và kiểm tra tính hợp lệ của file cấu hình JSON chứa các ngưỡng giám sát.
    Tự động nổ lỗi ngay lập tức (Fail-Fast) nếu phát hiện dữ liệu rác hoặc thiếu key.
    """
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


# ==============================================================================
# 2. TẦNG TRANSPORT NETWORKING (ASYNCSSH IMPLEMENTATION) - [Day 38]
# ==============================================================================
async def poll_node(host: str, username: str = "admin", password: str = "admin") -> Dict[str, Any]:
    """Mở kết nối SSH bất đồng bộ tới thiết bị, chạy lệnh lấy dữ liệu thô.
    Đóng vai trò là một Error Boundary bọc lỗi mạng an toàn để không kéo sập Event Loop.
    """
    logging.info(f"🔌 [SSH] Đang kết nối tới node: {host}...")
    
    try:
        # Bỏ qua host key check (known_hosts=None) để tối ưu vận hành trong môi trường Lab
        async with asyncssh.connect(
            host=host,
            username=username,
            password=password,
            known_hosts=None,
            connect_timeout=3  # Timeout ngắn để chu kỳ quét không bị nghẽn chết khi Node offline
        ) as conn:
            
            logging.info(f"🚀 [SSH] Kết nối thành công! Đang chạy lệnh trên node: {host}...")
            result = await conn.run("show version", timeout=3)
            
            # TODO (Day 39): Tích hợp Text Parser CLI để bóc tách text thật từ result.stdout ở đây.
            # Hiện tại, trả về mock data cấu trúc chuẩn tương đương dummy_fetcher để nuôi bộ gầm.
            logging.info(f"📄 [SSH] Nhận được output thô dài {len(result.stdout)} ký tự từ {host}")
            return {
                "cpu": [{"index": "all", "total": {"average-1": 15}}], 
                "memory": {"utilization": 22},
                "temperature": {"instant": 41, "alarm-status": False},
                "healthz": {"status": "healthy"}
            }
            
    except (asyncssh.Error, OSError) as e:
        # Khi node chết hoặc không phản hồi, bẫy lỗi tại biên mạng và trả về cấu trúc Fallback an toàn
        logging.error(f"❌ [SSH] Thất bại khi kết nối hoặc thực thi tại node {host}: {e}")
        return {
            "cpu": [{"index": "all", "total": {"average-1": 0}}], 
            "memory": {"utilization": 0},
            "temperature": {"instant": 0, "alarm-status": False},
            "healthz": {"status": "unreachable", "reason": str(e)}
        }


# ==============================================================================
# 3. TẦNG HIỂN THỊ VÀ OUTPUT CẢNH BÁO
# ==============================================================================
def emit_alert(host: str, alert: Dict[str, Any]) -> None:
    """Bắn log cảnh báo chuẩn hóa có kèm nhãn Context định danh Node mạng cụ thể."""
    msg = f"🚨 [{host}] [{alert['event']}] Metric '{alert['metric']}' is {alert['status']}. Reason: {alert['reason']}"
    logging.info(msg)


# ==============================================================================
# 4. THE FUNCTIONAL CORE (PURE LOGIC) - [Day 35]
# ==============================================================================
def tick(
    raw_data: Dict[str, Any], 
    past_state: SystemAlertState, 
    current_time: float, 
    thresholds: Dict[str, int], 
    cooldown_seconds: int
) -> Tuple[List[Dict[str, Any]], SystemAlertState]:
    """Hàm xử lý dữ liệu thuần túy (Pure Function). 
    Không IO, không side-effect, đầu vào giống nhau luôn cho ra đầu ra giống nhau.
    """
    analysis_result = evaluate_metrics(raw_data, thresholds)
    alerts, next_state = process_all_cooldowns(
        analysis_result=analysis_result,
        current_state=past_state,
        current_time=current_time,
        cooldown_seconds=cooldown_seconds
    )
    return alerts, next_state


# ==============================================================================
# 5. THE IMPERATIVE SHELL (ASYNC EVENT LOOP) - [Day 38]
# ==============================================================================
async def main_loop(
    interval_seconds: int = 2, 
    cooldown_seconds: int = 1, 
    config_path: str = "thresholds.json"
):
    """Vòng lặp điều phối chính của Daemon. Đảm nhận Structured Concurrency,
    quản lý vòng đời tác vụ song song, bẫy tín hiệu OS và cô lập trạng thái Fleet.
    """
    logging.info("🚀 Khởi động hệ thống giám sát SR Linux Monitor Fleet (Day 38)...")
    
    # 5.1 Đăng ký Tín hiệu tắt ứng dụng an toàn qua Event Loop
    _stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda: _stop_event.set())
    loop.add_signal_handler(signal.SIGINT, lambda: _stop_event.set())

    # 5.2 Nạp cấu hình tĩnh đầu mùa (Fail-Fast Loader)
    try:
        current_thresholds = load_thresholds(config_path)
        logging.info(f"⚙️ Nạp cấu hình thành công từ '{config_path}': {current_thresholds}")
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        logging.critical(f"💥 KHÔNG THỂ KHỞI ĐỘNG: Lỗi cấu hình nghiêm trọng tại file '{config_path}'")
        raise

    # 5.3 Khởi tạo danh sách Fleet và Cô lập trạng thái (Per-node State Isolation)
    nodes = ["192.168.1.1", "192.168.1.2", "192.168.1.3"]
    state_registry: Dict[str, SystemAlertState] = {host: SystemAlertState() for host in nodes}

    try:
        while not _stop_event.is_set():
            logging.info(f"--- 🔄 Bắt đầu chu kỳ quét mới trên toàn bộ {len(nodes)} nodes ---")
            
            node_tasks: Dict[str, asyncio.Task] = {}
            
            # 5.4 Kích hoạt Fan-out thu thập dữ liệu song song (TaskGroup)
            try:
                async with asyncio.TaskGroup() as tg:
                    for host in nodes:
                        node_tasks[host] = tg.create_task(poll_node(host))
            except* Exception as eg:
                # Bẫy ExceptionGroup nếu có lỗi chưa xử lý lọt ra từ các Task con
                logging.error(f"💥 Phát hiện lỗi nghiêm trọng trong nhóm tác vụ song song: {eg}")

            # 5.5 Đẩy kết quả an toàn qua Functional Core xử lý độc lập
            now = time.time()
            for host, task in node_tasks.items():
                try:
                    raw_data = task.result()
                    
                    # Trạng thái Cooldown của Node A hoàn toàn độc lập với Node B
                    alerts, next_state = tick(
                        raw_data=raw_data,
                        past_state=state_registry[host],
                        current_time=now,
                        thresholds=current_thresholds,
                        cooldown_seconds=cooldown_seconds
                    )
                    
                    # Lưu lại trạng thái chu kỳ mới cho riêng Node đó
                    state_registry[host] = next_state
                    
                    for alert in alerts:
                        emit_alert(host, alert)
                        
                except Exception as e:
                    logging.error(f"❌ Không thể xử lý dữ liệu phân tích cho node {host}: {e}")
            
            # 5.6 Tighter Loop: Ngủ bất đồng bộ nhưng tỉnh giấc NGAY LẬP TỨC nếu nhận tín hiệu dừng
            try:
                await asyncio.wait_for(_stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                # Hết chu kỳ timeout bình thường, lặp tiếp sang chu kỳ sau
                pass
                
    except asyncio.CancelledError:
        logging.warning("⚠️ Vòng lặp chính nhận tín hiệu hủy tác vụ (Task Cancelled).")
    finally:
        _stop_event.set()
        logging.info("🧹 Đang giải phóng tài nguyên hệ thống...")
        logging.info("🛑 SR Linux Monitor daemon stopped cleanly. Exit code 0.")


# ==============================================================================
# 6. BACKWARD COMPATIBILITY SHIMS (TƯƠNG THÍCH NGƯỢC CHO TEST CŨ)
# ==============================================================================
# Giữ lại ngưỡng memory sát để kích hoạt đúng trạng thái BREACH của test_pipeline cũ
DEFAULT_THRESHOLDS = {"cpu": 80, "memory": 25, "temperature": 75}


# ==============================================================================
# 7. ENTRYPOINT (ĐIỂM KÍCH HOẠT ĐỒNG BỘ DUY NHẤT)
# ==============================================================================
if __name__ == "__main__":
    target_config = sys.argv[1] if len(sys.argv) > 1 else "thresholds.json"
    
    try:
        # Điểm bọc đồng bộ duy nhất để dựng gầm Event Loop chạy daemon
        asyncio.run(main_loop(interval_seconds=2, cooldown_seconds=1, config_path=target_config))
    except Exception:
        sys.exit(1)
