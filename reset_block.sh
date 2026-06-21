cd ~/srl-monitor && source .venv/bin/activate 2>/dev/null
 
# Xoá file .py/.json cũ đã scramble rồi ghi lại bộ sạch (giữ pyproject.toml, .venv)
rm -f srl_engine.py srl_alert.py srl_monitor.py srl_fetcher.py conftest.py control_A.json test_srl_engine.py test_srl_alert.py test_srl_monitor.py test_srl_fetcher.py
 
cat > srl_engine.py << 'SRLEOF'
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
SRLEOF
 
cat > srl_alert.py << 'SRLEOF'
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
SRLEOF
 
cat > srl_monitor.py << 'SRLEOF'
from typing import Any, Optional
 
from srl_engine import parse_cpu_utilization, parse_memory_utilization, parse_temperature
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
 
    return {"metrics": metrics}
SRLEOF
 
cat > srl_fetcher.py << 'SRLEOF'
import json
from typing import Any
 
 
class SRLFetchError(Exception):
    """Lỗi kéo/parse control object từ thiết bị SR Linux."""
 
 
class SRLCliFetcher:
    """Kéo control object qua SSH CLI (netmiko).
 
    Gửi ĐÚNG lệnh đã tạo ra control_A.json, rồi json.loads output. KHÔNG bọc
    JSON-RPC: JSON-RPC của SR Linux là HTTP POST tới /jsonrpc, không phải SSH.
    Nếu muốn JSON-RPC, dùng requests qua HTTP, không phải netmiko (xem ghi chú).
    """
 
    CONTROL_CMD = "info from state platform control A | as json"
 
    def __init__(self, netmiko_connection: Any) -> None:
        """Nhận một kết nối netmiko đã authenticate (seam: transport được tiêm vào)."""
        self.ssh = netmiko_connection
 
    def fetch_control(self) -> dict[str, Any]:
        raw = self.ssh.send_command(self.CONTROL_CMD)
        if not raw or not raw.strip():
            raise SRLFetchError("device trả output rỗng cho lệnh control")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise SRLFetchError(f"output không phải JSON (CLI error?): {raw[:120]!r}") from e
        # '... | as json' có thể bọc object trong list 1 phần tử tùy path. Unwrap phòng thủ.
        # ENVELOPE THẬT (flat dict vs nested dưới /platform/control[A]) PHẢI verify trên device.
        if isinstance(data, list):
            if not data:
                raise SRLFetchError("device trả JSON list rỗng")
            data = data[0]
        if not isinstance(data, dict):
            raise SRLFetchError(f"control object phải là dict, nhận {type(data).__name__}")
        return data
SRLEOF
 
cat > conftest.py << 'SRLEOF'
import json
import pathlib
import pytest
 
_HERE = pathlib.Path(__file__).parent
 
 
@pytest.fixture
def control_capture():
    """Real capture: info from state platform control A | as json (v26.3.2)."""
    return json.loads((_HERE / "control_A.json").read_text())
SRLEOF
 
cat > control_A.json << 'SRLEOF'
{
  "slot": "A",
  "type": "imm48-25g-sfp28+8-100g-qsfp28+2-10g-sfpp",
  "oper-state": "up",
  "temperature": {
    "instant": 50,
    "maximum": 50,
    "maximum-time": "2026-06-16T01:19:03.535Z (4 days ago)",
    "alarm-status": false,
    "margin": 25
  },
  "cpu": [
    { "index": "all", "architecture": "aarch64", "type": "Oryon", "speed": "0.00",
      "total": {"instant": 4, "average-1": 2, "average-5": 2, "average-15": 2},
      "idle":  {"instant": 96, "average-1": 98, "average-5": 98, "average-15": 98} },
    { "index": "0", "total": {"instant": 2, "average-1": 2, "average-5": 2, "average-15": 2} },
    { "index": "1", "total": {"instant": 2, "average-1": 2, "average-5": 2, "average-15": 2} },
    { "index": "2", "total": {"instant": 18, "average-1": 2, "average-5": 2, "average-15": 2} }
  ],
  "memory": {
    "physical": "8105684992",
    "reserved": "2351325184",
    "free": "5754359808",
    "utilization": 29
  }
}
SRLEOF
 
cat > test_srl_engine.py << 'SRLEOF'
import pytest
 
from srl_engine import (parse_cpu_utilization, parse_memory_utilization,
                        parse_temperature, validate_percent_range)
 
 
# --- real-shape: chạy trên capture device thật (conftest.control_capture) ---
 
