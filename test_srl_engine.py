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
