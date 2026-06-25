import time
import logging
import sys
from typing import Dict, Any

from srl_monitor import evaluate_metrics
from srl_cooldown import SystemAlertState, process_all_cooldowns

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

# Biến toàn cục đếm số vòng lặp để giả lập dữ liệu biến thiên theo thời gian
_LOOP_COUNTER = 0

def dummy_fetcher() -> Dict[str, Any]:
    """
    Stateful Fetcher: Biến đổi dữ liệu qua mỗi vòng lặp để mô phỏng thực tế.
    - Vòng 1 (Counter=0): CPU quá tải (95%) -> Kích hoạt ALERT_TRIGGERED
    - Vòng 2 (Counter=1): CPU vẫn cao (95%), thời gian trôi qua vượt Cooldown -> ALERT_REPEATED
    - Vòng 3 (Counter=2): Kỹ sư xử lý xong, CPU hạ nhiệt (30%) -> ALERT_RECOVERED
    """
    global _LOOP_COUNTER
    
    if _LOOP_COUNTER == 0:
        # Chu kỳ 1: CPU lỗi nặng
        data = {
            "cpu": [{"index": "all", "total": {"average-1": 95}}],
            "memory": {"utilization": 20},
            "temperature": {"instant": 45, "alarm-status": False},
            "healthz": {"status": "healthy"}
        }
    elif _LOOP_COUNTER == 1:
        # Chu kỳ 2: Giữ nguyên trạng thái lỗi để test lặp lại (Repeated)
        data = {
            "cpu": [{"index": "all", "total": {"average-1": 95}}],
            "memory": {"utilization": 20},
            "temperature": {"instant": 45, "alarm-status": False},
            "healthz": {"status": "healthy"}
        }
    else:
        # Chu kỳ 3 trở đi: Hệ thống tự phục hồi về trạng thái an toàn
        data = {
            "cpu": [{"index": "all", "total": {"average-1": 30}}],
            "memory": {"utilization": 20},
            "temperature": {"instant": 45, "alarm-status": False},
            "healthz": {"status": "healthy"}
        }
        
    _LOOP_COUNTER += 1
    return data

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
    msg = f"🚨 [{alert['event']}] Metric '{alert['metric']}' is {alert['status']}. Reason: {alert['reason']}"
    logging.info(msg)

def main_loop(interval_seconds: int = 2, cooldown_seconds: int = 3):
    logging.info("🚀 Khởi động hệ thống giám sát SR Linux Monitor (Day 34)...")
    
    state_registry = SystemAlertState()

    try:
        while True:
            raw_json = dummy_fetcher()
            now = time.time()
            
            analysis_result = evaluate_metrics(raw_json, DEFAULT_THRESHOLDS)
            
            alerts, state_registry = process_all_cooldowns(
                analysis_result=analysis_result,
                current_state=state_registry,
                current_time=now,
                cooldown_seconds=cooldown_seconds
            )
            
            for alert in alerts:
                emit_alert(alert)
                
            time.sleep(interval_seconds)
            
    except KeyboardInterrupt:
        logging.info("🛑 Hệ thống giám sát đã dừng bởi người quản trị.")

if __name__ == "__main__":
    # Ép interval=2s và Cooldown=3s để chu kỳ trôi qua nhanh, thấy ngay kết quả
    main_loop(interval_seconds=2, cooldown_seconds=3)
