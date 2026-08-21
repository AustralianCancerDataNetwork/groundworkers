from __future__ import annotations

import logging
import re

import pytest

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer


def test_error_code_vocabulary_is_runtime_validated() -> None:
    with pytest.raises(ValueError, match="Unknown Groundworkers error code"):
        GroundworkersError("UNAVAILABLE", "invalid live code")


def test_expected_mcp_error_scrubs_credentials() -> None:
    server = GroundworkersMCPServer("errors")

    @server.tool("expected")
    def expected():
        raise GroundworkersError(
            "BACKEND_UNAVAIL",
            "failed postgresql://user:password@db/omop api_key=secret-value",
        )

    result = server.call("expected")

    assert result["code"] == "BACKEND_UNAVAIL"
    assert "password" not in result["message"]
    assert "secret-value" not in result["message"]


def test_unexpected_mcp_error_is_logged_with_incident_and_returns_generic_message(
    caplog,
) -> None:
    server = GroundworkersMCPServer("errors")

    @server.tool("unexpected")
    def unexpected():
        raise RuntimeError("driver failed password=credential-canary")

    with caplog.at_level(logging.ERROR):
        result = server.call("unexpected")

    assert result["code"] == "INTERNAL_ERROR"
    assert "credential-canary" not in result["message"]
    incident = re.search(r"Incident ID: ([0-9a-f]{32})", result["message"])
    assert incident is not None
    assert incident.group(1) in caplog.text
