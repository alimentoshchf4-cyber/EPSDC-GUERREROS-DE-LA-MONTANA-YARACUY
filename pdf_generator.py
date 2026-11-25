import os
from datetime import datetime
from fpdf import FPDF

def safe_text(text) -> str:
    if text is None:
        return ""
    return str(text).encode("latin-1", "replace").decode("latin-1")

class ComprobanteEPSDC(FPDF):
    def __init__(self, titulo_documento: str, codigo_documento: str, unidad_emisora: str):
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.titulo_documento = titulo_documento
        self.codigo_documento = codigo_documento
        self.unidad_emisora = unidad_emisora
        self.set_auto_page_break(auto=True, margin=22)

    def header(self):
        # Barra superior institucional verde cafetal
        self.set_fill_color(27, 67, 50)
        self.rect(0, 0, 216, 10, "F")
        self.set_fill_color(155, 34, 38)
        self.rect(0, 10, 216, 2, "F")

        # Logos corporativos
        logo_cafetal = "assets/logo_el_cafetal.png"
        logo_portu = "assets/logo_por_tu_cafe.png"

        if os.path.exists(logo_cafetal):
            self.image(logo_cafetal, x=12, y=15, w=24)
        if os.path.exists(logo_portu):
            self.image(logo_portu, x=182, y=14, w=20)

        # Encabezado institucional central
        self.set_xy(38, 15)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(27, 67, 50)
        self.cell(140, 5, safe_text("EMPRESA DE PROPIEDAD SOCIAL DIRECTA COMUNAL"), ln=True, align="C")

        self.set_x(38)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(155, 34, 38)
        self.cell(140, 6, safe_text("GUERREROS DE LA MONTAÑA DE YARACUY"), ln=True, align="C")

        self.set_x(38)
        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(70, 70, 70)
        self.cell(140, 4.5, safe_text("RIF: J-507789474 | Centro de Acopio y Distribución - Av. Bolívar, Nirgua, Edo. Yaracuy"), ln=True, align="C")

        self.set_x(38)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(45, 106, 79)
        self.cell(140, 4.5, safe_text("Alianza Productiva El Cafetal & Por Tu Café - Construcción Colectiva para la Vida"), ln=True, align="C")

        self.ln(4)
        # Caja de Título del Comprobante
        self.set_fill_color(241, 245, 249)
        self.set_draw_color(27, 67, 50)
        self.rect(12, 40, 192, 14, "DF")

        self.set_xy(15, 42)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(27, 67, 50)
        self.cell(115, 5, safe_text(self.titulo_documento.upper()), ln=False)

        self.set_font("Helvetica", "B", 10)
        self.set_text_color(155, 34, 38)
        self.cell(71, 5, safe_text(f"N° CONTROL: {self.codigo_documento}"), ln=True, align="R")

        self.set_x(15)
        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(60, 60, 60)
        self.cell(115, 5, safe_text(f"Unidad Emisora: {self.unidad_emisora}"), ln=False)
        self.cell(71, 5, safe_text(f"Emisión: {datetime.now().strftime('%d/%m/%Y %H:%M')}"), ln=True, align="R")

        self.ln(6)

    def footer(self):
        self.set_y(-20)
        self.set_draw_color(200, 200, 200)
        self.line(12, self.get_y(), 204, self.get_y())
        self.ln(2)
        self.set_font("Helvetica", "I", 7.5)
        self.set_text_color(100, 100, 100)
        self.cell(0, 4, safe_text("Documento Oficial generado por el Sistema Administrativo y de Control de Gestión - EPSDC Guerreros de la Montaña de Yaracuy."), ln=True, align="C")
        self.cell(0, 4, safe_text(f"Sujeto a Contraloría Social Comunal | Página {self.page_no()}"), ln=False, align="C")

def generar_pdf_comprobante(
    titulo_documento: str,
    codigo_documento: str,
    unidad_emisora: str,
    datos_generales: dict,
    columnas_tabla: list,
    filas_tabla: list,
    totales: dict = None,
    observaciones: str = "",
    firmas: tuple = ("Elaborado / Responsable Unidad", "Recibido / Conforme", "Vo.Bo. Contraloría / Gerencia")
) -> bytes:
    pdf = ComprobanteEPSDC(titulo_documento, codigo_documento, unidad_emisora)
    pdf.add_page()

    # Bloque de Datos Generales
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_fill_color(27, 67, 50)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(192, 6.5, safe_text("  1. INFORMACIÓN GENERAL DE LA OPERACIÓN"), ln=True, fill=True)
    pdf.ln(1)

    pdf.set_text_color(30, 30, 30)
    items = list(datos_generales.items())
    for i in range(0, len(items), 2):
        k1, v1 = items[i]
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(38, 6, safe_text(f"{k1}:"), border=0)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.cell(58, 6, safe_text(str(v1)), border=0)

        if i + 1 < len(items):
            k2, v2 = items[i + 1]
            pdf.set_font("Helvetica", "B", 8.5)
            pdf.cell(38, 6, safe_text(f"{k2}:"), border=0)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.cell(58, 6, safe_text(str(v2)), border=0)
        pdf.ln(6)

    pdf.ln(4)

    # Tabla de Detalles
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_fill_color(27, 67, 50)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(192, 6.5, safe_text("  2. DETALLE DE ARTÍCULOS / CONCEPTOS"), ln=True, fill=True)

    num_cols = len(columnas_tabla)
    col_widths = [192 / num_cols] * num_cols
    if num_cols == 5:
        col_widths = [26, 76, 26, 32, 32]
    elif num_cols == 4:
        col_widths = [30, 92, 35, 35]

    pdf.set_fill_color(233, 236, 239)
    pdf.set_text_color(27, 67, 50)
    pdf.set_font("Helvetica", "B", 8.5)
    for idx, col in enumerate(columnas_tabla):
        align = "L" if idx <= 1 else "R"
        pdf.cell(col_widths[idx], 7, safe_text(col), border=1, fill=True, align=align)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(20, 20, 20)
    for row in filas_tabla:
        for idx, val in enumerate(row):
            align = "L" if idx <= 1 else "R"
            pdf.cell(col_widths[idx], 6.5, safe_text(str(val)), border=1, align=align)
        pdf.ln()

    # Bloque de Totales
    if totales:
        pdf.ln(3)
        for tk, tv in totales.items():
            pdf.set_x(122)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(248, 249, 250)
            pdf.set_text_color(27, 67, 50)
            pdf.cell(42, 6.5, safe_text(tk), border=1, fill=True, align="L")
            pdf.set_text_color(155, 34, 38)
            pdf.cell(40, 6.5, safe_text(str(tv)), border=1, fill=True, align="R")
            pdf.ln()

    # Observaciones
    if observaciones:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_text_color(27, 67, 50)
        pdf.cell(192, 5, safe_text("Observaciones y Notas de Auditoría:"), ln=True)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(50, 50, 50)
        pdf.multi_cell(192, 5, safe_text(observaciones), border=1)

    # Firmas y Sellos
    pdf.ln(16)
    y_sig = pdf.get_y()
    if y_sig > 235:
        pdf.add_page()
        y_sig = pdf.get_y() + 12

    ancho_firma = 56
    posiciones_x = [14, 80, 146]
    pdf.set_draw_color(60, 60, 60)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(40, 40, 40)

    for i, etiqueta in enumerate(firmas):
        x = posiciones_x[i]
        pdf.line(x, y_sig, x + ancho_firma, y_sig)
        pdf.set_xy(x, y_sig + 1.5)
        pdf.cell(ancho_firma, 4.5, safe_text(etiqueta), align="C")

    return bytes(pdf.output())
