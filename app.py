import customtkinter as ctk
import threading
import importlib
import scrapCreditos
importlib.reload(scrapCreditos)
from scrapCreditos import obtener_creditos

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

app = ctk.CTk()
app.geometry("820x750")
app.title("Analizador SIU Guaraní")

senal_listo = threading.Event()

# ----------------------------
# FUNCIONES
# ----------------------------
def iniciar_analisis():
    boton_iniciar.configure(state="disabled")
    boton_listo.configure(state="normal")
    label_estado.configure(text="Logueate en el navegador, entrá a Plan de Estudios y presioná 'Listo'")
    limpiar()
    hilo = threading.Thread(target=ejecutar_scraping, daemon=True)
    hilo.start()

def usuario_listo():
    try:
        senal_listo.set()
        boton_listo.configure(state="disabled")
        label_estado.configure(text="Procesando datos...")
    except Exception as e:
        label_estado.configure(text=f"Error: {str(e)}")

def ejecutar_scraping():
    senal_listo.clear()
    try:
        materias, libres, total, detalle_materias, detalle_libres = obtener_creditos(
            log=lambda t: app.after(0, lambda t=t: None),  # silenciar consola
            esperar_senal=senal_listo
        )
        app.after(0, lambda: mostrar_resultados(materias, libres, total, detalle_materias, detalle_libres))
    except Exception as e:
        import traceback
        error_completo = traceback.format_exc()
        print(error_completo)  # ver en consola
        app.after(0, lambda: label_estado.configure(text=f"Error: {error_completo[-200:]}"))
        app.after(0, lambda: boton_iniciar.configure(state="normal"))

def agregar_fila_grid(frame, fila_idx, col1, col2, col3, es_header=False):
    font = ("Arial", 12, "bold") if es_header else ("Arial", 12)
    color = "#3a3a3a" if fila_idx % 2 == 0 else "#2a2a2a"

    row_frame = ctk.CTkFrame(frame, fg_color=color, corner_radius=0)
    row_frame.grid(row=fila_idx, column=0, sticky="ew", padx=2, pady=1)
    row_frame.grid_columnconfigure(0, weight=6)
    row_frame.grid_columnconfigure(1, weight=2)
    row_frame.grid_columnconfigure(2, weight=1)

    ctk.CTkLabel(row_frame, text=col1, font=font, anchor="w").grid(row=0, column=0, sticky="w", padx=8, pady=3)
    ctk.CTkLabel(row_frame, text=col2, font=font, anchor="w").grid(row=0, column=1, sticky="w", padx=8, pady=3)
    ctk.CTkLabel(row_frame, text=col3, font=font, anchor="e").grid(row=0, column=2, sticky="e", padx=8, pady=3)

def mostrar_resultados(materias, libres, total, detalle_materias, detalle_libres):
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
        agregar_fila_grid(scroll_materias, i, m["materia"], m["nota"], str(m["creditos"]))

    # Header créditos libres
    agregar_fila_grid(scroll_libres, 0, "Actividad", "Nota", "Créditos", es_header=True)
    for i, c in enumerate(detalle_libres, start=1):
        agregar_fila_grid(scroll_libres, i, c["actividad"], c["nota"], str(c["creditos"]))

    label_materias.configure(text=f"Créditos materias:  {materias}")
    label_libres.configure(text=f"Créditos libres:    {libres}")
    label_total.configure(text=f"TOTAL GLOBAL:  {total}")
    boton_iniciar.configure(state="normal")

def limpiar():
    for w in scroll_materias.winfo_children():
        w.destroy()
    for w in scroll_libres.winfo_children():
        w.destroy()
    label_materias.configure(text="")
    label_libres.configure(text="")
    label_total.configure(text="")

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

# Tabs para separar materias y créditos libres
tabview = ctk.CTkTabview(app, width=780, height=350)
tabview.pack(pady=8, padx=20)
tabview.add("Materias")
tabview.add("Créditos Libres")

scroll_materias = ctk.CTkScrollableFrame(tabview.tab("Materias"), width=740, height=280)
scroll_materias.pack(fill="both", expand=True)

scroll_libres = ctk.CTkScrollableFrame(tabview.tab("Créditos Libres"), width=740, height=280)
scroll_libres.pack(fill="both", expand=True)

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