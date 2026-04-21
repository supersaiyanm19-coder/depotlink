import flet as ft
import urllib.request
import json
import asyncio
from datetime import datetime, timedelta, timezone
import os
# ---------------- Config ----------------
BASE_URL = "https://firestore.googleapis.com/v1/projects/depotlink-f518e/databases/(default)/documents"

# ---------------- Globals ----------------
usuario_actual = {"value": "", "rol": ""}
ultimo_usuario = {"data": None}
surtido_actual = []
productos_cache = []

# ---------------- Firestore helpers ----------------
def delete_request(full_doc_name):
    try:
        url = f"https://firestore.googleapis.com/v1/{full_doc_name}"
        req = urllib.request.Request(url, method="DELETE")
        urllib.request.urlopen(req)
    except Exception as err:
        print("delete_request error:", err)

def get_request(url):
    try:
        with urllib.request.urlopen(url) as response:
            return json.loads(response.read().decode())
    except Exception as err:
        print("get_request error:", err)
        return {}

def post_request(url, data):
    try:
        print("POST URL:", url)
        print("DATA:", data)
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req)
    except Exception as err:
        print("post_request error:", err)

# ---------------- Auth / Login ----------------
def login(usuario, password):
    r = get_request(f"{BASE_URL}/usuarios")

    for doc in r.get("documents", []):
        f = doc.get("fields", {})

        if (
            f.get("usuario", {}).get("stringValue", "").lower().strip() == (usuario or "").lower().strip()
            and f.get("password", {}).get("stringValue", "").lower().strip() == (password or "").lower().strip()
        ):
            return {
                "nombre": f.get("nombre", {}).get("stringValue"),
                "usuario": f.get("usuario", {}).get("stringValue"),
                "rol": f.get("rol", {}).get("stringValue", "user")  # 🔥 CLAVE
            }

    return None

# ---------------- Surtidos / Productos helpers ----------------
def get_surtidos_for_user(usuario):
    if not usuario:
        return []
    r = get_request(f"{BASE_URL}/usuarios/{usuario}/surtidos")
    listas = []
    for doc in r.get("documents", []):
        f = doc.get("fields", {})
        ts = f.get("createdAt", {}).get("timestampValue")
        items_json = f.get("items", {}).get("stringValue", "[]")
        try:
            items = json.loads(items_json)
        except:
            items = []
        doc_name = doc.get("name")
        try:
            created = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except:
            created = None
        listas.append({"doc_name": doc_name, "createdAt": created, "items": items})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recientes = []
    for l in listas:
        if l["createdAt"] and l["createdAt"] < cutoff:
            try:
                delete_request(l["doc_name"])
            except:
                pass
        else:
            recientes.append(l)
    recientes.sort(key=lambda x: x["createdAt"] or datetime.min, reverse=True)
    return recientes

def crear_departamento_si_no_existe(depto):
    try:
        url = f"{BASE_URL}/departamentos/{depto}"
        data = {"fields": {"nombre": {"stringValue": str(depto)}}}
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="PATCH"
        )
        urllib.request.urlopen(req)
    except Exception as err:
        print("Error creando departamento:", err)

def agregar_producto(nombre, codigo, depto):
    try:
        crear_departamento_si_no_existe(depto)

        data = {
            "fields": {
                "nombre": {"stringValue": nombre},
                "codigo": {"stringValue": codigo}
            }
        }
        # guardar en subcolección del departamento
        post_request(f"{BASE_URL}/departamentos/{depto}/productos", data)

        # guardar en índice global (document id = codigo)
        url = f"{BASE_URL}/productos/{codigo}"
        data_global = {
            "fields": {
                "nombre": {"stringValue": nombre},
                "codigo": {"stringValue": codigo},
                "depto": {"stringValue": str(depto)}
            }
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(data_global).encode(),
            headers={"Content-Type": "application/json"},
            method="PATCH"
        )
        try:
            urllib.request.urlopen(req)
        except Exception as err:
            print("Error guardando índice global:", err)

        # invalidar cache global
        global productos_cache
        try:
            productos_cache.clear()
        except Exception:
            productos_cache = []

    except Exception as err:
        print("agregar_producto error:", err)

