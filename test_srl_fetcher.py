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
