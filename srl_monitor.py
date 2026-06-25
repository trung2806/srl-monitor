from typing import Any, Optional
 
from srl_engine import (parse_cpu_utilization, parse_healthz_status,
                        parse_memory_utilization, parse_temperature)
from srl_alert import evaluate_alert
 
_PARSERS = {
    "cpu": parse_cpu_utilization,
    "memory": parse_memory_utilization,
    "temperature": parse_temperature,
}
 
 
def evaluate_metrics(raw_json: dict[str, Any], thresholds: Optional[dict[str, int]] = None) -> dict[str, Any]:
    """Parse ba metric từ control object rồi gắn status, kèm 'basis' của mỗi phán quyết.
 
    Temperature: nếu device trả 'alarm-status' thì đó là phán quyết của vendor và
    AUTHORITATIVE trên threshold tay (True -> BREACH, False -> OK), basis='alarm-status',
    đính kèm 'margin'. Chỉ khi device KHÔNG trả alarm-status mới rơi về threshold.
 
    cpu / memory (và temperature-fallback): so value với threshold được trao, basis='threshold'.
 
    Metric không có cơ sở nào để kiểm (không alarm-status, không threshold) -> 'NO_THRESHOLD'.
    KHÔNG bịa 'OK' cho cái chưa kiểm.
    """
    thresholds = thresholds or {}
    metrics: dict[str, Any] = {}
 
    for name, parser in _PARSERS.items():
        value = parser(raw_json)
        entry: dict[str, Any] = {"value": value}
 
        if name == "temperature":
            temp_block = raw_json.get("temperature", {})
            alarm = temp_block.get("alarm-status")
            if alarm is not None:
                entry["status"] = "BREACH" if alarm else "OK"
                entry["basis"] = "alarm-status"
                entry["margin"] = temp_block.get("margin")
                metrics[name] = entry
                continue
 
        threshold = thresholds.get(name)
        if threshold is None:
            entry["status"] = "NO_THRESHOLD"
        else:
            entry["threshold"] = threshold
            entry["status"] = "BREACH" if evaluate_alert(name, value, threshold).breached else "OK"
            entry["basis"] = "threshold"
        metrics[name] = entry
 
    # healthz: module-health do device tự công bố. AUTHORITY-style như alarm-status:
    # KHÔNG threshold, phán quyết của device là cơ sở. Fail-safe: CHỈ đúng chuỗi 'healthy'
    # mới OK; mọi trạng thái khác (degraded, unknown, future, ...) -> BREACH, không bao
    # giờ bịa OK cho trạng thái lạ. OPTIONAL: device/cũ không trả healthz -> bỏ qua (không
    # có tín hiệu, không bịa entry), khớp triết lý NO_THRESHOLD. Status thật surface vào
    # 'value' để operator thấy ĐÚNG trạng thái nào, song song margin của alarm-status.
    if raw_json.get("healthz") is not None:
        health_status = parse_healthz_status(raw_json)
        metrics["healthz"] = {
            "value": health_status,
            "status": "OK" if health_status == "healthy" else "BREACH",
            "basis": "healthz",
        }
 
    return {"metrics": metrics}
