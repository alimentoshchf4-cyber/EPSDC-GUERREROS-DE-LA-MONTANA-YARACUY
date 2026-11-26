
import os
import io
import re
from pathlib import Path
from datetime import datetime, timedelta
import streamlit as st
import pandas as pd

from database import init_db, autenticar_usuario, get_connection, query_df, generar_codigo
from pdf_generator import generar_pdf_comprobante

st.set_page_config(
    page_title="ERP EPSDC Guerreros de la Montaña",
    page_icon="☕",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==============================================================================
# CONFIGURACIÓN DE ACTIVOS Y MARCAS
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent
LOGO_CAFETAL_PATH = BASE_DIR / "assets" / "logo_el_cafetal.png"
LOGO_PORTU_PATH = BASE_DIR / "assets" / "logo_por_tu_cafe.png"


def render_logo(logo_path: Path, caption_fallback: str = ""):
    if logo_path.exists():
        st.image(str(logo_path), use_container_width=True)
    elif os.path.exists(f"assets/{logo_path.name}"):
        st.image(f"assets/{logo_path.name}", use_container_width=True)
    elif caption_fallback:
        st.caption(caption_fallback)


# ==============================================================================
# CAPA TRANSVERSAL DE DATOS, REGLAS DE NEGOCIO, RBAC Y AUDITORÍA
# ==============================================================================
init_db()

TERRITORIOS_YARACUY = [
    "Circuito 01 - Eje Metropolitano",
    "Zona 02 - Eje Productivo Sur",
    "Zona 03 - Eje Centro-Occidente",
    "Zona 04 - Eje Centro-Norte",
    "Zona 05 - Eje Bolívar / Veroes / Monge",
    "Circuito 06 - Eje Costero Morón / Pto. Cabello"
]

MUNICIPIOS_OPERATIVOS = [
    "San Felipe", "Independencia", "Cocorote", "Nirgua", "Yaritagua",
    "Bruzual", "Sucre", "Bastidas", "Trinidad", "Urachiche",
    "Bolívar", "Veroes", "Manuel Monge", "Juan José Mora"
]

PROMOTORES_COMERCIALES = [
    "Carlos Pacheco", "Brayant López", "Salvador Leal",
    "Yofre Muñoz", "Carlos Oropeza", "Venta Directa Sede"
]

ESTADOS_PREVENTA = [
    "Borrador",
    "Registrada / Preventa",
    "Aprobada",
    "Facturada / Despachada",
    "Anulada"
]

TRANSICIONES_PREVENTA = {
    "Borrador": ["Registrada / Preventa", "Aprobada", "Anulada"],
    "Registrada / Preventa": ["Aprobada", "Facturada / Despachada", "Anulada"],
    "Aprobada": ["Facturada / Despachada", "Anulada"],
    "Facturada / Despachada": ["Anulada"],
    "Anulada": []
}


def asegurar_tabla_tasa_bcv():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS configuracion_tasa_bcv (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        tasa_bcv REAL NOT NULL,
        actualizada_en TEXT NOT NULL,
        expira_en TEXT NOT NULL,
        modificada_por TEXT NOT NULL
    )
    """)
    cur.execute("SELECT COUNT(*) FROM configuracion_tasa_bcv WHERE id = 1")
    if cur.fetchone()[0] == 0:
        ahora = datetime.now()
        expira = ahora + timedelta(hours=24)
        cur.execute(
            """
            INSERT INTO configuracion_tasa_bcv (id, tasa_bcv, actualizada_en, expira_en, modificada_por)
            VALUES (1, ?, ?, ?, ?)
            """,
            (46.50, ahora.strftime("%Y-%m-%d %H:%M:%S"), expira.strftime("%Y-%m-%d %H:%M:%S"), "gerencia")
        )
        conn.commit()
    conn.close()


def obtener_estado_tasa_bcv() -> dict:
    asegurar_tabla_tasa_bcv()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT tasa_bcv, actualizada_en, expira_en, modificada_por FROM configuracion_tasa_bcv WHERE id = 1")
    row = cur.fetchone()
    conn.close()

    tasa = float(row["tasa_bcv"])
    actualizada_dt = datetime.strptime(row["actualizada_en"], "%Y-%m-%d %H:%M:%S")
    expira_dt = datetime.strptime(row["expira_en"], "%Y-%m-%d %H:%M:%S")
    ahora = datetime.now()
    vigente = ahora < expira_dt
    restante = max(expira_dt - ahora, timedelta(0))
    horas_restantes = int(restante.total_seconds() // 3600)
    minutos_restantes = int((restante.total_seconds() % 3600) // 60)

    return {
        "tasa_bcv": tasa,
        "actualizada_en": actualizada_dt.strftime("%d/%m/%Y %H:%M:%S"),
        "expira_en": expira_dt.strftime("%d/%m/%Y %H:%M:%S"),
        "modificada_por": row["modificada_por"],
        "vigente": vigente,
        "tiempo_restante": f"{horas_restantes}h {minutos_restantes}m"
    }


def guardar_tasa_bcv_administrador(nueva_tasa: float, usuario_admin: str):
    asegurar_tabla_tasa_bcv()
    ahora = datetime.now()
    expira = ahora + timedelta(hours=24)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE configuracion_tasa_bcv
        SET tasa_bcv = ?, actualizada_en = ?, expira_en = ?, modificada_por = ?
        WHERE id = 1
        """,
        (float(nueva_tasa), ahora.strftime("%Y-%m-%d %H:%M:%S"), expira.strftime("%Y-%m-%d %H:%M:%S"), usuario_admin)
    )
    conn.commit()
    conn.close()


