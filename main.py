# ~/srl-monitor/main.py
from srl_monitor import evaluate_metrics
from srl_display import render_dashboard

raw_json = {
    "cpu": {"total": 95}, 
    "memory": {"utilization": 50}, 
    "temperature": {"instant": 85, "margin": 5}
}
thresholds = {"cpu": 80, "memory": 90, "temperature": 80}

# Khởi chạy qua Monitor Orchestrator
metrics_report = evaluate_metrics(raw_json, thresholds)

# Đồng bộ nhẹ lại srl_display vì cấu trúc status hiện tại nằm trong metrics_report
print("\n" + "="*45)
print(f"{'SR LINUX MONITORING DASHBOARD':^45}")
print("="*45)
for name, data in metrics_report["metrics"].items():
    unit = "°C" if name == "temperature" else "%"
    print(f" -> {name.upper():<12}: {data['value']:>6}{unit:<5} | STATUS: {data['status']}")
if "margin_reported" in metrics_report["metadata"]:
    print(f" -> Temp Margin : {metrics_report['metadata']['margin_reported']:>6}°C")
print("="*45 + "\n")
