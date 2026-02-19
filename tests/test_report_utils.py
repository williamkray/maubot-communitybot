"""Tests for report utility functions."""

import pytest

from community.helpers.report_utils import (
    generate_activity_report, split_doctor_report, format_ban_results,
    format_sync_results
)


class TestReportUtils:
    """Test cases for report utility functions."""

    def test_generate_activity_report_success(self):
        """Test generating activity report successfully."""
        database_results = {
            "active": [{"mxid": "@user1:example.com"}, {"mxid": "@user2:example.com"}],
            "inactive": [{"mxid": "@user3:example.com"}],
            "ignored": [{"mxid": "@user4:example.com"}]
        }

        result = generate_activity_report(database_results)

        assert result["active"] == ["@user1:example.com", "@user2:example.com"]
        assert result["inactive"] == ["@user3:example.com"]
        assert result["ignored"] == ["@user4:example.com"]

    def test_generate_activity_report_empty(self):
        """Test generating activity report with empty results."""
        database_results = {
            "active": [],
            "inactive": [],
            "ignored": []
        }

        result = generate_activity_report(database_results)

        assert result["active"] == ["none"]
        assert result["inactive"] == ["none"]
        assert result["ignored"] == ["none"]

    def test_generate_activity_report_missing_keys(self):
        """Test generating activity report with missing keys."""
        database_results = {}

        result = generate_activity_report(database_results)

        assert result["active"] == ["none"]
        assert result["inactive"] == ["none"]
        assert result["ignored"] == ["none"]

    def test_split_doctor_report_small(self):
        """Test splitting small report that doesn't need splitting."""
        report_text = "This is a small report that fits in one chunk."

        result = split_doctor_report(report_text, 1000)

        assert len(result) == 1
        assert result[0] == report_text

    def test_split_doctor_report_large(self):
        """Test splitting large report into chunks."""
        # Create a large report
        lines = [f"Line {i}: This is a test line for splitting" for i in range(100)]
        report_text = "\n".join(lines)

        result = split_doctor_report(report_text, 100)  # Small chunk size

        assert len(result) > 1
        assert all(len(chunk) <= 100 for chunk in result)

        # Verify all content is preserved (account for newlines)
        combined = "\n".join(result)
        assert combined == report_text

    def test_split_doctor_report_with_sections(self):
        """Test splitting report with section headers."""
        report_text = """<h3>Section 1</h3>
This is section 1 content.
<h3>Section 2</h3>
This is section 2 content.
<h3>Section 3</h3>
This is section 3 content."""

        result = split_doctor_report(report_text, 50)  # Small chunk size

        assert len(result) > 1
        assert all(len(chunk) <= 50 for chunk in result)

    def test_format_ban_results_success(self):
        """Test formatting ban results with successful bans."""
        ban_event_map = {
            "ban_list": {
                "@user1:example.com": ["Room 1", "Room 2"],
                "@user2:example.com": ["Room 3"]
            },
            "error_list": {}
        }

        result = format_ban_results(ban_event_map)

        assert "Banned @user1:example.com from: Room 1, Room 2" in result
        assert "Banned @user2:example.com from: Room 3" in result

    def test_format_ban_results_with_errors(self):
        """Test formatting ban results with errors."""
        ban_event_map = {
            "ban_list": {
                "@user1:example.com": ["Room 1"]
            },
            "error_list": {
                "@user2:example.com": ["Room 2", "Room 3"]
            }
        }

        result = format_ban_results(ban_event_map)

        assert "Banned @user1:example.com from: Room 1" in result
        assert "Failed to ban @user2:example.com from: Room 2, Room 3" in result

    def test_format_ban_results_empty(self):
        """Test formatting empty ban results."""
        ban_event_map = {
            "ban_list": {},
            "error_list": {}
        }

        result = format_ban_results(ban_event_map)

        assert result == "No ban operations performed"

    def test_format_sync_results_success(self):
        """Test formatting sync results with data."""
        sync_results = {
            "added": ["@user1:example.com", "@user2:example.com"],
            "dropped": ["@user3:example.com"]
        }

        result = format_sync_results(sync_results)

        assert "Added: @user1:example.com<br />@user2:example.com" in result
        assert "Dropped: @user3:example.com" in result

    def test_format_sync_results_empty(self):
        """Test formatting empty sync results."""
        sync_results = {
            "added": [],
            "dropped": []
        }

        result = format_sync_results(sync_results)

        assert "Added: none" in result
        assert "Dropped: none" in result

    def test_format_sync_results_missing_keys(self):
        """Test formatting sync results with missing keys."""
        sync_results = {}

        result = format_sync_results(sync_results)

        assert "Added: none" in result
        assert "Dropped: none" in result
