from setup_vps.steps.base import StepResult
from unittest.mock import MagicMock, patch

def test_step_result_reboot_default():
    r = StepResult(success=True)
    assert r.reboot_required is False

def test_step_result_reboot_set():
    r = StepResult(success=True, reboot_required=True)
    assert r.reboot_required is True

def test_main_run_step_handles_reboot():
    from setup_vps.main import _run_step
    from setup_vps.state import StepStatus
    
    mock_step = MagicMock()
    mock_step.preflight.return_value = False
    mock_step.run.return_value = StepResult(success=True, message="Need reboot", reboot_required=True)
    mock_step.name = "test_step"
    mock_step.title = "Test Step"
    
    mock_state = MagicMock()
    mock_state.get_status.return_value = StepStatus.PENDING
    
    success, reboot = _run_step(mock_step, MagicMock(), mock_state)
    
    assert success is True
    assert reboot is True
    mock_state.mark_done.assert_called_with("test_step")
