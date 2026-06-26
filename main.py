import json
import time
import logging
import sys
import signal
import threading
from typing import Dict, Any, List, Tuple

# Import tầng phân tích và trạng thái từ các bài học trước
from srl_monitor import evaluate_metrics
from srl_cooldown import SystemAlertState, process_all_cooldowns

# --- Cấu hình mặc định nội bộ (Chỉ dùng làm reference hoặc test) ---
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

# --- [Day 37] TẦNG CONFIGURATION LOADER (IMPERATIVE SHELL / FAIL-FAST) ---
def load_thresholds(filepath: str) -> Dict[str, int]:
    """Nạp, parse và kiểm tra tính hợp lệ của file cấu hình JSON chứa các ngưỡng giám sát."""
    # Bước 1: open + json.load (FileNotFoundError và JSONDecodeError tự propagate)
    with open(filepath, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    # Bước 2: type check config là dict
    if not isinstance(config, dict):
        raise TypeError(f"Cấu hình gốc trong file JSON phải là một dictionary. Nhận được: {type(config).__name__}")
        
    # Bước 3: check đủ required keys
    required_keys = {"cpu", "memory", "temperature"}
    missing_keys = required_keys - config.keys()
    if missing_keys:
        raise ValueError(f"File cấu hình thiếu các key bắt buộc: {', '.join(sorted(missing_keys))}")
        
    # Bước 4 & 5: validate từng value và đóng gói subset dữ liệu sạch để return
    validated_thresholds: Dict[str, int] = {}
    for key in required_keys:
        val = config[key]
        
        # Kiểm tra kiểu dữ liệu (loại trừ bool vì isinstance(True, int) là True)
        if not isinstance(val, int) or isinstance(val, bool):
            raise TypeError(f"Ngưỡng của '{key}' phải là số nguyên (int). Nhận được: {type(val).__name__}")
            
        # Kiểm tra ràng buộc logic (temperature có thể > 100 nên chỉ check > 0)
        if val <= 0:
            raise ValueError(f"Ngưỡng của '{key}' phải lớn hơn 0. Nhận được: {val}")
            
        validated_thresholds[key] = val
        
    return validated_thresholds


# --- [Day 36] THIẾT KẾ ASYNC-SIGNAL SAFE ---
_stop_event = threading.Event()

def sigterm_handler(signum: int, frame: Any) -> None:
    _stop_event.set()

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
        basis_str = f" ({basis})" if basis else ""
        lines.append(f"-> {metric.upper()}: status={status} (value={val}){basis_str}")
    
    temp_payload = report.get("metrics", {}).get("temperature", {})
    margin_val = None
    if isinstance(temp_payload, dict):
        margin_val = temp_payload.get("margin")
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


# --- [Day 36 & 37] THE IMPERATIVE SHELL ---
def main_loop(
    interval_seconds: int = 2, 
    cooldown_seconds: int = 1, 
    config_path: str = "thresholds.json"
):
    logging.info("🚀 Khởi động hệ thống giám sát SR Linux Monitor (Day 37)...")
    
    # 1. NẠP VÀ KIỂM TRA CẤU HÌNH ĐẦU VÀO (Fail-Fast)
    try:
        current_thresholds = load_thresholds(config_path)
        logging.info(f"⚙️ Nạp cấu hình thành công từ '{config_path}': {current_thresholds}")
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError) as e:
        logging.critical(f"💥 KHÔNG THỂ KHỞI ĐỘNG: Lỗi cấu hình nghiêm trọng tại file '{config_path}'")
        logging.critical(f"👉 Chi tiết lỗi: {e}")
        raise  # Re-raise để đẩy quyền quyết định Exit Code cho entrypoint bọc ngoài

    # 2. KHỞI TẠO TRẠNG THÁI HỆ THỐNG
    state_registry = SystemAlertState()

    try:
        while not _stop_event.is_set():
            raw_json = dummy_fetcher()
            now = time.time()
            
            alerts, state_registry = tick(
                raw_data=raw_json,
                past_state=state_registry,
                current_time=now,
                thresholds=current_thresholds,
                cooldown_seconds=cooldown_seconds
            )
            
            for alert in alerts:
                emit_alert(alert)
                
            is_interrupted = _stop_event.wait(timeout=interval_seconds)
            if is_interrupted:
                logging.warning("🚨 Nhận tín hiệu kích hoạt dừng hệ thống (_stop_event được set).")
                
    except KeyboardInterrupt:
        logging.warning("⚠️ Nhận tín hiệu ngắt từ bàn phím (Ctrl+C).")
    except Exception as e:
        logging.error(f"💥 Hệ thống gặp lỗi nghiêm trọng bất ngờ: {e}", exc_info=True)
    finally:
        _stop_event.set()
        logging.info("🧹 Đang giải phóng tài nguyên hệ thống...")
        logging.info("🛑 SR Linux Monitor daemon stopped cleanly. Exit code 0.")


if __name__ == "__main__":
    import sys
    target_config = sys.argv[1] if len(sys.argv) > 1 else "thresholds.json"
    
    try:
        main_loop(interval_seconds=2, cooldown_seconds=1, config_path=target_config)
    except Exception:
        # Chính sách Exit Code: Trả về lỗi 1 cho OS/Systemd nếu cấu hình hoặc khởi động fail
        sys.exit(1)