def guardar_surtido_en_firestore(usuario, surtido_list):
    """Guarda el surtido en Firestore y no modifica la lista local (la función que vacía la lista la llamará el UI)."""
    if not usuario or not surtido_list:
        return
    data = {
        "fields": {
            "createdAt": {"timestampValue": datetime.utcnow().isoformat() + "Z"},
            "items": {"stringValue": json.dumps(surtido_list)}
        }
    }
    post_request(f"{BASE_URL}/usuarios/{usuario}/surtidos", data)

def cargar_productos():
    global productos_cache

    try:
        productos_cache.clear()

        print("Cargando desde departamentos...")

        r_dept = get_request(f"{BASE_URL}/departamentos")

        if not r_dept or "documents" not in r_dept:
            print("⚠️ No hay departamentos")
            return

        for depto in r_dept.get("documents", []):
            depto_id = depto.get("name", "").split("/")[-1]

            r2 = get_request(f"{BASE_URL}/departamentos/{depto_id}/productos")

            if not r2 or "documents" not in r2:
                continue

            for doc in r2.get("documents", []):
                f = doc.get("fields", {})

                nombre = f.get("nombre", {}).get("stringValue", "")
                codigo = f.get("codigo", {}).get("stringValue", "")

                if codigo:
                    productos_cache.append({
                        "nombre": nombre,
                        "codigo": str(codigo),
                        "depto": depto_id
                    })

        print("Productos cargados:", len(productos_cache))

    except Exception as e:
        print("ERROR:", e)

# ---------------- small helper to focus controls safely ----------------
def safe_focus(control):
    try:
        asyncio.create_task(control.focus())
    except Exception:
        try:
            control.focus()
        except Exception:
            pass

