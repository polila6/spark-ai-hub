"""Excel report with xlsxwriter: formatted table, totals row with formulas and two native charts.

Run: python ejemplo_xlsxwriter.py  ->  informe_ventas.xlsx
"""
import xlsxwriter

rows = [
    ("Enero", 1200.0, 800.0),
    ("Febrero", 1500.0, 900.0),
    ("Marzo", 1100.0, 950.0),
    ("Abril", 1700.0, 1000.0),
]

wb = xlsxwriter.Workbook("informe_ventas.xlsx")
ws = wb.add_worksheet("Datos")

f_head = wb.add_format({"bold": True, "font_color": "white", "bg_color": "#1F4E78", "border": 1,
                        "align": "center", "valign": "vcenter", "text_wrap": True})
f_eur = wb.add_format({"num_format": '#,##0.00 "€"', "border": 1})
f_pct = wb.add_format({"num_format": "0.0%", "border": 1})
f_txt = wb.add_format({"border": 1})
f_tot = wb.add_format({"bold": True, "num_format": '#,##0.00 "€"', "top": 2})

headers = ["Mes", "Ingresos", "Gastos", "Margen", "Margen %"]
ws.write_row(0, 0, headers, f_head)
for r, (mes, ing, gas) in enumerate(rows, start=1):
    excel_row = r + 1  # 1-based row number used in formulas
    ws.write_string(r, 0, mes, f_txt)
    ws.write_number(r, 1, ing, f_eur)
    ws.write_number(r, 2, gas, f_eur)
    ws.write_formula(r, 3, f"=B{excel_row}-C{excel_row}", f_eur, ing - gas)  # cached value for viewers
    ws.write_formula(r, 4, f"=IF(B{excel_row}=0,0,D{excel_row}/B{excel_row})", f_pct, (ing - gas) / ing)

last = len(rows)  # last data row index (0-based)
tot = last + 1
ws.write_string(tot, 0, "Total", wb.add_format({"bold": True, "top": 2}))
for col, letter in ((1, "B"), (2, "C"), (3, "D")):
    ws.write_formula(tot, col, f"=SUM({letter}2:{letter}{last + 1})", f_tot)

ws.set_column("A:A", 12)
ws.set_column("B:E", 14)
ws.freeze_panes(1, 0)
ws.autofilter(0, 0, last, len(headers) - 1)
ws.conditional_format(1, 3, last, 3, {"type": "cell", "criteria": "<", "value": 0,
                                      "format": wb.add_format({"font_color": "#9C0006", "bg_color": "#FFC7CE"})})
ws.conditional_format(1, 1, last, 1, {"type": "data_bar", "bar_color": "#63BE7B"})

# Column chart (income vs expenses) combined with a line (margin) on the same axes
col = wb.add_chart({"type": "column"})
for c, color in ((1, "#1F77B4"), (2, "#FF7F0E")):
    col.add_series({"name": ["Datos", 0, c], "categories": ["Datos", 1, 0, last, 0],
                    "values": ["Datos", 1, c, last, c], "fill": {"color": color}})
line = wb.add_chart({"type": "line"})
line.add_series({"name": ["Datos", 0, 3], "categories": ["Datos", 1, 0, last, 0],
                 "values": ["Datos", 1, 3, last, 3], "marker": {"type": "circle"}, "line": {"color": "#2CA02C"}})
col.combine(line)
col.set_title({"name": "Ingresos, gastos y margen"})
col.set_x_axis({"name": "Mes"})
col.set_y_axis({"name": "€", "num_format": "#,##0", "major_gridlines": {"visible": True, "line": {"color": "#D9D9D9"}}})
col.set_legend({"position": "bottom"})
col.set_size({"width": 640, "height": 360})
ws.insert_chart("G2", col)

# Pie chart on its own sheet
pie_ws = wb.add_worksheet("Gráficos")
pie = wb.add_chart({"type": "pie"})
pie.add_series({"name": "Ingresos por mes", "categories": ["Datos", 1, 0, last, 0],
                "values": ["Datos", 1, 1, last, 1], "data_labels": {"percentage": True}})
pie.set_title({"name": "Reparto de ingresos"})
pie_ws.insert_chart("B2", pie)

wb.close()
print("Creado informe_ventas.xlsx")
