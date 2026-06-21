# ~/srl-monitor/main.py
"""Entry point: đọc capture control-module thật, evaluate, in dashboard.

Shape phải khớp engine list-based (cpu = list, index 'all', total.average-1).
Render inline, không phụ thuộc module ngoài. basis/margin đọc phòng thủ vì chúng
nằm trong từng metric ở bản monitor alarm-status (Day 28); bản cũ không có thì bỏ
qua, không nổ.
"""
import json
import pathlib

from srl_monitor import evaluate_metrics

HERE = pathlib.Path(__file__).parent
CAPTURE = HERE / "control_A.json"

# Ngưỡng demo. memory=25 cố tình sát để lộ nhánh BREACH trên capture thật
# (memory thật = 29%); cpu/temperature để mức vận hành thường.
DEFAULT_THRESHOLDS = {"cpu": 80, "memory": 25, "temperature": 70}


def build_report(thresholds: dict[str, int] | None = None) -> dict:
    raw_json = json.loads(CAPTURE.read_text())
    return evaluate_metrics(raw_json, thresholds or DEFAULT_THRESHOLDS)


def render(report: dict) -> str:
    lines = ["=" * 45, f"{'SR LINUX MONITORING DASHBOARD':^45}", "=" * 45]
    for name, data in report["metrics"].items():
        unit = "°C" if name == "temperature" else "%"
        basis = data.get("basis")
        basis_tag = f"  ({basis})" if basis else ""
        lines.append(
            f" {name.upper():<12}: {data['value']:>5}{unit:<3} | {data['status']}{basis_tag}"
        )
    margin = report["metrics"].get("temperature", {}).get("margin")
    if margin is not None:
        lines.append(f" {'TEMP MARGIN':<12}: {margin:>5}°C còn lại tới alarm")
    lines.append("=" * 45)
    return "\n".join(lines)


def main() -> None:
    print(render(build_report()))


if __name__ == "__main__":
    main()
