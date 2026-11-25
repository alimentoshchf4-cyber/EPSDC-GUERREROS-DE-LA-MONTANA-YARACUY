import sqlite3
import pandas as pd
from datetime import datetime
import hashlib

DB_PATH = "epsdc_guerreros.db"

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Tabla de Usuarios y Roles por Unidad
    cur.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        nombre_completo TEXT NOT NULL,
        cargo TEXT NOT NULL,
        unidad TEXT NOT NULL,
        rol TEXT NOT NULL
    )
    """)

    # Tabla de Catálogo e Inventario Maestro (Materias Primas, Insumos, Herramientas, Producto Terminado)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS inventario (
        sku TEXT PRIMARY KEY,
        nombre TEXT NOT NULL,
        categoria TEXT NOT NULL,
        unidad_medida TEXT NOT NULL,
        stock_actual REAL NOT NULL DEFAULT 0,
        stock_minimo REAL NOT NULL DEFAULT 10,
        costo_unitario_usd REAL NOT NULL DEFAULT 0,
        precio_detal_usd REAL NOT NULL DEFAULT 0,
        precio_mayor_usd REAL NOT NULL DEFAULT 0,
        ubicacion_bodega TEXT DEFAULT 'Almacén Principal Nirgua'
    )
    """)

    # Tabla de Movimientos de Almacén (Kardex)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS movimientos_almacen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_doc TEXT UNIQUE NOT NULL,
        fecha TEXT NOT NULL,
        tipo_movimiento TEXT NOT NULL,
        sku TEXT NOT NULL,
        cantidad REAL NOT NULL,
        lote TEXT,
        origen_destino TEXT NOT NULL,
        responsable TEXT NOT NULL,
        observaciones TEXT,
        FOREIGN KEY (sku) REFERENCES inventario(sku)
    )
    """)

    # Tabla de Compras y Acopio de Cosecha / Insumos
    cur.execute("""
    CREATE TABLE IF NOT EXISTS compras (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_compra TEXT UNIQUE NOT NULL,
        fecha TEXT NOT NULL,
        proveedor_productor TEXT NOT NULL,
        cedula_rif TEXT NOT NULL,
        municipio_estado TEXT NOT NULL,
        tipo_adquisicion TEXT NOT NULL,
        sku TEXT NOT NULL,
        cantidad REAL NOT NULL,
        costo_unitario_usd REAL NOT NULL,
        total_usd REAL NOT NULL,
        tasa_bcv REAL NOT NULL,
        total_bs REAL NOT NULL,
        estado_pago TEXT NOT NULL,
        observaciones TEXT,
        responsable TEXT NOT NULL
    )
    """)

    # Tabla de Ventas y Despachos Comerciales
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ventas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_venta TEXT UNIQUE NOT NULL,
        fecha TEXT NOT NULL,
        cliente_comercio TEXT NOT NULL,
        rif_cedula TEXT NOT NULL,
        territorio TEXT NOT NULL,
        municipio TEXT NOT NULL,
        promotor TEXT NOT NULL,
        escala_precio TEXT NOT NULL,
        sku TEXT NOT NULL,
        cantidad REAL NOT NULL,
        precio_unitario_usd REAL NOT NULL,
        total_usd REAL NOT NULL,
        tasa_bcv REAL NOT NULL,
        total_bs REAL NOT NULL,
        condicion_pago TEXT NOT NULL,
        estado_despacho TEXT NOT NULL,
        estado_cobro TEXT NOT NULL,
        responsable TEXT NOT NULL
    )
    """)

    # Tabla de Tesorería y Movimientos Financieros
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tesoreria (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_recibo TEXT UNIQUE NOT NULL,
        fecha TEXT NOT NULL,
        tipo_operacion TEXT NOT NULL,
        concepto TEXT NOT NULL,
        doc_asociado TEXT,
        tercero TEXT NOT NULL,
        metodo_pago TEXT NOT NULL,
        referencia TEXT,
        monto_usd REAL NOT NULL,
        tasa_bcv REAL NOT NULL,
        monto_bs REAL NOT NULL,
        responsable TEXT NOT NULL
    )
    """)

    # Sembrar usuarios iniciales si la tabla está vacía
    cur.execute("SELECT COUNT(*) FROM usuarios")
    if cur.fetchone()[0] == 0:
        usuarios_iniciales = [
            ("gerencia", hash_password("Admin2026*"), "Vocería de Administración y Contraloría", "Coordinador General EPSDC", "Gerencia General y Contraloría Social", "ADMIN"),
            ("almacen", hash_password("Almacen2026*"), "Coordinador de Almacén", "Jefe de Almacén e Inventario", "Unidad de Almacén, Recepción e Inventario", "ALMACEN"),
            ("compras", hash_password("Compras2026*"), "Coordinador de Acopio y Compras", "Analista de Compras y Abastecimiento", "Unidad de Compras y Abastecimiento", "COMPRAS"),
            ("finanzas", hash_password("Finanzas2026*"), "Coordinador de Finanzas y Tesorería", "Administrador Contable y Tesorero", "Unidad Financiera, Facturación y Tesorería", "FINANZAS"),
            ("ventas", hash_password("Ventas2026*"), "Coordinador de Comercialización", "Jefe de Despacho y Fuerza de Ventas", "Unidad de Despacho, Comercialización y Ventas", "VENTAS"),
        ]
        cur.executemany(
            "INSERT INTO usuarios (username, password_hash, nombre_completo, cargo, unidad, rol) VALUES (?, ?, ?, ?, ?, ?)",
            usuarios_iniciales
        )

    # Sembrar catálogo inicial de productos (Café 50g, 100g, 200g, Materias Primas e Insumos Agrícolas)
    cur.execute("SELECT COUNT(*) FROM inventario")
    if cur.fetchone()[0] == 0:
        productos_iniciales = [
            ("PTC-050", "Por Tu Café Molido Gourmet 50g (Bulto x 40 unid)", "Producto Terminado - Café", "Bulto", 120.0, 25.0, 22.00, 28.00, 26.00, "Estante A1 - Producto Terminado"),
            ("PTC-100", "Por Tu Café Molido Gourmet 100g (Bulto x 25 unid)", "Producto Terminado - Café", "Bulto", 150.0, 30.0, 26.50, 34.00, 31.50, "Estante A2 - Producto Terminado"),
            ("PTC-200", "Por Tu Café Molido Gourmet 200g (Bulto x 20 unid)", "Producto Terminado - Café", "Bulto", 180.0, 35.0, 41.00, 52.00, 48.00, "Estante A3 - Producto Terminado"),
            ("MP-CAF-ORO", "Café Verde / Oro Seleccionado (Quintal 46 kg)", "Materia Prima Agrícola", "Quintal", 65.0, 15.0, 135.00, 155.00, 148.00, "Silo / Zona Acopio B1"),
            ("MP-ARR-PAD", "Arroz Paddy Húmedo/Acondicionado (Saco 50 kg)", "Materia Prima Agrícola", "Saco", 90.0, 20.0, 24.00, 30.00, 28.00, "Zona Acopio B2"),
            ("INS-FERT-NPK", "Fertilizante Granulado NPK 15-15-15 (Saco 50 kg)", "Insumos Agrícolas", "Saco", 80.0, 15.0, 32.00, 38.00, 36.00, "Bodega Insumos C1"),
            ("INS-BIO-FOL", "Bioestimulante Orgánico Foliar (Galón 4 Lts)", "Insumos Agrícolas", "Galón", 55.0, 10.0, 14.00, 18.50, 17.00, "Bodega Insumos C2"),
            ("HER-MACH-22", "Machete Agrícola Acero Templado 22 Pulgadas", "Herramientas Agrícolas", "Unidad", 70.0, 15.0, 6.50, 9.00, 8.20, "Estante Herramientas D1"),
            ("HER-ASPER-20", "Asperjadora Manual de Espalda 20 Litros", "Herramientas Agrícolas", "Unidad", 25.0, 5.0, 34.00, 44.00, 41.00, "Estante Herramientas D2"),
        ]
        cur.executemany(
            """INSERT INTO inventario 
            (sku, nombre, categoria, unidad_medida, stock_actual, stock_minimo, costo_unitario_usd, precio_detal_usd, precio_mayor_usd, ubicacion_bodega) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            productos_iniciales
        )

    # Sembrar transacciones de demostración para tableros iniciales
    cur.execute("SELECT COUNT(*) FROM ventas")
    if cur.fetchone()[0] == 0:
        hoy = datetime.now().strftime("%Y-%m-%d %H:%M")
        cur.execute("""
            INSERT INTO compras (codigo_compra, fecha, proveedor_productor, cedula_rif, municipio_estado, tipo_adquisicion, sku, cantidad, costo_unitario_usd, total_usd, tasa_bcv, total_bs, estado_pago, observaciones, responsable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("COMP-2026-001", hoy, "Asociación Productores Salom", "V-12458963", "Nirgua, Yaracuy", "Acopio de Cosecha", "MP-CAF-ORO", 20, 135.0, 2700.0, 46.50, 125550.0, "Pagado", "Cosecha ciclo norte-verano", "compras"))

        cur.execute("""
            INSERT INTO ventas (codigo_venta, fecha, cliente_comercio, rif_cedula, territorio, municipio, promotor, escala_precio, sku, cantidad, precio_unitario_usd, total_usd, tasa_bcv, total_bs, condicion_pago, estado_despacho, estado_cobro, responsable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("VTA-2026-001", hoy, "Abastos y Panadería La Montaña C.A.", "J-40129831-0", "Circuito 01 - Eje Metropolitano", "San Felipe", "Carlos Pacheco", "Mayorista", "PTC-200", 10, 48.0, 480.0, 46.50, 22320.0, "Contado", "Entregado", "Cobrado", "ventas"))

        cur.execute("""
            INSERT INTO tesoreria (codigo_recibo, fecha, tipo_operacion, concepto, doc_asociado, tercero, metodo_pago, referencia, monto_usd, tasa_bcv, monto_bs, responsable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("REC-2026-001", hoy, "Ingreso", "Cobro Venta Mayorista Por Tu Café 200g", "VTA-2026-001", "Abastos y Panadería La Montaña C.A.", "Pago Móvil", "00489211", 480.0, 46.50, 22320.0, "finanzas"))

    conn.commit()
    conn.close()

def autenticar_usuario(username: str, password: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM usuarios WHERE username = ? AND password_hash = ?", (username.strip().lower(), hash_password(password)))
    user = cur.fetchone()
    conn.close()
    return dict(user) if user else None

def query_df(sql: str, params=()):
    conn = get_connection()
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df

def generar_codigo(prefijo: str, tabla: str) -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {tabla}")
    count = cur.fetchone()[0] + 1
    conn.close()
    year = datetime.now().year
    return f"{prefijo}-{year}-{count:04d}"
