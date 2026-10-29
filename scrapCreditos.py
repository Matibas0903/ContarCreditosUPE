import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from threading import Event
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import (
    JavascriptException,
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
TIEMPO_ESPERA_PANELES = 3  # segundos máximos esperando a que se abran todos los paneles

# ----------------------------
# SCRIPTS JAVASCRIPT (modo rápido)
# ----------------------------
# Clickea todos los acordeones colapsados del plan. Los clicks van ESCALONADOS
# (60ms entre cada uno): el colapso de Bootstrap anima con transiciones y, si
# todos los toggles se clickean en el mismo tick, los que llegan mientras otra
# animación corre quedan sin abrir.
# Devuelve {total, abiertos, pendientes: [ids]}.
JS_ABRIR_TODO = """
const paneles = document.querySelectorAll('a.modulos, a.materia_generica');
let abiertos = 0;
const pendientes = [];
let orden = 0;
paneles.forEach(boton => {
    const destino = boton.getAttribute('data-target') || boton.getAttribute('href') || '';
    if (!destino.startsWith('#')) return;
    const panel = document.getElementById(destino.slice(1));
    if (!panel) return;
    const clases = panel.classList;
    const abierto = clases.contains('in') || clases.contains('collapsing')
        || (panel.getAttribute('style') || '').includes('auto');
    if (abierto) return;
    setTimeout(() => boton.click(), orden * 60);
    orden++;
    abiertos++;
    pendientes.push(panel.id);
});
return JSON.stringify({total: paneles.length, abiertos, pendientes});
"""

# Lee todas las filas del plan en un solo viaje y devuelve un JSON con las filas
# válidas (>= COLUMNAS_MINIMAS columnas), marcando cuáles viven en el módulo de
# créditos libres. Se le pasan los ids de ese módulo como argumento.
JS_LEER_FILAS = """
const idsLibres = arguments[0];
const minCols = arguments[1];
const filas = [];
document.querySelectorAll('tbody tr').forEach(fila => {
    const celdas = fila.querySelectorAll('td');
    if (celdas.length < minCols) return;   // tablas ajenas al plan
    const texto = i => (celdas[i] ? celdas[i].textContent.trim() : '');
    if (!texto(0)) return;
    filas.push({
        materia: texto(0),
        tipo: texto(1),
        nota: texto(4),
        creditos: texto(6),
        esLibre: idsLibres.some(id => fila.closest('#' + CSS.escape(id)) !== null),
    });
});
return JSON.stringify(filas);
"""



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
    """True si el panel está abierto o en plena animación de apertura."""
    return _panel_abierto_definitivo(panel) or _panel_en_animacion(panel)


def _panel_abierto_definitivo(panel):
    """True solo si el panel terminó de abrirse ('in' en clases o 'auto' en style)."""
    clases = (panel.get_attribute("class") or "").split()
    estilo = panel.get_attribute("style") or ""
    return "in" in clases or "auto" in estilo


def _panel_en_animacion(panel):
    """True si el panel se está abriendo/cerrando (Bootstrap pone 'collapsing')."""
    return "collapsing" in (panel.get_attribute("class") or "").split()


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


def _abrir_acordeones_clasico(driver, log, control):
    """Camino lento (respaldo): abre los acordeones de a uno, con espera por cada."""
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


def _abrir_acordeones(driver, log, control):
    """Abre los módulos del plan y después cada materia genérica.

    Los módulos van primero: si están colapsados, el contenido de adentro no es
    visible y Selenium leería las filas vacías.

    Modo rápido: un solo clic en lote desde JS y una única espera colectiva
    (en vez de una espera de 0.5s por acordeón). Si falla, se usa el camino clásico.
    """
    if control.cancelado.is_set():
        raise RuntimeError("Análisis cancelado por el usuario.")

    try:
        datos = json.loads(driver.execute_script(JS_ABRIR_TODO))
    except (JavascriptException, TypeError, ValueError):
        log("El clic en lote no estuvo disponible; se abren los acordeones de a uno.")
        _abrir_acordeones_clasico(driver, log, control)
        return

    if not datos.get("total"):
        raise RuntimeError("No se encontraron secciones del plan de estudios.")

    pendientes = datos.get("pendientes") or []
    if pendientes:
        log(f"Abriendo {len(pendientes)} de {datos['total']} secciones del plan...")
        _esperar_paneles(driver, pendientes, TIEMPO_ESPERA_PANELES)

        # Reintento en lote con stagger ancho (350ms > duración de la animación):
        # cada click cae cuando el anterior ya terminó, así no se pisan.
        sin_abrir = _paneles_cerrados(driver, pendientes)
        if sin_abrir:
            log(f"Reintentando {len(sin_abrir)} paneles que no abrieron...")
            try:
                json.loads(driver.execute_script(JS_REINTENTAR, sin_abrir))
                _esperar_paneles(driver, sin_abrir, max(TIEMPO_ESPERA_PANELES, len(sin_abrir) * 0.4))
            except (JavascriptException, TypeError, ValueError):
                pass

        # Último recurso: los que sigan cerrados se clickean de a uno.
        aun_sin_abrir = _paneles_cerrados(driver, pendientes)
        for pid in aun_sin_abrir:
            if control.cancelado.is_set():
                raise RuntimeError("Análisis cancelado por el usuario.")
            _reintentar_panel(driver, pid, log)

        # Estado final
        cerrados = _paneles_cerrados(driver, pendientes)
        if cerrados:
            log(
                f"⚠ {len(cerrados)} paneles no abrieron ({', '.join(cerrados[:6])}); "
                f"los totales pueden estar INCOMPLETOS."
            )
    else:
        log(f"Las {datos['total']} secciones ya estaban abiertas.")

    if control.cancelado.is_set():
        raise RuntimeError("Análisis cancelado por el usuario.")


# Devuelve la lista de ids de paneles que NO terminaron de abrir.
# Un solo viaje de red en vez de un find_element por panel.
JS_PANELES_CERRADOS = """
return JSON.stringify(arguments[0].filter(id => {
    const p = document.getElementById(id);
    if (!p) return false;                 // ya no existe: no lo cuenta como cerrado
    const c = p.classList;
    const style = p.getAttribute('style') || '';
    return !(c.contains('in') || style.includes('auto'));
}));
"""


def _paneles_cerrados(driver, ids):
    """Ids de paneles que siguen sin haber terminado de abrir (1 solo viaje)."""
    if not ids:
        return []
    try:
        crudo = driver.execute_script(JS_PANELES_CERRADOS, list(ids))
        cerrados = json.loads(crudo)
        return cerrados if isinstance(cerrados, list) else list(ids)
    except (JavascriptException, TypeError, ValueError):
        # Sin JS confiable: chequeo uno por uno.
        return [pid for pid in ids if not _panel_abierto_definitivo_si_existe(driver, pid)]


def _esperar_paneles(driver, ids, tiempo_max):
    """Espera a que los paneles terminen de abrir; sale temprano si deja de haber progreso.

    El chequeo es un solo viaje de red por polling (no un find_element por panel).
    Si la cantidad abierta no cambia en ~0.8s, se corta y se pasa al reintento.
    """
    ultimo_abiertos = -1
    ultimo_cambio = time.perf_counter()
    fin = time.perf_counter() + tiempo_max
    while time.perf_counter() < fin:
        cerrados = _paneles_cerrados(driver, ids)
        if not cerrados:
            return
        abiertos = len(ids) - len(cerrados)
        if abiertos != ultimo_abiertos:
            ultimo_abiertos = abiertos
            ultimo_cambio = time.perf_counter()
        elif time.perf_counter() - ultimo_cambio > 0.8:
            return  # sin progreso: se acelera al reintento
        time.sleep(0.1)


def _reintentar_panel(driver, id_panel, log):
    """Clickea de a uno el toggle de un panel que no abrió. True si terminó abierto.

    Si el click fue descartado por el plugin (pasa cuando hay otra animación en
    curso), se reclickea en vez de esperar los 4s completos en vano.
    """
    try:
        toggles = driver.find_elements(
            By.CSS_SELECTOR, f"[data-target='#{id_panel}'], [href='#{id_panel}']"
        )
        if not toggles:
            return False

        for _ in range(5):  # hasta 5 clicks: el primero suele descartarse
            driver.execute_script("arguments[0].click();", toggles[0])
            try:
                # En ~0.5s el panel debe abrir o al menos empezar a animarse.
                WebDriverWait(driver, 0.5, poll_frequency=0.05).until(
                    lambda d: _panel_abierto_definitivo(d.find_element(By.ID, id_panel))
                    or _panel_en_animacion(d.find_element(By.ID, id_panel))
                )
            except TimeoutException:
                continue  # el click no surtió efecto; se vuelve a intentar

            # Ya arrancó: se espera a que la animación termine.
            WebDriverWait(driver, 4, poll_frequency=0.1).until(
                lambda d: _panel_abierto_definitivo(d.find_element(By.ID, id_panel))
            )
            return True

        return False
    except (TimeoutException, NoSuchElementException, StaleElementReferenceException):
        return False
    except Exception:
        return False


# Segundo intento para los paneles que el primer lote dejó cerrados: igual que
# el lote inicial pero con el stagger más ancho que la animación (~350ms), para
# que cada click caiga cuando el anterior ya terminó de abrirse.
JS_REINTENTAR = """
const ids = arguments[0];
let orden = 0;
ids.forEach(id => {
    const destino = '#' + id;
    const toggle = document.querySelector(
        `[data-target="${destino}"], [href="${destino}"]`
    );
    if (!toggle) return;
    setTimeout(() => toggle.click(), orden * 350);
    orden++;
});
return JSON.stringify({reintentados: orden});
"""


def _panel_abierto_definitivo_si_existe(driver, id_panel):
    """Chequeo definitivo tolerante a que el panel haya desaparecido del DOM."""
    try:
        return _panel_abierto_definitivo(driver.find_element(By.ID, id_panel))
    except NoSuchElementException:
        return False


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


def _leer_filas_js(driver, ids_libres):
    """Modo rápido: lee todas las filas del plan en un solo viaje de red.

    Devuelve una lista de dicts (materia, tipo, nota, creditos, esLibre) o None
    si el script no funcionó (para que el llamador caiga al camino clásico).
    """
    try:
        crudo = driver.execute_script(JS_LEER_FILAS, sorted(ids_libres), COLUMNAS_MINIMAS)
        filas = json.loads(crudo)
    except (JavascriptException, TypeError, ValueError):
        return None
    return filas if isinstance(filas, list) else None


def _fila_a_dict(fila, ids_libres):
    """Convierte una fila (elemento Selenium) al mismo formato que devuelve el JS."""
    columnas = fila.find_elements(By.TAG_NAME, "td")
    if len(columnas) < COLUMNAS_MINIMAS:
        return None
    materia = columnas[0].text.strip()
    if not materia:
        return None
    return {
        "materia": materia,
        "tipo": columnas[1].text.strip(),
        "nota": columnas[4].text.strip(),
        "creditos": columnas[6].text.strip(),
        "esLibre": es_creditoLibre(fila, ids_libres),
    }


def _procesar_filas(filas, log, modulos=None, ids_libres=None):
    """Clasifica las filas en materias y créditos libres.

    Recibe dicts ya clasificados (camino rápido por JS, o el clásico convertido
    con _fila_a_dict). El criterio principal es el MÓDULO donde vive la fila (no
    el tipo de la columna): 'Taller' o 'Seminario' pueden ser materias
    curriculares. Solo se recurre al tipo de columna si no había módulos.
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
        materia = fila["materia"]
        tipo = fila["tipo"]
        nota = fila["nota"]
        creditos = fila["creditos"]
        es_libre = fila["esLibre"]

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

        # A partir de acá se cronometra: la espera del usuario no cuenta.
        inicio = time.perf_counter()

        _ir_a_plan(driver, log)

        # El módulo define si una fila es materia o crédito libre.
        modulos = _modulos_del_plan(driver, log)
        ids_libres = _ids_creditos_libres(modulos)
        for id_panel in sorted(ids_libres):
            log(f"Módulo de créditos libres detectado: '{modulos[id_panel]}'.")
        for id_panel, nombre in sorted(modulos.items()):
            if id_panel not in ids_libres:
                log(f"Módulo '{nombre}' → materias curriculares.")
        t_modulos = time.perf_counter()

        _abrir_acordeones(driver, log, control)
        t_acordeones = time.perf_counter()

        # Lectura rápida en un solo viaje; si falla, camino clásico paso a paso.
        filas = _leer_filas_js(driver, ids_libres)
        if filas is None:
            log("Lectura rápida no disponible; se lee la tabla de a fila.")
            filas_crudas = _obtener_filas_plan(driver, log)
            filas = [d for d in (_fila_a_dict(f, ids_libres) for f in filas_crudas) if d]
        t_lectura = time.perf_counter()

        materias, creditos_libres = _procesar_filas(filas, log, modulos, ids_libres)
        total_materias, total_libres, total, materias_aprobadas = _calcular_totales(
            materias, creditos_libres, log
        )
        t_total = time.perf_counter()

        log(
            f"⏱ Módulos: {len(modulos)} ({t_modulos - inicio:.2f}s) · "
            f"Apertura: {t_acordeones - t_modulos:.2f}s · "
            f"Lectura: {len(filas)} filas ({t_lectura - t_acordeones:.2f}s) · "
            f"Total: {t_total - inicio:.2f}s"
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
