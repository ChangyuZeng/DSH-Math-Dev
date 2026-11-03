"""Model-only read bounds, using synthetic stores and no inference."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import open_store
from model_tools import _execute, READ_OUTPUT_BYTES
from schema import StoreError
import test_product as product
from test_product import note


class ModelReadBounds(unittest.TestCase):
    setUp = product.Product.setUp
    tearDown = product.Product.tearDown
    commit = product.Product.commit

    def read(self, **options):
        arguments = dict(kind=None, state=None, text="", object_id=None, rev=None)
        arguments.update(options)
        with open_store(self.research) as store:
            return _execute(self.projects, store, {}, "research_read", arguments)

    def test_legacy_exact_shapes_and_filters(self):
        self.commit("Note", "N", note())
        for options in ({}, {"kind": "Note", "text": "even"}, {"object_id": "N", "rev": 1}):
            arguments = dict(kind=None, state=None, text="", object_id=None, rev=None)
            arguments.update(options)
            with open_store(self.research) as store:
                expected = store.research_read(**arguments)
            self.assertEqual(self.read(**options), expected)
        self.assertIsInstance(self.read()["results"], list)

    def test_large_body_exact_historical_unicode_windows(self):
        old = note()
        old["body"] = "数学🙂\n" * 20000
        self.commit("Note", "N", old)
        current = deepcopy(old)
        current["body"] = "New revision"
        self.commit("Note", "N", current, 1)
        with self.assertRaisesRegex(StoreError, "page=.*window=") as caught:
            self.read(object_id="N", rev=1)
        self.assertEqual(caught.exception.code, "READ_OUTPUT_LIMIT")
        chunks, offset = [], 0
        while offset is not None:
            result = self.read(object_id="N", rev=1, window={"field": "body", "offset": offset, "limit": 8192})
            self.assertEqual(result["reference"], {"id": "N", "rev": 1})
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), READ_OUTPUT_BYTES)
            chunks.append(result["window"]["text"])
            offset = result["window"]["next_offset"]
        self.assertEqual("".join(chunks), old["body"])
        self.assertEqual(self.read(object_id="N", rev=2)["object"]["data"]["body"], "New revision")
        empty = self.read(object_id="N", rev=1, window={"field": "body", "offset": 999999, "limit": 10})
        self.assertEqual(empty["window"]["text"], "")
        self.assertIsNone(empty["window"]["next_offset"])

    def test_large_directory_pagination_and_filtering(self):
        for index in range(17):
            data = note()
            data["body"] = "x" * 5000
            data["title"] = str(index) + "a" * 200
            self.commit("Note", "N" + str(index), data)
        with self.assertRaises(StoreError) as caught:
            self.read(kind="Note")
        self.assertEqual(caught.exception.code, "READ_OUTPUT_LIMIT")
        ids, offset = [], 0
        while offset is not None:
            result = self.read(kind="Note", text="Full text retained", page={"offset": offset, "limit": 3})
            self.assertNotIn("results", result)
            self.assertEqual(result["page"]["total"], 17)
            self.assertTrue(all(row["title_truncated"] for row in result["entries"]))
            ids.extend(row["id"] for row in result["entries"])
            offset = result["page"]["next_offset"]
        self.assertEqual(ids, ["N" + str(index) for index in range(17)])
        with self.assertRaises(StoreError) as caught:
            self.read(kind="Note", page={"offset": 0, "limit": 17}, max_output_bytes=1024)
        self.assertEqual(caught.exception.code, "READ_OUTPUT_LIMIT")
        empty = self.read(kind="Note", text="not in the synthetic notes", page={"offset": 0, "limit": 3})
        self.assertEqual(empty["page"]["total"], 0)
        self.assertEqual(empty["entries"], [])
        empty = self.read(kind="Note", page={"offset": 100, "limit": 3})
        self.assertEqual(empty["entries"], [])
        self.assertIsNone(empty["page"]["next_offset"])

    def test_invalid_options_and_explicit_budget(self):
        data = note()
        data["body"] = "x" * 10000
        self.commit("Note", "N", data)
        invalid = [
            {"page": None}, {"window": None}, {"page": {"offset": -1, "limit": 1}},
            {"page": {"offset": 0, "limit": True}}, {"page": {"offset": 0, "limit": 101}},
            {"object_id": "N", "page": {"offset": 0, "limit": 1}},
            {"object_id": "N", "window": {"field": "body", "offset": 0, "limit": 10}},
            {"object_id": "N", "rev": 1, "window": {"field": "uses", "offset": 0, "limit": 10}},
            {"object_id": "N", "rev": 1, "window": {"field": "body", "offset": 0, "limit": 16385}},
            {"page": {"offset": 0, "limit": 1}, "window": {"field": "body", "offset": 0, "limit": 10}},
            {"max_output_bytes": 65537}, {"max_output_bytes": True}, {"max_output_bytes": 1023},
        ]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(StoreError) as caught:
                self.read(**options)
            self.assertEqual(caught.exception.code, "INVALID_SCHEMA")
        with self.assertRaises(StoreError) as caught:
            self.read(object_id="N", rev=1, max_output_bytes=1024,
                      window={"field": "body", "offset": 0, "limit": 2000})
        self.assertEqual(caught.exception.code, "READ_OUTPUT_LIMIT")
        result = self.read(object_id="N", rev=1, max_output_bytes=1024,
                           window={"field": "body", "offset": 0, "limit": 100})
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 1024)


if __name__ == "__main__":
    unittest.main()
