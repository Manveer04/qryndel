"""Tests for backend capability declarations and preflight."""

import pytest

from qryndel.backends import get_backend
from qryndel.capabilities import CapabilityError, required_capabilities
from qryndel.logical_plan import (build_plan)
from qryndel.parser import parse


def test_preflight_reports_plan_requirements():
    query = parse("FROM users RETURN COUNT(*) GROUP BY city")
    requirements = required_capabilities(build_plan(query))
    assert {"select", "group_by", "aggregate:COUNT"} <= requirements
