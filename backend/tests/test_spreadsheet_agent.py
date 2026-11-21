"""End-to-end local tests for spreadsheet structure and cell evidence."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np
from openpyxl import Workbook
from fastapi.testclient import TestClient

from app.extraction.api import SpreadsheetExtractionRequest, extract_spreadsheet_schema
from app.extraction.projects import projects
from app.ingest.excel import ingest_excel
from app import main, pipeline
from app.spreadsheet_structure import build_structure, inspect_range, latex_range, render_range_png
from app.store import store
from app.tables import describe_df, write_tables


class SpreadsheetAgentEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_root = store.root
        store.root = Path(self.temp.name) / "docs"
        store.root.mkdir()
        wb = Workbook()
        ws = wb.active
        ws.title = "Summary"
        ws.append(["Customer", "Revenue", "Region"])
        ws.append(["Acme", 1250, "West"])
        ws.append(["Beta", 900, "East"])
        data = io.BytesIO()
        wb.save(data)
        wb.close()
        self.doc = store.create("sales.xlsx", "excel", data.getvalue())
        self.doc.status = "ready"
        chunks, parents, self.tables, self.info = ingest_excel(self.doc.id, store.source_path(self.doc.id), lambda *_: None)
        store.set_chunks(self.doc.id, chunks, parents)
        table_meta = [{"name": t["name"], "title": t["title"], "source": t["source"], "sheet": t["sheet"],
                       "n_rows": len(t["df"]), "n_cols": len(t["df"].columns), "columns": describe_df(t["df"]),
                       "source_bounds": t["source_bounds"], "header_row": t["header_row"],
                       "data_start_row": t["data_start_row"]} for t in self.tables]
        write_tables(store.tables_db(self.doc.id), self.tables)
        self.doc.sheets = self.info["sheets"]
        self.doc.tables = table_meta
        build_structure(self.doc.id, self.info, table_meta, store.source_path(self.doc.id))

    def tearDown(self):
        for project in projects.list():
            if self.doc.id in project.get("documents", []):
                projects.delete(project["id"])
        store.delete(self.doc.id)
        store.root = self.old_root
        self.temp.cleanup()

    def test_structure_range_serializers_and_caps(self):
        structure = build_structure(self.doc.id, self.info, self.doc.tables, store.source_path(self.doc.id))
        region = structure["sheets"][0]["regions"][0]
        self.assertEqual(region["address"], "A1:C3")
        result = inspect_range(self.doc.id, "Summary", "A2:B3")
        self.assertEqual(result["rows"][0][0]["address"], "A2")
        self.assertEqual(result["rows"][0][1]["value"], 1250)
        self.assertIn(r"\begin{tabular}", latex_range(self.doc.id, "Summary", "A1:B2"))
        self.assertTrue(render_range_png(self.doc.id, "Summary", "A1:B2").startswith(b"\x89PNG"))
        with self.assertRaisesRegex(ValueError, "at most"):
            inspect_range(self.doc.id, "Summary", "A1:ZZZ100")

    def test_schema_extraction_cites_source_cells(self):
        project = projects.create("Sales", "Extract customer revenue")
        projects.update(project["id"], lambda p: p.update({
            "documents": [self.doc.id],
            "schema": {"fields": [{"id": "revenue", "name": "Revenue", "description": "", "data_type": "number",
                                    "required": False, "nullable": True, "unit": "", "allowed_values": [],
                                    "multiple": False, "constraints": {}, "normalization": "", "instructions": ""}]},
        }))
        result = extract_spreadsheet_schema(project["id"], SpreadsheetExtractionRequest())
        self.assertEqual(result["observation_count"], 2)
        evidence = result["observations"][0]["fields"]["revenue"]["evidence"][0]
        self.assertEqual(evidence["sheet"], "Summary")
        self.assertEqual(evidence["address"], "B2")
        self.assertTrue(evidence["verified"])

    def test_http_upload_to_structure_and_source_range(self):
        source = store.source_path(self.doc.id).read_bytes()

        @asynccontextmanager
        async def no_lifespan(_app):
            yield

        old_lifespan = main.app.router.lifespan_context
        main.app.router.lifespan_context = no_lifespan
        try:
            with TestClient(main.app) as client, patch.object(pipeline, "submit"):
                uploaded = client.post("/api/documents", files={"files": ("api.xlsx", source,
                                                                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
                self.assertEqual(uploaded.status_code, 200, uploaded.text)
                doc_id = uploaded.json()[0]["id"]
                with patch.object(pipeline, "embed_passages", side_effect=lambda texts, contexts: np.zeros((len(texts), 4))), \
                        patch.object(pipeline, "embedding_signature", return_value="test:4"), \
                        patch.object(pipeline.retriever, "invalidate"), \
                        patch.object(pipeline, "submit_extraction"), patch.object(pipeline, "submit_visual"):
                    pipeline._process(doc_id, auto_extract=False, contextual_retrieval=False)
                structure = client.get(f"/api/documents/{doc_id}/spreadsheet-structure")
                self.assertEqual(structure.status_code, 200, structure.text)
                cells = client.get(f"/api/documents/{doc_id}/sheets/Summary/range", params={"address": "A2:B2"})
                self.assertEqual(cells.status_code, 200, cells.text)
                self.assertEqual(cells.json()["rows"][0][1]["address"], "B2")
        finally:
            main.app.router.lifespan_context = old_lifespan
