
# SIU Guaraní Credit Counter

Aplicación desarrollada en Python para automatizar el conteo de créditos académicos en SIU Guaraní UPE.

## ¿Qué hace?

La aplicación inicia automáticamente una sesión de análisis sobre tu cuenta de SIU Guaraní, accede al plan de estudios y contabiliza:

- Materias aprobadas (nota numérica mínima 4, acepta notas con coma decimal como "4,5")
- Créditos obtenidos
- Créditos libres (Actividades Extracurriculares, Cursos, Seminarios, Pasantías y Talleres)
- Actividades recreativas que suman créditos

Todo de forma automática, evitando tener que contar los créditos manualmente.

---

## Instalación

Requisitos: Python 3.10+ y Google Chrome.

```bash
pip install -r requirements.txt
```

La primera vez que se ejecuta, Selenium descarga el controlador de Chrome si hace falta (por eso el primer inicio puede tardar un poco).

---

# Primer inicio

La primera vez que se ejecuta la aplicación puede tardar un poco en abrirse.  
Esto es normal, ya que durante el primer inicio se terminan de instalar y configurar algunos componentes necesarios para la automatización.

Los siguientes inicios serán mucho más rápidos.

---

# Cómo usarlo

## 1. Iniciar análisis

Abrí la aplicación y presioná el botón:

Iniciar análisis


Esto abrirá automáticamente una ventana de Google Chrome con SIU Guaraní UPE.

---

## 2. Iniciar sesión

Iniciá sesión normalmente con tu usuario y contraseña de SIU Guaraní.

---

## 3. Continuar desde la aplicación

Una vez iniciada la sesión, volvés a la aplicación y presionás:

```text
Listo, continuar
```

---

## 4. Análisis automático

La aplicación navegará automáticamente hacia el plan de estudios y comenzará el conteo de créditos.

Este proceso puede tardar unos segundos dependiendo de la velocidad de conexión y carga de SIU Guaraní.

Mientras tanto, el cuadro **Registro** muestra el avance del análisis y cualquier aviso (por ejemplo, si una fila no se pudo interpretar).

También podés frenar el proceso en cualquier momento con el botón:

```text
✖ Cancelar
```

---

# Resultado

Al finalizar el análisis aparecerán automáticamente:

* Las materias aprobadas
* El total de créditos obtenidos
* Los créditos libres
* Las actividades recreativas contabilizadas

Y listo. Así de simple.

---

# Tecnologías utilizadas

* Python
* Selenium
* Google Chrome

---

# Importante

* Si cerrás la ventana de Chrome, la aplicación lo detecta y te avisa (podés reiniciar con "Iniciar").
* Se recomienda tener Google Chrome actualizado.
* Los tipos de créditos libres y la nota mínima de aprobación se pueden ajustar en `scrapCreditos.py` (constantearriba del archivo).