def test_parsers_on_real_capture(control_capture):
    assert parse_cpu_utilization(control_capture) == 2     # total.average-1 của index=all
    assert parse_memory_utilization(control_capture) == 29
    assert parse_temperature(control_capture) == 50
 
 
# --- cpu: list per-core, aggregate index=all, chọn average-1 ---
 
def test_cpu_selects_average_1_not_instant():
    """Crosswire: total.instant=99 (cao, flap) vs average-1=10. Parser PHẢI trả 10."""
    raw = {"cpu": [{"index": "all", "total": {"instant": 99, "average-1": 10}}]}
    assert parse_cpu_utilization(raw) == 10
 
 
def test_cpu_picks_all_aggregate_not_per_core():
    raw = {"cpu": [
        {"index": "0", "total": {"instant": 80, "average-1": 80}},
        {"index": "all", "total": {"instant": 5, "average-1": 5}},
    ]}
    assert parse_cpu_utilization(raw) == 5
 
 
def test_cpu_missing_all_entry_raises():
    raw = {"cpu": [{"index": "0", "total": {"average-1": 5}}]}
    with pytest.raises(ValueError, match="no cpu entry with index 'all'"):
        parse_cpu_utilization(raw)
 
 
def test_cpu_out_of_range_raises():
    raw = {"cpu": [{"index": "all", "total": {"average-1": 105}}]}
    with pytest.raises(ValueError, match="cpu.total.average-1 value 105 is out of valid percent range"):
        parse_cpu_utilization(raw)
 
 
# --- memory: utilization %, range-checked ---
 
def test_memory_valid():
    assert parse_memory_utilization({"memory": {"utilization": 82}}) == 82
 
 
def test_memory_missing_utilization_raises():
    with pytest.raises(ValueError, match="memory object is missing 'utilization' value"):
        parse_memory_utilization({"memory": {}})
 
 
def test_memory_out_of_range_raises():
    with pytest.raises(ValueError, match="memory.utilization value -1 is out of valid percent range"):
        parse_memory_utilization({"memory": {"utilization": -1}})
 
 
# --- temperature: instant, KHÔNG range-check (không phải %) ---
 
def test_temperature_valid():
    assert parse_temperature({"temperature": {"instant": 50, "margin": 25}}) == 50
 
 
def test_temperature_missing_instant_raises():
    with pytest.raises(ValueError, match="temperature object is missing 'instant' value"):
        parse_temperature({"temperature": {"maximum": 50}})
 
 
def test_temperature_accepts_values_outside_percent_range():
    # omission pin both-sides: temperature không kẹp 0-100
    assert parse_temperature({"temperature": {"instant": 130}}) == 130
    assert parse_temperature({"temperature": {"instant": -5}}) == -5
SRLEOF
 
cat > test_srl_alert.py << 'SRLEOF'
import pytest
 
from srl_alert import MetricAlert, evaluate_alert
 
 
def test_below_threshold_not_breached():
    assert evaluate_alert("cpu", 45, 90) == MetricAlert("cpu", 45, 90, False)
 
 
def test_above_threshold_breached():
    assert evaluate_alert("memory", 95, 80).breached is True
 
 
def test_at_threshold_not_breached():
    assert evaluate_alert("temperature", 75, 75).breached is False
 
 
def test_one_above_threshold_breached():
    assert evaluate_alert("temperature", 76, 75).breached is True
 
 
def test_rejects_bool_threshold():
    with pytest.raises(TypeError, match="threshold expects a plain int"):
        evaluate_alert("cpu", 50, True)
 
 
def test_rejects_non_int_threshold():
    with pytest.raises(TypeError, match="threshold expects a plain int"):
        evaluate_alert("cpu", 50, "90")
SRLEOF
 
cat > test_srl_monitor.py << 'SRLEOF'
from srl_monitor import evaluate_metrics
 
# minimal control object dùng cho test tổng hợp shape (cpu list/index=all, memory, temp)
_CPU_ALL = [{"index": "all", "total": {"average-1": 2}}]
 
 
def test_evaluate_metrics_on_real_capture(control_capture):
    out = evaluate_metrics(control_capture, {"cpu": 80, "memory": 80, "temperature": 70})
    m = out["metrics"]
    assert m["cpu"]["value"] == 2 and m["cpu"]["status"] == "OK"
    assert m["memory"]["value"] == 29 and m["memory"]["status"] == "OK"
    assert m["temperature"]["value"] == 50
    # alarm-status=false của device THẮNG threshold tay 70: vendor đã clear
    assert m["temperature"]["status"] == "OK"
    assert m["temperature"]["basis"] == "alarm-status"
    assert m["temperature"]["margin"] == 25
 
 
