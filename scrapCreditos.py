import re
import time
import unicodedata
from dataclasses import dataclass, field
from threading import Event
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ----------------------------
# CONFIGURACIÓN
# ----------------------------
URL_ACCESO = "http://34.232.173.37/upe/acceso"
URL_PLAN_ESTUDIO = "http://34.232.173.37/upe/plan_estudio"
NOTA_MINIMA_APROBACION = 4
# Fallback: solo se usa si la página no tiene módulos identificables.
TIPOS_CREDITO_LIBRE = ("Actividad Extracurricular", "Curso", "Seminario", "Pasantía", "Taller")
COLUMNAS_MINIMAS = 7
TIEMPO_ESPERA_LISTO = 600  # segundos máximos esperando el botón "Listo"


# ----------------------------
# CONTROL COMPARTIDO CON LA GUI
# ----------------------------
@dataclass
class ControlScraping:
    """Coordina la GUI con el hilo de scraping (señales y cancelación)."""

    esperar_senal: bool = True
    senal_listo: Event = field(default_factory=Event)
    cancelado: Event = field(default_factory=Event)
    driver: Optional[webdriver.Chrome] = None

    def listo(self):
        """La GUI avisa que el usuario terminó de preparar la página."""
        self.senal_listo.set()

    def cancelar(self):
        """La GUI pide frenar el análisis: cierra el navegador y avisa al hilo."""
        self.cancelado.set()
        driver = self.driver
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


# ----------------------------
# NAVEGADOR
# ----------------------------
def _crear_driver():
    """Crea el driver de Chrome (usa webdriver-manager si está instalado)."""
    try:
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        return webdriver.Chrome(service=Service(ChromeDriverManager().install()))
    except ImportError:
        # Selenium 4.6+ trae su propio gestor de drivers (Selenium Manager).
        return webdriver.Chrome()


# ----------------------------
# PARSEOS
# ----------------------------
def parsear_nota(texto):
    """Convierte el texto de una nota a float ('4,5' y '4.5' valen 4.5).

    Devuelve None si no es numérico (por ejemplo 'PROMO', 'LIB' o vacío).
    """
    primero = texto.strip().split(" ")[0].replace(",", ".")
    try:
        return float(primero)
    except ValueError:
        return None


def parsear_creditos(texto):
    """Convierte el texto de créditos a float. Devuelve None si no se puede."""
    limpio = texto.strip().replace(",", ".")
    if not limpio:
        return None
    try:
        return float(limpio)
    except ValueError:
        return None


def es_aprobada(nota):
    """True si la nota es numérica y alcanza la mínima de aprobación."""
    return nota is not None and nota >= NOTA_MINIMA_APROBACION


def formatear_creditos(valor):
    """Muestra los créditos sin ceros de sobra: 6.0 -> '6', 4.5 -> '4,5'."""
    if valor is None:
        return "-"
    if float(valor).is_integer():
        return str(int(valor))
    return str(valor).replace(".", ",")


# ----------------------------
# MÓDULOS DEL PLAN
# ----------------------------
def _normalizar(texto):
    """Minúsculas, sin tildes y con espacios colapsados, para comparar textos."""
    texto = unicodedata.normalize("NFD", texto or "")
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return " ".join(texto.lower().split())


def _nombre_modulo(ancla):
    """Nombre limpio del módulo: quita el prefijo 'Módulo:' y los espacios de más."""
    texto = " ".join((ancla.get_attribute("textContent") or "").split())
    if _normalizar(texto).startswith("modulo:"):
        texto = texto.split(":", 1)[1].strip()
    return texto or "Módulo sin nombre"


def _modulos_del_plan(driver, log):
    """Mapa id del panel -> nombre del módulo (ej. {'collapse662': 'Créditos libres'}).

    Los módulos son acordeones de nivel superior (a.modulos) que agrupan las
    materias genéricas. Se leen por textContent porque pueden estar colapsados.
    """
    modulos = {}
    for ancla in driver.find_elements(By.CSS_SELECTOR, "a.modulos"):
        destino = ancla.get_attribute("data-target") or ancla.get_attribute("href") or ""
        id_panel = destino.split("#")[-1].strip()
        if id_panel:
            modulos[id_panel] = _nombre_modulo(ancla)

    if not modulos:
        log("No se detectaron módulos del plan; se clasificará por tipo de columna (menos confiable).")
    return modulos


def _ids_creditos_libres(modulos):
    """Ids de los paneles cuyo módulo se llama 'Créditos libres'."""
    return {id_panel for id_panel, nombre in modulos.items() if "creditos libres" in _normalizar(nombre)}


def es_creditoLibre(fila, ids_modulo_libre):
    """True si la fila está dentro del módulo 'Créditos libres'; False si es curricular."""
    if not ids_modulo_libre:
        return False
    condicion = " or ".join(f"@id='{i}'" for i in sorted(ids_modulo_libre))
    return bool(fila.find_elements(By.XPATH, f"ancestor::*[{condicion}]"))


