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
    page_title="ERP EPSDC Guerreros de la Montaña Yaracuy",
    page_icon="☕",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==============================================================================
# CONFIGURACIÓN DE ACTIVOS Y MARCAS
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent
LOGO_CAFETAL_PATH = BASE_DIR / "assets" / "logo_el_cafetal.jpg"
LOGO_PORTU_PATH = BASE_DIR / "assets" / "logo_portu_cafe.jpg"


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

CIRCUITOS_COMERCIALES_YARACUY = [
    "Circuito 01 - Eje Metropolitano",
    "Circuito 02 - Eje Productivo Sur",
    "Circuito 03 - Eje Centro-Occidente",
    "Circuito 04 - Eje Centro-Norte",
    "Circuiro 05 - Eje Bolívar / Veroes / Monge",
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
# MÓDULO 3: UNIDAD FINANCIERA, FACTURACIÓN Y TESORERÍA
# ==============================================================================
elif modulo_activo == "3. Finanzas, Facturación y Tesorería":
    st.subheader("Unidad Financiera, Facturación y Tesorería")
    tab1, tab2 = st.tabs(["Registrar Cobro / Pago y Emitir Comprobante PDF", "Libro de Caja y Conciliación Bancaria"])

    with tab1:
        with st.form("form_tesoreria"):
            c1, c2, c3 = st.columns(3)
            tipo_op = c1.selectbox("Tipo de Operación", ["Ingreso", "Egreso"])
            tercero = c2.text_input("Cliente / Productor / Beneficiario", placeholder="Ej: Comercializadora Yaracuy C.A.")
            doc_ref = c3.text_input("Documento Asociado (N° Venta o Compra)", placeholder="Ej: VTA-2026-0001")

            c4, c5, c6 = st.columns(3)
            metodo = c4.selectbox("Instrumento de Pago", ["Pago Móvil", "Transferencia Bancaria", "Divisas Efectivo ($)", "Bolívares Efectivo", "Zelle / Digital"])
            referencia = c5.text_input("N° Referencia / Serial", placeholder="Ej: 00984512")
            monto_usd = c6.number_input("Monto de la Operación ($ USD)", min_value=0.1, value=100.0)

            concepto = st.text_input("Concepto Detallado del Movimiento", placeholder="Ej: Cancelación total de pedido de Por Tu Café 100g y 200g")

            submitted_teso = st.form_submit_button("Registrar en Tesorería y Generar Comprobante PDF", type="primary")

        if submitted_teso:
            if not tercero.strip() or not concepto.strip():
                st.error("Debe completar el Tercero y el Concepto del movimiento.")
            else:
                cod_rec = generar_codigo("REC", "tesoreria")
                fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                monto_bs = monto_usd * tasa_bcv_global

                conn = get_connection()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO tesoreria (codigo_recibo, fecha, tipo_operacion, concepto, doc_asociado, tercero, metodo_pago, referencia, monto_usd, tasa_bcv, monto_bs, responsable)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (cod_rec, fecha_str, tipo_op, concepto, doc_ref, tercero, metodo, referencia, monto_usd, tasa_bcv_global, monto_bs, user["username"]))

                if tipo_op == "Ingreso" and doc_ref.startswith("VTA-"):
                    cur.execute("UPDATE ventas SET estado_cobro = 'Cobrado' WHERE codigo_venta = ?", (doc_ref.strip(),))
                elif tipo_op == "Egreso" and doc_ref.startswith("COMP-"):
                    cur.execute("UPDATE compras SET estado_pago = 'Pagado' WHERE codigo_compra = ?", (doc_ref.strip(),))

                conn.commit()
                conn.close()

                titulo_pdf = "Comprobante Oficial de Ingreso y Cobranza" if tipo_op == "Ingreso" else "Comprobante Oficial de Egreso y Pago"
                pdf_bytes = generar_pdf_comprobante(
                    titulo_documento=titulo_pdf,
                    codigo_documento=cod_rec,
                    unidad_emisora="Unidad Financiera, Facturación y Tesorería",
                    datos_generales={
                        "Tipo Movimiento": tipo_op.upper(),
                        "Fecha Contable": fecha_str,
                        "Tercero / Razón Social": tercero,
                        "Doc. Asociado": doc_ref or "N/A",
                        "Método de Pago": metodo,
                        "Referencia Bancaria": referencia or "Efectivo en Caja"
                    },
                    columnas_tabla=["Código", "Concepto Contable", "Tasa BCV", "Monto ($)", "Equivalente (Bs)"],
                    filas_tabla=[[cod_rec, concepto, f"Bs {tasa_bcv_global:,.2f}", f"${monto_usd:,.2f}", f"Bs {monto_bs:,.2f}"]],
                    totales={
                        "MONTO LIQUIDADO ($):": f"${monto_usd:,.2f}",
                        "MONTO LIQUIDADO (Bs):": f"Bs {monto_bs:,.2f}"
                    },
                    observaciones="Operación conciliada y asentada en los libros auxiliares de la EPSDC Guerreros de la Montaña.",
                    firmas=("Administrador / Tesorero", "Recibido / Conforme", "Contraloría Social")
                )
                st.session_state.ultimo_pdf_tesoreria = pdf_bytes
                st.session_state.ultimo_cod_tesoreria = cod_rec
                st.success(f"Comprobante {cod_rec} emitido por ${monto_usd:,.2f} (Bs {monto_bs:,.2f}).")

        if st.session_state.ultimo_pdf_tesoreria is not None:
            st.download_button(
                label="Descargar Comprobante Financiero (PDF)",
                data=st.session_state.ultimo_pdf_tesoreria,
                file_name=f"{st.session_state.ultimo_cod_tesoreria}.pdf",
                mime="application/pdf",
                key="btn_descargar_comprobante_teso"
            )

    with tab2:
        st.dataframe(query_df("SELECT * FROM tesoreria ORDER BY id DESC"), use_container_width=True, hide_index=True)

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
