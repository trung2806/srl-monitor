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