# ----------------------------
# ESPERA Y VERIFICACIONES
# ----------------------------
def _driver_vivo(driver):
    try:
        driver.title
        return True
    except Exception:
        return False


def _esperar_listo(control, log, driver):
    """Espera a que la GUI señale que el usuario está logueado."""
    log("Logueate en el SIU Guaraní y volvé a la aplicación.")
    log("Cuando estés listo, presioná '✔ Listo, continuar'.")
    inicio = time.time()
    while not control.senal_listo.is_set():
        if control.cancelado.is_set():
            raise RuntimeError("Análisis cancelado por el usuario.")
        if not _driver_vivo(driver):
            raise RuntimeError("Se cerró el navegador. Presioná 'Iniciar' para empezar de nuevo.")
        if time.time() - inicio > TIEMPO_ESPERA_LISTO:
            raise TimeoutError(
                f"Se agotó la espera ({TIEMPO_ESPERA_LISTO}s) sin apretar 'Listo'. "
                "Presioná 'Iniciar' para intentar de nuevo."
            )
        control.senal_listo.wait(timeout=1)


def _ir_a_plan(driver, log):
    """Navega directo al plan de estudios y espera a que cargue."""
    driver.get(URL_PLAN_ESTUDIO)
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-toggle='collapse']"))
    )
    log("Plan de estudios cargado.")


# ----------------------------
# APERTURA DE ACORDEONES
# ----------------------------
def _panel_esta_abierto(panel):
    clases = (panel.get_attribute("class") or "").split()
    estilo = panel.get_attribute("style") or ""
    # Abierto si tiene "in" en clases O "auto" en style (Bootstrap)
    return "in" in clases or "auto" in estilo


def _abrir_boton(boton, driver, log):
    destino = boton.get_attribute("href") or boton.get_attribute("data-target")
    if not destino:
        return

    id_div = destino.split("#")[-1].strip()
    if not id_div:
        return

    try:
        panel = driver.find_element(By.ID, id_div)
    except NoSuchElementException:
        log(f"No se encontró el panel '#{id_div}', se omite esa sección.")
        return

    if _panel_esta_abierto(panel):
        return

    driver.execute_script("arguments[0].click();", boton)
    try:
        WebDriverWait(driver, 5).until(lambda d, pid=id_div: _panel_esta_abierto(d.find_element(By.ID, pid)))
    except (TimeoutException, NoSuchElementException):
        log(f"El panel '#{id_div}' no terminó de abrir; se continúa igual.")
    except StaleElementReferenceException:
        pass


def _abrir_acordeones(driver, log, control):
    """Abre los módulos del plan y después cada materia genérica.

    Los módulos van primero: si están colapsados, el contenido de adentro no es
    visible y Selenium leería las filas vacías.
    """
    modulos = driver.find_elements(By.CSS_SELECTOR, "a.modulos")
    botones = driver.find_elements(By.CSS_SELECTOR, "a.materia_generica")
    if not botones:
        raise RuntimeError("No se encontraron secciones del plan de estudios.")

    log(f"Abriendo {len(modulos)} módulos y {len(botones)} secciones del plan...")
    for grupo, etiqueta in ((modulos, "módulo"), (botones, "sección")):
        for i in range(len(grupo)):
            if control.cancelado.is_set():
                raise RuntimeError("Análisis cancelado por el usuario.")
            try:
                _abrir_boton(grupo[i], driver, log)
            except StaleElementReferenceException:
                # La página se volvió a dibujar: se recuperan los elementos y se reintenta una vez.
                try:
                    selector = "a.modulos" if etiqueta == "módulo" else "a.materia_generica"
                    grupo = driver.find_elements(By.CSS_SELECTOR, selector)
                    _abrir_boton(grupo[i], driver, log)
                except Exception as e:
                    log(f"{'Módulo' if etiqueta == 'módulo' else 'Sección'} {i + 1}: no se pudo abrir ({e}).")
            except Exception as e:
                log(f"{'Módulo' if etiqueta == 'módulo' else 'Sección'} {i + 1}: no se pudo abrir ({e}).")


# ----------------------------
# LECTURA DE LA TABLA
# ----------------------------
def _obtener_filas_plan(driver, log):
    """Devuelve las filas de la tabla del plan, ignorando otras tablas de la página."""
    WebDriverWait(driver, 15).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tbody tr")))

    filas = []
    for tbody in driver.find_elements(By.CSS_SELECTOR, "tbody"):
        filas_tabla = tbody.find_elements(By.CSS_SELECTOR, "tr")
        # Solo se toman los <tbody> que tienen filas con el formato del plan (7 columnas).
        if any(len(f.find_elements(By.TAG_NAME, "td")) >= COLUMNAS_MINIMAS for f in filas_tabla):
            filas.extend(filas_tabla)

    if not filas:
        raise RuntimeError("No se encontraron filas del plan de estudios.")

    log(f"Se leyeron {len(filas)} filas del plan.")
    return filas


