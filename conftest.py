import json
import pathlib
import pytest
 
_HERE = pathlib.Path(__file__).parent
 
 
@pytest.fixture
def control_capture():
    """Real capture: info from state platform control A | as json (v26.3.2)."""
    return json.loads((_HERE / "control_A.json").read_text())
