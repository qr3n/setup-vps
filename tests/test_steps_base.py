# tests/test_steps_base.py
import pytest
from unittest.mock import MagicMock, patch
from setup_vps.steps.base import BaseStep, StepResult, VerifyResult
from setup_vps.state import StepStatus


class ConcreteStep(BaseStep):
    name = "test_step"
    title = "Test Step"

    def preflight(self, config, state) -> bool:
        return False  # not done

    def run(self, config, state) -> StepResult:
        return StepResult(success=True, message="ran")

    def verify(self, config, state) -> VerifyResult:
        return VerifyResult(passed=True, checks={"echo": "ok"})


def test_step_result_success():
    r = StepResult(success=True, message="ok")
    assert r.success is True


def test_verify_result_passed():
    r = VerifyResult(passed=True, checks={"check1": "value"})
    assert r.passed is True
    assert r.checks["check1"] == "value"


def test_base_step_has_name():
    step = ConcreteStep()
    assert step.name == "test_step"
    assert step.title == "Test Step"


def test_base_step_preflight_returns_bool():
    step = ConcreteStep()
    result = step.preflight(config=None, state=None)
    assert isinstance(result, bool)


def test_base_step_run_returns_step_result():
    step = ConcreteStep()
    result = step.run(config=None, state=None)
    assert isinstance(result, StepResult)


def test_base_step_verify_returns_verify_result():
    step = ConcreteStep()
    result = step.verify(config=None, state=None)
    assert isinstance(result, VerifyResult)
