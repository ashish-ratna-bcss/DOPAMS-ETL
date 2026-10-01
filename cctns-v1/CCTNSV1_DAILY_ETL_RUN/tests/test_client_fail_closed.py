"""Fail-closed payload parsing for unfiltered / chunked API responses."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apis.client import ApiError, _records_from_payload, fetch_unfiltered  # noqa: E402


class RecordsFromPayloadTests(unittest.TestCase):
    def test_list_ok(self):
        self.assertEqual(_records_from_payload([{"a": 1}], context="t"), [{"a": 1}])

    def test_dict_data_ok(self):
        self.assertEqual(
            _records_from_payload({"data": [{"a": 1}], "message": "ok"}, context="t"),
            [{"a": 1}],
        )

    def test_missing_data_raises(self):
        with self.assertRaises(ApiError):
            _records_from_payload({"message": "Error fetching"}, context="t")

    def test_non_list_data_raises(self):
        with self.assertRaises(ApiError):
            _records_from_payload({"data": {"x": 1}}, context="t")

    def test_error_message_empty_data_raises(self):
        with self.assertRaises(ApiError):
            _records_from_payload(
                {"data": [], "message": "Error fetching data from Dao"},
                context="t",
            )

    def test_empty_data_ok_message_ok(self):
        self.assertEqual(
            _records_from_payload({"data": [], "message": "Success"}, context="t"),
            [],
        )


class FetchUnfilteredTests(unittest.TestCase):
    @patch("apis.client.request_with_retry")
    def test_weird_scalar_raises(self, mock_req):
        mock_req.return_value = "not-json-object"
        with self.assertRaises(ApiError):
            fetch_unfiltered("GET", "http://example.test/api")


if __name__ == "__main__":
    unittest.main()
