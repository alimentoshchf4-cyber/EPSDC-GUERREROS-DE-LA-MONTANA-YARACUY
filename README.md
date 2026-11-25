# Sistema Integral de Gestión Administrativa y Control Operativo (ERP)
**EPSDC Guerreros de la Montaña de Yaracuy** • RIF: J-507789474

Sistema modular desarrollado en Python con **Streamlit**, base de datos relacional embebida en **SQLite**, control de acceso basado en roles por unidad administrativa y generación automática de comprobantes oficiales en **PDF** con identidad corporativa (*Por Tu Café* / *El Cafetal*).

## Unidades Administrativas Integradas
1. **Unidad de Almacén, Recepción e Inventario:** Control de entradas (materias primas, insumos agrícolas, herramientas, producto terminado) y salidas mediante método FIFO/PEPS, alertas de stock mínimo y Acta de Recepción en PDF.
2. **Unidad de Compras y Abastecimiento:** Gestión de compras de cosecha a pequeños productores (Yaracuy, Lara, Portuguesa, Trujillo), adquisición de insumos/herramientas y emisión de Orden de Compra / Liquidación al Productor en PDF.
3. **Unidad Financiera, Facturación y Tesorería:** Conciliación de ingresos/egresos multimoneda (USD / Bs a tasa BCV), validación de pagos (Pago Móvil, Transferencia, Efectivo), flujo de caja y Comprobante de Ingreso / Egreso y Factura Proforma en PDF.
4. **Unidad de Despacho, Comercialización y Ventas:** Registro de preventas y ventas directas (50g, 100g, 200g e insumos agrícolas), asignación por territorios de Yaracuy y emisión de Nota de Entrega / Guía de Despacho en PDF.
5. **Contraloría Social y Gerencia General:** Panel ejecutivo consolidado en tiempo real, auditoría cruzada entre unidades y exportación a Excel.

## Credenciales de Acceso por Unidad (Demostración)
| Unidad Administrativa | Usuario | Contraseña |
| :--- | :--- | :--- |
| Gerencia General / Contraloría Social | `gerencia` | `Admin2026*` |
| Almacén, Recepción e Inventario | `almacen` | `Almacen2026*` |
| Compras y Abastecimiento | `compras` | `Compras2026*` |
| Finanzas, Facturación y Tesorería | `finanzas` | `Finanzas2026*` |
| Despacho, Comercialización y Ventas | `ventas` | `Ventas2026*` |

## Despliegue en GitHub y Streamlit Community Cloud
1. Sube toda la carpeta del proyecto a un repositorio en **GitHub** (`app.py`, `database.py`, `pdf_generator.py`, `requirements.txt`, `.streamlit/config.toml`, `assets/`).
2. Ingresa a [share.streamlit.io](https://share.streamlit.io), conecta tu cuenta de GitHub y selecciona el repositorio y el archivo principal `app.py`.
3. Haz clic en **Deploy**.

