"""Unit tests for the credential-pattern scanner."""

from __future__ import annotations

import pytest

from toolforge.guardrails.credentials import scan_credentials


@pytest.mark.unit
class TestGitHubPAT:
    def test_ghp_detected(self):
        assert scan_credentials("my token is ghp_abcdefghijklmnopqrstuvwxyz") == "github_pat"

    def test_gho_detected(self):
        assert scan_credentials("gho_abcdefghijklmnopqrstuvwxyz") == "github_pat"

    def test_ghu_detected(self):
        assert scan_credentials("ghu_abcdefghijklmnopqrstuvwxyz") == "github_pat"

    def test_ghs_detected(self):
        assert scan_credentials("ghs_abcdefghijklmnopqrstuvwxyz") == "github_pat"

    def test_ghr_detected(self):
        assert scan_credentials("ghr_abcdefghijklmnopqrstuvwxyz") == "github_pat"

    def test_too_short_not_detected(self):
        # Only 10 chars after prefix — minimum is 20
        assert scan_credentials("ghp_short12345") is None

    def test_prefix_only_not_detected(self):
        assert scan_credentials("ghp_") is None

    def test_in_sentence(self):
        assert scan_credentials("Please rotate ghp_abcdefghijklmnopqrstuv for me") == "github_pat"


@pytest.mark.unit
class TestSlackToken:
    def test_xoxb_detected(self):
        assert scan_credentials("xoxb-123456789012-abcdefghijk") == "slack_token"

    def test_xoxp_detected(self):
        assert scan_credentials("xoxp-123456789012-abcdefghijk") == "slack_token"

    def test_xoxa_detected(self):
        assert scan_credentials("xoxa-123456789012-abcdefghijk") == "slack_token"

    def test_xoxr_detected(self):
        assert scan_credentials("xoxr-123456789012-abcdefghijk") == "slack_token"

    def test_xoxs_detected(self):
        assert scan_credentials("xoxs-123456789012-abcdefghijk") == "slack_token"

    def test_too_short_not_detected(self):
        # Only 5 chars after xoxb- — minimum is 10
        assert scan_credentials("xoxb-12345") is None


@pytest.mark.unit
class TestAWSAccessKey:
    def test_akia_detected(self):
        assert scan_credentials("AKIAIOSFODNN7EXAMPLE") == "aws_access_key"

    def test_akia_in_sentence(self):
        assert scan_credentials("key=AKIAIOSFODNN7EXAMPLE rest") == "aws_access_key"

    def test_akia_wrong_length_not_detected(self):
        # AKIA followed by 15 uppercase chars — needs exactly 16
        assert scan_credentials("AKIAIOSFODNN7EX") is None

    def test_akia_lowercase_not_detected(self):
        assert scan_credentials("akiaiosfodnn7example") is None


@pytest.mark.unit
class TestNegativeCorpus:
    @pytest.mark.parametrize("text", [
        "deploy the service",
        "read file /tmp/hello.txt",
        "ghp without underscore",
        "xoxb without dash",
        "The AKIA prefix is 4 chars",
        "create a GitHub repository",
        "describe how to rotate credentials",
        "",
        "hello world",
    ])
    def test_benign_prompt_not_detected(self, text: str):
        assert scan_credentials(text) is None
