from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, Optional

@dataclass(frozen=True)
class MetricState:
    """Trạng thái lưu trữ quá khứ của duy nhất một metric. Bất biến (Immutable)."""
    last_status: str = "NO_THRESHOLD"
    last_alert_time: float = 0.0

@dataclass(frozen=True)
class SystemAlertState:
    """Cái túi chứa registry trạng thái của toàn bộ các metric trong hệ thống."""
    metrics: Dict[str, MetricState] = field(default_factory=dict)

    def get_metric(self, name: str) -> MetricState:
        return self.metrics.get(name, MetricState())

def evaluate_metric_cooldown(
    metric_name: str,
    current_status: str,
    past_state: MetricState,
    current_time: float,
    cooldown_seconds: int = 300
) -> Tuple[Optional[Dict[str, Any]], MetricState]:
    """
    Pure Function: Đánh giá trạng thái cooldown của ĐƠN METRIC.
    Không thay đổi trực tiếp ô nhớ cũ, luôn trả ra một instance trạng thái mới.
    """
    alert_event = None
    next_status = current_status
    next_alert_time = past_state.last_alert_time

    # KỊCH BẢN 1: Transition -> BREACH (Phát súng đầu tiên)
    if current_status == "BREACH" and past_state.last_status != "BREACH":
        alert_event = {
            "event": "ALERT_TRIGGERED",
            "metric": metric_name,
            "status": "BREACH",
            "timestamp": current_time,
            "reason": "Metric threshold breached"
        }
        next_alert_time = current_time

    # KỊCH BẢN 2: Tiếp tục giữ BREACH và VƯỢT ngưỡng cooldown (Nhắc lại định kỳ)
    elif current_status == "BREACH" and past_state.last_status == "BREACH":
        if (current_time - past_state.last_alert_time) >= cooldown_seconds:
            alert_event = {
                "event": "ALERT_REPEATED",
                "metric": metric_name,
                "status": "BREACH",
                "timestamp": current_time,
                "reason": f"Metric remains in BREACH for over {cooldown_seconds}s"
            }
            next_alert_time = current_time

    # KỊCH BẢN 3: Transition từ BREACH -> OK hoặc NO_THRESHOLD (Hồi phục hoặc Ngừng theo dõi)
    elif current_status in ("OK", "NO_THRESHOLD") and past_state.last_status == "BREACH":
        alert_event = {
            "event": "ALERT_RECOVERED",
            "metric": metric_name,
            "status": current_status,
            "timestamp": current_time,
            "reason": f"Metric cleared from BREACH (Current status: {current_status})"
        }
        next_alert_time = 0.0  # Reset sạch đồng hồ bảo vệ

    # KỊCH BẢN 4: OK -> OK hoặc NO_THRESHOLD -> Tuyệt đối im lặng, giữ nguyên trạng thái
    return alert_event, MetricState(last_status=next_status, last_alert_time=next_alert_time)

def process_all_cooldowns(
    analysis_result: Dict[str, Any],
    current_state: SystemAlertState,
    current_time: float,
    cooldown_seconds: int = 300
) -> Tuple[List[Dict[str, Any]], SystemAlertState]:
    """
    Pure Orchestrator: Nhận kết quả từ evaluate_metrics() và đẩy qua đường ống xử lý thời gian.
    """
    emitted_alerts: List[Dict[str, Any]] = []
    next_metrics_map = dict(current_state.metrics)
    
    # Ráp nối cấu trúc dữ liệu phẳng từ srl_monitor sang
    metrics_payload = analysis_result.get("metrics", {})

    for metric_name, payload in metrics_payload.items():
        current_status = payload.get("status", "NO_THRESHOLD")
        past_metric_state = current_state.get_metric(metric_name)

        alert, new_metric_state = evaluate_metric_cooldown(
            metric_name=metric_name,
            current_status=current_status,
            past_state=past_metric_state,
            current_time=current_time,
            cooldown_seconds=cooldown_seconds
        )

        if alert:
            emitted_alerts.append(alert)
        
        next_metrics_map[metric_name] = new_metric_state

    return emitted_alerts, SystemAlertState(metrics=next_metrics_map)
