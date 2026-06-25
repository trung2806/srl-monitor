from typing import Any
 
 
def validate_percent_range(value: int, field_name: str) -> int:
    if not 0 <= value <= 100:
        raise ValueError(f"{field_name} value {value} is out of valid percent range [0, 100]")
    return value
 
 
def parse_cpu_utilization(raw_json: dict[str, Any]) -> int:
    """CPU utilization % của control module. 'cpu' là LIST per-core; lấy entry
    index=='all' (aggregate), đọc total.average-1 (smoothed 1 phút), KHÔNG đọc
    total.instant (flap, alert nhiễu). Range-check 0-100 (là %)."""
    cpu_list = raw_json.get("cpu")
    if not isinstance(cpu_list, list) or not cpu_list:
        raise ValueError("control object has no non-empty 'cpu' list")
    aggregate = next((c for c in cpu_list if isinstance(c, dict) and c.get("index") == "all"), None)
    if aggregate is None:
        raise ValueError("no cpu entry with index 'all'")
    total = aggregate.get("total")
    if not isinstance(total, dict):
        raise ValueError("cpu aggregate has no 'total' object")
    avg1 = total.get("average-1")
    if avg1 is None:
        raise ValueError("cpu aggregate 'total' is missing 'average-1'")
    return validate_percent_range(int(avg1), "cpu.total.average-1")
 
 
def parse_memory_utilization(raw_json: dict[str, Any]) -> int:
    mem_block = raw_json.get("memory", {})
    utilization = mem_block.get("utilization")
    if utilization is None:
        raise ValueError("memory object is missing 'utilization' value")
    return validate_percent_range(int(utilization), "memory.utilization")
 
 
def parse_temperature(raw_json: dict[str, Any]) -> int:
    """Temperature celsius. KHÔNG range-check (không phải %)."""
    temp_block = raw_json.get("temperature", {})
    instant = temp_block.get("instant")
    if instant is None:
        raise ValueError("temperature object is missing 'instant' value")
    return int(instant)


def parse_healthz_status(raw_json: dict[str, Any]) -> str:
    """Trạng thái sức khỏe module do device tự công bố (healthz.status): enum chuỗi
    ('healthy', 'degraded', ...). KHÔNG range-check, KHÔNG ép int (phạm trù, không phải %).
    Chỉ canh có mặt và là chuỗi không rỗng; phán quyết OK/BREACH thuộc monitor
    (authority-style, xem evaluate_metrics)."""
    healthz = raw_json.get("healthz")
    if not isinstance(healthz, dict):
        raise ValueError("control object has no 'healthz' object")
    status = healthz.get("status")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("healthz object is missing a non-empty 'status' string")
    return status
