"""Generate deliberately messy sample documents for trying DocuSight.

    python scripts/make_samples.py   ->  samples/*.xlsx, samples/*.pdf
"""
from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

import openpyxl
import pymupdf
from openpyxl.styles import Font

OUT = Path(__file__).resolve().parents[1] / "samples"
OUT.mkdir(exist_ok=True)
random.seed(7)


def workbook() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    # form-style block (label: value)
    ws["A1"] = "ACME Industries — FY2025 Regional Sales Report"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A3"], ws["B3"] = "Prepared by:", "Dana Whitfield (Finance)"
    ws["A4"], ws["B4"] = "Report date:", dt.date(2026, 2, 14)
    ws["A5"], ws["B5"] = "Currency:", "USD"
    ws["A6"], ws["B6"] = "Status:", "FINAL — approved by CFO"
    # multi-row header with merged group cells
    ws["A9"] = "Quarterly revenue by region"
    ws.merge_cells("B10:C10"); ws["B10"] = "H1"
    ws.merge_cells("D10:E10"); ws["D10"] = "H2"
    ws["A11"], ws["B11"], ws["C11"], ws["D11"], ws["E11"], ws["F11"] = "Region", "Q1", "Q2", "Q3", "Q4", "Total"
    regions = {"North America": 4.2, "EMEA": 3.1, "APAC": 2.7, "LATAM": 0.9}
    r = 12
    for name, base in regions.items():
        ws.cell(r, 1, name)
        for q in range(4):
            ws.cell(r, 2 + q, round(base * 1e6 * (1 + 0.06 * q + random.uniform(-0.05, 0.05)), 0))
        ws.cell(r, 6, f"=SUM(B{r}:E{r})")
        r += 1
    # second table side by side (separated by blank column G)
    ws["H9"] = "Headcount"
    ws["H11"], ws["I11"], ws["J11"] = "Team", "FTE", "Open roles"
    for i, (t, f, o) in enumerate([("Sales", 48, 6), ("Support", 22, 2), ("Engineering", 75, 11), ("Ops", 15, 0)]):
        ws.cell(12 + i, 8, t); ws.cell(12 + i, 9, f); ws.cell(12 + i, 10, o)
    ws["A18"] = "Note: Q4 EMEA includes a one-off $350,000 license deal (contract #EM-2291)."

    ws2 = wb.create_sheet("Transactions")
    ws2.append(["Transaction log — all deals > $5k"])
    ws2.append([])
    ws2.append(["Deal ID", "Date", "Customer", "Region", "Product", "Units", "Unit price", "Discount", "Sales rep"])
    products = {"Atlas Pro": 1200, "Atlas Lite": 450, "Beacon Suite": 3800, "Comet API": 900}
    custs = ["Globex", "Initech", "Umbrella", "Stark Ind.", "Wayne Ent.", "Hooli", "Soylent", "Tyrell", "Cyberdyne"]
    reps = ["J. Ortiz", "M. Chen", "A. Novak", "R. Patel"]
    for i in range(240):
        p = random.choice(list(products))
        ws2.append([f"D-{10400 + i}", dt.date(2025, 1, 1) + dt.timedelta(days=random.randint(0, 364)),
                    random.choice(custs), random.choice(list(regions)), p, random.randint(5, 120),
                    f"${products[p]:,}", f"{random.choice([0, 5, 10, 15])}%", random.choice(reps)])
    wb.save(OUT / "acme_sales_fy2025.xlsx")


def pdf() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 60

    def line(text: str, size: float = 10.5, bold: bool = False, gap: float = 16) -> None:
        nonlocal y
        page.insert_text((56, y), text, fontsize=size, fontname="hebo" if bold else "helv")
        y += gap

    line("INVOICE", 22, True, 30)
    line("Northwind Components GmbH · Industriestraße 12 · 70565 Stuttgart · VAT DE811223344", 9)
    y += 10
    for k, v in [("Invoice number", "INV-2025-00871"), ("Invoice date", "2025-11-03"), ("Due date", "2025-12-03"),
                 ("Customer", "ACME Industries Inc., 500 Market St, San Francisco, CA"),
                 ("PO number", "PO-77812"), ("Payment terms", "Net 30, 2% discount if paid within 10 days")]:
        line(f"{k}: {v}")
    y += 12
    rows = [("Item", "Description", "Qty", "Unit price", "Amount"),
            ("A-100", "Servo motor 400W", "12", "€ 310.00", "€ 3,720.00"),
            ("A-215", "Controller board rev.C", "12", "€ 145.50", "€ 1,746.00"),
            ("C-009", "Shielded cable 5m", "40", "€ 12.90", "€ 516.00"),
            ("S-001", "On-site commissioning (days)", "2", "€ 950.00", "€ 1,900.00")]
    xs = [56, 120, 330, 380, 470]
    top = y
    for row in rows:
        for x, cell in zip(xs, row):
            page.insert_text((x, y), cell, fontsize=10, fontname="hebo" if row is rows[0] else "helv")
        y += 18
    for yy in [top - 13, top + 5, y - 13]:
        page.draw_line((52, yy), (545, yy))
    y += 8
    for k, v in [("Subtotal", "€ 7,882.00"), ("VAT 19%", "€ 1,497.58"), ("Total due", "€ 9,379.58")]:
        page.insert_text((380, y), k, fontsize=10.5, fontname="hebo" if k == "Total due" else "helv")
        page.insert_text((470, y), v, fontsize=10.5, fontname="hebo" if k == "Total due" else "helv")
        y += 16
    y += 20
    line("Bank: Commerzbank Stuttgart · IBAN DE89 3704 0044 0532 0130 00 · BIC COBADEFFXXX", 9)

    # page 2: a bar chart drawn as vector graphics with almost no text (needs vision)
    p2 = doc.new_page()
    p2.insert_text((56, 60), "Delivery performance 2025", fontsize=16, fontname="hebo")
    vals = [("Q1", 91), ("Q2", 94), ("Q3", 88), ("Q4", 97)]
    for i, (q, v) in enumerate(vals):
        x = 90 + i * 110
        h = v * 3
        p2.draw_rect(pymupdf.Rect(x, 420 - h, x + 60, 420), color=(0.2, 0.4, 0.8), fill=(0.2, 0.4, 0.8))
        p2.insert_text((x + 18, 440), q, fontsize=11)
        p2.insert_text((x + 14, 412 - h), f"{v}%", fontsize=11)
    p2.insert_text((56, 480), "On-time delivery rate by quarter. Target: 95%.", fontsize=10)
    doc.save(OUT / "northwind_invoice.pdf")


if __name__ == "__main__":
    workbook()
    pdf()
    print("samples written to", OUT)
