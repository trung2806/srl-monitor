import time
import logging
import sys
from typing import Dict, Any, List, Tuple

# Import các thành phần thuần túy từ tầng thư viện dưới
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

# Biến đếm vòng lặp để tạo dữ liệu biến thiên qua dòng thời gian thực
_LOOP_COUNTER = 0

def dummy_fetcher() -> Dict[str, Any]:
    """
    Stateful Fetcher: Giả lập thiết bị thay đổi trạng thái qua các vòng poller.
    - Vòng 0: CPU vọt lên 95% -> Kích hoạt ALERT_TRIGGERED
    - Vòng 1: CPU vẫn giữ 95% -> Vượt Cooldown sẽ kích hoạt ALERT_REPEATED
    - Vòng 2 trở đi: CPU hạ về 30% -> Kích hoạt ALERT_RECOVERED và im lặng
    """
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

# --- Day 33 Backward Compatibility (Giữ chặt hợp đồng với các bộ test cũ) ---
def build_report(raw_data: Dict[str, Any] = None) -> Dict[str, Any]:
    data = raw_data or dummy_fetcher()
    return evaluate_metrics(data, DEFAULT_THRESHOLDS)

def render(report: Dict[str, Any]) -> str:
    lines = ["\n=== SR LINUX MONITORING DASHBOARD ==="]
    for metric, payload in report.get("metrics", {}).items():
        status = payload.get("status", "UNKNOWN")
        val = payload.get("value", "N/A")
        basis = payload.get("basis", "")
        margin = payload.get("margin", "")
        
        metric_line = f"-> {metric.upper()}: status={status} (value={val})"
        if basis:
            metric_line += f" ({basis})"
        if margin is not None and margin != "":
            metric_line += f" TEMP MARGIN: {margin}"
            
        lines.append(metric_line)
    lines.append("=============================\n")
    return "\n".join(lines)

def emit_alert(alert: Dict[str, Any]) -> None:
    """Side-effect ngoại vi: Đẩy thông tin cảnh báo ra màn hình và file log."""
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
    """
    Hàm lõi xử lý trọn vẹn logic của đúng một vòng lặp giám sát.
    Tuyệt đối không chưa while, không sleep, không phụ thuộc clock vật lý.
    """
    analysis_result = evaluate_metrics(raw_data, thresholds)
    
    alerts, next_state = process_all_cooldowns(
        analysis_result=analysis_result,
        current_state=past_state,
        current_time=current_time,
        cooldown_seconds=cooldown_seconds
    )
    
    return alerts, next_state


# --- [Day 35] THE IMPERATIVE SHELL ---
def main_loop(interval_seconds: int = 2, cooldown_seconds: int = 1):
    logging.info("🚀 Khởi động hệ thống giám sát SR Linux Monitor (Day 35)...")
    
    # Khởi tạo vùng nhớ lưu trạng thái rỗng ban đầu ngoài vòng lặp
    state_registry = SystemAlertState()

    try:
        while True:
            # 1. Thu thập dữ liệu (I/O Mạng)
            raw_json = dummy_fetcher()
            
            # 2. Lấy thời gian thực (I/O OS Clock)
            now = time.time()
            
            # 3. Ủy quyền xử lý tính toán hoàn toàn cho hàm thuần túy tick()
            alerts, state_registry = tick(
                raw_data=raw_json,
                past_state=state_registry,
                current_time=now,
                thresholds=DEFAULT_THRESHOLDS,
                cooldown_seconds=cooldown_seconds
            )
            
            # 4. Phát tán cảnh báo (I/O Side-Effect)
            for alert in alerts:
                emit_alert(alert)
                
            # 5. Nghỉ theo chu kỳ poller
            time.sleep(interval_seconds)
            
    except KeyboardInterrupt:
        logging.info("🛑 Hệ thống giám sát đã dừng bởi người quản trị.")

if __name__ == "__main__":
    # Ép interval=2s và Cooldown=1s để đảm bảo vòng 2 trôi qua (2s > 1s) sẽ sinh REPEATED ngay lập tức
    main_loop(interval_seconds=2, cooldown_seconds=1)
