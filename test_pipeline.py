import json
import pathlib
import pytest

from srl_fetcher import SRLCliFetcher, SRLFetchError
from srl_monitor import evaluate_metrics
import main

CAPTURE = pathlib.Path(__file__).parent / "control_A.json"
REAL_BYTES = CAPTURE.read_text()


class FakeNetmiko:
    """Transport giả thay netmiko: trả output đã nạp, ghi lại lệnh đã gửi.
    Khớp đúng seam SRLCliFetcher kỳ vọng: .send_command(cmd) -> str."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.last_command = None

    def send_command(self, command):
        self.last_command = command
        return self._response


def run_pipeline(ssh, thresholds=None):
    """Chuỗi thật: fetch (qua seam) -> evaluate. Lỗi fetch phải nổ TẠI ĐÂY,
    evaluate không bao giờ thấy dữ liệu rác."""
    raw = SRLCliFetcher(ssh).fetch_control()
    return evaluate_metrics(raw, thresholds or main.DEFAULT_THRESHOLDS)


# --- happy path: seam trên bytes thật, suốt tới chuỗi render ---

def test_fetcher_sends_canonical_control_command():
    """Lệnh seam gửi phải đúng lệnh đã sinh ra control_A.json (provenance khớp)."""
    ssh = FakeNetmiko(REAL_BYTES)
    SRLCliFetcher(ssh).fetch_control()
    assert ssh.last_command == "info from state platform control A | as json"
    assert ssh.last_command == SRLCliFetcher.CONTROL_CMD


def test_pipeline_on_real_capture_bytes_through_evaluate():
    """fetch(mock bytes thật) -> evaluate. Temperature do alarm-status lái (vendor),
    memory BREACH do ngưỡng demo sát (mem>25), cpu OK. Đây là contract fetcher↔monitor."""
    cap = json.loads(REAL_BYTES)
    cpu_all = next(c for c in cap["cpu"] if c["index"] == "all")
    report = run_pipeline(FakeNetmiko(REAL_BYTES))
    m = report["metrics"]
    assert m["temperature"]["value"] == cap["temperature"]["instant"]
    assert m["temperature"]["status"] == "OK" and m["temperature"]["basis"] == "alarm-status"
    assert m["temperature"]["margin"] == cap["temperature"]["margin"]
    assert m["memory"]["status"] == "BREACH" and m["memory"]["basis"] == "threshold"
    assert m["cpu"]["value"] == cpu_all["total"]["average-1"] and m["cpu"]["status"] == "OK"


def test_pipeline_unwraps_single_element_list_envelope():
    """'... | as json' có path bọc object trong list 1 phần tử. Unwrap phòng thủ
    must cho metrics y hệt flat dict."""
    cap = json.loads(REAL_BYTES)
    cpu_all = next(c for c in cap["cpu"] if c["index"] == "all")
    wrapped = json.dumps([cap])
    report = run_pipeline(FakeNetmiko(wrapped))
    assert report["metrics"]["temperature"]["value"] == cap["temperature"]["instant"]
    assert report["metrics"]["cpu"]["value"] == cpu_all["total"]["average-1"]


# --- error paths: lỗi fetch short-circuit pipeline, evaluate không chạy ---

@pytest.mark.parametrize("bad_output, expected_exception, why", [
    ("",                       ValueError,            "output rỗng"),
    ("   \n",                  ValueError,            "whitespace-only -> rỗng"),
    ("ERROR: unknown command", json.JSONDecodeError, "CLI error, không phải JSON"),
    ("[]",                     ValueError,            "JSON list rỗng"),
    ("42",                     TypeError,             "JSON scalar, không phải dict"),
    ('"a string"',             TypeError,             "JSON string, không phải dict"),
])
def test_pipeline_fetch_errors_raise_before_evaluate(bad_output, expected_exception, why):
    """CẬP NHẬT: Kiểm tra chính xác loại stdlib exception bắn ra ứng với từng loại lỗi dữ liệu,
    đảm bảo dữ liệu rác bị chặn đứng trước khi vào tầng evaluate."""
    with pytest.raises(expected_exception):
        run_pipeline(FakeNetmiko(bad_output))