def _procesar_filas(filas, log, modulos=None, ids_libres=None):
    """Clasifica las filas en materias y créditos libres.

    El criterio principal es el MÓDULO donde vive la fila (no el tipo de la
    columna): 'Taller' o 'Seminario' pueden ser materias curriculares. Solo se
    recurre al tipo de columna si la página no tenía módulos identificables.
    """
    modulos = modulos or {}
    ids_libres = ids_libres or set()
    nombre_modulo_libre = next((modulos[i] for i in sorted(ids_libres)), "Créditos libres")
    modo_fallback = not ids_libres
    if modo_fallback and modulos:
        log("Aviso: no se encontró el módulo 'Créditos libres'; se usa el tipo de columna (menos confiable).")

    materias = {}
    creditos_libres = []

    for fila in filas:
        try:
            columnas = fila.find_elements(By.TAG_NAME, "td")
            if len(columnas) < COLUMNAS_MINIMAS:
                continue
            es_libre = es_creditoLibre(fila, ids_libres)
        except StaleElementReferenceException:
            log("Se perdió una fila durante la lectura (página redibujada); se ignora.")
            continue

        materia = columnas[0].text.strip()
        tipo = columnas[1].text.strip()
        nota = columnas[4].text.strip()
        creditos = columnas[6].text.strip()

        if not materia:
            continue

        # Créditos libres: las filas del módulo 'Créditos libres' con nota cargada
        if es_libre or (modo_fallback and tipo in TIPOS_CREDITO_LIBRE):
            if nota == "":
                continue
            valor = parsear_creditos(creditos)
            if valor is None:
                log(f"'{materia}': no se pudo interpretar el crédito '{creditos}'; no se suma.")
                continue
            creditos_libres.append({
                "actividad": materia,
                "tipo": tipo,
                "nota": nota,
                "creditos": valor,
                "origen": nombre_modulo_libre if es_libre else tipo,
            })
            continue

        # Materias normales: se agrupan por código (número entre paréntesis)
        match = re.search(r"\((\d+)\)", materia)
        clave = match.group(1) if match else materia
        actual = materias.get(clave)
        if actual is None or (nota != "" and actual["nota"] == ""):
            materias[clave] = {"materia": materia, "nota": nota, "creditos": creditos}

    return materias, creditos_libres


def _calcular_totales(materias, creditos_libres, log):
    total_materias = 0
    materias_aprobadas = []

    for m in materias.values():
        nota = parsear_nota(m["nota"])
        if not es_aprobada(nota):
            continue

        creditos = parsear_creditos(m["creditos"])
        if creditos is None:
            log(f"'{m['materia']}': no se pudo interpretar el crédito '{m['creditos']}'; no se suma.")
            continue

        total_materias += creditos
        materias_aprobadas.append({
            "materia": m["materia"],
            "nota": m["nota"],
            "creditos": creditos,
        })

    total_libres = sum(c["creditos"] for c in creditos_libres)
    return total_materias, total_libres, total_materias + total_libres, materias_aprobadas


# ----------------------------
# FUNCIÓN PRINCIPAL
# ----------------------------
def obtener_creditos(log=print, control=None):
    """Abre Guaraní, scrapea el plan de estudios y devuelve los créditos.

    Devuelve: (total_materias, total_libres, total, detalle_materias, detalle_libres)

    - Si se pasa un ControlScraping, la GUI maneja la señal "Listo" y la cancelación.
    - Sin control, funciona por consola: presionás ENTER cuando estés logueado.
    """
    modo_consola = control is None
    if modo_consola:
        control = ControlScraping(esperar_senal=False)

    driver = _crear_driver()
    control.driver = driver
    driver.maximize_window()
    driver.get(URL_ACCESO)

    try:
        if modo_consola:
            log("Logueate en el SIU Guaraní en el navegador.")
            input("Presioná ENTER cuando estés logueado... ")
        else:
            _esperar_listo(control, log, driver)

        _ir_a_plan(driver, log)

        # El módulo define si una fila es materia o crédito libre.
        modulos = _modulos_del_plan(driver, log)
        ids_libres = _ids_creditos_libres(modulos)
        for id_panel in sorted(ids_libres):
            log(f"Módulo de créditos libres detectado: '{modulos[id_panel]}'.")
        for id_panel, nombre in sorted(modulos.items()):
            if id_panel not in ids_libres:
                log(f"Módulo '{nombre}' → materias curriculares.")

        _abrir_acordeones(driver, log, control)
        filas = _obtener_filas_plan(driver, log)
        materias, creditos_libres = _procesar_filas(filas, log, modulos, ids_libres)
        total_materias, total_libres, total, materias_aprobadas = _calcular_totales(
            materias, creditos_libres, log
        )

        log("Análisis finalizado.")
        return total_materias, total_libres, total, materias_aprobadas, creditos_libres
    finally:
        # El navegador se cierra siempre: éxito, error o cancelación.
        control.driver = None
        try:
            driver.quit()
        except Exception:
            pass
