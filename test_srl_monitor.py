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