def asegurar_columnas_inventario():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(inventario)")
    cols = {row["name"] for row in cur.fetchall()}
    if "ubicacion_bodega" not in cols:
        cur.execute("ALTER TABLE inventario ADD COLUMN ubicacion_bodega TEXT DEFAULT 'Almacén Principal Nirgua'")
    if "precio_detal_usd" not in cols:
        cur.execute("ALTER TABLE inventario ADD COLUMN precio_detal_usd REAL NOT NULL DEFAULT 0")
    if "precio_mayor_usd" not in cols:
        cur.execute("ALTER TABLE inventario ADD COLUMN precio_mayor_usd REAL NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()


def asegurar_esquema_comercial_extendido():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS clientes_cartera (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_cliente TEXT UNIQUE NOT NULL,
        nombre_comercio TEXT NOT NULL,
        rif_cedula TEXT NOT NULL,
        telefono TEXT NOT NULL,
        direccion TEXT NOT NULL,
        territorio TEXT NOT NULL,
        municipio TEXT NOT NULL,
        geolocalizacion TEXT DEFAULT '',
        segmento TEXT DEFAULT 'Bodega / Abasto',
        vendedor_asignado TEXT NOT NULL,
        estado_cliente TEXT DEFAULT 'Activo',
        limite_credito_usd REAL DEFAULT 0.0,
        dias_credito INTEGER DEFAULT 0,
        saldo_pendiente_usd REAL DEFAULT 0.0,
        fecha_ultima_compra TEXT DEFAULT '',
        observaciones TEXT DEFAULT '',
        creado_en TEXT NOT NULL,
        actualizado_en TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS preventas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_preventa TEXT UNIQUE NOT NULL,
        fecha TEXT NOT NULL,
        cliente_id INTEGER,
        codigo_cliente TEXT NOT NULL,
        cliente_nombre TEXT NOT NULL,
        rif_cliente TEXT NOT NULL,
        vendedor TEXT NOT NULL,
        territorio TEXT NOT NULL,
        municipio TEXT NOT NULL,
        almacen_origen TEXT NOT NULL,
        moneda TEXT DEFAULT 'USD',
        tasa_bcv REAL NOT NULL,
        condicion_pago TEXT NOT NULL,
        escala_precio TEXT NOT NULL,
        sku TEXT NOT NULL,
        producto_nombre TEXT NOT NULL,
        cantidad REAL NOT NULL,
        precio_unitario_usd REAL NOT NULL,
        descuento_pct REAL DEFAULT 0.0,
        impuesto_pct REAL DEFAULT 0.0,
        subtotal_usd REAL NOT NULL,
        descuento_usd REAL NOT NULL,
        impuesto_usd REAL NOT NULL,
        total_usd REAL NOT NULL,
        total_bs REAL NOT NULL,
        stock_reservado REAL DEFAULT 0.0,
        estado_preventa TEXT NOT NULL,
        requiere_aprobacion INTEGER DEFAULT 0,
        aprobado_por TEXT DEFAULT '',
        codigo_venta_vinculado TEXT DEFAULT '',
        observaciones TEXT DEFAULT '',
        creado_por TEXT NOT NULL,
        actualizado_en TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS historial_preventas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_preventa TEXT NOT NULL,
        estado_anterior TEXT NOT NULL,
        estado_nuevo TEXT NOT NULL,
        usuario TEXT NOT NULL,
        rol TEXT NOT NULL,
        motivo TEXT NOT NULL,
        fecha TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bitacora_cartera (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_cliente TEXT NOT NULL,
        cliente_nombre TEXT NOT NULL,
        tipo_evento TEXT NOT NULL,
        resumen TEXT NOT NULL,
        proxima_accion TEXT DEFAULT '',
        vendedor TEXT NOT NULL,
        fecha TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS metas_comerciales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        periodo TEXT NOT NULL,
        meta_diaria_usd REAL NOT NULL,
        meta_mensual_usd REAL NOT NULL,
        dias_habiles_mes INTEGER NOT NULL,
        actualizado_por TEXT NOT NULL,
        fecha TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS auditoria_comercial (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        usuario TEXT NOT NULL,
        rol TEXT NOT NULL,
        modulo TEXT NOT NULL,
        accion TEXT NOT NULL,
        entidad_id TEXT NOT NULL,
        detalle TEXT NOT NULL
    )
    """)

    cur.execute("SELECT COUNT(*) FROM metas_comerciales")
    if cur.fetchone()[0] == 0:
        cur.execute(
            """
            INSERT INTO metas_comerciales
            (periodo, meta_diaria_usd, meta_mensual_usd, dias_habiles_mes, actualizado_por, fecha)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (datetime.now().strftime("%Y-%m"), 500.0, 11000.0, 22, "gerencia", datetime.now().strftime("%Y-%m-%d %H:%M"))
        )

    cur.execute("SELECT COUNT(*) FROM clientes_cartera")
    if cur.fetchone()[0] == 0:
        ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        clientes_semilla = [
            ("CLI-YAR-000001", "Comercializadora El Cafetal San Felipe C.A.", "J-40129876-1", "0414-5550101", "Av. Caracas con Calle 18, San Felipe", "Circuito 01 - Eje Metropolitano", "San Felipe", "10.3368,-68.7425", "Distribuidor / Mayorista", "Carlos Pacheco", "Activo", 1500.0, 15, 0.0, (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"), "Cliente recurrente de alta rotación.", ahora_str, ahora_str),
            ("CLI-YAR-000002", "Abasto y Bodegón La Montaña", "V-16489201-0", "0424-5550102", "Carrera 4 Independencia, Yaracuy", "Circuito 01 - Eje Metropolitano", "Independencia", "10.3412,-68.7610", "Bodega / Abasto", "Brayant López", "Activo", 600.0, 7, 145.0, (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%d"), "Pago puntual en ciclos semanales.", ahora_str, ahora_str),
            ("CLI-YAR-000003", "Panadería y Café Aroa Gourmet", "J-50112233-4", "0412-5550103", "Calle Principal Cocorote, Yaracuy", "Circuito 01 - Eje Metropolitano", "Cocorote", "10.3289,-68.7118", "HORECA / Cafetería", "Salvador Leal", "Moroso", 400.0, 7, 210.0, (datetime.now() - timedelta(days=28)).strftime("%Y-%m-%d"), "Saldo pendiente por gestionar cobranza.", ahora_str, ahora_str)
        ]
        cur.executemany(
            """
            INSERT INTO clientes_cartera
            (codigo_cliente, nombre_comercio, rif_cedula, telefono, direccion, territorio, municipio,
             geolocalizacion, segmento, vendedor_asignado, estado_cliente, limite_credito_usd,
             dias_credito, saldo_pendiente_usd, fecha_ultima_compra, observaciones, creado_en, actualizado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            clientes_semilla
        )

    conn.commit()
    conn.close()


asegurar_tabla_tasa_bcv()
asegurar_columnas_inventario()
asegurar_esquema_comercial_extendido()


def obtener_nivel_acceso_comercial(user: dict) -> str:
    rol = (user.get("rol") or "").upper()
    cargo = (user.get("cargo") or "").lower()
    if rol == "ADMIN":
        return "ADMIN"
    if any(k in cargo for k in ["jefe", "supervisor", "coordinador", "gerente"]):
        return "SUPERVISOR"
    return "VENDEDOR"


def registrar_auditoria_comercial(user: dict, modulo: str, accion: str, entidad_id: str, detalle: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO auditoria_comercial (fecha, usuario, rol, modulo, accion, entidad_id, detalle)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            user.get("username", "sistema"),
            obtener_nivel_acceso_comercial(user),
            modulo,
            accion,
            str(entidad_id),
            detalle
        )
    )
    conn.commit()
    conn.close()


# ==============================================================================
# REGLAS DE NEGOCIO: CLIENTES, PREVENTAS, CARTERA Y KPIs
# ==============================================================================
def normalizar_rif_cedula(raw_rif: str) -> str:
    limpio = re.sub(r"[^A-Za-z0-9]", "", (raw_rif or "").upper())
    if not limpio:
        return ""
    if limpio[0] in {"V", "E", "J", "G", "P"} and len(limpio) > 1:
        pref = limpio[0]
        cuerpo = re.sub(r"\D", "", limpio[1:])
        if pref in {"J", "G"} and len(cuerpo) >= 2:
            return f"{pref}-{cuerpo[:-1]}-{cuerpo[-1]}"
        return f"{pref}-{cuerpo}"
    digitos = re.sub(r"\D", "", limpio)
    return f"V-{digitos}" if digitos else limpio


def normalizar_telefono(raw_tel: str) -> str:
    digitos = re.sub(r"\D", "", raw_tel or "")
    if len(digitos) == 11 and digitos.startswith("0"):
        return f"{digitos[:4]}-{digitos[4:]}"
    if len(digitos) == 10:
        return f"0{digitos[:3]}-{digitos[3:]}"
    return (raw_tel or "").strip()


def normalizar_texto_titulo(raw_txt: str) -> str:
    return re.sub(r"\s+", " ", (raw_txt or "").strip())


def generar_codigo_cliente_unico(territorio: str, rif_normalizado: str = "", modo: str = "Correlativo por Zona") -> str:
    zon_match = re.search(r"(\d+)", territorio or "")
    num_zona = zon_match.group(1).zfill(2) if zon_match else "00"
    prefijo = f"CLI-Z{num_zona}"

    conn = get_connection()
    cur = conn.cursor()

    if modo == "Basado en RIF Normalizado" and rif_normalizado:
        base_code = f"CLI-{re.sub(r'[^A-Z0-9]', '', rif_normalizado)}"
        cur.execute("SELECT COUNT(*) FROM clientes_cartera WHERE codigo_cliente = ?", (base_code,))
        if cur.fetchone()[0] == 0:
            conn.close()
            return base_code
        idx = 2
        while True:
            candidato = f"{base_code}-{idx:02d}"
            cur.execute("SELECT COUNT(*) FROM clientes_cartera WHERE codigo_cliente = ?", (candidato,))
            if cur.fetchone()[0] == 0:
                conn.close()
                return candidato
            idx += 1

    cur.execute("SELECT codigo_cliente FROM clientes_cartera WHERE codigo_cliente LIKE ?", (f"{prefijo}-%",))
    codigos = [r["codigo_cliente"] for r in cur.fetchall()]
    max_seq = 0
    for c in codigos:
        m = re.search(r"-(\d+)$", c)
        if m:
            max_seq = max(max_seq, int(m.group(1)))

    while True:
        max_seq += 1
        candidato = f"{prefijo}-{max_seq:06d}"
        cur.execute("SELECT COUNT(*) FROM clientes_cartera WHERE codigo_cliente = ?", (candidato,))
        if cur.fetchone()[0] == 0:
            conn.close()
            return candidato


def calcular_lineas_preventa(cantidad: float, precio_unitario_usd: float, descuento_pct: float, impuesto_pct: float, tasa_bcv: float) -> dict:
    cant = max(0.0, float(cantidad))
    precio = max(0.0, float(precio_unitario_usd))
    desc_pct = min(100.0, max(0.0, float(descuento_pct)))
    imp_pct = max(0.0, float(impuesto_pct))

    subtotal_bruto = cant * precio
    descuento_usd = subtotal_bruto * (desc_pct / 100.0)
    base_imponible = max(0.0, subtotal_bruto - descuento_usd)
    impuesto_usd = base_imponible * (imp_pct / 100.0)
    total_usd = base_imponible + impuesto_usd
    total_bs = total_usd * float(tasa_bcv)

    return {
        "subtotal_usd": round(subtotal_bruto, 2),
        "descuento_usd": round(descuento_usd, 2),
        "base_imponible_usd": round(base_imponible, 2),
        "impuesto_usd": round(impuesto_usd, 2),
        "total_usd": round(total_usd, 2),
        "total_bs": round(total_bs, 2)
    }


def evaluar_margen_minimo(precio_unitario_usd: float, costo_unitario_usd: float, descuento_pct: float, margen_min_pct: float = 15.0) -> dict:
    costo = max(0.0, float(costo_unitario_usd))
    precio_efectivo = float(precio_unitario_usd) * (1.0 - float(descuento_pct) / 100.0)
    if precio_efectivo <= 0:
        return {"margen_pct": -100.0, "cumple_margen": False, "precio_efectivo_usd": 0.0}
    margen_actual = ((precio_efectivo - costo) / precio_efectivo) * 100.0
    return {
        "margen_pct": round(margen_actual, 2),
        "cumple_margen": margen_actual >= float(margen_min_pct),
        "precio_efectivo_usd": round(precio_efectivo, 2)
    }


def obtener_stock_reservado_sku(sku: str, excluir_preventa_id: int | None = None) -> float:
    conn = get_connection()
    cur = conn.cursor()
    if excluir_preventa_id is not None:
        cur.execute(
            """
            SELECT COALESCE(SUM(cantidad), 0) AS reservado
            FROM preventas
            WHERE sku = ?
              AND estado_preventa IN ('Registrada / Preventa', 'Aprobada')
              AND id != ?
            """,
            (sku, excluir_preventa_id)
        )
    else:
        cur.execute(
            """
            SELECT COALESCE(SUM(cantidad), 0) AS reservado
            FROM preventas
            WHERE sku = ?
              AND estado_preventa IN ('Registrada / Preventa', 'Aprobada')
            """,
            (sku,)
        )
    val = cur.fetchone()["reservado"]
    conn.close()
    return float(val or 0.0)


def calcular_kpis_comerciales(df_ventas: pd.DataFrame, df_preventas: pd.DataFrame, df_clientes: pd.DataFrame, df_inv: pd.DataFrame, meta_diaria_usd: float, meta_mensual_usd: float) -> dict:
    hoy_str = datetime.now().strftime("%Y-%m-%d")
    mes_str = datetime.now().strftime("%Y-%m")

    ventas_validas = df_ventas.copy() if not df_ventas.empty else pd.DataFrame()
    if not ventas_validas.empty and "estado_despacho" in ventas_validas.columns:
        ventas_validas_netas = ventas_validas[ventas_validas["estado_despacho"] != "Anulada"]
    else:
        ventas_validas_netas = ventas_validas

    total_ventas_usd = float(ventas_validas_netas["total_usd"].sum()) if not ventas_validas_netas.empty else 0.0
    num_ordenes = int(len(ventas_validas_netas)) if not ventas_validas_netas.empty else 0
    ticket_promedio_usd = (total_ventas_usd / num_ordenes) if num_ordenes > 0 else 0.0
    unidades_vendidas = float(ventas_validas_netas["cantidad"].sum()) if not ventas_validas_netas.empty else 0.0

    venta_hoy_usd = 0.0
    venta_mes_usd = 0.0
    if not ventas_validas_netas.empty and "fecha" in ventas_validas_netas.columns:
        fechas_s = ventas_validas_netas["fecha"].astype(str)
        venta_hoy_usd = float(ventas_validas_netas[fechas_s.str.startswith(hoy_str)]["total_usd"].sum())
        venta_mes_usd = float(ventas_validas_netas[fechas_s.str.startswith(mes_str)]["total_usd"].sum())

    avance_diario_pct = (venta_hoy_usd / meta_diaria_usd * 100.0) if meta_diaria_usd > 0 else 0.0
    brecha_diaria_usd = max(0.0, meta_diaria_usd - venta_hoy_usd)
    avance_mensual_pct = (venta_mes_usd / meta_mensual_usd * 100.0) if meta_mensual_usd > 0 else 0.0

    total_prev = int(len(df_preventas)) if not df_preventas.empty else 0
    prev_cerradas = int(len(df_preventas[df_preventas["estado_preventa"] == "Facturada / Despachada"])) if not df_preventas.empty else 0
    prev_anuladas = int(len(df_preventas[df_preventas["estado_preventa"] == "Anulada"])) if not df_preventas.empty else 0
    tasa_conversion_pct = (prev_cerradas / total_prev * 100.0) if total_prev > 0 else 0.0

    total_clientes = int(len(df_clientes)) if not df_clientes.empty else 0
    clientes_activos = int(len(df_clientes[df_clientes["estado_cliente"] == "Activo"])) if not df_clientes.empty else 0
    clientes_con_compra = set(ventas_validas_netas["cliente_comercio"].dropna().unique()) if not ventas_validas_netas.empty else set()
    clientes_activados_cnt = sum(1 for c in (df_clientes["nombre_comercio"].tolist() if not df_clientes.empty else []) if c in clientes_con_compra)
    cobertura_cartera_pct = (clientes_activados_cnt / total_clientes * 100.0) if total_clientes > 0 else 0.0

    saldo_total_cartera = float(df_clientes["saldo_pendiente_usd"].sum()) if not df_clientes.empty else 0.0
    saldo_moroso = float(df_clientes[df_clientes["estado_cliente"] == "Moroso"]["saldo_pendiente_usd"].sum()) if not df_clientes.empty else 0.0
    morosidad_pct = (saldo_moroso / saldo_total_cartera * 100.0) if saldo_total_cartera > 0 else 0.0

    margen_bruto_usd = 0.0
    if not ventas_validas_netas.empty and not df_inv.empty:
        costos_map = dict(zip(df_inv["sku"], df_inv["costo_unitario_usd"]))
        for _, r in ventas_validas_netas.iterrows():
            c_u = float(costos_map.get(r["sku"], 0.0))
            margen_bruto_usd += (float(r["precio_unitario_usd"]) - c_u) * float(r["cantidad"])

    return {
        "total_ventas_usd": total_ventas_usd,
        "num_ordenes": num_ordenes,
        "ticket_promedio_usd": ticket_promedio_usd,
        "unidades_vendidas": unidades_vendidas,
        "venta_hoy_usd": venta_hoy_usd,
        "avance_diario_pct": avance_diario_pct,
        "brecha_diaria_usd": brecha_diaria_usd,
        "venta_mes_usd": venta_mes_usd,
        "avance_mensual_pct": avance_mensual_pct,
        "total_preventas": total_prev,
        "preventas_cerradas": prev_cerradas,
        "preventas_anuladas": prev_anuladas,
        "tasa_conversion_pct": tasa_conversion_pct,
        "total_clientes": total_clientes,
        "clientes_activos": clientes_activos,
        "clientes_activados_cnt": clientes_activados_cnt,
        "cobertura_cartera_pct": cobertura_cartera_pct,
        "saldo_total_cartera": saldo_total_cartera,
        "saldo_moroso": saldo_moroso,
        "morosidad_pct": morosidad_pct,
        "margen_bruto_usd": margen_bruto_usd
    }


# ==============================================================================
# CAPA FINANCIERA INTEGRADA (DECIMAL, TRANSACCIONES ATÓMICAS Y SERVICIOS)
# ==============================================================================
from dataclasses import dataclass, field
from datetime import date as dt_date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import List, Optional

CENTAVOS = Decimal("0.01")


def redondear_moneda(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def a_decimal(valor, por_defecto: str = "0.00") -> Decimal:
    if isinstance(valor, Decimal):
        return valor
    if valor is None or str(valor).strip() == "":
        return Decimal(por_defecto)
    try:
        return Decimal(str(valor).replace(",", "").strip())
    except InvalidOperation:
        return Decimal(por_defecto)


@dataclass
class LineaFactura:
    descripcion: str
    cantidad: Decimal
    precio_unitario: Decimal
    costo_unitario: Decimal
    impuesto_tasa: Decimal = Decimal("0.00")

    @property
    def subtotal(self) -> Decimal:
        return redondear_moneda(self.cantidad * self.precio_unitario)

    @property
    def monto_impuesto(self) -> Decimal:
        return redondear_moneda(self.subtotal * self.impuesto_tasa)

    @property
    def total_linea(self) -> Decimal:
        return redondear_moneda(self.subtotal + self.monto_impuesto)

    @property
    def costo_total_linea(self) -> Decimal:
        return redondear_moneda(self.cantidad * self.costo_unitario)


@dataclass
class Factura:
    numero: str
    cliente_id: str
    fecha_emision: dt_date
    fecha_vencimiento: dt_date
    moneda: str = "VES"
    tasa_cambio: Decimal = Decimal("1.00")
    lineas: List[LineaFactura] = field(default_factory=list)
    monto_cobrado: Decimal = Decimal("0.00")
    estado: str = "EMITIDA"  # EMITIDA | PARCIAL | PAGADA | ANULADA

    @property
    def subtotal(self) -> Decimal:
        return redondear_moneda(sum((l.subtotal for l in self.lineas), Decimal("0.00")))

    @property
    def impuestos(self) -> Decimal:
        return redondear_moneda(sum((l.monto_impuesto for l in self.lineas), Decimal("0.00")))

    @property
    def total(self) -> Decimal:
        return redondear_moneda(self.subtotal + self.impuestos)

    @property
    def saldo_pendiente(self) -> Decimal:
        if self.estado == "ANULADA":
            return Decimal("0.00")
        return max(Decimal("0.00"), redondear_moneda(self.total - self.monto_cobrado))


class FacturacionService:
    def registrar_cobro(self, factura: Factura, monto: Decimal) -> Factura:
        monto_dec = redondear_moneda(a_decimal(monto))
        if factura.estado == "ANULADA":
            raise ValueError("No se puede cobrar una factura anulada.")
        if monto_dec <= Decimal("0.00"):
            raise ValueError("El monto del cobro debe ser mayor que cero.")
        if monto_dec > factura.saldo_pendiente:
            raise ValueError("El cobro supera el saldo pendiente de la factura.")

        factura.monto_cobrado = redondear_moneda(factura.monto_cobrado + monto_dec)
        factura.estado = "PAGADA" if factura.saldo_pendiente == Decimal("0.00") else "PARCIAL"
        return factura


class AnalisisFinancieroService:
    def resumen_operativo(
        self,
        facturas: List[Factura],
        gastos_operativos: Decimal,
        presupuesto_asignado: Decimal,
    ) -> dict:
        gastos_dec = redondear_moneda(a_decimal(gastos_operativos))
        pres_dec = redondear_moneda(a_decimal(presupuesto_asignado))
        facturas_validas = [f for f in facturas if f.estado != "ANULADA"]
        ventas_netas = redondear_moneda(sum((f.subtotal for f in facturas_validas), Decimal("0.00")))
        costo_ventas = redondear_moneda(
            sum(
                (l.costo_total_linea for f in facturas_validas for l in f.lineas),
                Decimal("0.00"),
            )
        )
        cxc_pendiente = redondear_moneda(sum((f.saldo_pendiente for f in facturas_validas), Decimal("0.00")))
        utilidad_bruta = redondear_moneda(ventas_netas - costo_ventas)
        utilidad_operativa = redondear_moneda(utilidad_bruta - gastos_dec)
        ejecucion_presupuestaria = (
            redondear_moneda((gastos_dec / pres_dec) * Decimal("100"))
            if pres_dec > Decimal("0.00")
            else Decimal("0.00")
        )

        return {
            "ventas_netas": ventas_netas,
            "costo_ventas": costo_ventas,
            "utilidad_bruta": utilidad_bruta,
            "gastos_operativos": gastos_dec,
            "utilidad_operativa": utilidad_operativa,
            "cxc_pendiente": cxc_pendiente,
            "ejecucion_presupuestaria_pct": ejecucion_presupuestaria,
        }


def asegurar_esquema_finanzas_robusto():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cuentas_tesoreria (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_cuenta TEXT UNIQUE NOT NULL,
        nombre_cuenta TEXT NOT NULL,
        tipo_cuenta TEXT NOT NULL,
        moneda TEXT NOT NULL,
        saldo_actual REAL NOT NULL DEFAULT 0.0,
        estado TEXT NOT NULL DEFAULT 'Activa'
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS facturas_emitidas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_factura TEXT UNIQUE NOT NULL,
        doc_origen TEXT DEFAULT '',
        cliente_codigo TEXT NOT NULL,
        cliente_nombre TEXT NOT NULL,
        rif_cliente TEXT NOT NULL,
        fecha_emision TEXT NOT NULL,
        fecha_vencimiento TEXT NOT NULL,
        moneda TEXT NOT NULL DEFAULT 'USD',
        tasa_cambio REAL NOT NULL,
        sku TEXT NOT NULL,
        descripcion TEXT NOT NULL,
        cantidad REAL NOT NULL,
        precio_unitario_usd REAL NOT NULL,
        costo_unitario_usd REAL NOT NULL,
        descuento_pct REAL NOT NULL DEFAULT 0.0,
        impuesto_pct REAL NOT NULL DEFAULT 0.0,
        subtotal_usd REAL NOT NULL,
        impuesto_usd REAL NOT NULL,
        total_usd REAL NOT NULL,
        total_bs REAL NOT NULL,
        monto_cobrado_usd REAL NOT NULL DEFAULT 0.0,
        saldo_pendiente_usd REAL NOT NULL DEFAULT 0.0,
        estado_documento TEXT NOT NULL DEFAULT 'EMITIDA',
        motivo_estado TEXT DEFAULT '',
        responsable TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS factura_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_factura TEXT NOT NULL,
        sku TEXT NOT NULL,
        descripcion TEXT NOT NULL,
        cantidad REAL NOT NULL,
        precio_unitario_usd REAL NOT NULL,
        costo_unitario_usd REAL NOT NULL,
        descuento_pct REAL NOT NULL DEFAULT 0.0,
        impuesto_pct REAL NOT NULL DEFAULT 0.0,
        subtotal_usd REAL NOT NULL,
        impuesto_usd REAL NOT NULL,
        total_usd REAL NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS movimientos_cxc (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_factura TEXT NOT NULL,
        cliente_codigo TEXT NOT NULL,
        cliente_nombre TEXT NOT NULL,
        fecha_movimiento TEXT NOT NULL,
        tipo_movimiento TEXT NOT NULL,
        monto_usd REAL NOT NULL,
        monto_bs REAL NOT NULL,
        referencia TEXT DEFAULT '',
        responsable TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS compromisos_cxp (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_cxp TEXT UNIQUE NOT NULL,
        doc_compra TEXT DEFAULT '',
        proveedor TEXT NOT NULL,
        rif_proveedor TEXT NOT NULL,
        fecha_emision TEXT NOT NULL,
        fecha_vencimiento TEXT NOT NULL,
        prioridad TEXT NOT NULL DEFAULT 'Media',
        monto_total_usd REAL NOT NULL,
        monto_pagado_usd REAL NOT NULL DEFAULT 0.0,
        saldo_pendiente_usd REAL NOT NULL,
        descuento_pronto_pago_pct REAL NOT NULL DEFAULT 0.0,
        estado_cxp TEXT NOT NULL DEFAULT 'PENDIENTE',
        responsable TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS gastos_costos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_registro TEXT UNIQUE NOT NULL,
        fecha TEXT NOT NULL,
        clasificacion TEXT NOT NULL,
        naturaleza TEXT NOT NULL,
        centro_costo TEXT NOT NULL,
        rubro_o_sku TEXT NOT NULL,
        concepto TEXT NOT NULL,
        monto_usd REAL NOT NULL,
        tasa_bcv REAL NOT NULL,
        monto_bs REAL NOT NULL,
        estado_aprobacion TEXT NOT NULL DEFAULT 'Aprobado',
        responsable TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS presupuestos_partidas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        periodo TEXT NOT NULL,
        unidad_o_centro TEXT NOT NULL,
        rubro TEXT NOT NULL,
        monto_presupuestado_usd REAL NOT NULL,
        actualizado_por TEXT NOT NULL,
        fecha TEXT NOT NULL
    )
    """)

    cur.execute("SELECT COUNT(*) FROM cuentas_tesoreria")
    if cur.fetchone()[0] == 0:
        cuentas_ini = [
            ("CTA-CAJA-USD", "Caja Efectivo Divisas Sede", "Caja", "USD", 0.0, "Activa"),
            ("CTA-CAJA-VES", "Caja Chica Bolívares", "Caja", "VES", 0.0, "Activa"),
            ("CTA-BCO-VES", "Cuenta Bancaria Principal (BCV / Transferencia / Pago Móvil)", "Banco", "VES", 0.0, "Activa"),
            ("CTA-DIG-USD", "Billetera / Canal Digital Divisas", "Digital", "USD", 0.0, "Activa")
        ]
        cur.executemany(
            "INSERT INTO cuentas_tesoreria (codigo_cuenta, nombre_cuenta, tipo_cuenta, moneda, saldo_actual, estado) VALUES (?, ?, ?, ?, ?, ?)",
            cuentas_ini
        )

    cur.execute("SELECT COUNT(*) FROM presupuestos_partidas")
    if cur.fetchone()[0] == 0:
        periodo_act = datetime.now().strftime("%Y-%m")
        f_act = datetime.now().strftime("%Y-%m-%d %H:%M")
        partidas_ini = [
            (periodo_act, "Operaciones y Logística", "Logística, Fletes y Transporte", 1800.0, "gerencia", f_act),
            (periodo_act, "Administración y Gestión", "Servicios, Nómina y Operación", 1500.0, "gerencia", f_act),
            (periodo_act, "Compras y Acopio", "Acopio y Materia Prima", 4500.0, "gerencia", f_act),
            (periodo_act, "Comercialización y Ventas", "Promoción y Despacho", 900.0, "gerencia", f_act)
        ]
        cur.executemany(
            "INSERT INTO presupuestos_partidas (periodo, unidad_o_centro, rubro, monto_presupuestado_usd, actualizado_por, fecha) VALUES (?, ?, ?, ?, ?, ?)",
            partidas_ini
        )

    conn.commit()
    conn.close()


asegurar_esquema_finanzas_robusto()


def emitir_factura_atomica(
    doc_origen: str,
    cliente_codigo: str,
    cliente_nombre: str,
    rif_cliente: str,
    fecha_emision_str: str,
    fecha_vencimiento_str: str,
    moneda: str,
    tasa_bcv: float,
    sku: str,
    descripcion: str,
    cantidad,
    precio_unitario_usd,
    costo_unitario_usd,
    descuento_pct,
    impuesto_pct,
    condicion_pago: str,
    responsable: str,
    user_ctx: dict
) -> dict:
    cant_d = a_decimal(cantidad)
    precio_d = a_decimal(precio_unitario_usd)
    costo_d = a_decimal(costo_unitario_usd)
    desc_d = a_decimal(descuento_pct)
    imp_d = a_decimal(impuesto_pct)
    tasa_d = a_decimal(tasa_bcv, "1.00")

    if cant_d <= Decimal("0.00") or precio_d <= Decimal("0.00"):
        raise ValueError("Cantidad y precio unitario deben ser mayores a cero.")

    subtotal_bruto = redondear_moneda(cant_d * precio_d)
    descuento_usd = redondear_moneda(subtotal_bruto * (desc_d / Decimal("100")))
    base_imponible = max(Decimal("0.00"), redondear_moneda(subtotal_bruto - descuento_usd))
    impuesto_usd = redondear_moneda(base_imponible * (imp_d / Decimal("100")))
    total_usd = redondear_moneda(base_imponible + impuesto_usd)
    total_bs = redondear_moneda(total_usd * tasa_d)

    es_contado = (condicion_pago == "Contado")
    cobrado_ini = total_usd if es_contado else Decimal("0.00")
    saldo_ini = Decimal("0.00") if es_contado else total_usd
    estado_doc = "PAGADA" if es_contado else "EMITIDA"

    conn = get_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        num_fac = generar_codigo("FAC", "facturas_emitidas")

        cur.execute(
            """
            INSERT INTO facturas_emitidas
            (numero_factura, doc_origen, cliente_codigo, cliente_nombre, rif_cliente,
             fecha_emision, fecha_vencimiento, moneda, tasa_cambio, sku, descripcion,
             cantidad, precio_unitario_usd, costo_unitario_usd, descuento_pct, impuesto_pct,
             subtotal_usd, impuesto_usd, total_usd, total_bs, monto_cobrado_usd,
             saldo_pendiente_usd, estado_documento, motivo_estado, responsable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
            """,
            (
                num_fac, doc_origen.strip(), cliente_codigo.strip(), cliente_nombre.strip(),
                rif_cliente.strip(), fecha_emision_str, fecha_vencimiento_str, moneda,
                float(tasa_d), sku, descripcion, float(cant_d), float(precio_d), float(costo_d),
                float(desc_d), float(imp_d), float(base_imponible), float(impuesto_usd),
                float(total_usd), float(total_bs), float(cobrado_ini), float(saldo_ini),
                estado_doc, responsable
            )
        )

        if saldo_ini > Decimal("0.00"):
            cur.execute(
                """
                INSERT INTO movimientos_cxc
                (numero_factura, cliente_codigo, cliente_nombre, fecha_movimiento,
                 tipo_movimiento, monto_usd, monto_bs, referencia, responsable)
                VALUES (?, ?, ?, ?, 'CARGO_FACTURA', ?, ?, ?, ?)
                """,
                (
                    num_fac, cliente_codigo.strip(), cliente_nombre.strip(),
                    fecha_emision_str, float(saldo_ini), float(redondear_moneda(saldo_ini * tasa_d)),
                    doc_origen.strip() or num_fac, responsable
                )
            )
            cur.execute(
                """
                UPDATE clientes_cartera
                SET saldo_pendiente_usd = ROUND(saldo_pendiente_usd + ?, 2),
                    fecha_ultima_compra = ?
                WHERE codigo_cliente = ?
                """,
                (float(saldo_ini), fecha_emision_str[:10], cliente_codigo.strip())
            )
        elif es_contado:
            cod_rec = generar_codigo("REC", "tesoreria")
            cur.execute(
                """
                INSERT INTO tesoreria
                (codigo_recibo, fecha, tipo_operacion, concepto, doc_asociado, tercero,
                 metodo_pago, referencia, monto_usd, tasa_bcv, monto_bs, responsable)
                VALUES (?, ?, 'Ingreso', ?, ?, ?, 'Contado / Facturación', ?, ?, ?, ?, ?)
                """,
                (
                    cod_rec, fecha_emision_str, f"Cobro contado factura {num_fac} ({descripcion})",
                    num_fac, cliente_nombre.strip(), num_fac, float(total_usd),
                    float(tasa_d), float(total_bs), responsable
                )
            )
            cur.execute(
                "UPDATE cuentas_tesoreria SET saldo_actual = ROUND(saldo_actual + ?, 2) WHERE codigo_cuenta = 'CTA-CAJA-USD'",
                (float(total_usd),)
            )

        conn.commit()
        registrar_auditoria_comercial(user_ctx, "Finanzas", "EMITIR_FACTURA", num_fac, f"Total ${total_usd:,.2f} | Estado {estado_doc}")
        return {"numero_factura": num_fac, "total_usd": total_usd, "total_bs": total_bs, "estado": estado_doc}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def emitir_factura_multisku_atomica(
    doc_origen: str,
    cliente_codigo: str,
    cliente_nombre: str,
    rif_cliente: str,
    fecha_emision_str: str,
    fecha_vencimiento_str: str,
    moneda: str,
    tasa_bcv: float,
    items: list,
    condicion_pago: str,
    responsable: str,
    user_ctx: dict
) -> dict:
    if not items:
        raise ValueError("Debe agregar al menos un SKU o producto a la factura.")

    tasa_d = a_decimal(tasa_bcv, "1.00")
    lineas_calc = []
    sub_tot = Decimal("0.00")
    imp_tot = Decimal("0.00")
    tot_usd = Decimal("0.00")

    for it in items:
        cant_d = a_decimal(it["cantidad"])
        precio_d = a_decimal(it["precio_unitario_usd"])
        costo_d = a_decimal(it.get("costo_unitario_usd", "0.00"))
        desc_d = a_decimal(it.get("descuento_pct", "0.00"))
        imp_d = a_decimal(it.get("impuesto_pct", "0.00"))

        if cant_d <= Decimal("0.00") or precio_d <= Decimal("0.00"):
            raise ValueError(f"Cantidad y precio unitario deben ser mayores a cero en SKU {it.get('sku', '')}.")

        subtotal_bruto = redondear_moneda(cant_d * precio_d)
        descuento_usd = redondear_moneda(subtotal_bruto * (desc_d / Decimal("100")))
        base_imp = max(Decimal("0.00"), redondear_moneda(subtotal_bruto - descuento_usd))
        imp_linea = redondear_moneda(base_imp * (imp_d / Decimal("100")))
        tot_linea = redondear_moneda(base_imp + imp_linea)

        sub_tot = redondear_moneda(sub_tot + base_imp)
        imp_tot = redondear_moneda(imp_tot + imp_linea)
        tot_usd = redondear_moneda(tot_usd + tot_linea)

        lineas_calc.append({
            "sku": str(it["sku"]).strip(),
            "descripcion": str(it["descripcion"]).strip(),
            "cantidad": cant_d,
            "precio_unitario_usd": precio_d,
            "costo_unitario_usd": costo_d,
            "descuento_pct": desc_d,
            "impuesto_pct": imp_d,
            "subtotal_usd": base_imp,
            "impuesto_usd": imp_linea,
            "total_usd": tot_linea,
        })

    total_bs = redondear_moneda(tot_usd * tasa_d)
    es_contado = (condicion_pago == "Contado")
    cobrado_ini = tot_usd if es_contado else Decimal("0.00")
    saldo_ini = Decimal("0.00") if es_contado else tot_usd
    estado_doc = "PAGADA" if es_contado else "EMITIDA"

    if len(lineas_calc) == 1:
        sku_resumen = lineas_calc[0]["sku"]
        desc_resumen = lineas_calc[0]["descripcion"]
        cant_resumen = float(lineas_calc[0]["cantidad"])
        precio_resumen = float(lineas_calc[0]["precio_unitario_usd"])
        costo_resumen = float(lineas_calc[0]["costo_unitario_usd"])
        desc_pct_resumen = float(lineas_calc[0]["descuento_pct"])
        imp_pct_resumen = float(lineas_calc[0]["impuesto_pct"])
    else:
        sku_resumen = "MULTI-SKU"
        desc_resumen = f"Factura multi-SKU ({len(lineas_calc)} ítems): " + ", ".join(
            f"{l['sku']} x{l['cantidad']:g}" for l in lineas_calc[:4]
        ) + ("..." if len(lineas_calc) > 4 else "")
        cant_resumen = float(sum((l["cantidad"] for l in lineas_calc), Decimal("0.00")))
        precio_resumen = float(redondear_moneda(sub_tot / a_decimal(cant_resumen, "1.00"))) if cant_resumen > 0 else 0.0
        costo_tot_all = sum((l["cantidad"] * l["costo_unitario_usd"] for l in lineas_calc), Decimal("0.00"))
        costo_resumen = float(redondear_moneda(costo_tot_all / a_decimal(cant_resumen, "1.00"))) if cant_resumen > 0 else 0.0
        desc_pct_resumen = 0.0
        imp_pct_resumen = float(redondear_moneda((imp_tot / sub_tot) * Decimal("100"))) if sub_tot > Decimal("0.00") else 0.0

    conn = get_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        num_fac = generar_codigo("FAC", "facturas_emitidas")

        cur.execute(
            """
            INSERT INTO facturas_emitidas
            (numero_factura, doc_origen, cliente_codigo, cliente_nombre, rif_cliente,
             fecha_emision, fecha_vencimiento, moneda, tasa_cambio, sku, descripcion,
             cantidad, precio_unitario_usd, costo_unitario_usd, descuento_pct, impuesto_pct,
             subtotal_usd, impuesto_usd, total_usd, total_bs, monto_cobrado_usd,
             saldo_pendiente_usd, estado_documento, motivo_estado, responsable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
            """,
            (
                num_fac, doc_origen.strip(), cliente_codigo.strip(), cliente_nombre.strip(),
                rif_cliente.strip(), fecha_emision_str, fecha_vencimiento_str, moneda,
                float(tasa_d), sku_resumen, desc_resumen[:240], cant_resumen, precio_resumen,
                costo_resumen, desc_pct_resumen, imp_pct_resumen, float(sub_tot), float(imp_tot),
                float(tot_usd), float(total_bs), float(cobrado_ini), float(saldo_ini),
                estado_doc, responsable
            )
        )

        for l in lineas_calc:
            cur.execute(
                """
                INSERT INTO factura_items
                (numero_factura, sku, descripcion, cantidad, precio_unitario_usd,
                 costo_unitario_usd, descuento_pct, impuesto_pct, subtotal_usd,
                 impuesto_usd, total_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    num_fac, l["sku"], l["descripcion"], float(l["cantidad"]),
                    float(l["precio_unitario_usd"]), float(l["costo_unitario_usd"]),
                    float(l["descuento_pct"]), float(l["impuesto_pct"]),
                    float(l["subtotal_usd"]), float(l["impuesto_usd"]), float(l["total_usd"])
                )
            )

        if saldo_ini > Decimal("0.00"):
            cur.execute(
                """
                INSERT INTO movimientos_cxc
                (numero_factura, cliente_codigo, cliente_nombre, fecha_movimiento,
                 tipo_movimiento, monto_usd, monto_bs, referencia, responsable)
                VALUES (?, ?, ?, ?, 'CARGO_FACTURA', ?, ?, ?, ?)
                """,
                (
                    num_fac, cliente_codigo.strip(), cliente_nombre.strip(),
                    fecha_emision_str, float(saldo_ini), float(redondear_moneda(saldo_ini * tasa_d)),
                    doc_origen.strip() or num_fac, responsable
                )
            )
            cur.execute(
                """
                UPDATE clientes_cartera
                SET saldo_pendiente_usd = ROUND(saldo_pendiente_usd + ?, 2),
                    fecha_ultima_compra = ?
                WHERE codigo_cliente = ?
                """,
                (float(saldo_ini), fecha_emision_str[:10], cliente_codigo.strip())
            )
        elif es_contado:
            cod_rec = generar_codigo("REC", "tesoreria")
            cur.execute(
                """
                INSERT INTO tesoreria
                (codigo_recibo, fecha, tipo_operacion, concepto, doc_asociado, tercero,
                 metodo_pago, referencia, monto_usd, tasa_bcv, monto_bs, responsable)
                VALUES (?, ?, 'Ingreso', ?, ?, ?, 'Contado / Facturación', ?, ?, ?, ?, ?)
                """,
                (
                    cod_rec, fecha_emision_str, f"Cobro contado factura {num_fac} ({len(lineas_calc)} SKUs)",
                    num_fac, cliente_nombre.strip(), num_fac, float(tot_usd),
                    float(tasa_d), float(total_bs), responsable
                )
            )
            cur.execute(
                "UPDATE cuentas_tesoreria SET saldo_actual = ROUND(saldo_actual + ?, 2) WHERE codigo_cuenta = 'CTA-CAJA-USD'",
                (float(tot_usd),)
            )

        conn.commit()
        registrar_auditoria_comercial(
            user_ctx, "Finanzas", "EMITIR_FACTURA_MULTISKU", num_fac,
            f"{len(lineas_calc)} SKUs | Subtotal ${sub_tot:,.2f} | Imp ${imp_tot:,.2f} | Total ${tot_usd:,.2f} | {estado_doc}"
        )
        return {
            "numero_factura": num_fac,
            "subtotal_usd": sub_tot,
            "impuesto_usd": imp_tot,
            "total_usd": tot_usd,
            "total_bs": total_bs,
            "estado": estado_doc,
            "lineas": lineas_calc
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def registrar_cobro_cxc_atomico(
    numero_factura: str,
    monto_cobro,
    retencion_usd,
    codigo_cuenta: str,
    metodo_pago: str,
    referencia: str,
    tasa_bcv: float,
    responsable: str,
    user_ctx: dict
) -> dict:
    cobro_d = redondear_moneda(a_decimal(monto_cobro))
    ret_d = redondear_moneda(a_decimal(retencion_usd))
    tasa_d = a_decimal(tasa_bcv, "1.00")
    total_aplicado = redondear_moneda(cobro_d + ret_d)

    if total_aplicado <= Decimal("0.00"):
        raise ValueError("El monto del cobro o retención debe ser mayor a cero.")

    conn = get_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur.execute("SELECT * FROM facturas_emitidas WHERE numero_factura = ?", (numero_factura,))
        fac = cur.fetchone()
        if not fac:
            raise ValueError(f"No existe la factura {numero_factura}.")
        if fac["estado_documento"] == "ANULADA":
            raise ValueError("No se puede registrar cobro sobre una factura ANULADA.")

        saldo_act = redondear_moneda(a_decimal(fac["saldo_pendiente_usd"]))
        if total_aplicado > saldo_act:
            raise ValueError(f"El monto aplicado (${total_aplicado:,.2f}) supera el saldo pendiente (${saldo_act:,.2f}).")

        nuevo_cobrado = redondear_moneda(a_decimal(fac["monto_cobrado_usd"]) + total_aplicado)
        nuevo_saldo = max(Decimal("0.00"), redondear_moneda(saldo_act - total_aplicado))
        nuevo_estado = "PAGADA" if nuevo_saldo == Decimal("0.00") else "PARCIAL"
        fecha_now = datetime.now().strftime("%Y-%m-%d %H:%M")

        cur.execute(
            """
            UPDATE facturas_emitidas
            SET monto_cobrado_usd = ?, saldo_pendiente_usd = ?, estado_documento = ?
            WHERE numero_factura = ?
            """,
            (float(nuevo_cobrado), float(nuevo_saldo), nuevo_estado, numero_factura)
        )

        cur.execute(
            """
            INSERT INTO movimientos_cxc
            (numero_factura, cliente_codigo, cliente_nombre, fecha_movimiento,
             tipo_movimiento, monto_usd, monto_bs, referencia, responsable)
            VALUES (?, ?, ?, ?, 'ABONO_COBRANZA', ?, ?, ?, ?)
            """,
            (
                numero_factura, fac["cliente_codigo"], fac["cliente_nombre"], fecha_now,
                float(total_aplicado), float(redondear_moneda(total_aplicado * tasa_d)),
                referencia.strip() or numero_factura, responsable
            )
        )

        cur.execute(
            """
            UPDATE clientes_cartera
            SET saldo_pendiente_usd = MAX(0.0, ROUND(saldo_pendiente_usd - ?, 2)),
                estado_cliente = CASE WHEN MAX(0.0, ROUND(saldo_pendiente_usd - ?, 2)) = 0 AND estado_cliente = 'Moroso' THEN 'Activo' ELSE estado_cliente END
            WHERE codigo_cliente = ?
            """,
            (float(total_aplicado), float(total_aplicado), fac["cliente_codigo"])
        )

        cod_rec = generar_codigo("REC", "tesoreria")
        monto_bs_cobro = redondear_moneda(cobro_d * tasa_d)
        cur.execute(
            """
            INSERT INTO tesoreria
            (codigo_recibo, fecha, tipo_operacion, concepto, doc_asociado, tercero,
             metodo_pago, referencia, monto_usd, tasa_bcv, monto_bs, responsable)
            VALUES (?, ?, 'Ingreso', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cod_rec, fecha_now, f"Cobro CxC factura {numero_factura} (Retención: ${ret_d:,.2f})",
                numero_factura, fac["cliente_nombre"], metodo_pago, referencia.strip() or cod_rec,
                float(cobro_d), float(tasa_d), float(monto_bs_cobro), responsable
            )
        )
        cur.execute(
            "UPDATE cuentas_tesoreria SET saldo_actual = ROUND(saldo_actual + ?, 2) WHERE codigo_cuenta = ?",
            (float(cobro_d), codigo_cuenta)
        )

        conn.commit()
        registrar_auditoria_comercial(user_ctx, "CxC", "COBRO_FACTURA", numero_factura, f"Cobro ${cobro_d:,.2f} + Ret ${ret_d:,.2f} | Saldo ${nuevo_saldo:,.2f}")
        return {"codigo_recibo": cod_rec, "nuevo_saldo": nuevo_saldo, "estado": nuevo_estado}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def anular_o_emitir_nota_credito_factura(numero_factura: str, accion_doc: str, motivo: str, tasa_bcv: float, responsable: str, user_ctx: dict):
    if not motivo.strip():
        raise ValueError("Debe especificar el motivo de anulación o nota de crédito.")

    conn = get_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur.execute("SELECT * FROM facturas_emitidas WHERE numero_factura = ?", (numero_factura,))
        fac = cur.fetchone()
        if not fac:
            raise ValueError(f"Factura {numero_factura} no encontrada.")
        if fac["estado_documento"] == "ANULADA":
            raise ValueError("La factura ya se encuentra anulada.")

        saldo_pend = redondear_moneda(a_decimal(fac["saldo_pendiente_usd"]))
        fecha_now = datetime.now().strftime("%Y-%m-%d %H:%M")
        tasa_d = a_decimal(tasa_bcv, "1.00")

        nuevo_estado = "ANULADA" if accion_doc == "Anular Factura" else "NOTA_CREDITO"
        cur.execute(
            """
            UPDATE facturas_emitidas
            SET estado_documento = ?, saldo_pendiente_usd = 0.0, motivo_estado = ?
            WHERE numero_factura = ?
            """,
            (nuevo_estado, motivo.strip(), numero_factura)
        )

        if saldo_pend > Decimal("0.00"):
            cur.execute(
                """
                INSERT INTO movimientos_cxc
                (numero_factura, cliente_codigo, cliente_nombre, fecha_movimiento,
                 tipo_movimiento, monto_usd, monto_bs, referencia, responsable)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    numero_factura, fac["cliente_codigo"], fac["cliente_nombre"], fecha_now,
                    nuevo_estado, float(-saldo_pend), float(redondear_moneda(-saldo_pend * tasa_d)),
                    motivo.strip(), responsable
                )
            )
            cur.execute(
                """
                UPDATE clientes_cartera
                SET saldo_pendiente_usd = MAX(0.0, ROUND(saldo_pendiente_usd - ?, 2))
                WHERE codigo_cliente = ?
                """,
                (float(saldo_pend), fac["cliente_codigo"])
            )

        conn.commit()
        registrar_auditoria_comercial(user_ctx, "Facturación", nuevo_estado, numero_factura, motivo.strip())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def registrar_gasto_o_costo_atomico(
    clasificacion: str,
    naturaleza: str,
    centro_costo: str,
    rubro_o_sku: str,
    concepto: str,
    monto_usd,
    tasa_bcv: float,
    codigo_cuenta: str,
    descontar_caja: bool,
    responsable: str,
    user_ctx: dict
) -> str:
    monto_d = redondear_moneda(a_decimal(monto_usd))
    tasa_d = a_decimal(tasa_bcv, "1.00")
    if monto_d <= Decimal("0.00"):
        raise ValueError("El monto del gasto/costo debe ser mayor a cero.")
    if not concepto.strip():
        raise ValueError("Debe ingresar el concepto del gasto o costo.")

    monto_bs = redondear_moneda(monto_d * tasa_d)
    fecha_now = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = get_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cod_reg = generar_codigo("GST" if clasificacion.startswith("Gasto") else "CST", "gastos_costos")
        cur.execute(
            """
            INSERT INTO gastos_costos
            (codigo_registro, fecha, clasificacion, naturaleza, centro_costo,
             rubro_o_sku, concepto, monto_usd, tasa_bcv, monto_bs, estado_aprobacion, responsable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Aprobado', ?)
            """,
            (
                cod_reg, fecha_now, clasificacion, naturaleza, centro_costo,
                rubro_o_sku, concepto.strip(), float(monto_d), float(tasa_d), float(monto_bs), responsable
            )
        )

        if descontar_caja:
            cod_rec = generar_codigo("REC", "tesoreria")
            cur.execute(
                """
                INSERT INTO tesoreria
                (codigo_recibo, fecha, tipo_operacion, concepto, doc_asociado, tercero,
                 metodo_pago, referencia, monto_usd, tasa_bcv, monto_bs, responsable)
                VALUES (?, ?, 'Egreso', ?, ?, ?, 'Tesorería / Caja', ?, ?, ?, ?, ?)
                """,
                (
                    cod_rec, fecha_now, f"[{clasificacion}] {concepto.strip()}",
                    cod_reg, centro_costo, cod_reg, float(monto_d), float(tasa_d), float(monto_bs), responsable
                )
            )
            cur.execute(
                "UPDATE cuentas_tesoreria SET saldo_actual = ROUND(saldo_actual - ?, 2) WHERE codigo_cuenta = ?",
                (float(monto_d), codigo_cuenta)
            )

        conn.commit()
        registrar_auditoria_comercial(user_ctx, "CostosGastos", "REGISTRAR_COSTO_GASTO", cod_reg, f"{clasificacion} ({naturaleza}) ${monto_d:,.2f} en {centro_costo}")
        return cod_reg
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def registrar_cxp_y_pago_atomico(
    proveedor: str,
    rif_proveedor: str,
    doc_compra: str,
    fecha_vencimiento_str: str,
    prioridad: str,
    monto_total_usd,
    descuento_pronto_pago_pct,
    responsable: str,
    user_ctx: dict
) -> str:
    monto_d = redondear_moneda(a_decimal(monto_total_usd))
    desc_pp = a_decimal(descuento_pronto_pago_pct)
    if monto_d <= Decimal("0.00"):
        raise ValueError("El monto del compromiso CxP debe ser mayor a cero.")

    fecha_now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cod_cxp = generar_codigo("CXP", "compromisos_cxp")
        cur.execute(
            """
            INSERT INTO compromisos_cxp
            (codigo_cxp, doc_compra, proveedor, rif_proveedor, fecha_emision,
             fecha_vencimiento, prioridad, monto_total_usd, monto_pagado_usd,
             saldo_pendiente_usd, descuento_pronto_pago_pct, estado_cxp, responsable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, 'PENDIENTE', ?)
            """,
            (
                cod_cxp, doc_compra.strip(), proveedor.strip(), rif_proveedor.strip(),
                fecha_now[:10], fecha_vencimiento_str, prioridad, float(monto_d),
                float(monto_d), float(desc_pp), responsable
            )
        )
        conn.commit()
        registrar_auditoria_comercial(user_ctx, "CxP", "CREAR_CXP", cod_cxp, f"Proveedor {proveedor} | Saldo ${monto_d:,.2f}")
        return cod_cxp
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def abonar_cxp_atomico(codigo_cxp: str, monto_pago, codigo_cuenta: str, metodo_pago: str, referencia: str, tasa_bcv: float, responsable: str, user_ctx: dict):
    pago_d = redondear_moneda(a_decimal(monto_pago))
    tasa_d = a_decimal(tasa_bcv, "1.00")
    if pago_d <= Decimal("0.00"):
        raise ValueError("El monto del desembolso debe ser mayor a cero.")

    conn = get_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur.execute("SELECT * FROM compromisos_cxp WHERE codigo_cxp = ?", (codigo_cxp,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Compromiso {codigo_cxp} no encontrado.")
        saldo_act = redondear_moneda(a_decimal(row["saldo_pendiente_usd"]))
        if pago_d > saldo_act:
            raise ValueError(f"El pago (${pago_d:,.2f}) supera el saldo pendiente (${saldo_act:,.2f}).")

        nuevo_pagado = redondear_moneda(a_decimal(row["monto_pagado_usd"]) + pago_d)
        nuevo_saldo = max(Decimal("0.00"), redondear_moneda(saldo_act - pago_d))
        nuevo_estado = "PAGADA" if nuevo_saldo == Decimal("0.00") else "PARCIAL"
        fecha_now = datetime.now().strftime("%Y-%m-%d %H:%M")
        monto_bs = redondear_moneda(pago_d * tasa_d)

        cur.execute(
            """
            UPDATE compromisos_cxp
            SET monto_pagado_usd = ?, saldo_pendiente_usd = ?, estado_cxp = ?
            WHERE codigo_cxp = ?
            """,
            (float(nuevo_pagado), float(nuevo_saldo), nuevo_estado, codigo_cxp)
        )

        cod_rec = generar_codigo("REC", "tesoreria")
        cur.execute(
            """
            INSERT INTO tesoreria
            (codigo_recibo, fecha, tipo_operacion, concepto, doc_asociado, tercero,
             metodo_pago, referencia, monto_usd, tasa_bcv, monto_bs, responsable)
            VALUES (?, ?, 'Egreso', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cod_rec, fecha_now, f"Pago compromiso CxP {codigo_cxp}",
                codigo_cxp, row["proveedor"], metodo_pago, referencia.strip() or cod_rec,
                float(pago_d), float(tasa_d), float(monto_bs), responsable
            )
        )
        cur.execute(
            "UPDATE cuentas_tesoreria SET saldo_actual = ROUND(saldo_actual - ?, 2) WHERE codigo_cuenta = ?",
            (float(pago_d), codigo_cuenta)
        )

        conn.commit()
        registrar_auditoria_comercial(user_ctx, "CxP", "PAGAR_CXP", codigo_cxp, f"Pago ${pago_d:,.2f} | Saldo ${nuevo_saldo:,.2f}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



# ==============================================================================
# ESTILOS CORPORATIVOS CSS
# ==============================================================================
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(90deg, #1B4332 0%, #2D6A4F 70%, #9B2226 100%);
        padding: 1.2rem 1.5rem;
        border-radius: 10px;
        color: white;
        margin-bottom: 1.2rem;
        box-shadow: 0 4px 10px rgba(0,0,0,0.08);
    }
    .main-header h2 {
        margin: 0;
        font-size: 1.45rem;
        font-weight: 700;
        color: #FFFFFF;
    }
    .main-header p {
        margin: 0.25rem 0 0 0;
        font-size: 0.9rem;
        color: #E2E8F0;
    }
    .stButton>button {
        border-radius: 6px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# ==============================================================================
# ESTADO DE SESIÓN
# ==============================================================================
if "usuario" not in st.session_state:
    st.session_state.usuario = None

for key in [
    "ultimo_pdf_almacen", "ultimo_cod_almacen",
    "ultimo_pdf_entrada_alm", "ultimo_cod_entrada_alm",
    "ultimo_pdf_compra", "ultimo_cod_compra",
    "ultimo_pdf_tesoreria", "ultimo_cod_tesoreria",
    "ultimo_pdf_venta", "ultimo_cod_venta",
    "ultimo_pdf_preventa", "ultimo_cod_preventa"
]:
    if key not in st.session_state:
        st.session_state[key] = None

# ==============================================================================
# PANTALLA DE INICIO DE SESIÓN POR UNIDAD ADMINISTRATIVA
# ==============================================================================
if st.session_state.usuario is None:
    col_izq, col_centro, col_der = st.columns([1, 1.4, 1])
    with col_centro:
        st.markdown("<br>", unsafe_allow_html=True)
        c_logo1, c_title, c_logo2 = st.columns([1, 2.2, 1])
        with c_logo1:
            render_logo(LOGO_CAFETAL_PATH, "El Cafetal")
        with c_title:
            st.markdown(
                "<h3 style='text-align:center; color:#1B4332; margin-bottom:0;'>EPSDC Guerreros de la Montaña</h3>"
                "<p style='text-align:center; color:#9B2226; font-weight:bold; margin-top:2px;'>RIF: J-507789474 • Yaracuy</p>",
                unsafe_allow_html=True
            )
        with c_logo2:
            render_logo(LOGO_PORTU_PATH, "Por Tu Café")

        st.markdown("---")
        st.subheader("Acceso al Sistema Administrativo y Control de Gestión")
        st.caption("Ingrese las credenciales asignadas a su Unidad Administrativa.")

        with st.form("form_login"):
            username = st.text_input("Usuario de Unidad", placeholder="Ej: almacen, compras, finanzas, ventas, gerencia")
            password = st.text_input("Clave de Seguridad", type="password", placeholder="••••••••••••")
            submitted = st.form_submit_button("Ingresar al Sistema", use_container_width=True, type="primary")

            if submitted:
                user_data = autenticar_usuario(username, password)
                if user_data:
                    st.session_state.usuario = user_data
                    registrar_auditoria_comercial(user_data, "Acceso", "LOGIN", user_data["username"], f"Ingreso a unidad {user_data['unidad']}")
                    st.rerun()
                else:
                    st.error("Credenciales inválidas. Verifique su usuario y clave asignada.")

        with st.expander("Ver Claves de Acceso de Prueba por Unidad"):
            st.markdown("""
            * **Gerencia / Contraloría Social (Administrador):** `gerencia` | `Admin2026*`
            * **Unidad de Almacén e Inventario:** `almacen` | `Almacen2026*`
            * **Unidad de Compras y Abastecimiento:** `compras` | `Compras2026*`
            * **Unidad Financiera y Tesorería:** `finanzas` | `Finanzas2026*`
            * **Unidad de Despacho y Ventas:** `ventas` | `Ventas2026*`
            """)
    st.stop()

# ==============================================================================
# INTERFAZ PRINCIPAL AUTENTICADA
# ==============================================================================
user = st.session_state.usuario
rol = user["rol"]
es_admin = (rol == "ADMIN")
nivel_rbac = obtener_nivel_acceso_comercial(user)

estado_tasa = obtener_estado_tasa_bcv()
tasa_bcv_global = estado_tasa["tasa_bcv"]

with st.sidebar:
    c1, c2 = st.columns(2)
    with c1:
        render_logo(LOGO_CAFETAL_PATH, "El Cafetal")
    with c2:
        render_logo(LOGO_PORTU_PATH, "Por Tu Café")

    st.markdown(f"**Unidad Activa:**\n`{user['unidad']}`")
    st.markdown(f"**Responsable:** {user['nombre_completo']}")
    st.markdown(f"**Cargo / Nivel RBAC:** {user['cargo']} (`{nivel_rbac}`)")
    st.markdown("---")

    st.markdown("### Tasa Oficial BCV (Bloqueada 24h)")
    st.metric("Tasa BCV Vigente (Bs/$)", f"Bs {tasa_bcv_global:,.2f}")
    st.caption(f"Fijada: {estado_tasa['actualizada_en']} por `{estado_tasa['modificada_por']}`")
    if estado_tasa["vigente"]:
        st.caption(f"Vigencia activa: vence en {estado_tasa['tiempo_restante']} ({estado_tasa['expira_en']})")
    else:
        st.warning(f"Ciclo de 24h vencido el {estado_tasa['expira_en']}. Requiere actualización del Administrador.")

    if es_admin:
        with st.form("form_tasa_bcv_admin"):
            nueva_tasa_bcv = st.number_input(
                "Actualizar Tasa BCV (Vigencia 24 Horas)",
                min_value=1.0,
                value=float(tasa_bcv_global),
                step=0.10,
                format="%.2f"
            )
            guardar_tasa = st.form_submit_button("Fijar Tasa por 24 Horas", use_container_width=True, type="primary")
            if guardar_tasa:
                guardar_tasa_bcv_administrador(nueva_tasa_bcv, user["username"])
                registrar_auditoria_comercial(user, "Configuración", "ACTUALIZAR_TASA_BCV", "BCV-24H", f"Nueva tasa fijada: Bs {nueva_tasa_bcv:,.2f}")
                st.success(f"Tasa BCV actualizada a Bs {nueva_tasa_bcv:,.2f} por 24 horas.")
                st.rerun()
    else:
        st.info("Solo el Administrador puede modificar la tasa BCV (vigencia de 24 horas).")

    st.markdown("---")

    opciones_menu = ["Panel Ejecutivo y Contraloría"]
    if rol in ["ADMIN", "ALMACEN"]:
        opciones_menu.append("1. Almacén, Recepción e Inventario")
    if rol in ["ADMIN", "COMPRAS"]:
        opciones_menu.append("2. Compras y Acopio Agrícola")
    if rol in ["ADMIN", "FINANZAS"]:
        opciones_menu.append("3. Finanzas, Facturación y Tesorería")
    if rol in ["ADMIN", "VENTAS"]:
        opciones_menu.append("4. Despacho, Comercialización y Ventas")

    modulo_activo = st.radio("Módulos del Sistema", opciones_menu)

    st.markdown("---")
    if st.button("Cerrar Sesión", use_container_width=True):
        st.session_state.usuario = None
        st.rerun()

# Encabezado Principal
st.markdown(f"""
<div class="main-header">
    <h2>EPSDC Guerreros de la Montaña de Yaracuy — Control Administrativo Integral</h2>
    <p>RIF: J-507789474 | Centro de Acopio y Distribución • Marca Comercial: <b>Por Tu Café</b> & Aliados <b>El Cafetal</b></p>
</div>
""", unsafe_allow_html=True)

if not estado_tasa["vigente"]:
    st.error(
        f"La tasa BCV registrada (Bs {tasa_bcv_global:,.2f}/$) cumplió su ventana de 24 horas el {estado_tasa['expira_en']}. "
        "Solicite al usuario Administrador (`gerencia`) actualizarla antes de emitir nuevos comprobantes."
    )

# ==============================================================================
# MÓDULO 0: PANEL EJECUTIVO Y CONTRALORÍA SOCIAL
# ==============================================================================
if modulo_activo == "Panel Ejecutivo y Contraloría":
    st.subheader("Tablero Consolidado en Tiempo Real y Auditoría Cruzada")

    df_inv = query_df("SELECT * FROM inventario")
    df_ventas = query_df("SELECT * FROM ventas")
    df_compras = query_df("SELECT * FROM compras")
    df_teso = query_df("SELECT * FROM tesoreria")

    valor_inventario = (df_inv["stock_actual"] * df_inv["costo_unitario_usd"]).sum()
    total_ventas_usd = df_ventas["total_usd"].sum() if not df_ventas.empty else 0.0
    total_compras_usd = df_compras["total_usd"].sum() if not df_compras.empty else 0.0
    ingresos_teso = df_teso[df_teso["tipo_operacion"] == "Ingreso"]["monto_usd"].sum() if not df_teso.empty else 0.0
    egresos_teso = df_teso[df_teso["tipo_operacion"] == "Egreso"]["monto_usd"].sum() if not df_teso.empty else 0.0
    saldo_caja_usd = ingresos_teso - egresos_teso

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Valorización Almacén ($)", f"${valor_inventario:,.2f}", f"Bs {valor_inventario * tasa_bcv_global:,.2f}")
    k2.metric("Compras y Acopio ($)", f"${total_compras_usd:,.2f}", f"{len(df_compras)} órdenes")
    k3.metric("Ventas Facturadas ($)", f"${total_ventas_usd:,.2f}", f"{len(df_ventas)} pedidos")
    k4.metric("Saldo Neto Tesorería ($)", f"${saldo_caja_usd:,.2f}", f"Bs {saldo_caja_usd * tasa_bcv_global:,.2f}")

    st.markdown("---")
    c_left, c_right = st.columns([1.3, 1])

    with c_left:
        st.markdown("#### Existencias Actuales en Centro de Acopio y Almacén")
        st.dataframe(
            df_inv[["sku", "nombre", "categoria", "unidad_medida", "stock_actual", "stock_minimo", "costo_unitario_usd", "precio_detal_usd", "precio_mayor_usd", "ubicacion_bodega"]],
            use_container_width=True,
            hide_index=True
        )

    with c_right:
        st.markdown("#### Alertas de Reposición (Stock Mínimo)")
        bajo_stock = df_inv[df_inv["stock_actual"] <= df_inv["stock_minimo"]]
        if bajo_stock.empty:
            st.success("Todos los rubros cumplen con el nivel óptimo de inventario.")
        else:
            st.warning("Rubros que requieren compra o maquila urgente:")
            st.dataframe(bajo_stock[["sku", "nombre", "stock_actual", "stock_minimo"]], use_container_width=True, hide_index=True)

        st.markdown("#### Exportación Contable Consolidada")
        buffer_excel = io.BytesIO()
        with pd.ExcelWriter(buffer_excel, engine="openpyxl") as writer:
            df_inv.to_excel(writer, sheet_name="Inventario", index=False)
            df_compras.to_excel(writer, sheet_name="Compras_Acopio", index=False)
            df_ventas.to_excel(writer, sheet_name="Ventas_Despacho", index=False)
            df_teso.to_excel(writer, sheet_name="Tesoreria", index=False)
        st.download_button(
            label="Descargar Libro Administrativo Completo (.XLSX)",
            data=buffer_excel.getvalue(),
            file_name=f"Consolidado_EPSDC_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

# ==============================================================================
# MÓDULO 1: UNIDAD DE ALMACÉN, RECEPCIÓN E INVENTARIO
# ==============================================================================
elif modulo_activo == "1. Almacén, Recepción e Inventario":
    st.subheader("Unidad de Almacén, Recepción e Inventario (Custodia Física y Kardex)")
    tab_entrada, tab_sku, tab_mov, tab_kardex = st.tabs([
        "Entrada en Almacén",
        "Registro / Edición de SKU",
        "Otras Salidas / Ajustes",
        "Historial Kardex y Actas PDF"
    ])

    df_inv = query_df("SELECT * FROM inventario")

    with tab_entrada:
        st.markdown("#### Módulo de Entrada en Almacén (Recepción Física de Mercancía / Cosecha)")
        st.caption("Registra el ingreso físico de unidades al stock, asigna lote y ubicación de bodega, y genera el Acta de Entrada en PDF.")

        with st.form("form_entrada_almacen"):
            c1, c2, c3 = st.columns(3)
            prod_ent = c1.selectbox(
                "SKU / Producto a Ingresar",
                df_inv["sku"] + " | " + df_inv["nombre"] + " [" + df_inv["unidad_medida"] + "]"
            )
            cant_entrada = c2.number_input("Cantidad de Entrada", min_value=0.01, value=10.0, step=1.0)
            lote_entrada = c3.text_input("Número de Lote / Cosecha", value=f"LOTE-{datetime.now().strftime('%Y%m%d')}")

            c4, c5, c6 = st.columns(3)
            origen_entrada = c4.text_input("Origen / Proveedor / Productor", value="Centro de Acopio / Torrefactora El Cafetal")
            doc_soporte = c5.text_input("Documento Soporte (Orden / Guía / Factura)", placeholder="Ej: COMP-2026-0001")
            ubicacion_entrada = c6.text_input("Ubicación en Bodega", value="Almacén Principal Nirgua")

            obs_entrada = st.text_area("Inspección de Entrada (Peso, Humedad, Integridad del Empaque)")
            submitted_entrada = st.form_submit_button("Confirmar Entrada en Almacén y Generar Acta PDF", type="primary")

        if submitted_entrada:
            if not origen_entrada.strip():
                st.error("Debe indicar el Origen, Proveedor o Productor de la mercancía.")
            else:
                sku_e = prod_ent.split(" | ")[0]
                prod_row = df_inv[df_inv["sku"] == sku_e].iloc[0]
                cod_ent = generar_codigo("ENT", "movimientos_almacen")
                fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                nuevo_stock = float(prod_row["stock_actual"]) + float(cant_entrada)
                ubic_final = ubicacion_entrada.strip() or prod_row["ubicacion_bodega"]
                obs_completa = f"Soporte: {doc_soporte.strip() or 'N/A'}. {obs_entrada.strip()}".strip()

                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE inventario SET stock_actual = ?, ubicacion_bodega = ? WHERE sku = ?",
                    (nuevo_stock, ubic_final, sku_e)
                )
                cur.execute(
                    """
                    INSERT INTO movimientos_almacen
                    (codigo_doc, fecha, tipo_movimiento, sku, cantidad, lote, origen_destino, responsable, observaciones)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cod_ent,
                        fecha_str,
                        "Entrada - Almacén",
                        sku_e,
                        float(cant_entrada),
                        lote_entrada.strip(),
                        origen_entrada.strip(),
                        user["username"],
                        obs_completa
                    )
                )
                conn.commit()
                conn.close()

                pdf_bytes = generar_pdf_comprobante(
                    titulo_documento="Acta de Entrada en Almacén",
                    codigo_documento=cod_ent,
                    unidad_emisora="Unidad de Almacén, Recepción e Inventario",
                    datos_generales={
                        "Tipo Movimiento": "Entrada en Almacén",
                        "Fecha y Hora": fecha_str,
                        "Origen / Proveedor": origen_entrada.strip(),
                        "Doc. Soporte": doc_soporte.strip() or "N/A",
                        "Lote": lote_entrada.strip() or "N/A",
                        "Ubicación Bodega": ubic_final
                    },
                    columnas_tabla=["SKU", "Descripción", "Unidad", "Entrada", "Stock Actual"],
                    filas_tabla=[[
                        sku_e,
                        prod_row["nombre"],
                        prod_row["unidad_medida"],
                        f"{cant_entrada:,.2f}",
                        f"{nuevo_stock:,.2f}"
                    ]],
                    observaciones=obs_completa or "Entrada verificada y contabilizada en el inventario físico del almacén.",
                    firmas=("Jefe de Almacén e Inventario", "Entrega / Transportista", "Contraloría Social")
                )
                st.session_state.ultimo_pdf_entrada_alm = pdf_bytes
                st.session_state.ultimo_cod_entrada_alm = cod_ent
                st.success(f"Entrada {cod_ent} registrada para {sku_e}. Nuevo stock: {nuevo_stock:,.2f} {prod_row['unidad_medida']}.")

        if st.session_state.ultimo_pdf_entrada_alm is not None:
            st.download_button(
                label="Descargar Acta de Entrada en Almacén (PDF)",
                data=st.session_state.ultimo_pdf_entrada_alm,
                file_name=f"{st.session_state.ultimo_cod_entrada_alm}.pdf",
                mime="application/pdf",
                key="btn_descargar_entrada_almacen"
            )

        st.markdown("##### Últimas Entradas Registradas en Almacén")
        df_entradas = query_df("SELECT * FROM movimientos_almacen WHERE tipo_movimiento LIKE 'Entrada%' ORDER BY id DESC")
        st.dataframe(df_entradas, use_container_width=True, hide_index=True)

    with tab_sku:
        st.markdown("#### Registrar o Actualizar SKU con sus Propiedades Completas")
        st.caption("Campos estructurados: `sku`, `nombre`, `categoria`, `unidad_medida`, `stock_actual`, `stock_minimo`, `costo_unitario_usd`, `precio_detal_usd`, `precio_mayor_usd`, `ubicacion_bodega`.")

        with st.form("form_sku_completo"):
            c1, c2, c3, c4 = st.columns(4)
            sku_val = c1.text_input("sku", placeholder="Ej: PTC-500").strip().upper()
            nombre_val = c2.text_input("nombre", placeholder="Ej: Por Tu Café Molido Gourmet 500g")
            categoria_val = c3.selectbox(
                "categoria",
                [
                    "Producto Terminado - Café",
                    "Materia Prima Agrícola",
                    "Insumos Agrícolas",
                    "Herramientas Agrícolas",
                    "Otros Alimentos"
                ]
            )
            unidad_medida_val = c4.text_input("unidad_medida", value="Bulto").strip()

            c5, c6, c7 = st.columns(3)
            stock_actual_val = c5.number_input("stock_actual", min_value=0.0, value=0.0, step=1.0, format="%.2f")
            stock_minimo_val = c6.number_input("stock_minimo", min_value=0.0, value=10.0, step=1.0, format="%.2f")
            costo_unitario_val = c7.number_input("costo_unitario_usd ($)", min_value=0.0, value=15.0, step=0.50, format="%.2f")

            c8, c9, c10 = st.columns(3)
            precio_detal_val = c8.number_input("precio_detal_usd ($)", min_value=0.0, value=22.0, step=0.50, format="%.2f")
            precio_mayor_val = c9.number_input("precio_mayor_usd ($)", min_value=0.0, value=19.50, step=0.50, format="%.2f")
            ubicacion_bodega_val = c10.text_input("ubicacion_bodega", value="Almacén Principal Nirgua").strip()

            submitted_sku_full = st.form_submit_button("Guardar Propiedades del SKU", type="primary")

        if submitted_sku_full:
            if not sku_val or not nombre_val or not unidad_medida_val:
                st.error("Debe completar al menos `sku`, `nombre` y `unidad_medida`.")
            else:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM inventario WHERE sku = ?", (sku_val,))
                existe = cur.fetchone()[0] > 0

                if existe:
                    cur.execute(
                        """
                        UPDATE inventario
                        SET nombre = ?, categoria = ?, unidad_medida = ?,
                            stock_actual = ?, stock_minimo = ?, costo_unitario_usd = ?,
                            precio_detal_usd = ?, precio_mayor_usd = ?, ubicacion_bodega = ?
                        WHERE sku = ?
                        """,
                        (
                            nombre_val,
                            categoria_val,
                            unidad_medida_val,
                            float(stock_actual_val),
                            float(stock_minimo_val),
                            float(costo_unitario_val),
                            float(precio_detal_val),
                            float(precio_mayor_val),
                            ubicacion_bodega_val or "Almacén Principal Nirgua",
                            sku_val
                        )
                    )
                    conn.commit()
                    conn.close()
                    st.success(f"Propiedades del SKU `{sku_val}` actualizadas correctamente.")
                else:
                    cur.execute(
                        """
                        INSERT INTO inventario
                        (sku, nombre, categoria, unidad_medida, stock_actual, stock_minimo,
                         costo_unitario_usd, precio_detal_usd, precio_mayor_usd, ubicacion_bodega)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sku_val,
                            nombre_val,
                            categoria_val,
                            unidad_medida_val,
                            float(stock_actual_val),
                            float(stock_minimo_val),
                            float(costo_unitario_val),
                            float(precio_detal_val),
                            float(precio_mayor_val),
                            ubicacion_bodega_val or "Almacén Principal Nirgua"
                        )
                    )
                    if float(stock_actual_val) > 0:
                        cod_ini = generar_codigo("ENT", "movimientos_almacen")
                        fecha_ini = datetime.now().strftime("%Y-%m-%d %H:%M")
                        cur.execute(
                            """
                            INSERT INTO movimientos_almacen
                            (codigo_doc, fecha, tipo_movimiento, sku, cantidad, lote, origen_destino, responsable, observaciones)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                cod_ini,
                                fecha_ini,
                                "Entrada - Inventario Inicial SKU",
                                sku_val,
                                float(stock_actual_val),
                                "INICIAL",
                                ubicacion_bodega_val or "Almacén Principal Nirgua",
                                user["username"],
                                "Stock inicial asignado al crear el SKU."
                            )
                        )
                    conn.commit()
                    conn.close()
                    st.success(f"SKU `{sku_val}` registrado con todas sus propiedades.")

        df_inv_actual = query_df(
            """
            SELECT sku, nombre, categoria, unidad_medida, stock_actual, stock_minimo,
                   costo_unitario_usd, precio_detal_usd, precio_mayor_usd, ubicacion_bodega
            FROM inventario
            ORDER BY sku ASC
            """
        )
        st.dataframe(df_inv_actual, use_container_width=True, hide_index=True)

    with tab_mov:
        st.markdown("#### Salida por Consumo Interno, Traslado o Ajuste de Almacén")
        with st.form("form_mov_almacen"):
            c1, c2, c3 = st.columns(3)
            tipo_mov = c1.selectbox("Tipo de Salida / Ajuste", ["Salida - Consumo / Merma / Traslado", "Entrada - Ajuste Positivo"])
            producto_sel = c2.selectbox("Rubro / SKU", df_inv["sku"] + " | " + df_inv["nombre"])
            cantidad_mov = c3.number_input("Cantidad (Bultos / Sacos / Unidades)", min_value=0.01, value=1.0, step=1.0)

            c4, c5 = st.columns(2)
            lote_mov = c4.text_input("Número de Lote / Cosecha", value=f"LOTE-{datetime.now().strftime('%Y%m')}")
            origen_mov = c5.text_input("Destino / Área Interna", value="Planta / Despacho Interno")
            obs_mov = st.text_area("Motivo o Observaciones de Control de Calidad")

            submitted_alm = st.form_submit_button("Registrar Movimiento y Generar Acta PDF", type="primary")

        if submitted_alm:
            sku_sel = producto_sel.split(" | ")[0]
            prod_row = df_inv[df_inv["sku"] == sku_sel].iloc[0]
            cod_doc = generar_codigo("ALM", "movimientos_almacen")
            fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            if "Salida" in tipo_mov and float(prod_row["stock_actual"]) < float(cantidad_mov):
                st.error("Stock insuficiente en almacén para procesar esta salida.")
            else:
                nuevo_stock = (
                    float(prod_row["stock_actual"]) + float(cantidad_mov)
                    if "Entrada" in tipo_mov
                    else float(prod_row["stock_actual"]) - float(cantidad_mov)
                )
                conn = get_connection()
                cur = conn.cursor()
                cur.execute("UPDATE inventario SET stock_actual = ? WHERE sku = ?", (nuevo_stock, sku_sel))
                cur.execute("""
                    INSERT INTO movimientos_almacen (codigo_doc, fecha, tipo_movimiento, sku, cantidad, lote, origen_destino, responsable, observaciones)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (cod_doc, fecha_str, tipo_mov, sku_sel, float(cantidad_mov), lote_mov, origen_mov, user["username"], obs_mov))
                conn.commit()
                conn.close()

                pdf_bytes = generar_pdf_comprobante(
                    titulo_documento="Acta de Movimiento de Almacén",
                    codigo_documento=cod_doc,
                    unidad_emisora="Unidad de Almacén, Recepción e Inventario",
                    datos_generales={
                        "Tipo Operación": tipo_mov,
                        "Fecha y Hora": fecha_str,
                        "Destino / Área": origen_mov,
                        "Lote Asignado": lote_mov,
                        "Ubicación Física": prod_row["ubicacion_bodega"],
                        "Responsable": user["nombre_completo"]
                    },
                    columnas_tabla=["SKU", "Descripción del Rubro", "Medida", "Cantidad", "Saldo Final"],
                    filas_tabla=[[sku_sel, prod_row["nombre"], prod_row["unidad_medida"], f"{cantidad_mov:,.2f}", f"{nuevo_stock:,.2f}"]],
                    observaciones=obs_mov or "Verificado peso, integridad de empaque y condiciones de almacenamiento.",
                    firmas=("Jefe de Almacén e Inventario", "Transportista / Entregado Por", "Contraloría Social")
                )
                st.session_state.ultimo_pdf_almacen = pdf_bytes
                st.session_state.ultimo_cod_almacen = cod_doc
                st.success(f"Movimiento {cod_doc} registrado correctamente. Nuevo stock: {nuevo_stock:,.2f}")

        if st.session_state.ultimo_pdf_almacen is not None:
            st.download_button(
                label="Descargar Acta de Movimiento (PDF)",
                data=st.session_state.ultimo_pdf_almacen,
                file_name=f"{st.session_state.ultimo_cod_almacen}.pdf",
                mime="application/pdf",
                key="btn_descargar_acta_almacen"
            )

    with tab_kardex:
        df_kardex = query_df("SELECT * FROM movimientos_almacen ORDER BY id DESC")
        st.dataframe(df_kardex, use_container_width=True, hide_index=True)

# ==============================================================================
# MÓDULO 2: UNIDAD DE COMPRAS Y ABASTECIMIENTO
# ==============================================================================
elif modulo_activo == "2. Compras y Acopio Agrícola":
    st.subheader("Unidad de Compras, Acopio de Cosechas y Abastecimiento")
    df_inv = query_df("SELECT * FROM inventario")

    tab1, tab2 = st.tabs(["Emitir Orden de Compra / Liquidación al Productor", "Registro Histórico de Compras"])

    with tab1:
        with st.form("form_compra"):
            c1, c2, c3 = st.columns(3)
            tipo_adq = c1.selectbox("Tipo de Adquisición", ["Acopio de Cosecha (Pequeños Productores)", "Compra Insumos Agrícolas", "Compra Herramientas Agrícolas", "Servicio de Maquila / Empaque"])
            proveedor = c2.text_input("Productor / Asociación / Proveedor", placeholder="Ej: Comité Cafetalero Aroa")
            rif_prov = c3.text_input("Cédula o RIF", placeholder="Ej: V-14523698")

            c4, c5, c6 = st.columns(3)
            origen_geo = c4.text_input("Municipio y Estado de Origen", value="Nirgua, Yaracuy")
            prod_compra = c5.selectbox("Rubro Adquirido", df_inv["sku"] + " | " + df_inv["nombre"])
            cant_compra = c6.number_input("Cantidad Adquirida", min_value=1.0, value=10.0)

            c7, c8 = st.columns(2)
            costo_u = c7.number_input("Precio de Compra Unitario ($)", min_value=0.1, value=22.0)
            est_pago = c8.selectbox("Condición Financiera", ["Pendiente por Tesorería", "Pagado Contado", "Cruce por Insumos / Herramientas"])
            obs_comp = st.text_area("Notas de Compra y Calidad del Grano / Insumo")

            submitted_compra = st.form_submit_button("Aprobar Orden de Compra y Generar Comprobante PDF", type="primary")

        if submitted_compra:
            if not proveedor.strip() or not rif_prov.strip():
                st.error("Debe indicar el nombre del Productor/Proveedor y su Cédula o RIF.")
            else:
                sku_c = prod_compra.split(" | ")[0]
                prod_row = df_inv[df_inv["sku"] == sku_c].iloc[0]
                total_usd = cant_compra * costo_u
                total_bs = total_usd * tasa_bcv_global
                cod_comp = generar_codigo("COMP", "compras")
                fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")

                conn = get_connection()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO compras (codigo_compra, fecha, proveedor_productor, cedula_rif, municipio_estado, tipo_adquisicion, sku, cantidad, costo_unitario_usd, total_usd, tasa_bcv, total_bs, estado_pago, observaciones, responsable)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (cod_comp, fecha_str, proveedor, rif_prov, origen_geo, tipo_adq, sku_c, cant_compra, costo_u, total_usd, tasa_bcv_global, total_bs, est_pago, obs_comp, user["username"]))
                cur.execute("UPDATE inventario SET stock_actual = stock_actual + ?, costo_unitario_usd = ? WHERE sku = ?", (cant_compra, costo_u, sku_c))
                conn.commit()
                conn.close()

                pdf_bytes = generar_pdf_comprobante(
                    titulo_documento="Orden de Compra y Liquidación de Acopio",
                    codigo_documento=cod_comp,
                    unidad_emisora="Unidad de Compras y Abastecimiento",
                    datos_generales={
                        "Productor / Proveedor": proveedor,
                        "Cédula / RIF": rif_prov,
                        "Procedencia": origen_geo,
                        "Modalidad": tipo_adq,
                        "Condición Pago": est_pago,
                        "Tasa BCV Aplicada": f"Bs {tasa_bcv_global:,.2f} / $"
                    },
                    columnas_tabla=["SKU", "Descripción del Rubro", "Cantidad", "Precio Unit. ($)", "Total ($)"],
                    filas_tabla=[[sku_c, prod_row["nombre"], f"{cant_compra:,.2f}", f"${costo_u:,.2f}", f"${total_usd:,.2f}"]],
                    totales={
                        "TOTAL DIVISAS ($):": f"${total_usd:,.2f}",
                        "TOTAL BOLÍVARES:": f"Bs {total_bs:,.2f}"
                    },
                    observaciones=obs_comp or "Compra aprobada bajo tabla oficial de precios justos al productor.",
                    firmas=("Analista de Compras", "Productor / Proveedor", "Autorizado Finanzas")
                )
                st.session_state.ultimo_pdf_compra = pdf_bytes
                st.session_state.ultimo_cod_compra = cod_comp
                st.success(f"Orden {cod_comp} registrada por ${total_usd:,.2f} (Bs {total_bs:,.2f}).")

        if st.session_state.ultimo_pdf_compra is not None:
            st.download_button(
                label="Descargar Orden de Compra (PDF)",
                data=st.session_state.ultimo_pdf_compra,
                file_name=f"{st.session_state.ultimo_cod_compra}.pdf",
                mime="application/pdf",
                key="btn_descargar_orden_compra"
            )

    with tab2:
        st.dataframe(query_df("SELECT * FROM compras ORDER BY id DESC"), use_container_width=True, hide_index=True)

# ==============================================================================
# ==============================================================================
# MÓDULO 3: UNIDAD FINANCIERA, FACTURACIÓN, TESORERÍA Y ANÁLISIS INTEGRADO
# ==============================================================================
elif modulo_activo == "3. Finanzas, Facturación y Tesorería":
    st.subheader("Unidad Financiera, Facturación, Tesorería y Análisis Integral")
    st.caption("Control atómico con Decimal: Facturación trazable, Tesorería y flujo de caja, CxC/CxP, Costos y Gastos, Presupuesto y Análisis Financiero.")

    df_inv_f = query_df("SELECT * FROM inventario")
    df_cli_f = query_df("SELECT * FROM clientes_cartera ORDER BY nombre_comercio ASC")
    df_prev_f = query_df("SELECT * FROM preventas ORDER BY id DESC")
    df_fac_f = query_df("SELECT * FROM facturas_emitidas ORDER BY id DESC")
    df_cxc_f = query_df("SELECT * FROM movimientos_cxc ORDER BY id DESC")
    df_cxp_f = query_df("SELECT * FROM compromisos_cxp ORDER BY id DESC")
    df_cta_f = query_df("SELECT * FROM cuentas_tesoreria ORDER BY id ASC")
    df_teso_f = query_df("SELECT * FROM tesoreria ORDER BY id DESC")
    df_gc_f = query_df("SELECT * FROM gastos_costos ORDER BY id DESC")
    df_pres_f = query_df("SELECT * FROM presupuestos_partidas ORDER BY id DESC")

    t_fac, t_teso, t_cxc_cxp, t_cg_pres, t_ana = st.tabs([
        "1. Facturación y Control de Ingresos",
        "2. Tesorería y Flujo de Caja",
        "3. CxC y CxP",
        "4. Costos, Gastos y Presupuesto",
        "5. Análisis Financiero"
    ])

    # --------------------------------------------------------------------------
    # 1. FACTURACIÓN Y CONTROL DE INGRESOS
    # --------------------------------------------------------------------------
    with t_fac:
        st.markdown("### 1. Emisión Trazable de Facturas y Estados de Documento (Multi-SKU)")
        st.caption("Agregue uno o varios SKUs a una misma factura, calcule subtotales, descuentos e impuestos con Decimal y emita el comprobante consolidado.")

        if "carrito_factura_items" not in st.session_state:
            st.session_state.carrito_factura_items = []

        # Encabezado general de la factura
        h1, h2, h3 = st.columns(3)
        prev_opts = ["Emisión Directa (Sin Preventa)"]
        if not df_prev_f.empty:
            prev_opts += (
                df_prev_f["codigo_preventa"] + " | " + df_prev_f["cliente_nombre"] +
                " | " + df_prev_f["sku"] + " (" + df_prev_f["estado_preventa"] + ")"
            ).tolist()
        origen_sel = h1.selectbox("Preventa / Orden Vinculada", prev_opts, key="fac_hdr_prev")

        cli_opts_f = []
        if not df_cli_f.empty:
            cli_opts_f = (df_cli_f["codigo_cliente"] + " | " + df_cli_f["nombre_comercio"] + " [" + df_cli_f["rif_cedula"] + "]").tolist()
        cli_fac_sel = h2.selectbox("Cliente Receptor", cli_opts_f if cli_opts_f else ["CLI-GEN | Cliente General [V-00000000]"], key="fac_hdr_cli")
        cond_fac = h3.selectbox("Condición de Pago", ["Contado", "Crédito 7 Días", "Crédito 15 Días", "Crédito 30 Días"], key="fac_hdr_cond")

        h4, h5, h6, h7 = st.columns(4)
        f_emision = h4.date_input("Fecha de Emisión", value=datetime.now().date(), key="fac_hdr_fe")
        f_venc = h5.date_input("Fecha de Vencimiento", value=(datetime.now() + timedelta(days=7)).date(), key="fac_hdr_fv")
        moneda_fac = h6.selectbox("Moneda de Factura", ["USD", "VES"], key="fac_hdr_mon")
        escala_fac = h7.selectbox("Escala de Precio Base", ["Mayorista", "Detal / Bodega"], key="fac_hdr_esc")

        st.markdown("#### Agregar SKU / Producto al Detalle de la Factura")
        with st.form("form_agregar_item_factura"):
            a1, a2, a3, a4, a5 = st.columns([2.4, 1.0, 1.0, 1.0, 1.0])
            prod_fac_sel = a1.selectbox(
                "SKU / Producto",
                df_inv_f["sku"] + " | " + df_inv_f["nombre"] + " (Stock: " + df_inv_f["stock_actual"].astype(str) + ")",
                key="fac_item_sku"
            )
            cant_fac = a2.number_input("Cantidad", min_value=0.01, value=5.0, step=1.0, key="fac_cant")
            desc_fac = a3.number_input("Descuento (%)", min_value=0.0, max_value=50.0, value=0.0, step=1.0, key="fac_desc")
            imp_fac = a4.number_input("IVA / Imp. (%)", min_value=0.0, max_value=30.0, value=16.0, step=1.0, key="fac_imp")
            precio_custom_fac = a5.number_input("Precio Unit. $ (0=Auto)", min_value=0.0, value=0.0, step=0.50, key="fac_precio_custom")

            btn_add_item = st.form_submit_button("Agregar SKU a la Factura", use_container_width=True)

        if btn_add_item:
            sku_f = prod_fac_sel.split(" | ")[0]
            prow = df_inv_f[df_inv_f["sku"] == sku_f].iloc[0]
            if precio_custom_fac > 0:
                precio_u_f = redondear_moneda(a_decimal(precio_custom_fac))
            else:
                precio_u_f = a_decimal(prow["precio_mayor_usd"] if escala_fac == "Mayorista" else prow["precio_detal_usd"])
                if precio_u_f <= Decimal("0.00"):
                    precio_u_f = redondear_moneda(a_decimal(prow["costo_unitario_usd"]) * Decimal("1.25"))

            cant_d = a_decimal(cant_fac)
            desc_d = a_decimal(desc_fac)
            imp_d = a_decimal(imp_fac)
            sub_bruto = redondear_moneda(cant_d * precio_u_f)
            desc_usd = redondear_moneda(sub_bruto * (desc_d / Decimal("100")))
            base_imp = max(Decimal("0.00"), redondear_moneda(sub_bruto - desc_usd))
            imp_usd = redondear_moneda(base_imp * (imp_d / Decimal("100")))
            tot_lin = redondear_moneda(base_imp + imp_usd)

            st.session_state.carrito_factura_items.append({
                "sku": sku_f,
                "descripcion": str(prow["nombre"]),
                "cantidad": float(cant_d),
                "precio_unitario_usd": float(precio_u_f),
                "costo_unitario_usd": float(a_decimal(prow["costo_unitario_usd"])),
                "descuento_pct": float(desc_d),
                "impuesto_pct": float(imp_d),
                "subtotal_usd": float(base_imp),
                "impuesto_usd": float(imp_usd),
                "total_usd": float(tot_lin),
            })
            st.success(f"Agregado `{sku_f}` ({cant_d:g} und. a ${precio_u_f:,.2f}) al detalle de la factura.")
            st.rerun()

        # Vista del detalle multi-SKU actual
        if st.session_state.carrito_factura_items:
            df_cart_fac = pd.DataFrame(st.session_state.carrito_factura_items)
            st.markdown("##### Ítems Cargados en la Factura Actual")
            st.dataframe(
                df_cart_fac[["sku", "descripcion", "cantidad", "precio_unitario_usd", "descuento_pct", "impuesto_pct", "subtotal_usd", "impuesto_usd", "total_usd"]],
                use_container_width=True,
                hide_index=True
            )

            sub_ac = redondear_moneda(sum((a_decimal(x["subtotal_usd"]) for x in st.session_state.carrito_factura_items), Decimal("0.00")))
            imp_ac = redondear_moneda(sum((a_decimal(x["impuesto_usd"]) for x in st.session_state.carrito_factura_items), Decimal("0.00")))
            tot_ac = redondear_moneda(sum((a_decimal(x["total_usd"]) for x in st.session_state.carrito_factura_items), Decimal("0.00")))
            tot_bs_ac = redondear_moneda(tot_ac * a_decimal(tasa_bcv_global, "1.00"))

            m_f1, m_f2, m_f3, m_f4 = st.columns(4)
            m_f1.metric("SKUs en Factura", f"{len(st.session_state.carrito_factura_items)}")
            m_f2.metric("Subtotal Neto ($)", f"${sub_ac:,.2f}")
            m_f3.metric("Total Impuestos ($)", f"${imp_ac:,.2f}")
            m_f4.metric("Total Factura", f"${tot_ac:,.2f}", f"Bs {tot_bs_ac:,.2f}")

            c_act1, c_act2, c_act3 = st.columns([2, 1, 1])
            idx_quitar = c_act1.selectbox(
                "Quitar Línea Específica",
                [f"#{i+1} - {it['sku']} ({it['descripcion']} | Cant: {it['cantidad']})" for i, it in enumerate(st.session_state.carrito_factura_items)],
                key="fac_quitar_sel"
            )
            if c_act2.button("Quitar Ítem", use_container_width=True):
                pos_rm = int(idx_quitar.split("#")[1].split(" - ")[0]) - 1
                st.session_state.carrito_factura_items.pop(pos_rm)
                st.rerun()
            if c_act3.button("Vaciar Ítems", use_container_width=True):
                st.session_state.carrito_factura_items = []
                st.rerun()
        else:
            st.info("Aún no ha agregado SKUs al carrito de la factura. Puede agregar varios productos arriba o emitir directamente con los valores actuales.")

        if st.button("Emitir Factura Multi-SKU (Transacción Atómica)", type="primary", use_container_width=True):
            try:
                doc_orig_txt = "" if origen_sel.startswith("Emisión Directa") else origen_sel.split(" | ")[0]
                cli_cod_txt = cli_fac_sel.split(" | ")[0]
                if not df_cli_f.empty and cli_cod_txt in df_cli_f["codigo_cliente"].values:
                    crow = df_cli_f[df_cli_f["codigo_cliente"] == cli_cod_txt].iloc[0]
                    cli_nom_txt = crow["nombre_comercio"]
                    rif_txt = crow["rif_cedula"]
                else:
                    cli_nom_txt = cli_fac_sel.split(" | ")[1].split(" [")[0] if " | " in cli_fac_sel else "Cliente General"
                    rif_txt = "V-00000000"

                items_a_facturar = list(st.session_state.carrito_factura_items)
                if not items_a_facturar:
                    sku_f = prod_fac_sel.split(" | ")[0]
                    prow = df_inv_f[df_inv_f["sku"] == sku_f].iloc[0]
                    precio_u_f = a_decimal(prow["precio_mayor_usd"] if escala_fac == "Mayorista" else prow["precio_detal_usd"])
                    if precio_u_f <= Decimal("0.00"):
                        precio_u_f = redondear_moneda(a_decimal(prow["costo_unitario_usd"]) * Decimal("1.25"))
                    items_a_facturar = [{
                        "sku": sku_f,
                        "descripcion": str(prow["nombre"]),
                        "cantidad": float(cant_fac),
                        "precio_unitario_usd": float(precio_u_f),
                        "costo_unitario_usd": float(a_decimal(prow["costo_unitario_usd"])),
                        "descuento_pct": float(desc_fac),
                        "impuesto_pct": float(imp_fac),
                    }]

                res_fac = emitir_factura_multisku_atomica(
                    doc_origen=doc_orig_txt,
                    cliente_codigo=cli_cod_txt,
                    cliente_nombre=cli_nom_txt,
                    rif_cliente=rif_txt,
                    fecha_emision_str=f_emision.strftime("%Y-%m-%d"),
                    fecha_vencimiento_str=f_venc.strftime("%Y-%m-%d"),
                    moneda=moneda_fac,
                    tasa_bcv=tasa_bcv_global,
                    items=items_a_facturar,
                    condicion_pago=cond_fac,
                    responsable=user["username"],
                    user_ctx=user
                )

                filas_pdf_fac = []
                for l in res_fac["lineas"]:
                    filas_pdf_fac.append([
                        l["sku"],
                        l["descripcion"][:34],
                        f"{l['cantidad']:,.2f}",
                        f"${l['precio_unitario_usd']:,.2f}",
                        f"${l['total_usd']:,.2f}"
                    ])

                pdf_fac_bytes = generar_pdf_comprobante(
                    titulo_documento="Factura Comercial Multi-SKU",
                    codigo_documento=res_fac["numero_factura"],
                    unidad_emisora="Unidad Financiera y Facturación",
                    datos_generales={
                        "Cliente": f"{cli_cod_txt} - {cli_nom_txt}",
                        "RIF": rif_txt,
                        "Emisión / Venc.": f"{f_emision.strftime('%Y-%m-%d')} / {f_venc.strftime('%Y-%m-%d')}",
                        "Condición / Estado": f"{cond_fac} ({res_fac['estado']})",
                        "Tasa BCV": f"Bs {a_decimal(tasa_bcv_global):,.2f} / $",
                        "Doc. Origen": doc_orig_txt or "Directa"
                    },
                    columnas_tabla=["SKU", "Descripción", "Cant.", "P. Unit ($)", "Total ($)"],
                    filas_tabla=filas_pdf_fac,
                    totales={
                        "SUBTOTAL NETO ($):": f"${res_fac['subtotal_usd']:,.2f}",
                        "IMPUESTOS ($):": f"${res_fac['impuesto_usd']:,.2f}",
                        "TOTAL FACTURA ($):": f"${res_fac['total_usd']:,.2f}",
                        "EQUIVALENTE (Bs):": f"Bs {res_fac['total_bs']:,.2f}"
                    },
                    observaciones=f"Factura emitida con {len(res_fac['lineas'])} SKU(s) bajo aritmética exacta Decimal.",
                    firmas=("Facturación y Finanzas", "Cliente / Receptor", "Contraloría Social")
                )
                st.session_state["ultimo_pdf_factura_emitida"] = pdf_fac_bytes
                st.session_state["ultimo_cod_factura_emitida"] = res_fac["numero_factura"]
                st.session_state.carrito_factura_items = []
                st.success(f"Factura `{res_fac['numero_factura']}` emitida con {len(res_fac['lineas'])} SKU(s) por ${res_fac['total_usd']:,.2f} (Bs {res_fac['total_bs']:,.2f}) — Estado: `{res_fac['estado']}`.")
                st.rerun()
            except Exception as e:
                st.error(f"Error al emitir factura: {e}")

        if st.session_state.get("ultimo_pdf_factura_emitida") is not None:
            st.download_button(
                label="Descargar Última Factura Emitida (PDF)",
                data=st.session_state["ultimo_pdf_factura_emitida"],
                file_name=f"{st.session_state.get('ultimo_cod_factura_emitida', 'Factura')}.pdf",
                mime="application/pdf",
                key="btn_descargar_ultima_factura_pdf"
            )

        st.markdown("#### Libro de Facturas Emitidas y Control de Estados")
        st.dataframe(df_fac_f, use_container_width=True, hide_index=True)

        df_fac_items_all = query_df("SELECT * FROM factura_items ORDER BY id DESC")
        if not df_fac_items_all.empty:
            st.markdown("##### Detalle por SKU de Facturas Emitidas")
            st.dataframe(df_fac_items_all, use_container_width=True, hide_index=True)

        if not df_fac_f.empty:
            st.markdown("##### Anulación o Emisión de Nota de Crédito")
            with st.form("form_anular_nc_f"):
                c1, c2, c3 = st.columns(3)
                fac_activas = df_fac_f[df_fac_f["estado_documento"] != "ANULADA"]
                if not fac_activas.empty:
                    fac_sel_an = c1.selectbox("Factura a Afectar", fac_activas["numero_factura"] + " | " + fac_activas["cliente_nombre"] + " [" + fac_activas["estado_documento"] + "]")
                    acc_doc_f = c2.selectbox("Acción de Documento", ["Anular Factura", "Emitir Nota de Crédito"])
                    motivo_doc_f = c3.text_input("Motivo / Justificación Contable", placeholder="Ej: Devolución de mercancía o error en orden")
                    btn_afectar_fac = st.form_submit_button("Aplicar Anulación / Nota de Crédito", type="primary")
                else:
                    st.info("No hay facturas activas disponibles para anular o emitir nota de crédito.")
                    btn_afectar_fac = False

            if btn_afectar_fac:
                try:
                    num_f_sel = fac_sel_an.split(" | ")[0]
                    anular_o_emitir_nota_credito_factura(num_f_sel, acc_doc_f, motivo_doc_f, tasa_bcv_global, user["username"], user)
                    st.success(f"Documento `{num_f_sel}` actualizado bajo `{acc_doc_f}`.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    # --------------------------------------------------------------------------
    # 2. TESORERÍA Y FLUJO DE CAJA
    # --------------------------------------------------------------------------
    with t_teso:
        st.markdown("### 2. Cuentas, Cajas, Canales Digitales y Flujo de Caja")
        st.caption("Centraliza saldos por cuenta/canal, registra ingresos/egresos con comprobante PDF y proyecta flujo de caja a 7 y 30 días.")

        if not df_cta_f.empty:
            cols_cta = st.columns(len(df_cta_f))
            for idx_c, (_, crow_t) in enumerate(df_cta_f.iterrows()):
                s_dec = redondear_moneda(a_decimal(crow_t["saldo_actual"]))
                eq_txt = f"Bs {redondear_moneda(s_dec * a_decimal(tasa_bcv_global)):,.2f}" if crow_t["moneda"] == "USD" else f"${redondear_moneda(s_dec / max(Decimal('1.00'), a_decimal(tasa_bcv_global))):,.2f}"
                cols_cta[idx_c].metric(f"{crow_t['codigo_cuenta']} ({crow_t['moneda']})", f"{s_dec:,.2f}", eq_txt)

        st.markdown("#### Registrar Movimiento de Tesorería y Emitir Comprobante PDF")
        with st.form("form_tesoreria_f"):
            c1, c2, c3 = st.columns(3)
            tipo_op = c1.selectbox("Tipo de Operación", ["Ingreso", "Egreso"], key="teso_f_tipo")
            cta_opts = df_cta_f["codigo_cuenta"] + " | " + df_cta_f["nombre_cuenta"] if not df_cta_f.empty else ["CTA-CAJA-USD | Caja Efectivo Divisas Sede"]
            cta_sel_f = c2.selectbox("Cuenta / Caja / Canal", cta_opts)
            tercero = c3.text_input("Cliente / Productor / Beneficiario", placeholder="Ej: Comercializadora Yaracuy C.A.")

            c4, c5, c6, c7 = st.columns(4)
            doc_ref = c4.text_input("Documento Asociado", placeholder="Ej: FAC-2026-0001 o COMP-2026-0001")
            metodo = c5.selectbox("Instrumento de Pago", ["Pago Móvil", "Transferencia Bancaria", "Divisas Efectivo ($)", "Bolívares Efectivo", "Billetera / Digital"])
            referencia = c6.text_input("N° Referencia / Comprobante", placeholder="Ej: 00984512")
            monto_usd = c7.number_input("Monto de la Operación ($ USD)", min_value=0.01, value=100.0, step=10.0)

            concepto = st.text_input("Concepto Detallado del Movimiento", placeholder="Ej: Conciliación de cobro / desembolso operativo")
            submitted_teso = st.form_submit_button("Registrar en Tesorería y Generar Comprobante PDF", type="primary")

        if submitted_teso:
            if not tercero.strip() or not concepto.strip():
                st.error("Debe completar el Tercero y el Concepto del movimiento.")
            else:
                monto_d = redondear_moneda(a_decimal(monto_usd))
                tasa_d = a_decimal(tasa_bcv_global, "1.00")
                monto_bs = redondear_moneda(monto_d * tasa_d)
                cod_cta_sel = cta_sel_f.split(" | ")[0]
                fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")

                conn = get_connection()
                cur = conn.cursor()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    cod_rec = generar_codigo("REC", "tesoreria")
                    cur.execute("""
                        INSERT INTO tesoreria (codigo_recibo, fecha, tipo_operacion, concepto, doc_asociado, tercero, metodo_pago, referencia, monto_usd, tasa_bcv, monto_bs, responsable)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (cod_rec, fecha_str, tipo_op, concepto.strip(), doc_ref.strip(), tercero.strip(), metodo, referencia.strip(), float(monto_d), float(tasa_d), float(monto_bs), user["username"]))

                    delta_cta = float(monto_d) if tipo_op == "Ingreso" else float(-monto_d)
                    cur.execute("UPDATE cuentas_tesoreria SET saldo_actual = ROUND(saldo_actual + ?, 2) WHERE codigo_cuenta = ?", (delta_cta, cod_cta_sel))

                    if tipo_op == "Ingreso" and doc_ref.strip().startswith("VTA-"):
                        cur.execute("UPDATE ventas SET estado_cobro = 'Cobrado' WHERE codigo_venta = ?", (doc_ref.strip(),))
                    elif tipo_op == "Egreso" and doc_ref.strip().startswith("COMP-"):
                        cur.execute("UPDATE compras SET estado_pago = 'Pagado' WHERE codigo_compra = ?", (doc_ref.strip(),))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    st.error(f"Error en transacción de tesorería: {e}")
                    conn.close()
                    st.stop()
                conn.close()

                registrar_auditoria_comercial(user, "Tesorería", f"TESO_{tipo_op.upper()}", cod_rec, f"{cod_cta_sel} | ${monto_d:,.2f} | {concepto.strip()}")

                titulo_pdf = "Comprobante Oficial de Ingreso y Cobranza" if tipo_op == "Ingreso" else "Comprobante Oficial de Egreso y Pago"
                pdf_bytes = generar_pdf_comprobante(
                    titulo_documento=titulo_pdf,
                    codigo_documento=cod_rec,
                    unidad_emisora="Unidad Financiera, Facturación y Tesorería",
                    datos_generales={
                        "Tipo Movimiento": tipo_op.upper(),
                        "Cuenta / Canal": cod_cta_sel,
                        "Fecha Contable": fecha_str,
                        "Tercero / Razón Social": tercero.strip(),
                        "Doc. Asociado": doc_ref.strip() or "N/A",
                        "Método / Ref.": f"{metodo} | {referencia.strip() or 'N/A'}"
                    },
                    columnas_tabla=["Código", "Concepto Contable", "Tasa BCV", "Monto ($)", "Equivalente (Bs)"],
                    filas_tabla=[[cod_rec, concepto.strip(), f"Bs {tasa_d:,.2f}", f"${monto_d:,.2f}", f"Bs {monto_bs:,.2f}"]],
                    totales={
                        "MONTO LIQUIDADO ($):": f"${monto_d:,.2f}",
                        "MONTO LIQUIDADO (Bs):": f"Bs {monto_bs:,.2f}"
                    },
                    observaciones="Operación asentada con integridad transaccional en tesorería.",
                    firmas=("Administrador / Tesorero", "Recibido / Conforme", "Contraloría Social")
                )
                st.session_state.ultimo_pdf_tesoreria = pdf_bytes
                st.session_state.ultimo_cod_tesoreria = cod_rec
                st.success(f"Comprobante `{cod_rec}` emitido por ${monto_d:,.2f} (Bs {monto_bs:,.2f}).")
                st.rerun()

        if st.session_state.ultimo_pdf_tesoreria is not None:
            st.download_button(
                label="Descargar Comprobante Financiero (PDF)",
                data=st.session_state.ultimo_pdf_tesoreria,
                file_name=f"{st.session_state.ultimo_cod_tesoreria}.pdf",
                mime="application/pdf",
                key="btn_descargar_comprobante_teso_f"
            )

        st.markdown("#### Proyección de Flujo de Caja (Corto Plazo)")
        saldo_disp_teso = redondear_moneda(sum((a_decimal(r["saldo_actual"]) for _, r in df_cta_f.iterrows()), Decimal("0.00"))) if not df_cta_f.empty else Decimal("0.00")
        cxc_proy = redondear_moneda(sum((a_decimal(r["saldo_pendiente_usd"]) for _, r in df_fac_f[df_fac_f["estado_documento"] != "ANULADA"].iterrows()), Decimal("0.00"))) if not df_fac_f.empty else Decimal("0.00")
        cxp_proy = redondear_moneda(sum((a_decimal(r["saldo_pendiente_usd"]) for _, r in df_cxp_f[df_cxp_f["estado_cxp"] != "PAGADA"].iterrows()), Decimal("0.00"))) if not df_cxp_f.empty else Decimal("0.00")
        flujo_proyectado = redondear_moneda(saldo_disp_teso + cxc_proy - cxp_proy)

        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Saldo Disponible Cuentas ($)", f"${saldo_disp_teso:,.2f}")
        p2.metric("(+) Cobros Esperados CxC ($)", f"${cxc_proy:,.2f}")
        p3.metric("(-) Compromisos Pendientes CxP ($)", f"${cxp_proy:,.2f}")
        p4.metric("Flujo Neto Proyectado ($)", f"${flujo_proyectado:,.2f}")

        st.markdown("#### Libro de Caja, Movimientos y Conciliación")
        st.dataframe(df_teso_f, use_container_width=True, hide_index=True)

    # --------------------------------------------------------------------------
    # 3. CUENTAS POR COBRAR (CxC) Y CUENTAS POR PAGAR (CxP)
    # --------------------------------------------------------------------------
    with t_cxc_cxp:
        st.markdown("### 3. Cuentas por Cobrar (CxC) y Cuentas por Pagar (CxP)")

        col_cxc, col_cxp = st.columns(2)

        with col_cxc:
            st.markdown("#### Cuentas por Cobrar (Antigüedad, Retenciones y Cobros)")
            fac_cxc_pend = df_fac_f[(df_fac_f["estado_documento"] != "ANULADA") & (df_fac_f["saldo_pendiente_usd"] > 0)].copy() if not df_fac_f.empty else pd.DataFrame()
            if not fac_cxc_pend.empty:
                hoy_d = datetime.now().date()
                antig_list = []
                for _, fr in fac_cxc_pend.iterrows():
                    try:
                        fv = datetime.strptime(str(fr["fecha_vencimiento"])[:10], "%Y-%m-%d").date()
                        dias_venc = (hoy_d - fv).days
                    except Exception:
                        dias_venc = 0
                    bucket = "Por Vencer / Vigente" if dias_venc <= 0 else ("1-15 Días Vencido" if dias_venc <= 15 else ("16-30 Días Vencido" if dias_venc <= 30 else "+30 Días en Mora"))
                    antig_list.append({
                        "numero_factura": fr["numero_factura"],
                        "cliente": fr["cliente_nombre"],
                        "vencimiento": fr["fecha_vencimiento"],
                        "dias_mora": max(0, dias_venc),
                        "antiguedad": bucket,
                        "saldo_usd": fr["saldo_pendiente_usd"]
                    })
                st.dataframe(pd.DataFrame(antig_list), use_container_width=True, hide_index=True)

                with st.form("form_cobro_cxc_f"):
                    f_cob_sel = st.selectbox("Factura Pendiente", fac_cxc_pend["numero_factura"] + " | " + fac_cxc_pend["cliente_nombre"] + " (Saldo: $" + fac_cxc_pend["saldo_pendiente_usd"].astype(str) + ")")
                    c_abono, c_ret = st.columns(2)
                    monto_ab_cxc = c_abono.number_input("Abono Efectivo / Transferencia ($)", min_value=0.0, value=25.0, step=5.0)
                    monto_ret_cxc = c_ret.number_input("Retención Fiscal / IVA ($)", min_value=0.0, value=0.0, step=1.0)
                    cta_cob_sel = st.selectbox("Cuenta Destino", df_cta_f["codigo_cuenta"] if not df_cta_f.empty else ["CTA-CAJA-USD"])
                    ref_cob_cxc = st.text_input("Referencia de Cobro / Comprobante Retención", placeholder="Ej: REF-4410")
                    btn_cob_cxc = st.form_submit_button("Registrar Cobro / Retención CxC", type="primary")

                if btn_cob_cxc:
                    try:
                        num_f_c = f_cob_sel.split(" | ")[0]
                        res_c = registrar_cobro_cxc_atomico(num_f_c, monto_ab_cxc, monto_ret_cxc, cta_cob_sel, "Cobranza CxC", ref_cob_cxc, tasa_bcv_global, user["username"], user)
                        st.success(f"Cobro aplicado a `{num_f_c}` (Recibo `{res_c['codigo_recibo']}`). Saldo restante: ${res_c['nuevo_saldo']:,.2f} (`{res_c['estado']}`).")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
            else:
                st.info("No hay facturas pendientes de cobro en CxC.")

            st.markdown("##### Historial de Movimientos CxC")
            st.dataframe(df_cxc_f, use_container_width=True, hide_index=True)

        with col_cxp:
            st.markdown("#### Cuentas por Pagar (Compromisos, Prioridad y Desembolsos)")
            with st.form("form_crear_cxp_f"):
                p_nom = st.text_input("Proveedor / Acreedor", placeholder="Ej: Cooperativa Cafetalera Nirgua")
                p_rif, p_doc = st.columns(2)
                rif_p_f = p_rif.text_input("RIF Proveedor", placeholder="Ej: J-40556677-8")
                doc_c_f = p_doc.text_input("Doc. Compra / Orden", placeholder="Ej: COMP-2026-0001")
                p_fv, p_pri, p_pp = st.columns(3)
                fv_cxp = p_fv.date_input("Vencimiento", value=(datetime.now() + timedelta(days=15)).date())
                pri_cxp = p_pri.selectbox("Prioridad", ["Alta", "Media", "Baja"])
                pp_cxp = p_pp.number_input("Desc. Pronto Pago (%)", min_value=0.0, max_value=25.0, value=0.0, step=0.5)
                m_cxp = st.number_input("Monto del Compromiso ($ USD)", min_value=0.01, value=250.0, step=25.0)
                btn_add_cxp = st.form_submit_button("Registrar Compromiso CxP", type="primary")

            if btn_add_cxp:
                if not p_nom.strip():
                    st.error("Indique el nombre del proveedor.")
                else:
                    try:
                        cod_c_new = registrar_cxp_y_pago_atomico(p_nom, rif_p_f or "V-00000000", doc_c_f, fv_cxp.strftime("%Y-%m-%d"), pri_cxp, m_cxp, pp_cxp, user["username"], user)
                        st.success(f"Compromiso `{cod_c_new}` registrado en CxP.")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

            st.dataframe(df_cxp_f, use_container_width=True, hide_index=True)

            cxp_pend = df_cxp_f[df_cxp_f["saldo_pendiente_usd"] > 0] if not df_cxp_f.empty else pd.DataFrame()
            if not cxp_pend.empty:
                with st.form("form_pagar_cxp_f"):
                    cxp_s = st.selectbox("Compromiso Pendiente", cxp_pend["codigo_cxp"] + " | " + cxp_pend["proveedor"] + " (Saldo: $" + cxp_pend["saldo_pendiente_usd"].astype(str) + ")")
                    pago_cxp_m = st.number_input("Monto a Desembolsar ($ USD)", min_value=0.01, value=50.0, step=10.0)
                    cta_pag_s = st.selectbox("Cuenta Origen", df_cta_f["codigo_cuenta"] if not df_cta_f.empty else ["CTA-CAJA-USD"], key="cxp_cta")
                    ref_pag_c = st.text_input("Referencia de Pago", placeholder="Ej: TRANSF-8821")
                    btn_pag_cxp = st.form_submit_button("Ejecutar Pago a Proveedor")

                if btn_pag_cxp:
                    try:
                        cod_cxp_target = cxp_s.split(" | ")[0]
                        abonar_cxp_atomico(cod_cxp_target, pago_cxp_m, cta_pag_s, "Transferencia / Pago Proveedor", ref_pag_c, tasa_bcv_global, user["username"], user)
                        st.success(f"Desembolso aplicado a `{cod_cxp_target}`.")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    # --------------------------------------------------------------------------
    # 4. COSTOS, GASTOS Y PRESUPUESTO
    # --------------------------------------------------------------------------
    with t_cg_pres:
        st.markdown("### 4. Clasificación de Costos/Gastos y Control Presupuestario")

        col_cg, col_pr = st.columns([1.2, 1])

        with col_cg:
            st.markdown("#### Registrar Costo Directo/Indirecto o Gasto Operativo/Administrativo")
            with st.form("form_gasto_costo_f"):
                c1, c2, c3 = st.columns(3)
                clasif_cg = c1.selectbox("Clasificación", ["Gasto Operativo", "Gasto Administrativo", "Costo Directo", "Costo Indirecto"])
                nat_cg = c2.selectbox("Naturaleza", ["Fijo", "Variable"])
                cc_cg = c3.selectbox("Centro de Costo", ["Operaciones y Logística", "Administración y Gestión", "Compras y Acopio", "Comercialización y Ventas"])

                c4, c5, c6 = st.columns(3)
                rubro_cg = c4.text_input("Rubro / SKU / Categoría", value="Operación General")
                monto_cg = c5.number_input("Monto ($ USD)", min_value=0.01, value=75.0, step=10.0)
                cta_cg = c6.selectbox("Cuenta para Desembolso", df_cta_f["codigo_cuenta"] if not df_cta_f.empty else ["CTA-CAJA-USD"], key="cg_cta")

                conc_cg = st.text_input("Concepto del Gasto o Costo", placeholder="Ej: Flete ruta San Felipe - Nirgua / Mantenimiento / Servicio")
                desc_c_bool = st.checkbox("Registrar salida inmediata en Tesorería", value=True)
                btn_cg = st.form_submit_button("Guardar Costo / Gasto", type="primary")

            if btn_cg:
                try:
                    cod_g_new = registrar_gasto_o_costo_atomico(clasif_cg, nat_cg, cc_cg, rubro_cg, conc_cg, monto_cg, tasa_bcv_global, cta_cg, desc_c_bool, user["username"], user)
                    st.success(f"Registro `{cod_g_new}` guardado correctamente.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

            st.dataframe(df_gc_f, use_container_width=True, hide_index=True)

        with col_pr:
            st.markdown("#### Presupuesto vs. Ejecución Real por Centro de Costo")
            with st.form("form_presupuesto_f"):
                per_p = st.text_input("Período (YYYY-MM)", value=datetime.now().strftime("%Y-%m"))
                cc_p = st.selectbox("Unidad / Centro de Costo", ["Operaciones y Logística", "Administración y Gestión", "Compras y Acopio", "Comercialización y Ventas"], key="pres_cc")
                rub_p = st.text_input("Partida / Rubro", value="Presupuesto Operativo Mensual")
                m_pres_p = st.number_input("Monto Presupuestado ($ USD)", min_value=0.0, value=1500.0, step=100.0)
                btn_p = st.form_submit_button("Guardar Partida Presupuestaria")

            if btn_p:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO presupuestos_partidas (periodo, unidad_o_centro, rubro, monto_presupuestado_usd, actualizado_por, fecha) VALUES (?, ?, ?, ?, ?, ?)",
                    (per_p.strip(), cc_p, rub_p.strip(), float(m_pres_p), user["username"], datetime.now().strftime("%Y-%m-%d %H:%M"))
                )
                conn.commit()
                conn.close()
                registrar_auditoria_comercial(user, "Presupuesto", "GUARDAR_PARTIDA", cc_p, f"{per_p} | ${m_pres_p:,.2f}")
                st.success("Partida presupuestaria registrada.")
                st.rerun()

            if not df_pres_f.empty:
                pres_grp = df_pres_f.groupby("unidad_o_centro", as_index=False)["monto_presupuestado_usd"].sum()
                real_grp = df_gc_f.groupby("centro_costo", as_index=False)["monto_usd"].sum() if not df_gc_f.empty else pd.DataFrame(columns=["centro_costo", "monto_usd"])
                comp_df = pd.merge(pres_grp, real_grp, left_on="unidad_o_centro", right_on="centro_costo", how="left").fillna({"monto_usd": 0.0})
                comp_df["desviacion_usd"] = comp_df["monto_presupuestado_usd"] - comp_df["monto_usd"]
                comp_df["ejecucion_pct"] = comp_df.apply(lambda r: round((r["monto_usd"] / r["monto_presupuestado_usd"] * 100.0), 1) if r["monto_presupuestado_usd"] > 0 else 0.0, axis=1)
                st.dataframe(comp_df[["unidad_o_centro", "monto_presupuestado_usd", "monto_usd", "desviacion_usd", "ejecucion_pct"]], use_container_width=True, hide_index=True)

    # --------------------------------------------------------------------------
    # 5. ANÁLISIS FINANCIERO (LIQUIDEZ, RENTABILIDAD, GRÁFICOS Y PDF EJECUTIVO)
    # --------------------------------------------------------------------------
    with t_ana:
        st.markdown("### 5. Análisis Financiero: Liquidez, Capital de Trabajo, Rentabilidad y Reportes")

        facturas_obj = []
        if not df_fac_f.empty:
            for _, r in df_fac_f.iterrows():
                try:
                    fe_d = datetime.strptime(str(r["fecha_emision"])[:10], "%Y-%m-%d").date()
                except Exception:
                    fe_d = datetime.now().date()
                try:
                    fv_d = datetime.strptime(str(r["fecha_vencimiento"])[:10], "%Y-%m-%d").date()
                except Exception:
                    fv_d = fe_d
                linea = LineaFactura(
                    descripcion=str(r["descripcion"]),
                    cantidad=a_decimal(r["cantidad"]),
                    precio_unitario=a_decimal(r["precio_unitario_usd"]),
                    costo_unitario=a_decimal(r["costo_unitario_usd"]),
                    impuesto_tasa=a_decimal(r["impuesto_pct"]) / Decimal("100")
                )
                fac_obj = Factura(
                    numero=str(r["numero_factura"]),
                    cliente_id=str(r["cliente_codigo"]),
                    fecha_emision=fe_d,
                    fecha_vencimiento=fv_d,
                    moneda=str(r["moneda"]),
                    tasa_cambio=a_decimal(r["tasa_cambio"], "1.00"),
                    lineas=[linea],
                    monto_cobrado=a_decimal(r["monto_cobrado_usd"]),
                    estado=str(r["estado_documento"])
                )
                facturas_obj.append(fac_obj)

        gastos_op_tot = redondear_moneda(sum((a_decimal(r["monto_usd"]) for _, r in df_gc_f.iterrows()), Decimal("0.00"))) if not df_gc_f.empty else Decimal("0.00")
        pres_asig_tot = redondear_moneda(sum((a_decimal(r["monto_presupuestado_usd"]) for _, r in df_pres_f.iterrows()), Decimal("0.00"))) if not df_pres_f.empty else Decimal("0.00")

        svc_ana = AnalisisFinancieroService()
        res_ana = svc_ana.resumen_operativo(facturas_obj, gastos_op_tot, pres_asig_tot)

        saldo_caja_act = redondear_moneda(sum((a_decimal(r["saldo_actual"]) for _, r in df_cta_f.iterrows()), Decimal("0.00"))) if not df_cta_f.empty else Decimal("0.00")
        cxp_pend_act = redondear_moneda(sum((a_decimal(r["saldo_pendiente_usd"]) for _, r in df_cxp_f[df_cxp_f["estado_cxp"] != "PAGADA"].iterrows()), Decimal("0.00"))) if not df_cxp_f.empty else Decimal("0.00")
        capital_trabajo = redondear_moneda(saldo_caja_act + res_ana["cxc_pendiente"] - cxp_pend_act)
        razon_liquidez = redondear_moneda((saldo_caja_act + res_ana["cxc_pendiente"]) / cxp_pend_act) if cxp_pend_act > Decimal("0.00") else Decimal("99.99")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Ventas Netas Facturadas ($)", f"${res_ana['ventas_netas']:,.2f}", f"Costo: ${res_ana['costo_ventas']:,.2f}")
        m2.metric("Utilidad Bruta ($)", f"${res_ana['utilidad_bruta']:,.2f}", f"Gastos/Costos: ${res_ana['gastos_operativos']:,.2f}")
        m3.metric("Utilidad Operativa ($)", f"${res_ana['utilidad_operativa']:,.2f}", f"Ejecución Presup.: {res_ana['ejecucion_presupuestaria_pct']}%")
        m4.metric("Capital de Trabajo ($)", f"${capital_trabajo:,.2f}", f"Liquidez (Act.Corto/CxP): {razon_liquidez}")

        st.markdown("---")
        st.markdown("#### Gráficos Interactivos: Desviación Presupuestaria y Flujo de Caja")
        g_col1, g_col2 = st.columns(2)

        with g_col1:
            st.markdown("##### Desviación Presupuestaria por Centro de Costo ($ USD)")
            if not df_pres_f.empty:
                p_grp = df_pres_f.groupby("unidad_o_centro", as_index=False)["monto_presupuestado_usd"].sum()
                r_grp = df_gc_f.groupby("centro_costo", as_index=False)["monto_usd"].sum() if not df_gc_f.empty else pd.DataFrame(columns=["centro_costo", "monto_usd"])
                df_desv = pd.merge(p_grp, r_grp, left_on="unidad_o_centro", right_on="centro_costo", how="left").fillna({"monto_usd": 0.0})
                df_desv["desviacion_usd"] = df_desv["monto_presupuestado_usd"] - df_desv["monto_usd"]

                try:
                    import plotly.graph_objects as go
                    fig_p = go.Figure()
                    fig_p.add_trace(go.Bar(
                        name="Presupuestado ($)",
                        x=df_desv["unidad_o_centro"],
                        y=df_desv["monto_presupuestado_usd"],
                        marker_color="#2D6A4F"
                    ))
                    fig_p.add_trace(go.Bar(
                        name="Ejecutado Real ($)",
                        x=df_desv["unidad_o_centro"],
                        y=df_desv["monto_usd"],
                        marker_color="#9B2226"
                    ))
                    fig_p.add_trace(go.Scatter(
                        name="Desviación Neta ($)",
                        x=df_desv["unidad_o_centro"],
                        y=df_desv["desviacion_usd"],
                        mode="lines+markers",
                        marker=dict(size=8, color="#1D3557"),
                        line=dict(width=2.5, dash="dot", color="#1D3557")
                    ))
                    fig_p.update_layout(
                        barmode="group",
                        xaxis_title="Centro de Costo / Unidad",
                        yaxis_title="Monto ($ USD)",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                        height=360,
                        margin=dict(l=20, r=20, t=40, b=20)
                    )
                    st.plotly_chart(fig_p, use_container_width=True)
                except Exception:
                    st.bar_chart(df_desv.set_index("unidad_o_centro")[["monto_presupuestado_usd", "monto_usd", "desviacion_usd"]])
            else:
                st.info("No hay partidas presupuestarias registradas para graficar desviaciones.")

        with g_col2:
            st.markdown("##### Evolución del Flujo de Caja (Entradas, Salidas y Saldo Acumulado)")
            if not df_teso_f.empty:
                df_fc = df_teso_f.copy()
                df_fc["fecha_dia"] = df_fc["fecha"].astype(str).str.slice(0, 10)
                df_fc["entrada_usd"] = df_fc.apply(lambda r: float(r["monto_usd"]) if str(r["tipo_operacion"]).lower() == "ingreso" else 0.0, axis=1)
                df_fc["salida_usd"] = df_fc.apply(lambda r: float(r["monto_usd"]) if str(r["tipo_operacion"]).lower() == "egreso" else 0.0, axis=1)
                df_fc_dia = df_fc.groupby("fecha_dia", as_index=False).agg(
                    entradas=("entrada_usd", "sum"),
                    salidas=("salida_usd", "sum")
                ).sort_values("fecha_dia")
                df_fc_dia["flujo_neto_dia"] = df_fc_dia["entradas"] - df_fc_dia["salidas"]
                df_fc_dia["saldo_acumulado"] = df_fc_dia["flujo_neto_dia"].cumsum()

                try:
                    import plotly.graph_objects as go
                    fig_fc = go.Figure()
                    fig_fc.add_trace(go.Bar(
                        name="Entradas ($)",
                        x=df_fc_dia["fecha_dia"],
                        y=df_fc_dia["entradas"],
                        marker_color="#2A9D8F"
                    ))
                    fig_fc.add_trace(go.Bar(
                        name="Salidas ($)",
                        x=df_fc_dia["fecha_dia"],
                        y=df_fc_dia["salidas"],
                        marker_color="#E76F51"
                    ))
                    fig_fc.add_trace(go.Scatter(
                        name="Saldo Acumulado ($)",
                        x=df_fc_dia["fecha_dia"],
                        y=df_fc_dia["saldo_acumulado"],
                        mode="lines+markers",
                        line=dict(color="#1B4332", width=3),
                        marker=dict(size=7)
                    ))
                    fig_fc.update_layout(
                        barmode="group",
                        xaxis_title="Fecha de Operación",
                        yaxis_title="Monto ($ USD)",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                        height=360,
                        margin=dict(l=20, r=20, t=40, b=20)
                    )
                    st.plotly_chart(fig_fc, use_container_width=True)
                except Exception:
                    st.line_chart(df_fc_dia.set_index("fecha_dia")[["entradas", "salidas", "saldo_acumulado"]])
            else:
                st.info("No hay movimientos de tesorería registrados para graficar el flujo de caja.")

        st.markdown("---")
        st.markdown("#### Reporte PDF Ejecutivo y Exportación Financiera (.XLSX)")
        exp_c1, exp_c2 = st.columns(2)

        with exp_c1:
            if st.button("Generar Reporte PDF Ejecutivo Financiero", type="primary", use_container_width=True):
                cod_rep_fin = f"REP-FIN-{datetime.now().strftime('%Y%m%d-%H%M')}"
                tasa_dec = a_decimal(tasa_bcv_global)
                filas_resumen_pdf = [
                    ["[RESUMEN] Ventas Netas Facturadas", f"${res_ana['ventas_netas']:,.2f}", f"Bs {redondear_moneda(res_ana['ventas_netas'] * tasa_dec):,.2f}"],
                    ["[RESUMEN] Costo Directo de Ventas", f"${res_ana['costo_ventas']:,.2f}", f"Bs {redondear_moneda(res_ana['costo_ventas'] * tasa_dec):,.2f}"],
                    ["[RESUMEN] Utilidad Bruta", f"${res_ana['utilidad_bruta']:,.2f}", f"Bs {redondear_moneda(res_ana['utilidad_bruta'] * tasa_dec):,.2f}"],
                    ["[RESUMEN] Gastos / Costos Registrados", f"${res_ana['gastos_operativos']:,.2f}", f"Bs {redondear_moneda(res_ana['gastos_operativos'] * tasa_dec):,.2f}"],
                    ["[RESUMEN] Utilidad Operativa", f"${res_ana['utilidad_operativa']:,.2f}", f"Bs {redondear_moneda(res_ana['utilidad_operativa'] * tasa_dec):,.2f}"],
                    ["[RESUMEN] Cuentas por Cobrar Pendientes", f"${res_ana['cxc_pendiente']:,.2f}", f"Bs {redondear_moneda(res_ana['cxc_pendiente'] * tasa_dec):,.2f}"],
                    ["[RESUMEN] Cuentas por Pagar Pendientes", f"${cxp_pend_act:,.2f}", f"Bs {redondear_moneda(cxp_pend_act * tasa_dec):,.2f}"],
                    ["[RESUMEN] Saldo Disponible en Tesorería", f"${saldo_caja_act:,.2f}", f"Bs {redondear_moneda(saldo_caja_act * tasa_dec):,.2f}"],
                    ["[RESUMEN] Capital de Trabajo Neto", f"${capital_trabajo:,.2f}", f"Bs {redondear_moneda(capital_trabajo * tasa_dec):,.2f}"]
                ]

                # Desglose de Rentabilidad por SKU
                fac_validas_df = df_fac_f[df_fac_f["estado_documento"] != "ANULADA"].copy() if not df_fac_f.empty else pd.DataFrame()
                df_f_items_rep = query_df("SELECT * FROM factura_items")
                if not fac_validas_df.empty:
                    sku_stats = {}
                    validas_nums = set(fac_validas_df["numero_factura"].astype(str).tolist())
                    items_validos = df_f_items_rep[df_f_items_rep["numero_factura"].astype(str).isin(validas_nums)] if not df_f_items_rep.empty else pd.DataFrame()
                    facs_con_items = set(items_validos["numero_factura"].astype(str).tolist()) if not items_validos.empty else set()

                    if not items_validos.empty:
                        for _, r_it in items_validos.iterrows():
                            s_cod = str(r_it["sku"])
                            s_nom = str(r_it["descripcion"])
                            v_neta = redondear_moneda(a_decimal(r_it["subtotal_usd"]))
                            c_tot = redondear_moneda(a_decimal(r_it["cantidad"]) * a_decimal(r_it["costo_unitario_usd"]))
                            if s_cod not in sku_stats:
                                sku_stats[s_cod] = {"nombre": s_nom, "ventas": Decimal("0.00"), "costo": Decimal("0.00")}
                            sku_stats[s_cod]["ventas"] = redondear_moneda(sku_stats[s_cod]["ventas"] + v_neta)
                            sku_stats[s_cod]["costo"] = redondear_moneda(sku_stats[s_cod]["costo"] + c_tot)

                    for _, r_sku in fac_validas_df[~fac_validas_df["numero_factura"].astype(str).isin(facs_con_items)].iterrows():
                        s_cod = str(r_sku["sku"])
                        s_nom = str(r_sku["descripcion"])
                        v_neta = redondear_moneda(a_decimal(r_sku["subtotal_usd"]))
                        c_tot = redondear_moneda(a_decimal(r_sku["cantidad"]) * a_decimal(r_sku["costo_unitario_usd"]))
                        if s_cod not in sku_stats:
                            sku_stats[s_cod] = {"nombre": s_nom, "ventas": Decimal("0.00"), "costo": Decimal("0.00")}
                        sku_stats[s_cod]["ventas"] = redondear_moneda(sku_stats[s_cod]["ventas"] + v_neta)
                        sku_stats[s_cod]["costo"] = redondear_moneda(sku_stats[s_cod]["costo"] + c_tot)

                    for s_cod, st_data in sorted(sku_stats.items(), key=lambda x: x[1]["ventas"] - x[1]["costo"], reverse=True):
                        util_sku = redondear_moneda(st_data["ventas"] - st_data["costo"])
                        margen_sku = redondear_moneda((util_sku / st_data["ventas"]) * Decimal("100")) if st_data["ventas"] > Decimal("0.00") else Decimal("0.00")
                        filas_resumen_pdf.append([
                            f"[SKU {s_cod}] {st_data['nombre'][:28]} (Ventas: ${st_data['ventas']:,.2f})",
                            f"${util_sku:,.2f}",
                            f"Margen: {margen_sku}%"
                        ])
                else:
                    filas_resumen_pdf.append(["[RENTABILIDAD SKU] Sin facturas activas registradas", "$0.00", "Margen: 0.00%"])

                # Desglose de Antigüedad de Cartera (CxC)
                hoy_corte = datetime.now().date()
                buckets_cxc = {
                    "Por Vencer / Vigente (0 días)": {"monto": Decimal("0.00"), "docs": 0},
                    "Vencida 1 - 15 Días": {"monto": Decimal("0.00"), "docs": 0},
                    "Vencida 16 - 30 Días": {"monto": Decimal("0.00"), "docs": 0},
                    "Mora Crítica (+30 Días)": {"monto": Decimal("0.00"), "docs": 0},
                }
                if not fac_validas_df.empty:
                    for _, r_cxc in fac_validas_df[fac_validas_df["saldo_pendiente_usd"] > 0].iterrows():
                        saldo_doc = redondear_moneda(a_decimal(r_cxc["saldo_pendiente_usd"]))
                        try:
                            fv_doc = datetime.strptime(str(r_cxc["fecha_vencimiento"])[:10], "%Y-%m-%d").date()
                            dias_m = (hoy_corte - fv_doc).days
                        except Exception:
                            dias_m = 0
                        if dias_m <= 0:
                            b_key = "Por Vencer / Vigente (0 días)"
                        elif dias_m <= 15:
                            b_key = "Vencida 1 - 15 Días"
                        elif dias_m <= 30:
                            b_key = "Vencida 16 - 30 Días"
                        else:
                            b_key = "Mora Crítica (+30 Días)"
                        buckets_cxc[b_key]["monto"] = redondear_moneda(buckets_cxc[b_key]["monto"] + saldo_doc)
                        buckets_cxc[b_key]["docs"] += 1

                tot_cxc_antig = sum((v["monto"] for v in buckets_cxc.values()), Decimal("0.00"))
                for b_label, b_info in buckets_cxc.items():
                    pct_cart = redondear_moneda((b_info["monto"] / tot_cxc_antig) * Decimal("100")) if tot_cxc_antig > Decimal("0.00") else Decimal("0.00")
                    filas_resumen_pdf.append([
                        f"[CARTERA CxC] {b_label} ({b_info['docs']} doc.)",
                        f"${b_info['monto']:,.2f}",
                        f"{pct_cart}% | Bs {redondear_moneda(b_info['monto'] * tasa_dec):,.2f}"
                    ])

                pdf_ejecutivo = generar_pdf_comprobante(
                    titulo_documento="Reporte Ejecutivo Financiero, Rentabilidad por SKU y Cartera",
                    codigo_documento=cod_rep_fin,
                    unidad_emisora="Unidad Financiera, Facturación, Tesorería y Análisis",
                    datos_generales={
                        "Fecha de Corte": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "Tasa BCV Vigente": f"Bs {tasa_bcv_global:,.2f} / $",
                        "Ejecución Presupuestaria": f"{res_ana['ejecucion_presupuestaria_pct']}%",
                        "Razón de Liquidez": f"{razon_liquidez}",
                        "Presupuesto Asignado": f"${pres_asig_tot:,.2f}",
                        "Emitido Por": user["nombre_completo"]
                    },
                    columnas_tabla=["Sección / Concepto Ejecutivo", "Monto ($ USD)", "Detalle / Margen / Bs"],
                    filas_tabla=filas_resumen_pdf,
                    totales={
                        "UTILIDAD OPERATIVA ($):": f"${res_ana['utilidad_operativa']:,.2f}",
                        "CAPITAL DE TRABAJO ($):": f"${capital_trabajo:,.2f}"
                    },
                    observaciones="Incluye estado financiero consolidado, utilidad bruta y margen porcentual por SKU, y estratificación de antigüedad de cuentas por cobrar con aritmética Decimal.",
                    firmas=("Administración y Finanzas", "Gerencia General", "Contraloría Social")
                )
                st.session_state["ultimo_pdf_ejecutivo_fin"] = pdf_ejecutivo
                st.session_state["ultimo_cod_ejecutivo_fin"] = cod_rep_fin
                registrar_auditoria_comercial(user, "Análisis Financiero", "REPORTE_PDF_EJECUTIVO", cod_rep_fin, f"Utilidad Op: ${res_ana['utilidad_operativa']:,.2f} | Cap. Trabajo: ${capital_trabajo:,.2f}")
                st.success(f"Reporte PDF ejecutivo `{cod_rep_fin}` listo para descargar.")

            if st.session_state.get("ultimo_pdf_ejecutivo_fin") is not None:
                st.download_button(
                    label="Descargar Reporte PDF Ejecutivo",
                    data=st.session_state["ultimo_pdf_ejecutivo_fin"],
                    file_name=f"{st.session_state.get('ultimo_cod_ejecutivo_fin', 'Reporte_Ejecutivo')}.pdf",
                    mime="application/pdf",
                    key="btn_descargar_pdf_ejecutivo_fin",
                    use_container_width=True
                )

        with exp_c2:
            buf_fin = io.BytesIO()
            with pd.ExcelWriter(buf_fin, engine="openpyxl") as writer:
                df_fac_f.to_excel(writer, sheet_name="Facturas", index=False)
                df_cxc_f.to_excel(writer, sheet_name="Movimientos_CxC", index=False)
                df_cxp_f.to_excel(writer, sheet_name="Compromisos_CxP", index=False)
                df_cta_f.to_excel(writer, sheet_name="Cuentas_Tesoreria", index=False)
                df_teso_f.to_excel(writer, sheet_name="Libro_Tesoreria", index=False)
                df_gc_f.to_excel(writer, sheet_name="Costos_Gastos", index=False)
                df_pres_f.to_excel(writer, sheet_name="Presupuesto", index=False)
            st.download_button(
                label="Descargar Paquete Financiero Completo (.XLSX)",
                data=buf_fin.getvalue(),
                file_name=f"Analisis_Financiero_EPSDC_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

# ==============================================================================
# MÓDULO 4: UNIDAD DE DESPACHO, COMERCIALIZACIÓN Y VENTAS (INTEGRAL)
# ==============================================================================
elif modulo_activo == "4. Despacho, Comercialización y Ventas":
    st.subheader("Unidad de Despacho, Comercialización y Ventas — Gestión Integral")
    st.caption("Arquitectura comercial unificada: Clientes y Códigos | Preventas y Flujo de Estados | Cartera y Cobranza | Analítica y KPIs | Auditoría RBAC y Exportación.")

    df_inv_all = query_df("SELECT * FROM inventario")
    df_clientes = query_df("SELECT * FROM clientes_cartera ORDER BY id DESC")
    df_preventas = query_df("SELECT * FROM preventas ORDER BY id DESC")
    df_ventas = query_df("SELECT * FROM ventas ORDER BY id DESC")
    df_bitacora = query_df("SELECT * FROM bitacora_cartera ORDER BY id DESC")
    df_metas = query_df("SELECT * FROM metas_comerciales ORDER BY id DESC")
    df_audit = query_df("SELECT * FROM auditoria_comercial ORDER BY id DESC")

    meta_diaria_vig = float(df_metas.iloc[0]["meta_diaria_usd"]) if not df_metas.empty else 500.0
    meta_mensual_vig = float(df_metas.iloc[0]["meta_mensual_usd"]) if not df_metas.empty else 11000.0

    tab_cli, tab_prev, tab_cart, tab_kpi, tab_gov = st.tabs([
        "1. Clientes y Códigos",
        "2. Preventas y Pedidos",
        "3. Cartera y Cobranza",
        "4. Analítica Diaria y KPIs",
        "5. Auditoría RBAC y Exportación"
    ])

    with tab_cli:
        st.markdown("### 1. Gestión, Normalización y Generación de Códigos de Clientes")
        st.caption("Evita duplicados mediante validación de RIF/nombre, normaliza datos de contacto/geolocalización y controla el estado comercial.")

        with st.form("form_registro_cliente_comercial"):
            c1, c2, c3 = st.columns(3)
            nombre_cli = c1.text_input("Razón Social / Nombre del Comercio", placeholder="Ej: Distribuidora Yaracuy Central C.A.")
            rif_cli_raw = c2.text_input("RIF o Cédula", placeholder="Ej: j409876543 o v18234567")
            tel_cli_raw = c3.text_input("Teléfono de Contacto", placeholder="Ej: 04145559988")

            c4, c5, c6 = st.columns(3)
            territorio_cli = c4.selectbox("Territorio Comercial", TERRITORIOS_YARACUY, key="cli_terr")
            municipio_cli = c5.selectbox("Municipio", MUNICIPIOS_OPERATIVOS, key="cli_mun")
            geo_cli = c6.text_input("Geolocalización / Referencia GPS", placeholder="Ej: 10.3385,-68.7420 / Sector Centro")

            dir_cli = st.text_input("Dirección Fiscal / Punto de Entrega", placeholder="Ej: Av. Libertador entre calles 12 y 13")

            c7, c8, c9, c10 = st.columns(4)
            segmento_cli = c7.selectbox("Segmento Comercial", ["Distribuidor / Mayorista", "Bodega / Abasto", "HORECA / Cafetería", "Productor / Aliado", "Institucional"])
            vendedor_cli = c8.selectbox("Vendedor / Promotor Asignado", PROMOTORES_COMERCIALES, key="cli_vend")
            estado_cli = c9.selectbox("Estado del Cliente", ["Prospecto", "Activo", "Inactivo", "Moroso", "Bloqueado"], index=1)
            modo_cod = c10.selectbox("Formato de Código", ["Correlativo por Zona", "Basado en RIF Normalizado"])

            c11, c12 = st.columns(2)
            limite_cred = c11.number_input("Límite de Crédito Asignado ($)", min_value=0.0, value=500.0, step=50.0)
            dias_cred = c12.number_input("Días de Crédito", min_value=0, max_value=120, value=7, step=1)
            obs_cli = st.text_area("Observaciones Comerciales / Condiciones Especiales")

            btn_guardar_cli = st.form_submit_button("Validar, Normalizar y Registrar Cliente", type="primary")

        if btn_guardar_cli:
            nombre_norm = normalizar_texto_titulo(nombre_cli)
            rif_norm = normalizar_rif_cedula(rif_cli_raw)
            tel_norm = normalizar_telefono(tel_cli_raw)
            dir_norm = normalizar_texto_titulo(dir_cli)
            geo_norm = normalizar_texto_titulo(geo_cli)

            if not nombre_norm or not rif_norm or not tel_norm or not dir_norm:
                st.error("Complete Nombre/Comercio, RIF/Cédula, Teléfono y Dirección antes de registrar.")
            else:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "SELECT codigo_cliente, nombre_comercio FROM clientes_cartera WHERE REPLACE(UPPER(rif_cedula), '-', '') = ? OR LOWER(nombre_comercio) = LOWER(?)",
                    (re.sub(r"[^A-Z0-9]", "", rif_norm), nombre_norm)
                )
                duplicado = cur.fetchone()
                if duplicado:
                    conn.close()
                    st.error(f"Registro duplicado detectado: ya existe el cliente `{duplicado['codigo_cliente']}` ({duplicado['nombre_comercio']}).")
                else:
                    cod_cli = generar_codigo_cliente_unico(territorio_cli, rif_norm, modo_cod)
                    ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                    cur.execute(
                        """
                        INSERT INTO clientes_cartera
                        (codigo_cliente, nombre_comercio, rif_cedula, telefono, direccion, territorio, municipio,
                         geolocalizacion, segmento, vendedor_asignado, estado_cliente, limite_credito_usd,
                         dias_credito, saldo_pendiente_usd, fecha_ultima_compra, observaciones, creado_en, actualizado_en)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, '', ?, ?, ?)
                        """,
                        (
                            cod_cli, nombre_norm, rif_norm, tel_norm, dir_norm, territorio_cli, municipio_cli,
                            geo_norm, segmento_cli, vendedor_cli, estado_cli, float(limite_cred),
                            int(dias_cred), normalizar_texto_titulo(obs_cli), ahora_str, ahora_str
                        )
                    )
                    conn.commit()
                    conn.close()
                    registrar_auditoria_comercial(user, "Clientes", "CREAR_CLIENTE", cod_cli, f"Cliente {nombre_norm} ({rif_norm}) en {territorio_cli} con estado {estado_cli}")
                    st.success(f"Cliente registrado con código único `{cod_cli}` | RIF normalizado: `{rif_norm}` | Teléfono: `{tel_norm}`.")
                    st.rerun()

        st.markdown("#### Directorio de Clientes Normalizados")
        f_col1, f_col2 = st.columns(2)
        filtro_estado_cli = f_col1.multiselect("Filtrar por Estado", ["Prospecto", "Activo", "Inactivo", "Moroso", "Bloqueado"], default=["Prospecto", "Activo", "Inactivo", "Moroso", "Bloqueado"])
        filtro_terr_cli = f_col2.selectbox("Filtrar por Territorio", ["Todos"] + TERRITORIOS_YARACUY)

        df_cli_view = df_clientes.copy()
        if not df_cli_view.empty:
            if filtro_estado_cli:
                df_cli_view = df_cli_view[df_cli_view["estado_cliente"].isin(filtro_estado_cli)]
            if filtro_terr_cli != "Todos":
                df_cli_view = df_cli_view[df_cli_view["territorio"] == filtro_terr_cli]
        st.dataframe(df_cli_view, use_container_width=True, hide_index=True)

    with tab_prev:
        st.markdown("### 2. Registro de Preventas, Control de Stock/Margen y Flujo de Estados")
        st.caption("Levanta intenciones de compra con reserva de stock, validación de margen mínimo, cálculo de impuestos/descuentos y transiciones auditadas.")

        if df_clientes.empty or df_inv_all.empty:
            st.warning("Debe existir al menos un cliente registrado y rubros en inventario para crear preventas.")
        else:
            with st.form("form_preventa_operativa"):
                c1, c2, c3 = st.columns(3)
                opciones_cli = df_clientes["codigo_cliente"] + " | " + df_clientes["nombre_comercio"] + " [" + df_clientes["estado_cliente"] + "]"
                cli_sel = c1.selectbox("Cliente Vinculado", opciones_cli)
                vendedor_prev = c2.selectbox("Vendedor / Promotor", PROMOTORES_COMERCIALES, key="prev_vend")
                almacen_orig = c3.selectbox("Almacén / Origen", df_inv_all["ubicacion_bodega"].dropna().unique().tolist() or ["Almacén Principal Nirgua"])

                c4, c5, c6, c7 = st.columns(4)
                moneda_prev = c4.selectbox("Moneda Base", ["USD", "VES"])
                cond_pago_prev = c5.selectbox("Condición de Pago", ["Contado", "Crédito 7 Días", "Crédito 15 Días", "Trueque / Acopio"], key="prev_cond")
                escala_prev = c6.selectbox("Escala de Precio", ["Mayorista", "Detal / Bodega"], key="prev_esc")
                estado_ini_prev = c7.selectbox("Estado Inicial", ["Borrador", "Registrada / Preventa", "Aprobada"], index=1)

                c8, c9, c10, c11 = st.columns(4)
                prod_prev = c8.selectbox("Producto / SKU", df_inv_all["sku"] + " | " + df_inv_all["nombre"] + " (Stock: " + df_inv_all["stock_actual"].astype(str) + ")", key="prev_prod")
                cant_prev = c9.number_input("Cantidad", min_value=0.01, value=5.0, step=1.0, key="prev_cant")
                desc_prev = c10.number_input("Descuento (%)", min_value=0.0, max_value=50.0, value=0.0, step=1.0)
                imp_prev = c11.number_input("Impuesto (%)", min_value=0.0, max_value=30.0, value=0.0, step=1.0)

                obs_prev = st.text_input("Observaciones de Cotización / Entrega")
                submitted_prev = st.form_submit_button("Guardar Preventa y Generar Cotización PDF", type="primary")

            if submitted_prev:
                cod_cli_sel = cli_sel.split(" | ")[0]
                cli_row = df_clientes[df_clientes["codigo_cliente"] == cod_cli_sel].iloc[0]
                sku_p = prod_prev.split(" | ")[0]
                prod_row = df_inv_all[df_inv_all["sku"] == sku_p].iloc[0]

                estado_cli_actual = cli_row["estado_cliente"]
                if estado_cli_actual in ["Bloqueado", "Inactivo"]:
                    st.error(f"Operación bloqueada: el cliente `{cli_row['codigo_cliente']}` se encuentra en estado `{estado_cli_actual}`.")
                elif cond_pago_prev.startswith("Crédito") and estado_cli_actual == "Moroso":
                    st.error("Crédito denegado: el cliente tiene estado `Moroso`. Seleccione Contado o regularice su cartera.")
                else:
                    precio_base = float(prod_row["precio_mayor_usd"]) if escala_prev == "Mayorista" else float(prod_row["precio_detal_usd"])
                    if precio_base <= 0:
                        precio_base = float(prod_row["costo_unitario_usd"]) * 1.25

                    calculos = calcular_lineas_preventa(cant_prev, precio_base, desc_prev, imp_prev, tasa_bcv_global)
                    eval_margen = evaluar_margen_minimo(precio_base, float(prod_row["costo_unitario_usd"]), desc_prev, margen_min_pct=15.0)
                    reservado_actual = obtener_stock_reservado_sku(sku_p)
                    stock_disponible_real = float(prod_row["stock_actual"]) - reservado_actual

                    if float(cant_prev) > stock_disponible_real and estado_ini_prev != "Borrador":
                        st.error(f"Stock disponible insuficiente para reservar ({stock_disponible_real:,.2f} disponibles; {reservado_actual:,.2f} ya reservados).")
                    elif not eval_margen["cumple_margen"] and nivel_rbac == "VENDEDOR":
                        st.error(f"Precio/descuento por debajo del margen mínimo (15.0%; margen calculado: {eval_margen['margen_pct']}%). Requiere aprobación de Supervisor o Administrador.")
                    else:
                        cod_prev = generar_codigo("PREV", "preventas")
                        fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                        stock_res = float(cant_prev) if estado_ini_prev in ["Registrada / Preventa", "Aprobada"] else 0.0
                        req_aprob = 0 if eval_margen["cumple_margen"] else 1
                        aprob_por = user["username"] if estado_ini_prev == "Aprobada" else ""

                        conn = get_connection()
                        cur = conn.cursor()
                        cur.execute(
                            """
                            INSERT INTO preventas
                            (codigo_preventa, fecha, cliente_id, codigo_cliente, cliente_nombre, rif_cliente,
                             vendedor, territorio, municipio, almacen_origen, moneda, tasa_bcv, condicion_pago,
                             escala_precio, sku, producto_nombre, cantidad, precio_unitario_usd, descuento_pct,
                             impuesto_pct, subtotal_usd, descuento_usd, impuesto_usd, total_usd, total_bs,
                             stock_reservado, estado_preventa, requiere_aprobacion, aprobado_por,
                             codigo_venta_vinculado, observaciones, creado_por, actualizado_en)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                            """,
                            (
                                cod_prev, fecha_str, int(cli_row["id"]), cli_row["codigo_cliente"],
                                cli_row["nombre_comercio"], cli_row["rif_cedula"], vendedor_prev,
                                cli_row["territorio"], cli_row["municipio"], almacen_orig, moneda_prev,
                                float(tasa_bcv_global), cond_pago_prev, escala_prev, sku_p, prod_row["nombre"],
                                float(cant_prev), precio_base, float(desc_prev), float(imp_prev),
                                calculos["subtotal_usd"], calculos["descuento_usd"], calculos["impuesto_usd"],
                                calculos["total_usd"], calculos["total_bs"], stock_res, estado_ini_prev,
                                req_aprob, aprob_por, normalizar_texto_titulo(obs_prev), user["username"], fecha_str
                            )
                        )
                        cur.execute(
                            """
                            INSERT INTO historial_preventas
                            (codigo_preventa, estado_anterior, estado_nuevo, usuario, rol, motivo, fecha)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (cod_prev, "N/A", estado_ini_prev, user["username"], nivel_rbac, "Creación inicial de preventa", fecha_str)
                        )
                        conn.commit()
                        conn.close()

                        registrar_auditoria_comercial(user, "Preventas", "CREAR_PREVENTA", cod_prev, f"Total ${calculos['total_usd']:,.2f} | Estado: {estado_ini_prev}")

                        pdf_prev = generar_pdf_comprobante(
                            titulo_documento="Cotización / Orden de Preventa Comercial",
                            codigo_documento=cod_prev,
                            unidad_emisora="Unidad de Despacho, Comercialización y Ventas",
                            datos_generales={
                                "Cliente": f"{cli_row['codigo_cliente']} - {cli_row['nombre_comercio']}",
                                "RIF": cli_row["rif_cedula"],
                                "Vendedor": vendedor_prev,
                                "Territorio / Almacén": f"{cli_row['territorio']} | {almacen_orig}",
                                "Condición Pago": cond_pago_prev,
                                "Estado Preventa": estado_ini_prev
                            },
                            columnas_tabla=["SKU", "Producto", "Cant.", "P. Unit ($)", "Total ($)"],
                            filas_tabla=[[sku_p, prod_row["nombre"], f"{cant_prev:,.2f}", f"${precio_base:,.2f}", f"${calculos['total_usd']:,.2f}"]],
                            totales={
                                "SUBTOTAL BRUTO:": f"${calculos['subtotal_usd']:,.2f}",
                                "DESCUENTO / IMP.:": f"-${calculos['descuento_usd']:,.2f} / +${calculos['impuesto_usd']:,.2f}",
                                "TOTAL PREVENTA ($):": f"${calculos['total_usd']:,.2f}",
                                "EQUIVALENTE (Bs):": f"Bs {calculos['total_bs']:,.2f}"
                            },
                            observaciones=obs_prev or f"Tasa BCV aplicada: Bs {tasa_bcv_global:,.2f}/$. Margen línea: {eval_margen['margen_pct']}%.",
                            firmas=("Vendedor / Promotor", "Supervisor Comercial", "Cliente / Conforme")
                        )
                        st.session_state.ultimo_pdf_preventa = pdf_prev
                        st.session_state.ultimo_cod_preventa = cod_prev
                        st.success(f"Preventa `{cod_prev}` registrada por ${calculos['total_usd']:,.2f} (Bs {calculos['total_bs']:,.2f}).")
                        st.rerun()

        if st.session_state.ultimo_pdf_preventa is not None:
            st.download_button(
                label="Descargar Cotización / Preventa (PDF)",
                data=st.session_state.ultimo_pdf_preventa,
                file_name=f"{st.session_state.ultimo_cod_preventa}.pdf",
                mime="application/pdf",
                key="btn_descargar_preventa_pdf"
            )

        st.markdown("#### Flujo de Estados y Conversión de Preventas a Venta/Despacho")
        st.dataframe(df_preventas, use_container_width=True, hide_index=True)

        if not df_preventas.empty:
            with st.form("form_transicion_preventa"):
                c1, c2, c3 = st.columns(3)
                prev_choices = df_preventas["codigo_preventa"] + " | " + df_preventas["cliente_nombre"] + " [" + df_preventas["estado_preventa"] + "]"
                prev_target = c1.selectbox("Seleccionar Preventa", prev_choices)
                nuevo_estado_prev = c2.selectbox("Nuevo Estado", ESTADOS_PREVENTA)
                motivo_cambio = c3.text_input("Motivo de Transición / Observación", placeholder="Ej: Aprobado por gerencia y despachado en ruta")
                btn_trans = st.form_submit_button("Aplicar Cambio de Estado", type="primary")

            if btn_trans:
                cod_p_sel = prev_target.split(" | ")[0]
                p_row = df_preventas[df_preventas["codigo_preventa"] == cod_p_sel].iloc[0]
                estado_ant = p_row["estado_preventa"]

                if nuevo_estado_prev == estado_ant:
                    st.info("La preventa ya se encuentra en el estado seleccionado.")
                elif nuevo_estado_prev not in TRANSICIONES_PREVENTA.get(estado_ant, []):
                    st.error(f"Transición no permitida de `{estado_ant}` a `{nuevo_estado_prev}`.")
                elif nuevo_estado_prev == "Aprobada" and nivel_rbac == "VENDEDOR":
                    st.error("Permiso RBAC denegado: solo Supervisor o Administrador pueden aprobar preventas.")
                else:
                    fecha_now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    conn = get_connection()
                    cur = conn.cursor()

                    cod_vta_gen = p_row["codigo_venta_vinculado"]
                    if nuevo_estado_prev == "Facturada / Despachada" and not cod_vta_gen:
                        cur.execute("SELECT stock_actual FROM inventario WHERE sku = ?", (p_row["sku"],))
                        inv_r = cur.fetchone()
                        stock_act_sku = float(inv_r["stock_actual"]) if inv_r else 0.0
                        if stock_act_sku < float(p_row["cantidad"]):
                            conn.close()
                            st.error(f"No hay stock físico suficiente en inventario ({stock_act_sku:,.2f}) para despachar `{cod_p_sel}`.")
                            st.stop()

                        cod_vta_gen = generar_codigo("VTA", "ventas")
                        est_cob = "Cobrado" if p_row["condicion_pago"] == "Contado" else "Pendiente de Cobro"
                        cur.execute(
                            """
                            INSERT INTO ventas
                            (codigo_venta, fecha, cliente_comercio, rif_cedula, territorio, municipio, promotor,
                             escala_precio, sku, cantidad, precio_unitario_usd, total_usd, tasa_bcv, total_bs,
                             condicion_pago, estado_despacho, estado_cobro, responsable)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                cod_vta_gen, fecha_now, p_row["cliente_nombre"], p_row["rif_cliente"],
                                p_row["territorio"], p_row["municipio"], p_row["vendedor"], p_row["escala_precio"],
                                p_row["sku"], float(p_row["cantidad"]), float(p_row["precio_unitario_usd"]),
                                float(p_row["total_usd"]), float(tasa_bcv_global), float(p_row["total_usd"]) * float(tasa_bcv_global),
                                p_row["condicion_pago"], "Despachado", est_cob, user["username"]
                            )
                        )
                        cur.execute("UPDATE inventario SET stock_actual = stock_actual - ? WHERE sku = ?", (float(p_row["cantidad"]), p_row["sku"]))
                        if est_cob == "Pendiente de Cobro":
                            cur.execute(
                                "UPDATE clientes_cartera SET saldo_pendiente_usd = saldo_pendiente_usd + ?, fecha_ultima_compra = ? WHERE codigo_cliente = ?",
                                (float(p_row["total_usd"]), fecha_now[:10], p_row["codigo_cliente"])
                            )
                        else:
                            cur.execute(
                                "UPDATE clientes_cartera SET fecha_ultima_compra = ? WHERE codigo_cliente = ?",
                                (fecha_now[:10], p_row["codigo_cliente"])
                            )

                    nuevo_reservado = float(p_row["cantidad"]) if nuevo_estado_prev in ["Registrada / Preventa", "Aprobada"] else 0.0
                    aprob_por_upd = user["username"] if nuevo_estado_prev == "Aprobada" else p_row["aprobado_por"]
                    cur.execute(
                        """
                        UPDATE preventas
                        SET estado_preventa = ?, stock_reservado = ?, aprobado_por = ?,
                            codigo_venta_vinculado = ?, actualizado_en = ?
                        WHERE codigo_preventa = ?
                        """,
                        (nuevo_estado_prev, nuevo_reservado, aprob_por_upd, cod_vta_gen, fecha_now, cod_p_sel)
                    )
                    cur.execute(
                        """
                        INSERT INTO historial_preventas
                        (codigo_preventa, estado_anterior, estado_nuevo, usuario, rol, motivo, fecha)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (cod_p_sel, estado_ant, nuevo_estado_prev, user["username"], nivel_rbac, motivo_cambio.strip() or "Actualización de flujo", fecha_now)
                    )
                    conn.commit()
                    conn.close()
                    registrar_auditoria_comercial(user, "Preventas", "TRANSICION_ESTADO", cod_p_sel, f"{estado_ant} -> {nuevo_estado_prev} | Venta: {cod_vta_gen or 'N/A'}")
                    st.success(f"Preventa `{cod_p_sel}` actualizada a `{nuevo_estado_prev}`.")
                    st.rerun()

        st.markdown("##### Historial de Cambios de Estado en Preventas")
        st.dataframe(query_df("SELECT * FROM historial_preventas ORDER BY id DESC"), use_container_width=True, hide_index=True)

    with tab_cart:
        st.markdown("### 3. Administración de Cartera, Crédito, Cobranza y Bitácora")
        st.caption("Control de segmentación, límites y días de crédito, saldos pendientes/morosos, abonos a cuenta y alertas de inactividad (churn).")

        if df_clientes.empty:
            st.info("No hay clientes registrados en cartera.")
        else:
            hoy_dt = datetime.now().date()
            alertas_churn = []
            for _, r in df_clientes.iterrows():
                f_ult = str(r.get("fecha_ultima_compra") or "").strip()
                dias_sin_compra = None
                if f_ult:
                    try:
                        dias_sin_compra = (hoy_dt - datetime.strptime(f_ult[:10], "%Y-%m-%d").date()).days
                    except ValueError:
                        dias_sin_compra = None
                if dias_sin_compra is None or dias_sin_compra >= 15:
                    alertas_churn.append({
                        "codigo_cliente": r["codigo_cliente"],
                        "cliente": r["nombre_comercio"],
                        "vendedor": r["vendedor_asignado"],
                        "estado": r["estado_cliente"],
                        "ultima_compra": f_ult or "Sin compras",
                        "dias_inactivo": dias_sin_compra if dias_sin_compra is not None else "N/A",
                        "saldo_pendiente_usd": r["saldo_pendiente_usd"]
                    })

            if alertas_churn:
                st.warning(f"Alerta de caída de frecuencia / posible churn ({len(alertas_churn)} clientes con >= 15 días sin compra o sin historial):")
                st.dataframe(pd.DataFrame(alertas_churn), use_container_width=True, hide_index=True)

            st.markdown("#### Segmentación, Crédito y Estado de Cartera")
            st.dataframe(
                df_clientes[[
                    "codigo_cliente", "nombre_comercio", "territorio", "municipio", "segmento",
                    "vendedor_asignado", "estado_cliente", "limite_credito_usd", "dias_credito",
                    "saldo_pendiente_usd", "fecha_ultima_compra"
                ]],
                use_container_width=True,
                hide_index=True
            )

            col_abono, col_bit = st.columns(2)

            with col_abono:
                st.markdown("##### Registro de Abono / Cobranza y Ajuste de Crédito")
                with st.form("form_abono_cartera"):
                    cli_cart_sel = st.selectbox("Cliente de Cartera", df_clientes["codigo_cliente"] + " | " + df_clientes["nombre_comercio"], key="cart_cli")
                    abono_usd = st.number_input("Monto del Abono / Cobro ($ USD)", min_value=0.0, value=50.0, step=10.0)
                    nuevo_estado_cart = st.selectbox("Actualizar Estado Cliente", ["Activo", "Moroso", "Prospecto", "Inactivo", "Bloqueado"], key="cart_est")
                    nuevo_lim_cart = st.number_input("Ajustar Límite Crédito ($)", min_value=0.0, value=500.0, step=50.0, key="cart_lim")
                    ref_abono = st.text_input("Referencia / Recibo de Cobranza", placeholder="Ej: REC-2026-0001")
                    btn_abono = st.form_submit_button("Aplicar Abono / Actualizar Cartera", type="primary")

                if btn_abono:
                    cod_c = cli_cart_sel.split(" | ")[0]
                    c_row = df_clientes[df_clientes["codigo_cliente"] == cod_c].iloc[0]
                    saldo_act = float(c_row["saldo_pendiente_usd"])
                    abono_val = float(abono_usd)
                    if abono_val > 0 and nivel_rbac == "VENDEDOR":
                        st.error("Solo Supervisor o Administrador pueden liquidar abonos directos de cartera.")
                    else:
                        nuevo_saldo = max(0.0, saldo_act - abono_val)
                        estado_final = "Activo" if (abono_val > 0 and nuevo_saldo == 0 and nuevo_estado_cart == "Moroso") else nuevo_estado_cart
                        fecha_now = datetime.now().strftime("%Y-%m-%d %H:%M")

                        conn = get_connection()
                        cur = conn.cursor()
                        cur.execute(
                            """
                            UPDATE clientes_cartera
                            SET saldo_pendiente_usd = ?, estado_cliente = ?, limite_credito_usd = ?, actualizado_en = ?
                            WHERE codigo_cliente = ?
                            """,
                            (nuevo_saldo, estado_final, float(nuevo_lim_cart), fecha_now, cod_c)
                        )
                        if abono_val > 0:
                            cod_rec = generar_codigo("REC", "tesoreria")
                            monto_bs = abono_val * float(tasa_bcv_global)
                            cur.execute(
                                """
                                INSERT INTO tesoreria
                                (codigo_recibo, fecha, tipo_operacion, concepto, doc_asociado, tercero,
                                 metodo_pago, referencia, monto_usd, tasa_bcv, monto_bs, responsable)
                                VALUES (?, ?, 'Ingreso', ?, ?, ?, 'Transferencia / Cobranza', ?, ?, ?, ?, ?)
                                """,
                                (
                                    cod_rec, fecha_now, f"Abono cartera cliente {c_row['nombre_comercio']}",
                                    ref_abono.strip() or cod_c, c_row["nombre_comercio"],
                                    ref_abono.strip() or cod_rec, abono_val, float(tasa_bcv_global), monto_bs, user["username"]
                                )
                            )
                        conn.commit()
                        conn.close()
                        registrar_auditoria_comercial(user, "Cartera", "ACTUALIZAR_CARTERA", cod_c, f"Abono: ${abono_val:,.2f} | Nuevo saldo: ${nuevo_saldo:,.2f} | Estado: {estado_final}")
                        st.success(f"Cartera de `{cod_c}` actualizada. Nuevo saldo pendiente: ${nuevo_saldo:,.2f}.")
                        st.rerun()

            with col_bit:
                st.markdown("##### Bitácora Comercial (Visitas, Reclamos y Seguimiento)")
                with st.form("form_bitacora_comercial"):
                    cli_bit_sel = st.selectbox("Cliente", df_clientes["codigo_cliente"] + " | " + df_clientes["nombre_comercio"], key="bit_cli")
                    tipo_ev = st.selectbox("Tipo de Registro", ["Visita Comercial", "Seguimiento de Cobranza", "Reclamo / Incidencia", "Cotización / Propuesta"])
                    resumen_ev = st.text_area("Detalle de la Interacción / Incidencia")
                    prox_acc = st.text_input("Próxima Acción y Fecha Sugerida", placeholder="Ej: Llamar y visitar en 5 días para cierre de pedido")
                    btn_bit = st.form_submit_button("Registrar en Bitácora", type="primary")

                if btn_bit:
                    if not resumen_ev.strip():
                        st.error("Debe ingresar el detalle del registro en la bitácora.")
                    else:
                        cod_b = cli_bit_sel.split(" | ")[0]
                        b_row = df_clientes[df_clientes["codigo_cliente"] == cod_b].iloc[0]
                        fecha_now = datetime.now().strftime("%Y-%m-%d %H:%M")
                        conn = get_connection()
                        cur = conn.cursor()
                        cur.execute(
                            """
                            INSERT INTO bitacora_cartera
                            (codigo_cliente, cliente_nombre, tipo_evento, resumen, proxima_accion, vendedor, fecha)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (cod_b, b_row["nombre_comercio"], tipo_ev, normalizar_texto_titulo(resumen_ev), normalizar_texto_titulo(prox_acc), user["nombre_completo"], fecha_now)
                        )
                        conn.commit()
                        conn.close()
                        registrar_auditoria_comercial(user, "Cartera", "BITACORA", cod_b, f"{tipo_ev}: {resumen_ev[:80]}")
                        st.success(f"Evento de bitácora guardado para `{cod_b}`.")
                        st.rerun()

        st.markdown("##### Historial de Bitácora Comercial")
        st.dataframe(df_bitacora, use_container_width=True, hide_index=True)

    with tab_kpi:
        st.markdown("### 4. Analítica de Ventas Diarias, Agregaciones y KPIs Comerciales")

        with st.expander("Configurar Metas Comerciales (Diaria / Mensual)"):
            with st.form("form_metas_comerciales"):
                m1, m2, m3 = st.columns(3)
                m_diaria = m1.number_input("Meta Diaria ($ USD)", min_value=10.0, value=float(meta_diaria_vig), step=25.0)
                m_mensual = m2.number_input("Meta Mensual ($ USD)", min_value=100.0, value=float(meta_mensual_vig), step=100.0)
                d_habiles = m3.number_input("Días Hábiles del Mes", min_value=1, max_value=31, value=22, step=1)
                btn_meta = st.form_submit_button("Guardar Metas Comerciales", type="primary")
            if btn_meta:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO metas_comerciales (periodo, meta_diaria_usd, meta_mensual_usd, dias_habiles_mes, actualizado_por, fecha)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (datetime.now().strftime("%Y-%m"), float(m_diaria), float(m_mensual), int(d_habiles), user["username"], datetime.now().strftime("%Y-%m-%d %H:%M"))
                )
                conn.commit()
                conn.close()
                registrar_auditoria_comercial(user, "KPIs", "ACTUALIZAR_METAS", "METAS", f"Meta diaria: ${m_diaria:,.2f} | Mensual: ${m_mensual:,.2f}")
                st.success("Metas comerciales actualizadas.")
                st.rerun()

        kpis = calcular_kpis_comerciales(df_ventas, df_preventas, df_clientes, df_inv_all, meta_diaria_vig, meta_mensual_vig)

        row1_1, row1_2, row1_3, row1_4 = st.columns(4)
        row1_1.metric("Venta Hoy vs. Meta Diaria", f"${kpis['venta_hoy_usd']:,.2f}", f"{kpis['avance_diario_pct']:.1f}% | Brecha: ${kpis['brecha_diaria_usd']:,.2f}")
        row1_2.metric("Conversión Preventas", f"{kpis['tasa_conversion_pct']:.1f}%", f"{kpis['preventas_cerradas']}/{kpis['total_preventas']} cerradas ({kpis['preventas_anuladas']} anuladas)")
        row1_3.metric("Ticket Promedio (AOV)", f"${kpis['ticket_promedio_usd']:,.2f}", f"{kpis['num_ordenes']} órdenes | {kpis['unidades_vendidas']:,.1f} und.")
        row1_4.metric("Cobertura y Activación", f"{kpis['cobertura_cartera_pct']:.1f}%", f"{kpis['clientes_activados_cnt']}/{kpis['total_clientes']} clientes con compra")

        row2_1, row2_2, row2_3, row2_4 = st.columns(4)
        row2_1.metric("Avance Mensual ($)", f"${kpis['venta_mes_usd']:,.2f}", f"{kpis['avance_mensual_pct']:.1f}% de ${meta_mensual_vig:,.2f}")
        row2_2.metric("Margen Bruto Estimado ($)", f"${kpis['margen_bruto_usd']:,.2f}", f"Bs {kpis['margen_bruto_usd'] * tasa_bcv_global:,.2f}")
        row2_3.metric("Saldo Cartera Pendiente ($)", f"${kpis['saldo_total_cartera']:,.2f}", f"Moroso: ${kpis['saldo_moroso']:,.2f}")
        row2_4.metric("Morosidad de Cartera", f"{kpis['morosidad_pct']:.1f}%", f"Sobre ${kpis['saldo_total_cartera']:,.2f} por cobrar")

        st.markdown("---")
        ag1, ag2 = st.columns(2)

        with ag1:
            st.markdown("#### Motor de Agregación Diaria y por Zona")
            if not df_ventas.empty:
                df_v_tmp = df_ventas.copy()
                df_v_tmp["dia"] = df_v_tmp["fecha"].astype(str).str.slice(0, 10)
                ag_dia = df_v_tmp.groupby(["dia", "territorio"], as_index=False).agg(
                    ordenes=("codigo_venta", "count"),
                    unidades=("cantidad", "sum"),
                    venta_usd=("total_usd", "sum"),
                    venta_bs=("total_bs", "sum")
                )
                ag_dia["ticket_promedio_usd"] = ag_dia["venta_usd"] / ag_dia["ordenes"].replace(0, 1)
                st.dataframe(ag_dia, use_container_width=True, hide_index=True)
                st.bar_chart(ag_dia.groupby("territorio")["venta_usd"].sum())
            else:
                st.info("Sin ventas registradas aún para agregar por día o zona.")

        with ag2:
            st.markdown("#### Efectividad por Vendedor / Promotor")
            if not df_ventas.empty:
                costos_map = dict(zip(df_inv_all["sku"], df_inv_all["costo_unitario_usd"]))
                df_ef = df_ventas.copy()
                df_ef["costo_linea_usd"] = df_ef.apply(lambda r: float(costos_map.get(r["sku"], 0.0)) * float(r["cantidad"]), axis=1)
                df_ef["margen_usd"] = df_ef["total_usd"] - df_ef["costo_linea_usd"]
                res_vend = df_ef.groupby("promotor", as_index=False).agg(
                    ordenes=("codigo_venta", "count"),
                    unidades=("cantidad", "sum"),
                    monto_vendido_usd=("total_usd", "sum"),
                    margen_aportado_usd=("margen_usd", "sum")
                )
                if not df_preventas.empty:
                    cnt_prev_v = df_preventas.groupby("vendedor").size().to_dict()
                    res_vend["preventas_levantadas"] = res_vend["promotor"].map(lambda x: int(cnt_prev_v.get(x, 0)))
                else:
                    res_vend["preventas_levantadas"] = 0
                st.dataframe(res_vend, use_container_width=True, hide_index=True)
            else:
                st.info("Sin datos de ventas para calcular efectividad por vendedor.")

    with tab_gov:
        st.markdown("### 5. Cierre Diario de Ventas, Exportación y Bitácora de Auditoría RBAC")

        with st.expander("Registrar Venta / Despacho Directo y Generar Guía PDF"):
            df_inv_disp = df_inv_all[df_inv_all["stock_actual"] > 0]
            if df_inv_disp.empty:
                st.warning("No hay existencia disponible en almacén para despachar.")
            else:
                with st.form("form_venta_directa_comercial"):
                    c1, c2, c3 = st.columns(3)
                    cliente_d = c1.text_input("Comercio / Cliente", placeholder="Ej: Supermercado El Sol C.A.")
                    rif_d = c2.text_input("RIF / Cédula", placeholder="Ej: J-30981234-5")
                    promotor_d = c3.selectbox("Promotor de Ventas", PROMOTORES_COMERCIALES, key="vta_dir_prom")

                    c4, c5, c6 = st.columns(3)
                    territorio_d = c4.selectbox("Territorio Comercial", TERRITORIOS_YARACUY, key="vta_dir_terr")
                    municipio_d = c5.selectbox("Municipio", MUNICIPIOS_OPERATIVOS, key="vta_dir_mun")
                    escala_d = c6.selectbox("Escala de Precio", ["Mayorista", "Detal / Bodega"], key="vta_dir_esc")

                    c7, c8, c9 = st.columns(3)
                    prod_d = c7.selectbox("Producto / SKU", df_inv_disp["sku"] + " | " + df_inv_disp["nombre"] + " (Disp: " + df_inv_disp["stock_actual"].astype(str) + ")", key="vta_dir_prod")
                    cant_d = c8.number_input("Cantidad", min_value=1.0, value=5.0, key="vta_dir_cant")
                    cond_d = c9.selectbox("Condición de Pago", ["Contado", "Crédito 7 Días", "Crédito 15 Días", "Trueque / Acopio"], key="vta_dir_cond")

                    btn_vta_d = st.form_submit_button("Procesar Venta Directa y Generar Guía PDF", type="primary")

                if btn_vta_d:
                    if not cliente_d.strip() or not rif_d.strip():
                        st.error("Debe indicar Nombre del Cliente y RIF/Cédula.")
                    else:
                        sku_vd = prod_d.split(" | ")[0]
                        prod_r = df_inv_disp[df_inv_disp["sku"] == sku_vd].iloc[0]
                        if float(prod_r["stock_actual"]) < float(cant_d):
                            st.error(f"Stock insuficiente. Disponible: {prod_r['stock_actual']}")
                        else:
                            precio_ud = float(prod_r["precio_mayor_usd"]) if escala_d == "Mayorista" else float(prod_r["precio_detal_usd"])
                            tot_usd = float(cant_d) * precio_ud
                            tot_bs = tot_usd * float(tasa_bcv_global)
                            cod_vd = generar_codigo("VTA", "ventas")
                            f_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                            est_cob_d = "Cobrado" if cond_d == "Contado" else "Pendiente de Cobro"

                            conn = get_connection()
                            cur = conn.cursor()
                            cur.execute(
                                """
                                INSERT INTO ventas
                                (codigo_venta, fecha, cliente_comercio, rif_cedula, territorio, municipio, promotor,
                                 escala_precio, sku, cantidad, precio_unitario_usd, total_usd, tasa_bcv, total_bs,
                                 condicion_pago, estado_despacho, estado_cobro, responsable)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    cod_vd, f_str, normalizar_texto_titulo(cliente_d), normalizar_rif_cedula(rif_d),
                                    territorio_d, municipio_d, promotor_d, escala_d, sku_vd, float(cant_d),
                                    precio_ud, tot_usd, float(tasa_bcv_global), tot_bs, cond_d, "Despachado",
                                    est_cob_d, user["username"]
                                )
                            )
                            cur.execute("UPDATE inventario SET stock_actual = stock_actual - ? WHERE sku = ?", (float(cant_d), sku_vd))
                            conn.commit()
                            conn.close()

                            registrar_auditoria_comercial(user, "Ventas", "VENTA_DIRECTA", cod_vd, f"Cliente: {cliente_d} | Total: ${tot_usd:,.2f}")

                            pdf_v = generar_pdf_comprobante(
                                titulo_documento="Nota de Entrega y Guía de Despacho Comercial",
                                codigo_documento=cod_vd,
                                unidad_emisora="Unidad de Despacho, Comercialización y Ventas",
                                datos_generales={
                                    "Cliente / Comercio": cliente_d,
                                    "RIF / Cédula": normalizar_rif_cedula(rif_d),
                                    "Territorio / Ruta": territorio_d,
                                    "Municipio Destino": municipio_d,
                                    "Promotor Asignado": promotor_d,
                                    "Condición Pago": f"{cond_d} ({est_cob_d})"
                                },
                                columnas_tabla=["SKU", "Descripción del Producto", "Bultos/Unid", "Precio Unit. ($)", "Subtotal ($)"],
                                filas_tabla=[[sku_vd, prod_r["nombre"], f"{cant_d:,.2f}", f"${precio_ud:,.2f}", f"${tot_usd:,.2f}"]],
                                totales={
                                    "TOTAL A PAGAR ($):": f"${tot_usd:,.2f}",
                                    "REF. BOLÍVARES (BCV):": f"Bs {tot_bs:,.2f}"
                                },
                                observaciones=f"Tarifa aplicada: {escala_d}. Tasa referencial BCV: Bs {tasa_bcv_global:,.2f}/$.",
                                firmas=("Jefe de Despacho y Ventas", "Recibido Conforme (Cliente)", "Control de Salida Almacén")
                            )
                            st.session_state.ultimo_pdf_venta = pdf_v
                            st.session_state.ultimo_cod_venta = cod_vd
                            st.success(f"Venta `{cod_vd}` registrada por ${tot_usd:,.2f} (Bs {tot_bs:,.2f}).")
                            st.rerun()

        if st.session_state.ultimo_pdf_venta is not None:
            st.download_button(
                label="Descargar Última Guía de Despacho (PDF)",
                data=st.session_state.ultimo_pdf_venta,
                file_name=f"{st.session_state.ultimo_cod_venta}.pdf",
                mime="application/pdf",
                key="btn_descargar_guia_comercial"
            )

        st.markdown("#### Exportación de Cierre Comercial (CSV y Excel)")
        exp1, exp2 = st.columns(2)
        if not df_ventas.empty:
            csv_ventas = df_ventas.to_csv(index=False).encode("utf-8")
            exp1.download_button(
                label="Exportar Cierre de Ventas (.CSV)",
                data=csv_ventas,
                file_name=f"Cierre_Ventas_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                use_container_width=True
            )
        else:
            exp1.info("No hay ventas registradas para exportar en CSV.")

        buf_com = io.BytesIO()
        with pd.ExcelWriter(buf_com, engine="openpyxl") as writer:
            df_clientes.to_excel(writer, sheet_name="Clientes_Cartera", index=False)
            df_preventas.to_excel(writer, sheet_name="Preventas", index=False)
            df_ventas.to_excel(writer, sheet_name="Ventas", index=False)
            df_bitacora.to_excel(writer, sheet_name="Bitacora_Cartera", index=False)
            df_audit.to_excel(writer, sheet_name="Auditoria_RBAC", index=False)
        exp2.download_button(
            label="Exportar Paquete Comercial Completo (.XLSX)",
            data=buf_com.getvalue(),
            file_name=f"Reporte_Comercial_Integral_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

        st.markdown("#### Registro de Pedidos / Ventas Ejecutadas")
        st.dataframe(df_ventas, use_container_width=True, hide_index=True)

        st.markdown("#### Auditoría de Operaciones y Control de Permisos (RBAC)")
        st.dataframe(df_audit, use_container_width=True, hide_index=True)
