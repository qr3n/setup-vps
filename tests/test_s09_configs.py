# tests/test_s09_configs.py
import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from setup_vps.steps.s09_configs import ClientConfigsStep
from setup_vps.config import Config

@pytest.fixture
def mock_config():
    return Config(
        main_domain="main.example.com",
        cdn_domain="cdn.example.com",
        xray_uuid="test-uuid",
        xray_reality_server_name="microsoft.com",
        xray_reality_public_key="test-pub-key",
        xray_reality_short_id="test-short-id",
        hysteria2_auth_password="test-h2-pass"
    )

@patch("setup_vps.steps.s09_configs.Path")
@patch("setup_vps.steps.s09_configs.run_shell")
@patch("setup_vps.steps.s09_configs.print_box")
def test_configs_step_run(mock_print_box, mock_run_shell, mock_path_class, mock_config):
    # Setup mocks
    mock_dir = MagicMock()
    mock_path_class.return_value = mock_dir
    
    # Simulate /root/client_configs
    mock_dir.mkdir = MagicMock()
    
    # We need to handle the loop: Path("/root/client_configs") and then Path("/root/client_configs") / name
    mock_file_path = MagicMock()
    mock_dir.__truediv__.return_value = mock_file_path
    
    # Mock XRAY_BIN exists check
    mock_xray_bin = MagicMock()
    mock_xray_bin.exists.return_value = True
    
    def side_effect(p):
        if "xray" in str(p):
            return mock_xray_bin
        return mock_dir
    
    mock_path_class.side_effect = side_effect

    mock_run_shell.return_value = MagicMock(returncode=0)

    step = ClientConfigsStep()
    result = step.run(mock_config, MagicMock())

    assert result.success is True
    assert "Client configurations generated" in result.message
    
    # Check if write_text was called for each file
    assert mock_file_path.write_text.call_count == 3
    
    # Verify content of one of the files
    args, _ = mock_file_path.write_text.call_args_list[0]
    written_json = json.loads(args[0])
    assert written_json["log"]["loglevel"] == "warning"
    
    # Check if run_shell (xray -test) was called
    assert mock_run_shell.call_count == 3
