"""End-to-end seam test: SRLCliFetcher -> evaluate_metrics -> render, trên bytes
THẬT của control_A.json qua mock SSH. Đây là lớp Day 28 còn thiếu: fetcher tới giờ
chỉ test cô lập, main đọc thẳng file (bỏ qua transport). Test này lock contract
fetcher↔monitor và đảm bảo lỗi fetch short-circuit trước khi evaluate thấy rác.

KHÔNG đóng được: SSH thật (đây là mock); envelope thật của '... | as json' trên
device (flat dict vs nested) vẫn nợ verify trên phần cứng.
"""
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
    memory BREACH do ngưỡng demo sát (29>25), cpu OK. Đây là contract fetcher↔monitor."""
    report = run_pipeline(FakeNetmiko(REAL_BYTES))
    m = report["metrics"]
    assert m["temperature"] == {"value": 50, "status": "OK",
                                "basis": "alarm-status", "margin": 25}
    assert m["memory"]["status"] == "BREACH" and m["memory"]["basis"] == "threshold"
    assert m["cpu"]["value"] == 2 and m["cpu"]["status"] == "OK"


def test_pipeline_render_reaches_dashboard_string():
    """End-to-end tới tận chuỗi màn hình: nhãn basis + dòng margin hiện đúng."""
    text = main.render(run_pipeline(FakeNetmiko(REAL_BYTES)))
    assert "SR LINUX MONITORING DASHBOARD" in text
    assert "TEMPERATURE" in text and "(alarm-status)" in text
    assert "MEMORY" in text and "BREACH" in text
    assert "TEMP MARGIN" in text and "25" in text


def test_pipeline_unwraps_single_element_list_envelope():
    """'... | as json' có path bọc object trong list 1 phần tử. Unwrap phòng thủ
    phải cho metrics y hệt flat dict. (Envelope thật vẫn nợ verify trên device.)"""
    wrapped = json.dumps([json.loads(REAL_BYTES)])
    report = run_pipeline(FakeNetmiko(wrapped))
    assert report["metrics"]["temperature"]["value"] == 50
    assert report["metrics"]["cpu"]["value"] == 2


# --- error paths: lỗi fetch short-circuit pipeline, evaluate không chạy ---

@pytest.mark.parametrize("bad_output, why", [
    ("",                       "output rỗng"),
    ("   \n",                  "whitespace-only -> rỗng"),
    ("ERROR: unknown command", "CLI error, không phải JSON"),
    ("[]",                     "JSON list rỗng"),
    ("42",                     "JSON scalar, không phải dict"),
    ('"a string"',             "JSON string, không phải dict"),
])
def test_pipeline_fetch_errors_raise_before_evaluate(bad_output, why):
    with pytest.raises(SRLFetchError):
        run_pipeline(FakeNetmiko(bad_output))