# ---------------- APP ----------------
def main(page: ft.Page):
    page.padding = 0
    page.title = "DepotLink"
    try:
        page.window.icon = "icon.png"
    except Exception:
        pass
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.route = "login"

    # UI state (local to main)
    user_name = {"value": ""}
    seleccionado = {"value": None}
    current_dialog = {"value": None}
    lbl_sel = ft.Text("Producto: Ninguno", text_align="center")

    # helpers
    def mostrar_mensaje(texto):
        sb = ft.SnackBar(ft.Text(texto))

        page.overlay.append(sb)
        sb.open = True

        page.update()

    def cerrar_dialog(e=None):
        if page.overlay:
            for c in page.overlay:
                if isinstance(c, ft.AlertDialog):
                    c.open = False
            page.update()

    def vaciar_resumen_local(e=None):
        surtido_actual.clear()
        cerrar_dialog()
        page.update()

    def toggle_theme(e):
        page.theme_mode = ft.ThemeMode.DARK if page.theme_mode == ft.ThemeMode.LIGHT else ft.ThemeMode.LIGHT
        page.update()

    def es_admin():
        return usuario_actual.get("rol") == "admin"

    # ver_historial (muestra diálogo en la página)
    def ver_historial(items):
        lines = []
        total = 0

        for it in items:
            nombre = it["producto"]["nombre"]
            codigo = it["producto"]["codigo"]
            cantidad = it["cantidad"]

            lines.append(f"{nombre} ({codigo}) x{cantidad}")
            total += cantidad

        contenido = ft.Column(
            [ft.Text(l) for l in lines] +
            [ft.Divider(), ft.Text(f"Total: {total}")],
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
            height=300
        )

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Historial - Detalle"),
            content=contenido,
            actions=[
                ft.TextButton("Cerrar", on_click=lambda e: cerrar_dialog())
            ]
        )

        # 🔥 CLAVE (igual que surtido)
        page.overlay.append(dlg)

        dlg.open = True
        page.update()

    # UI helpers
    def barra(titulo, volver=None):
        if volver:
            left = ft.TextButton("←", on_click=lambda e: ir_a(volver))
        else:
            left = ft.Container(width=40)

        title = ft.Text(titulo, size=20, weight="bold", expand=True, text_align="center")

        right_controls = [ft.TextButton("🌙", on_click=toggle_theme)]
        if usuario_actual.get("value"):
            right_controls.append(ft.TextButton("Historial", on_click=lambda e: ir_a("historial")))

        right = ft.Row(right_controls, alignment=ft.MainAxisAlignment.END, spacing=6)
        return ft.Row([left, title, right], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def layout(titulo, contenido, volver=None):
        return ft.SafeArea(
            content=ft.Column(
                [
                    barra(titulo, volver),
                    ft.Container(
                        content=ft.Column(
                            [contenido],
                            alignment=ft.MainAxisAlignment.CENTER,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            expand=True
                        ),
                        alignment=ft.Alignment.CENTER,
                        expand=True,
                        padding=ft.Padding(16,16,16,16),
                    )
                ],
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER
            ),
            expand=True
        )

    # NAV
    def ir_a(vista):
        page.controls.clear()
        page.route = vista

        # LOGIN
        if vista == "login":
            usuario_actual["value"] = ""
            user_name["value"] = ""

            u = ft.TextField(label="Usuario", autofocus=True)
            p = ft.TextField(label="Contraseña", password=True)

            def entrar(e=None):
                data = login(u.value, p.value)

                if data:
                    user_name["value"] = data["nombre"]
                    usuario_actual["value"] = data["usuario"]
                    usuario_actual["rol"] = data["rol"]  # 🔥 IMPORTANTE

                    ir_a("menu")
                else:
                    mostrar_mensaje("Usuario incorrecto")

            u.on_submit = lambda e: safe_focus(p)
            p.on_submit = entrar

            card = ft.Container(
                ft.Column(
                    [
                        ft.Text("DepotLink", size=34, weight="bold", color="blue", text_align="center"),
                        ft.Column([u,p,ft.Row([ft.Button("Entrar", on_click=entrar)],alignment=ft.MainAxisAlignment.CENTER)],
                                  spacing=12,
                                  alignment=ft.MainAxisAlignment.CENTER,
                                  horizontal_alignment=ft.CrossAxisAlignment.CENTER)
                    ],
                    spacing=18,
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER
                ),
                padding=20,
                border_radius=8
            )

            page.add(
                ft.Column(
                    [
                        ft.Container(expand=True),
                        ft.Row([card], alignment=ft.MainAxisAlignment.CENTER),
                        ft.Container(expand=True)
                    ],
                    expand=True,
                    alignment=ft.MainAxisAlignment.CENTER
                )
            )

        # AGREGAR PRODUCTO
        elif vista == "agregar":

            nombre = ft.TextField(label="Nombre del producto", width=420, autofocus=True)
            codigo = ft.TextField(label="Código", width=260)
            depto = ft.TextField(label="Departamento (ej. 90)", width=260)

            def guardar(e=None):
                if not nombre.value or not codigo.value or not depto.value:
                    mostrar_mensaje("⚠️ Completa todos los campos")
                    return

                agregar_producto(nombre.value, codigo.value, depto.value)
                mostrar_mensaje(f"Producto {nombre.value} agregado")

                nombre.value = ""
                codigo.value = ""
                depto.value = ""
                page.update()

            content = ft.Column(
                [
                    nombre,
                    ft.Row([codigo, depto], alignment=ft.MainAxisAlignment.CENTER),
                    ft.Row(
                        [
                            ft.Button("Guardar", on_click=guardar),
                            ft.Button("Volver", on_click=lambda e: ir_a("menu"))
                        ],
                        alignment=ft.MainAxisAlignment.CENTER
                    )
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=15,
                expand=True
            )

            # 🔥 ESTA LÍNEA TE FALTABA
            page.add(layout("Agregar producto", content, "menu"))

        # SUBIR DEPARTAMENTO (JSON)
        elif vista == "subir":
            depto = ft.TextField(label="Departamento", width=260)
            data = ft.TextField(label="JSON productos", multiline=True, width=700, height=300)

            def subir(e):
                try:
                    lista = json.loads(data.value)
                    for p in lista:
                        agregar_producto(p["nombre"], p["codigo"], depto.value)
                    mostrar_mensaje("Departamento subido correctamente")
                except Exception as err:
                    print("Error subir:", err)
                    mostrar_mensaje("Error en JSON")

            content = ft.Column([depto, data, ft.Row([ft.ElevatedButton("Subir", on_click=subir)], alignment=ft.MainAxisAlignment.CENTER)],
                                alignment=ft.MainAxisAlignment.CENTER,
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                expand=True)
            page.add(layout("Subir departamento", content, "menu"))

        # VER USUARIO
        elif vista == "ver_usuario":
            data = ultimo_usuario.get("data")

            def copiar(e):
                if data:
                    texto = f"Usuario: {data['usuario']}\nContraseña: {data['password']}"
                    try:
                        page.set_clipboard(texto)
                        mostrar_mensaje("✅ Copiado")
                    except:
                        mostrar_mensaje("⚠️ Error al copiar")

            if not data:
                content = ft.Text("No hay usuario reciente", text_align="center")
            else:
                content = ft.Column(
                    [
                        ft.Text(f"👤 Usuario: {data['usuario']}"),
                        ft.Text(f"🔑 Contraseña: {data['password']}"),
                        ft.Text(f"📛 Nombre: {data['nombre']}"),

                        ft.Row(
                            [
                                ft.Button("Copiar", on_click=copiar),
                                ft.Button("Volver", on_click=lambda e: ir_a("menu"))
                            ],
                            alignment=ft.MainAxisAlignment.CENTER
                        )
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=10
                )

            page.add(layout("Usuario creado", content, "menu"))

        # MENU
        elif vista == "menu":
            botones = ft.Column(
                [
                    ft.Text(f"Hola {user_name['value']}", size=18),

                    ft.Button("📦 Surtido", on_click=lambda e: ir_a("surtido"), width=250),
                    ft.Button("➕ Agregar producto", on_click=lambda e: ir_a("agregar"), width=250),

                    ft.Button("👤 Crear usuario", on_click=lambda e: ir_a("crear_usuario"), width=250) if es_admin() else ft.Container(),
                    ft.Button("📦 Subir departamento", on_click=lambda e: ir_a("subir"), width=250) if es_admin() else ft.Container(),

                    ft.TextButton("Cerrar sesión", on_click=lambda e: (usuario_actual.update({"value": ""}), ir_a("login")))
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=15,
                expand=True
            )

            page.add(layout("Menú principal", botones))

        # CREAR USUARIO
        elif vista == "crear_usuario":
            nombre = ft.TextField(label="Nombre", width=420, autofocus=True)
            usuario_input = ft.TextField(label="Usuario (opcional)", width=320)
            password = ft.TextField(label="Contraseña", width=320)

            def crear(e=None):
                if usuario_input.value:
                    usuario = usuario_input.value.lower().strip()
                else:
                    usuario = nombre.value.lower().replace(" ", "")[:8]
                data = {
                    "fields": {
                        "nombre": {"stringValue": nombre.value},
                        "usuario": {"stringValue": usuario},
                        "password": {"stringValue": password.value}
                    }
                }
                post_request(f"{BASE_URL}/usuarios", data)
                ultimo_usuario["data"] = {"nombre": nombre.value, "usuario": usuario, "password": password.value}
                nombre.value = ""
                password.value = ""
                page.update()
                ir_a("ver_usuario")

            nombre.on_submit = lambda e: safe_focus(password)
            password.on_submit = crear

            content = ft.Column([nombre,usuario_input,password, ft.Row([ft.ElevatedButton("Crear", on_click=crear, width=200)], alignment=ft.MainAxisAlignment.CENTER)],
                                alignment=ft.MainAxisAlignment.CENTER,
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                spacing=12,
                                expand=True)
            page.add(layout("Crear usuario", content, "menu"))

        # SURTIDO (BÚSQUEDA + AGREGAR)
        elif vista == "surtido":

            txt_cantidad = ft.TextField(label="Cantidad", width=120)
            search_field = ft.TextField(label="Buscar", width=700)

            list_results = ft.ListView(
                expand=True,
                spacing=6,
                padding=ft.Padding(8, 8, 8, 8),
                auto_scroll=False
            )

            def seleccionar_producto(prod, focus_quantity=True):
                seleccionado["value"] = prod
                print("DEBUG seleccionado OK:", prod)

                lbl_sel.value = f"Producto: {prod['nombre']} ({prod['codigo']})"
                page.update()

                if focus_quantity:
                    safe_focus(txt_cantidad)

            def mostrar_todos_en_listview():
                list_results.controls.clear()

                for item in productos_cache:
                    list_results.controls.append(
                        ft.ListTile(
                            title=ft.Text(item["nombre"]),
                            subtitle=ft.Text(f"{item['codigo']} — Dept: {item['depto']}"),
                            on_click=lambda e, p=item: seleccionar_producto(p),
                            content_padding=ft.Padding(8, 4, 8, 4)
                        )
                    )

                page.update()

            def buscar(e=None, submit=False):
                txt = (search_field.value or "").strip().lower()
                list_results.controls.clear()

                if not productos_cache:
                    cargar_productos()

                if not txt:
                    mostrar_todos_en_listview()
                    return

                resultados = []

                for p in productos_cache:
                    nombre = (p.get("nombre") or "").lower()
                    codigo = str(p.get("codigo") or "").lower()

                    if txt in nombre or txt in codigo:
                        resultados.append(p)

                for item in resultados:
                    list_results.controls.append(
                        ft.ListTile(
                            title=ft.Text(item["nombre"]),
                            subtitle=ft.Text(f"{item['codigo']} — Dept: {item['depto']}"),
                            on_click=lambda e, p=item: seleccionar_producto(p),
                            content_padding=ft.Padding(8, 4, 8, 4)
                        )
                    )

                page.update()

            search_field.on_change = lambda e: buscar()
            search_field.on_submit = lambda e: buscar(submit=True)

            def agregar_seleccion(e=None):
                prod = seleccionado.get("value")

                print("DEBUG seleccionado:", prod)

                if not prod:
                    mostrar_mensaje("⚠️ Selecciona un producto primero")
                    return

                try:
                    cantidad = int(txt_cantidad.value) if txt_cantidad.value else 1
                    if cantidad <= 0:
                        raise ValueError()
                except:
                    mostrar_mensaje("Cantidad inválida")
                    return

                surtido_actual.append({
                    "producto": prod,
                    "cantidad": cantidad
                })

                print("DEBUG agregado:", surtido_actual)

                mostrar_mensaje(f"Agregado: {prod['nombre']} x{cantidad}")

                seleccionado["value"] = None
                lbl_sel.value = "Producto: Ninguno"
                txt_cantidad.value = ""

                page.update()

            def guardar_y_vaciar(dlg):
                if not surtido_actual:
                    mostrar_mensaje("No hay productos para guardar")
                    return

                guardar_surtido_en_firestore(usuario_actual.get("value"), surtido_actual)
                surtido_actual.clear()

                # 🔥 CERRAR ESTE dialog directamente
                dlg.open = False

                page.update()

                mostrar_mensaje("✅ Surtido guardado")

            def ver_resumen(e=None):
                if not surtido_actual:
                    mostrar_mensaje("No hay productos en el resumen")
                    return

                lines = []
                total_items = 0

                for it in surtido_actual:
                    nombre = it["producto"]["nombre"]
                    codigo = it["producto"]["codigo"]
                    cantidad = it["cantidad"]

                    lines.append(f"{nombre} ({codigo}) x{cantidad}")
                    total_items += cantidad

                contenido = ft.Column(
                    [ft.Text(l) for l in lines] +
                    [ft.Divider(), ft.Text(f"Total items: {total_items}")],
                    spacing=6,
                    scroll=ft.ScrollMode.AUTO,
                    height=300
                )

                dlg = ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Resumen de surtido"),
                    content=contenido,
                    actions=[
                        ft.TextButton("Cerrar", on_click=lambda e: cerrar_dialog()),
                        ft.Button("Guardar resumen", on_click=lambda e: guardar_y_vaciar(dlg))
                    ]
                )

                page.overlay.clear()  # 🔥 importante
                page.overlay.append(dlg)

                dlg.open = True
                page.update()

            txt_cantidad.on_submit = agregar_seleccion

            acciones = ft.Column(
                [
                    ft.Row(
                        [
                            txt_cantidad,
                            ft.Button("Agregar", on_click=lambda e: agregar_seleccion())
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=10
                    ),

                    ft.Row(
                        [
                            ft.Button("Ver resumen", on_click=lambda e: ver_resumen(), width=200)
                        ],
                        alignment=ft.MainAxisAlignment.CENTER
                    )
                ],
                spacing=10
            )
            list_container = ft.Container(
                content=list_results,
                expand=True,
                height=300
            )

            content = ft.Column(
                [
                    ft.Row([search_field], alignment=ft.MainAxisAlignment.CENTER),
                    list_container,
                    lbl_sel,
                    acciones
                ],
                alignment=ft.MainAxisAlignment.START,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=10,
                expand=True
            )
            page.add(layout("Surtido", content, "menu"))

            # 🔥 SIEMPRE recargar
            cargar_productos()

            mostrar_todos_en_listview()

        # HISTORIAL
        elif vista == "historial":

            historial = get_surtidos_for_user(usuario_actual.get("value"))

            lista = ft.Column(spacing=10)

            if not historial:
                lista.controls.append(ft.Text("No hay historial"))
            else:
                for h in historial:
                    fecha = h["createdAt"].strftime("%Y-%m-%d %H:%M:%S") if h["createdAt"] else "Sin fecha"
                    resumen_text = f"{fecha} — {len(h['items'])} items"

                    def make_load(items):
                        def load(ev):
                            surtido_actual.clear()
                            for it in items:
                                surtido_actual.append(it)
                            mostrar_mensaje("Lista cargada en surtido")
                            ir_a("surtido")
                        return load

                    def make_delete(doc_name):
                        def delete(ev):
                            delete_request(doc_name)
                            mostrar_mensaje("Lista borrada")
                            ir_a("historial")
                        return delete

                    def make_view(items):
                        def view(ev):
                            ver_historial(items)
                        return view

                    lista.controls.append(
                        ft.Row(
                            [
                                ft.Text(resumen_text, expand=True),
                                ft.Row(
                                    [
                                        ft.Button("Ver", on_click=make_view(h["items"])),
                                        ft.Button("Cargar", on_click=make_load(h["items"])),
                                        ft.TextButton("Borrar", on_click=make_delete(h["doc_name"]))
                                    ],
                                    spacing=8
                                )
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            spacing=8
                        )
                    )

            page.add(layout("Historial", lista, "menu"))
            page.update()

    ir_a("login")

if __name__ == "__main__":
    ft.app(
        target=main,
        view=ft.AppView.WEB_BROWSER,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000))
    )