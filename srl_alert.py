from dataclasses import dataclass
 
 
@dataclass(frozen=True)
class MetricAlert:
    metric: str
    value: int
    threshold: int
    breached: bool
 
 
def evaluate_alert(metric: str, value: int, threshold: int) -> MetricAlert:
    """So một metric scalar với ngưỡng. Strict >: value == threshold KHÔNG breach.
    Type-guard threshold (chặn bool, non-int), KHÔNG range-check ngưỡng."""
    if isinstance(threshold, bool) or not isinstance(threshold, int):
        raise TypeError(f"threshold expects a plain int, got {type(threshold).__name__}")
    return MetricAlert(metric=metric, value=value, threshold=threshold, breached=value > threshold)