def test_evaluate_metrics_flags_breach(control_capture):
    out = evaluate_metrics(control_capture, {"memory": 20})    # 29 > 20
    m = out["metrics"]
    assert m["memory"]["status"] == "BREACH"
    assert m["cpu"]["status"] == "NO_THRESHOLD"        # không ngưỡng, không alarm -> chưa kiểm
    assert m["temperature"]["status"] == "OK"          # alarm-status=false: vendor đã clear
    assert m["temperature"]["basis"] == "alarm-status"
 
 
def test_no_thresholds_cpu_memory_unchecked_temperature_uses_alarm(control_capture):
    out = evaluate_metrics(control_capture)
    m = out["metrics"]
    assert m["cpu"]["status"] == "NO_THRESHOLD"
    assert m["memory"]["status"] == "NO_THRESHOLD"
    assert m["temperature"]["status"] == "OK"          # alarm-status authoritative, không cần threshold
    assert m["temperature"]["basis"] == "alarm-status"
 
 
# --- pin hành vi mới của Day 28: alarm-status authoritative cho temperature ---
 
def test_temperature_alarm_true_breaches_ignoring_threshold():
    raw = {"temperature": {"instant": 50, "alarm-status": True, "margin": -3},
           "cpu": _CPU_ALL, "memory": {"utilization": 10}}
    out = evaluate_metrics(raw, {"temperature": 999})   # threshold rộng vẫn KHÔNG cứu
    t = out["metrics"]["temperature"]
    assert t["status"] == "BREACH"
    assert t["basis"] == "alarm-status"
    assert t["margin"] == -3
 
 
def test_temperature_falls_back_to_threshold_when_no_alarm_status():
    raw = {"temperature": {"instant": 80},               # KHÔNG có alarm-status
           "cpu": _CPU_ALL, "memory": {"utilization": 10}}
    out = evaluate_metrics(raw, {"temperature": 70})      # 80 > 70
    t = out["metrics"]["temperature"]
    assert t["status"] == "BREACH"
    assert t["basis"] == "threshold"
 
 
def test_temperature_no_alarm_no_threshold_is_unchecked():
    raw = {"temperature": {"instant": 80},
           "cpu": _CPU_ALL, "memory": {"utilization": 10}}
    out = evaluate_metrics(raw)
    assert out["metrics"]["temperature"]["status"] == "NO_THRESHOLD"
SRLEOF
 
cat > test_srl_fetcher.py << 'SRLEOF'
import json
import pathlib
import pytest
from unittest.mock import MagicMock
 
from srl_fetcher import SRLCliFetcher, SRLFetchError
from srl_monitor import evaluate_metrics
 
_HERE = pathlib.Path(__file__).parent
 
 
def _ssh_returning(text):
    ssh = MagicMock()
    ssh.send_command.return_value = text
    return ssh
 
 
def test_fetch_sends_the_exact_capture_command():
    ssh = _ssh_returning("{}")
    SRLCliFetcher(ssh).fetch_control()
    ssh.send_command.assert_called_once_with("info from state platform control A | as json")
 
 
def test_fetch_then_parse_on_REAL_bytes():
    """End-to-end trên bytes THẬT: feed nguyên control_A.json device đã trả,
    qua fetcher rồi evaluate_metrics. Không bịa shape."""
    real_bytes = (_HERE / "control_A.json").read_text()
    raw = SRLCliFetcher(_ssh_returning(real_bytes)).fetch_control()
    out = evaluate_metrics(raw, {"cpu": 80, "memory": 80})
    m = out["metrics"]
    assert m["cpu"]["value"] == 2          # list/index=all/average-1, KHÔNG phải {"total": N}
    assert m["memory"]["value"] == 29
    assert m["temperature"]["value"] == 50
    assert m["temperature"]["basis"] == "alarm-status"
 
 
def test_fetch_unwraps_single_element_list():
    real_obj = json.loads((_HERE / "control_A.json").read_text())
    raw = SRLCliFetcher(_ssh_returning(json.dumps([real_obj]))).fetch_control()
    assert raw["temperature"]["instant"] == 50
 
 
def test_fetch_raises_on_cli_error_text():
    ssh = _ssh_returning("Error: unknown command")
    with pytest.raises(SRLFetchError, match="không phải JSON"):
        SRLCliFetcher(ssh).fetch_control()
 
 
def test_fetch_raises_on_empty():
    with pytest.raises(SRLFetchError, match="rỗng"):
        SRLCliFetcher(_ssh_returning("   ")).fetch_control()
SRLEOF
 
