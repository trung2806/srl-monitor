import main


def test_build_report_runs_on_real_capture():
    """Smoke: pipeline chạy end-to-end trên control_A.json thật, không bịa shape."""
    metrics = main.build_report()["metrics"]
    assert set(metrics) == {"cpu", "memory", "temperature"}
    assert metrics["cpu"]["value"] == 2        # list/index=all/average-1
    assert metrics["memory"]["value"] == 29
    assert metrics["temperature"]["value"] == 50


def test_render_emits_every_metric_without_crashing():
    text = main.render(main.build_report())
    assert "SR LINUX MONITORING DASHBOARD" in text
    for token in ("CPU", "MEMORY", "TEMPERATURE"):
        assert token in text
