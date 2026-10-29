import os
import threading
import traceback

import customtkinter as ctk

from scrapCreditos import ControlScraping, formatear_creditos, obtener_creditos

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

app = ctk.CTk()
app.geometry("820x880")
app.title("Analizador SIU Guaraní")

# Ícono de la ventana (si existe logo.ico junto a este archivo)
try:
    app.iconbitmap(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.ico"))
except Exception:
    pass

# Se crea un control nuevo por cada análisis: así no quedan señales viejas colgadas.
control = ControlScraping()

# ----------------------------
# FUNCIONES
# ----------------------------
def iniciar_analisis():
    global control
    # El estado se limpia en el HILO PRINCIPAL, antes de arrancar el hilo de scraping.
    control = ControlScraping()
    boton_iniciar.configure(state="disabled")
    boton_listo.configure(state="normal")
    boton_cancelar.configure(state="normal")
    label_estado.configure(text="Logueate en el SIU Guaraní y presioná 'Listo'")
    limpiar()
    threading.Thread(target=ejecutar_scraping, args=(control,), daemon=True).start()

def usuario_listo():
    control.listo()
    boton_listo.configure(state="disabled")
    label_estado.configure(text="Procesando datos...")

def cancelar_analisis():
    control.cancelar()
    boton_listo.configure(state="disabled")
    boton_cancelar.configure(state="disabled")
    label_estado.configure(text="Cancelando...")

def restablecer_botones():
    boton_iniciar.configure(state="normal")
    boton_listo.configure(state="disabled")
    boton_cancelar.configure(state="disabled")

def registrar_log(mensaje):
    """Callback que el scraper usa para reportar progreso; se dibuja en el hilo de Tk."""
    def escribir():
        caja_log.configure(state="normal")
        caja_log.insert("end", mensaje + "\n")
        caja_log.see("end")
        caja_log.configure(state="disabled")
    app.after(0, escribir)

def ejecutar_scraping(control_local):
    try:
        total_materias, total_libres, total, detalle_materias, detalle_libres = obtener_creditos(
            log=registrar_log,
            control=control_local,
        )
    except Exception as e:
        # El traceback completo va a la consola y al Registro de la GUI.
        error_completo = traceback.format_exc()
        print(error_completo)
        registrar_log(error_completo.rstrip())
        # El texto se formatea ANTES del after: 'e' deja de existir cuando termina el except.
        if control_local.cancelado.is_set():
            mensaje = "Análisis cancelado ✔"
        else:
            mensaje = f"Error: {e}"
        app.after(0, lambda: finalizar_con_error(mensaje))
        return

    app.after(0, lambda: mostrar_resultados(total_materias, total_libres, total, detalle_materias, detalle_libres))

def finalizar_con_error(mensaje):
    label_estado.configure(text=mensaje)
    restablecer_botones()

def color_fila(indice):
    """Rayado de filas según el modo de apariencia (oscuro o claro)."""
    colores = ("#3a3a3a", "#2a2a2a") if ctk.get_appearance_mode() == "Dark" else ("#ffffff", "#eeeeee")
    return colores[indice % 2]

def agregar_fila_grid(frame, fila_idx, col1, col2, col3, col4=None, es_header=False):
    font = ("Arial", 12, "bold") if es_header else ("Arial", 12)

    row_frame = ctk.CTkFrame(frame, fg_color=color_fila(fila_idx), corner_radius=0)
    row_frame.grid(row=fila_idx, column=0, sticky="ew", padx=2, pady=1)
    row_frame.grid_columnconfigure(0, weight=5)
    row_frame.grid_columnconfigure(1, weight=1)
    row_frame.grid_columnconfigure(2, weight=1)
    if col4 is not None:
        row_frame.grid_columnconfigure(3, weight=2)

    ctk.CTkLabel(row_frame, text=col1, font=font, anchor="w", justify="left", wraplength=360).grid(
        row=0, column=0, sticky="w", padx=8, pady=3
    )
    ctk.CTkLabel(row_frame, text=col2, font=font, anchor="w").grid(row=0, column=1, sticky="w", padx=8, pady=3)
    ctk.CTkLabel(row_frame, text=col3, font=font, anchor="e").grid(row=0, column=2, sticky="e", padx=8, pady=3)
    if col4 is not None:
        ctk.CTkLabel(row_frame, text=col4, font=font, anchor="w", justify="left", wraplength=160).grid(
            row=0, column=3, sticky="w", padx=8, pady=3
        )

def mostrar_resultados(total_materias, total_libres, total, detalle_materias, detalle_libres):
    label_estado.configure(text="Análisis completado ✔")

    # Limpiar grids
    for w in scroll_materias.winfo_children():
        w.destroy()
    for w in scroll_libres.winfo_children():
        w.destroy()

    scroll_materias.grid_columnconfigure(0, weight=1)
    scroll_libres.grid_columnconfigure(0, weight=1)

    # Header materias
    agregar_fila_grid(scroll_materias, 0, "Materia", "Nota", "Créditos", es_header=True)
    for i, m in enumerate(detalle_materias, start=1):
        agregar_fila_grid(scroll_materias, i, m["materia"], m["nota"], formatear_creditos(m["creditos"]))

    # Header créditos libres (con columna de origen: módulo/acordeón de la fila)
    agregar_fila_grid(scroll_libres, 0, "Actividad", "Nota", "Créditos", "Origen", es_header=True)
    for i, c in enumerate(detalle_libres, start=1):
        agregar_fila_grid(scroll_libres, i, c["actividad"], c["nota"], formatear_creditos(c["creditos"]), c.get("origen", ""))

    label_materias.configure(text=f"Créditos materias:  {formatear_creditos(total_materias)}")
    label_libres.configure(text=f"Créditos libres:    {formatear_creditos(total_libres)}")
    label_total.configure(text=f"TOTAL GLOBAL:  {formatear_creditos(total)}")
    restablecer_botones()

def limpiar():
    for w in scroll_materias.winfo_children():
        w.destroy()
    for w in scroll_libres.winfo_children():
        w.destroy()
    label_materias.configure(text="")
    label_libres.configure(text="")
    label_total.configure(text="")
    caja_log.configure(state="normal")
    caja_log.delete("1.0", "end")
    caja_log.configure(state="disabled")

# ----------------------------
# INTERFAZ
# ----------------------------
titulo = ctk.CTkLabel(app, text="Analizador de Créditos SIU", font=("Arial", 24, "bold"))
titulo.pack(pady=12)

label_estado = ctk.CTkLabel(app, text="Presioná 'Iniciar' para abrir el navegador", font=("Arial", 13))
label_estado.pack(pady=4)

frame_botones = ctk.CTkFrame(app, fg_color="transparent")
frame_botones.pack(pady=8)

boton_iniciar = ctk.CTkButton(frame_botones, text="Iniciar análisis", width=200, height=40, command=iniciar_analisis)
boton_iniciar.grid(row=0, column=0, padx=10)

boton_listo = ctk.CTkButton(
    frame_botones, text="✔ Listo, continuar",
    width=200, height=40, state="disabled",
    fg_color="#2d6a2d", hover_color="#1f4d1f",
    command=usuario_listo
)
boton_listo.grid(row=0, column=1, padx=10)

boton_cancelar = ctk.CTkButton(
    frame_botones, text="✖ Cancelar",
    width=120, height=40, state="disabled",
    fg_color="#8a2d2d", hover_color="#5c1f1f",
    command=cancelar_analisis
)
boton_cancelar.grid(row=0, column=2, padx=10)

# Tabs para separar materias y créditos libres
tabview = ctk.CTkTabview(app, width=780, height=350)
tabview.pack(pady=8, padx=20)
tabview.add("Materias")
tabview.add("Créditos Libres")

scroll_materias = ctk.CTkScrollableFrame(tabview.tab("Materias"), width=740, height=280)
scroll_materias.pack(fill="both", expand=True)

scroll_libres = ctk.CTkScrollableFrame(tabview.tab("Créditos Libres"), width=740, height=280)
scroll_libres.pack(fill="both", expand=True)

# Registro de progreso
label_log = ctk.CTkLabel(app, text="Registro", font=("Arial", 12, "bold"), anchor="w")
label_log.pack(fill="x", padx=24)

caja_log = ctk.CTkTextbox(app, height=90, font=("Arial", 11))
caja_log.pack(fill="x", padx=24, pady=(0, 4))
caja_log.configure(state="disabled")

# Totales
frame_totales = ctk.CTkFrame(app)
frame_totales.pack(pady=5, fill="x", padx=20)

label_materias = ctk.CTkLabel(frame_totales, text="", font=("Arial", 13))
label_materias.pack(pady=2)

label_libres = ctk.CTkLabel(frame_totales, text="", font=("Arial", 13))
label_libres.pack(pady=2)

label_total = ctk.CTkLabel(frame_totales, text="", font=("Arial", 20, "bold"))
label_total.pack(pady=6)

footer = ctk.CTkLabel(app, text="Python + Selenium + CustomTkinter", font=("Arial", 11))
footer.pack(side="bottom", pady=6)

app.mainloop()
