import os
import io
from datetime import datetime
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

# Inicializar Base de Datos
init_db()

# Estilos Corporativos CSS
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

# Estado de Sesión
if "usuario" not in st.session_state:
    st.session_state.usuario = None

for key in [
    "ultimo_pdf_almacen", "ultimo_cod_almacen",
    "ultimo_pdf_compra", "ultimo_cod_compra",
    "ultimo_pdf_tesoreria", "ultimo_cod_tesoreria",
    "ultimo_pdf_venta", "ultimo_cod_venta"
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
            if os.path.exists("assets/logo_el_cafetal.png"):
                st.image("assets/logo_el_cafetal.png", use_container_width=True)
        with c_title:
            st.markdown(
                "<h3 style='text-align:center; color:#1B4332; margin-bottom:0;'>EPSDC Guerreros de la Montaña</h3>"
                "<p style='text-align:center; color:#9B2226; font-weight:bold; margin-top:2px;'>RIF: J-507789474 • Yaracuy</p>",
                unsafe_allow_html=True
            )
        with c_logo2:
            if os.path.exists("assets/logo_por_tu_cafe.png"):
                st.image("assets/logo_por_tu_cafe.png", use_container_width=True)

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
                    st.rerun()
                else:
                    st.error("Credenciales inválidas. Verifique su usuario y clave asignada.")

        with st.expander("Ver Claves de Acceso de Prueba por Unidad"):
            st.markdown("""
            * **Gerencia / Contraloría Social:** `gerencia` | `Admin2026*`
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

# Barra Lateral Corporativa
with st.sidebar:
    c1, c2 = st.columns(2)
    with c1:
        if os.path.exists("assets/logo_el_cafetal.png"):
            st.image("assets/logo_el_cafetal.png", use_container_width=True)
    with c2:
        if os.path.exists("assets/logo_por_tu_cafe.png"):
            st.image("assets/logo_por_tu_cafe.png", use_container_width=True)

    st.markdown(f"**Unidad Activa:**\n`{user['unidad']}`")
    st.markdown(f"**Responsable:** {user['nombre_completo']}")
    st.markdown(f"**Cargo:** {user['cargo']}")
    st.markdown("---")

    tasa_bcv_global = st.number_input("Tasa Oficial BCV del Día (Bs/$)", min_value=1.0, value=46.50, step=0.10)
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
            df_inv[["sku", "nombre", "categoria", "stock_actual", "stock_minimo", "costo_unitario_usd", "precio_mayor_usd"]],
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
    tab1, tab2, tab3 = st.tabs(["Registrar Entrada / Salida", "Inventario Maestro y Nuevos Rubros", "Historial Kardex y Actas PDF"])

    df_inv = query_df("SELECT * FROM inventario")

    with tab1:
        st.markdown("#### Movimiento de Almacén (Recepción de Maquila/Cosecha o Ajuste)")
        with st.form("form_mov_almacen"):
            c1, c2, c3 = st.columns(3)
            tipo_mov = c1.selectbox("Tipo de Movimiento", ["Entrada - Recepción Mercancía", "Salida - Consumo / Merma / Traslado"])
            producto_sel = c2.selectbox("Rubro / SKU", df_inv["sku"] + " | " + df_inv["nombre"])
            cantidad_mov = c3.number_input("Cantidad (Bultos / Sacos / Unidades)", min_value=1.0, step=1.0)

            c4, c5 = st.columns(2)
            lote_mov = c4.text_input("Número de Lote / Cosecha", value=f"LOTE-{datetime.now().strftime('%Y%m')}")
            origen_mov = c5.text_input("Origen / Proveedor / Destino", value="Torrefactora El Cafetal - Portuguesa")
            obs_mov = st.text_area("Observaciones de Control de Calidad (Humedad, Sellado, Peso)")

            submitted_alm = st.form_submit_button("Registrar Movimiento y Generar Acta PDF", type="primary")

        if submitted_alm:
            sku_sel = producto_sel.split(" | ")[0]
            prod_row = df_inv[df_inv["sku"] == sku_sel].iloc[0]
            cod_doc = generar_codigo("ALM", "movimientos_almacen")
            fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            if "Salida" in tipo_mov and prod_row["stock_actual"] < cantidad_mov:
                st.error("Stock insuficiente en almacén para procesar esta salida.")
            else:
                nuevo_stock = prod_row["stock_actual"] + cantidad_mov if "Entrada" in tipo_mov else prod_row["stock_actual"] - cantidad_mov
                conn = get_connection()
                cur = conn.cursor()
                cur.execute("UPDATE inventario SET stock_actual = ? WHERE sku = ?", (nuevo_stock, sku_sel))
                cur.execute("""
                    INSERT INTO movimientos_almacen (codigo_doc, fecha, tipo_movimiento, sku, cantidad, lote, origen_destino, responsable, observaciones)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (cod_doc, fecha_str, tipo_mov, sku_sel, cantidad_mov, lote_mov, origen_mov, user["username"], obs_mov))
                conn.commit()
                conn.close()

                pdf_bytes = generar_pdf_comprobante(
                    titulo_documento="Acta de Recepción y Movimiento de Almacén",
                    codigo_documento=cod_doc,
                    unidad_emisora="Unidad de Almacén, Recepción e Inventario",
                    datos_generales={
                        "Tipo Operación": tipo_mov,
                        "Fecha y Hora": fecha_str,
                        "Origen / Destino": origen_mov,
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
                label="Descargar Acta de Almacén (PDF)",
                data=st.session_state.ultimo_pdf_almacen,
                file_name=f"{st.session_state.ultimo_cod_almacen}.pdf",
                mime="application/pdf",
                key="btn_descargar_acta_almacen"
            )

    with tab2:
        st.markdown("#### Incorporar Nuevo Rubro al Catálogo Maestro")
        with st.form("form_nuevo_sku"):
            c1, c2, c3 = st.columns(3)
            n_sku = c1.text_input("Código SKU Único", placeholder="Ej: GRA-CAR-01")
            n_nombre = c2.text_input("Nombre y Presentación", placeholder="Ej: Caraotas Negras Seleccionadas (Bulto x 20kg)")
            n_cat = c3.selectbox("Categoría", ["Producto Terminado - Café", "Materia Prima Agrícola", "Insumos Agrícolas", "Herramientas Agrícolas", "Otros Alimentos"])

            c4, c5, c6, c7 = st.columns(4)
            n_medida = c4.text_input("Unidad Medida", value="Bulto")
            n_min = c5.number_input("Stock Mínimo Alerta", min_value=1.0, value=10.0)
            n_costo = c6.number_input("Costo Unitario ($)", min_value=0.0, value=15.0)
            n_precio_m = c7.number_input("Precio Mayorista ($)", min_value=0.0, value=20.0)

            submitted_sku = st.form_submit_button("Guardar Nuevo Producto en Catálogo")

        if submitted_sku:
            if n_sku and n_nombre:
                conn = get_connection()
                cur = conn.cursor()
                try:
                    cur.execute("""
                        INSERT INTO inventario (sku, nombre, categoria, unidad_medida, stock_actual, stock_minimo, costo_unitario_usd, precio_detal_usd, precio_mayor_usd)
                        VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """, (n_sku.upper(), n_nombre, n_cat, n_medida, n_min, n_costo, n_precio_m * 1.12, n_precio_m))
                    conn.commit()
                    st.success(f"Rubro {n_sku.upper()} creado exitosamente.")
                except Exception as e:
                    st.error(f"Error al crear SKU: {e}")
                finally:
                    conn.close()

        st.dataframe(df_inv, use_container_width=True, hide_index=True)

    with tab3:
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
# MÓDULO 4: UNIDAD DE DESPACHO, COMERCIALIZACIÓN Y VENTAS
# ==============================================================================
elif modulo_activo == "4. Despacho, Comercialización y Ventas":
    st.subheader("Unidad de Despacho, Comercialización y Ventas (Territorios Yaracuy)")
    df_inv = query_df("SELECT * FROM inventario WHERE stock_actual > 0")

    tab1, tab2 = st.tabs(["Registrar Venta / Preventa y Guía de Despacho PDF", "Cartera de Pedidos y Rutas"])

    with tab1:
        with st.form("form_venta"):
            c1, c2, c3 = st.columns(3)
            cliente = c1.text_input("Comercio / Cliente / Productor", placeholder="Ej: Supermercado El Sol C.A.")
            rif_cli = c2.text_input("RIF / Cédula", placeholder="Ej: J-30981234-5")
            promotor = c3.selectbox("Promotor de Ventas", ["Carlos Pacheco", "Brayant López", "Salvador Leal", "Yofre Muñoz", "Carlos Oropeza", "Venta Directa Sede"])

            c4, c5, c6 = st.columns(3)
            territorio = c4.selectbox("Territorio Comercial", [
                "Circuito 01 - Eje Metropolitano",
                "Zona 02 - Eje Productivo Sur",
                "Zona 03 - Eje Centro-Occidente",
                "Zona 04 - Eje Centro-Norte",
                "Zona 05 - Eje Bolívar / Veroes / Monge",
                "Circuito 06 - Eje Costero Morón / Pto. Cabello"
            ])
            municipio = c5.selectbox("Municipio", ["San Felipe", "Independencia", "Cocorote", "Nirgua", "Yaritagua", "Bruzual", "Sucre", "Bastidas", "Trinidad", "Urachiche", "Bolívar", "Veroes", "Manuel Monge", "Juan José Mora"])
            escala = c6.selectbox("Escala de Precio", ["Mayorista", "Detal / Bodega"])

            c7, c8, c9 = st.columns(3)
            prod_vta = c7.selectbox("Producto / Rubro a Despachar", df_inv["sku"] + " | " + df_inv["nombre"] + " (Disp: " + df_inv["stock_actual"].astype(str) + ")")
            cant_vta = c8.number_input("Cantidad (Bultos / Unidades)", min_value=1.0, value=5.0)
            cond_pago = c9.selectbox("Condición de Pago", ["Contado", "Crédito 7 Días", "Crédito 15 Días", "Trueque / Acopio"])

            submitted_venta = st.form_submit_button("Procesar Pedido, Descontar Stock y Generar Guía PDF", type="primary")

        if submitted_venta:
            if not cliente.strip() or not rif_cli.strip():
                st.error("Debe completar el Nombre del Comercio / Cliente y el RIF o Cédula.")
            else:
                sku_v = prod_vta.split(" | ")[0]
                prod_row = df_inv[df_inv["sku"] == sku_v].iloc[0]

                if prod_row["stock_actual"] < cant_vta:
                    st.error(f"Stock insuficiente. Disponible en almacén: {prod_row['stock_actual']}")
                else:
                    precio_u = prod_row["precio_mayor_usd"] if escala == "Mayorista" else prod_row["precio_detal_usd"]
                    total_usd = cant_vta * precio_u
                    total_bs = total_usd * tasa_bcv_global
                    cod_vta = generar_codigo("VTA", "ventas")
                    fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                    est_cobro = "Cobrado" if cond_pago == "Contado" else "Pendiente de Cobro"

                    conn = get_connection()
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT INTO ventas (codigo_venta, fecha, cliente_comercio, rif_cedula, territorio, municipio, promotor, escala_precio, sku, cantidad, precio_unitario_usd, total_usd, tasa_bcv, total_bs, condicion_pago, estado_despacho, estado_cobro, responsable)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (cod_vta, fecha_str, cliente, rif_cli, territorio, municipio, promotor, escala, sku_v, cant_vta, precio_u, total_usd, tasa_bcv_global, total_bs, cond_pago, "Despachado", est_cobro, user["username"]))
                    cur.execute("UPDATE inventario SET stock_actual = stock_actual - ? WHERE sku = ?", (cant_vta, sku_v))
                    conn.commit()
                    conn.close()

                    pdf_bytes = generar_pdf_comprobante(
                        titulo_documento="Nota de Entrega y Guía de Despacho Comercial",
                        codigo_documento=cod_vta,
                        unidad_emisora="Unidad de Despacho, Comercialización y Ventas",
                        datos_generales={
                            "Cliente / Comercio": cliente,
                            "RIF / Cédula": rif_cli,
                            "Territorio / Ruta": territorio,
                            "Municipio Destino": municipio,
                            "Promotor Asignado": promotor,
                            "Condición Pago": f"{cond_pago} ({est_cobro})"
                        },
                        columnas_tabla=["SKU", "Descripción del Producto", "Bultos/Unid", "Precio Unit. ($)", "Subtotal ($)"],
                        filas_tabla=[[sku_v, prod_row["nombre"], f"{cant_vta:,.2f}", f"${precio_u:,.2f}", f"${total_usd:,.2f}"]],
                        totales={
                            "TOTAL A PAGAR ($):": f"${total_usd:,.2f}",
                            "REF. BOLÍVARES (BCV):": f"Bs {total_bs:,.2f}"
                        },
                        observaciones=f"Tarifa aplicada: {escala}. Tasa referencial BCV: Bs {tasa_bcv_global:,.2f}/$. Mercancía verificada al salir del Centro de Acopio.",
                        firmas=("Jefe de Despacho y Ventas", "Recibido Conforme (Cliente)", "Control de Salida Almacén")
                    )
                    st.session_state.ultimo_pdf_venta = pdf_bytes
                    st.session_state.ultimo_cod_venta = cod_vta
                    st.success(f"Pedido {cod_vta} procesado por ${total_usd:,.2f} (Bs {total_bs:,.2f}).")

        if st.session_state.ultimo_pdf_venta is not None:
            st.download_button(
                label="Descargar Nota de Entrega / Guía (PDF)",
                data=st.session_state.ultimo_pdf_venta,
                file_name=f"{st.session_state.ultimo_cod_venta}.pdf",
                mime="application/pdf",
                key="btn_descargar_guia_venta"
            )

    with tab2:
        st.dataframe(query_df("SELECT * FROM ventas ORDER BY id DESC"), use_container_width=True, hide_index=True)
