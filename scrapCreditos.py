from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time

def obtener_creditos(log=print, esperar_senal=None):
    URL = "http://34.232.173.37/upe/acceso"
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
    driver.maximize_window()
    driver.get(URL)
    wait = WebDriverWait(driver, 15)
    # Espera la señal del botón de la GUI
    if esperar_senal:
        esperar_senal.wait()
    else:
        input("Logueate manualmente y presioná ENTER...")
    # ----------------------------
    # ABRIR TODOS LOS ACORDEONES
    # ----------------------------


    # Navegar directo a Plan de Estudios
    driver.get("http://34.232.173.37/upe/plan_estudio")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-toggle='collapse']")))
    time.sleep(1)
    print("Plan de estudios cargado.")
        
    botones_mg = driver.find_elements(By.CSS_SELECTOR, "a.materia_generica")
    print("Procesando datos de creditos libres...")

    for i, b in enumerate(botones_mg, start=1):
        try:
            destino = b.get_attribute("href") or b.get_attribute("data-target")
            if not destino:
                continue

            id_div = destino.split("#")[-1].strip()
            panel = driver.find_element(By.ID, id_div)

            clases_panel = panel.get_attribute("class") or ""
            estilo_panel = panel.get_attribute("style") or ""

            # Abierto si tiene "in" en clases O "auto" en style
            esta_abierto = "in" in clases_panel.split() or "auto" in estilo_panel


            if not esta_abierto:
                driver.execute_script("arguments[0].click();", b)
            
                try:
                    WebDriverWait(driver, 5).until(
                        lambda d, pid=id_div: (
                            "in" in (d.find_element(By.ID, pid).get_attribute("class") or "").split()
                            or "auto" in (d.find_element(By.ID, pid).get_attribute("style") or "")
                        )
                    )
                
                except:
                    time.sleep(1)
            

        except Exception as e:
            print(f"  MG {i} error: {e}")

    time.sleep(1)
    # ----------------------------
    # OBTENER FILAS DE TABLA
    # ----------------------------
    filas = wait.until(
        EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tbody tr"))
    )


    # ----------------------------
    # SCRAPING
    # ----------------------------
    materias = {}
    creditos_libres = []

    for fila in filas:
        columnas = fila.find_elements(By.TAG_NAME, "td")

        if len(columnas) >= 7:
            materia = columnas[0].text.strip()
            tipo = columnas[1].text.strip()
            nota = columnas[4].text.strip()
            creditos = columnas[6].text.strip()

            # ✅ Créditos libres: sin nota pero con tipo extracurricular/curso
            if tipo in ("Actividad Extracurricular", "Curso"):
                try:
                    if nota != "":  # ✅ solo los que tienen nota
                        creditos_libres.append({
                            "actividad": materia,
                            "tipo": tipo,
                            "nota": nota,
                            "creditos": float(creditos.replace(",", "."))
                        })
                except:
                    pass
                continue

            # Materias normales (igual que antes)
            import re
            match = re.search(r'\((\d+)\)', materia)
            clave = match.group(1) if match else materia

            if clave not in materias or (nota != "" and materias[clave]["nota"] == ""):
                materias[clave] = {
                    "materia": materia,
                    "nota": nota,
                    "creditos": creditos
                }

    # ----------------------------
    # DEBUG + CÁLCULO
    # ----------------------------
    total_creditos = 0

    for m in materias.values():
        nota_texto = m["nota"]
        creditos_texto = m["creditos"]

        if nota_texto != "":
            nota_num = nota_texto.split(" ")[0]
            if nota_num.isdigit() and int(nota_num) >= 4:
                try:
                    creditos = float(creditos_texto.replace(",", "."))
                except:
                    creditos = 0
                total_creditos += creditos

    materias_aprobadas = [
        m for m in materias.values()
        if m["nota"] != "" and m["nota"].split(" ")[0].isdigit() and int(m["nota"].split(" ")[0]) >= 4
    ]

    # ----------------------------
    # RESULTADO CRÉDITOS LIBRES
    # ----------------------------
    total_creditos_libres = 0
    for c in creditos_libres:
        total_creditos_libres += c['creditos']

    return total_creditos, total_creditos_libres, total_creditos + total_creditos_libres, materias_aprobadas, creditos_libres