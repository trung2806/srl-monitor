import time
import logging
import sys
import signal
import threading
from typing import Dict, Any, List, Tuple

# Import tầng phân tích và trạng thái từ các bài học trước
from srl_monitor import evaluate_metrics
from srl_cooldown import SystemAlertState, process_all_cooldowns

# --- Cấu hình hệ thống ---
DEFAULT_THRESHOLDS = {
    "cpu": 80,
    "memory": 25,        
    "temperature": 75
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("srl_monitor.log", encoding="utf-8")
    ]
)

# --- [Day 36] THIẾT KẾ ASYNC-SIGNAL SAFE ---
_stop_event = threading.Event()

def sigterm_handler(signum: int, frame: Any) -> None:
    """
    Handler nguyên tử: TUYỆT ĐỐI KHÔNG I/O, KHÔNG LOGGING.
    Chỉ giải phóng cờ chờ để bảo đảm an toàn, tránh deadlock lock nội bộ.
    """
    _stop_event.set()

# Đăng ký bẫy tín hiệu SIGTERM (từ Hệ điều hành / Systemd) và SIGINT (từ Ctrl+C)
signal.signal(signal.SIGTERM, sigterm_handler)
signal.signal(signal.SIGINT, sigterm_handler)


# --- Bộ sinh dữ liệu giả lập qua dòng thời gian ---
_LOOP_COUNTER = 0
def dummy_fetcher() -> Dict[str, Any]:
    global _LOOP_COUNTER
    if _LOOP_COUNTER == 0:
        data = {
            "cpu": [{"index": "all", "total": {"average-1": 95}}], 
            "memory": {"utilization": 20},
            "temperature": {"instant": 45, "alarm-status": False},
            "healthz": {"status": "healthy"}
        }
    elif _LOOP_COUNTER == 1:
        data = {
            "cpu": [{"index": "all", "total": {"average-1": 95}}], 
            "memory": {"utilization": 20},
            "temperature": {"instant": 45, "alarm-status": False},
            "healthz": {"status": "healthy"}
        }
    else:
        data = {
            "cpu": [{"index": "all", "total": {"average-1": 30}}], 
            "memory": {"utilization": 20},
            "temperature": {"instant": 45, "alarm-status": False},
            "healthz": {"status": "healthy"}
        }
    _LOOP_COUNTER += 1
    return data

# --- Tương thích ngược ---
def build_report(raw_data: Dict[str, Any] = None) -> Dict[str, Any]:
    data = raw_data or dummy_fetcher()
    return evaluate_metrics(data, DEFAULT_THRESHOLDS)

def render(report: Dict[str, Any]) -> str:
    lines = ["\n=== SR LINUX MONITORING DASHBOARD ==="]
    for metric, payload in report.get("metrics", {}).items():
        status = payload.get("status", "UNKNOWN")
        val = payload.get("value", "N/A")
        basis = payload.get("basis", "")
        
        # Format hiển thị kèm basis ngữ cảnh
        basis_str = f" ({basis})" if basis else ""
        lines.append(f"-> {metric.upper()}: status={status} (value={val}){basis_str}")
    
    # KHÔI PHỤC: Trích xuất hiển thị dòng TEMP MARGIN để vượt qua bộ test kiểm thử cũ
    temp_payload = report.get("metrics", {}).get("temperature", {})
    margin_val = None
    if isinstance(temp_payload, dict):
        margin_val = temp_payload.get("margin")
        
    # Dự phòng nếu hệ thống đặt margin ở tầng ngoài của report
    if margin_val is None:
        margin_val = report.get("margin") or report.get("temperature_margin")
        
    if margin_val is not None:
        lines.append(f"-> TEMP MARGIN: {margin_val}")
        
    lines.append("=============================\n")
    return "\n".join(lines)

def emit_alert(alert: Dict[str, Any]) -> None:
    msg = f"🚨 [{alert['event']}] Metric '{alert['metric']}' is {alert['status']}. Reason: {alert['reason']}"
    logging.info(msg)


# --- [Day 35] THE FUNCTIONAL CORE ---
def tick(
    raw_data: Dict[str, Any], 
    past_state: SystemAlertState, 
    current_time: float, 
    thresholds: Dict[str, int], 
    cooldown_seconds: int
) -> Tuple[List[Dict[str, Any]], SystemAlertState]:
    analysis_result = evaluate_metrics(raw_data, thresholds)
    alerts, next_state = process_all_cooldowns(
        analysis_result=analysis_result,
        current_state=past_state,
        current_time=current_time,
        cooldown_seconds=cooldown_seconds
    )
    return alerts, next_state


# --- [Day 36] THE IMPERATIVE SHELL ---
def main_loop(interval_seconds: int = 2, cooldown_seconds: int = 1):
    logging.info("🚀 Khởi động hệ thống giám sát SR Linux Monitor (Day 36)...")
    state_registry = SystemAlertState()

    try:
        while not _stop_event.is_set():
            # 1. I/O lấy dữ liệu mạng và OS clock
            raw_json = dummy_fetcher()
            now = time.time()
            
            # 2. Ủy quyền tính toán thuần túy
            alerts, state_registry = tick(
                raw_data=raw_json,
                past_state=state_registry,
                current_time=now,
                thresholds=DEFAULT_THRESHOLDS,
                cooldown_seconds=cooldown_seconds
            )
            
            # 3. I/O Side-effect đẩy cảnh báo
            for alert in alerts:
                emit_alert(alert)
                
            # 4. Ngủ động thông minh qua Event
            is_interrupted = _stop_event.wait(timeout=interval_seconds)
            
            # Ghi log an toàn tại Main Thread sau khi bừng tỉnh
            if is_interrupted:
                logging.warning("🚨 Nhận tín hiệu kích hoạt dừng hệ thống (_stop_event được set).")
                
    except KeyboardInterrupt:
        logging.warning("⚠️ Nhận tín hiệu ngắt từ bàn phím (Ctrl+C).")
    except Exception as e:
        logging.error(f"💥 Hệ thống gặp lỗi nghiêm trọng bất ngờ: {e}", exc_info=True)
    finally:
        # Bảo hiểm tối thượng
        _stop_event.set()
        logging.info("🧹 Đang giải phóng tài nguyên hệ thống...")
        logging.info("🛑 SR Linux Monitor daemon stopped cleanly. Exit code 0.")

if __name__ == "__main__":
    main_loop(interval_seconds=2, cooldown_seconds=1)
