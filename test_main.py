import main


def test_build_report_runs_on_real_capture():
    """Smoke: pipeline chạy end-to-end trên control_A.json thật, không bịa shape."""
    metrics = main.build_report()["metrics"]
    assert set(metrics) == {"cpu", "memory", "temperature", "healthz"}
    # cpu/memory/temperature là số live (trôi mỗi capture) -> pin LOẠI int, không pin số;
    # healthz là phạm trù chuỗi (authority), không phải int.
    for name in ("cpu", "memory", "temperature"):
        assert isinstance(metrics[name]["value"], int)
    assert isinstance(metrics["healthz"]["value"], str)
    
def test_render_emits_every_metric_without_crashing():
    text = main.render(main.build_report())
    assert "SR LINUX MONITORING DASHBOARD" in text
    for token in ("CPU", "MEMORY", "TEMPERATURE"):
        assert token in text
