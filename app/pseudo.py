"""Generadores deterministas de seudónimos (HMAC con sal secreta)."""
import hashlib, hmac, os, unicodedata

SALT = os.environ.get("PSEUDO_SALT", "kipu-frontier-2026-sal-fija").encode()

NOMBRES_F = """Adriana Alejandra Alicia Ana Andrea Ángela Antonia Beatriz Carolina Catalina Cecilia Clara Claudia Cristina
Daniela Diana Dora Elena Elisa Esperanza Estela Eugenia Fabiola Fernanda Gabriela Gloria Graciela Inés Irene Isabel
Jimena Josefina Juliana Julieta Laura Leonor Liliana Lina Lorena Lucía Luisa Luz Magdalena Marcela Margarita Mariana
Marta Mercedes Milena Mónica Natalia Nidia Olga Patricia Paula Pilar Raquel Rocío Rosa Sandra Silvia Sofía Susana
Tatiana Teresa Valeria Verónica Victoria Viviana Ximena Yolanda Zoraida Amparo Aurora Constanza Edith Emilia Fanny
Helena Ingrid Johana Karina Lucero Maritza Nubia Omaira Piedad Rebeca Rubiela Soledad Yaneth Yesenia Zulma""".split()
NOMBRES_M = """Adolfo Agustín Alberto Alejandro Alfonso Álvaro Andrés Antonio Armando Arturo Bernardo Camilo Carlos César
Cristian Daniel David Diego Eduardo Emilio Enrique Ernesto Esteban Fabián Federico Felipe Fernando Francisco Gabriel
Germán Gilberto Gonzalo Guillermo Gustavo Héctor Hernán Hugo Ignacio Iván Jaime Javier Jorge José Julián Leonardo
Luis Manuel Marco Mario Martín Mauricio Miguel Nicolás Óscar Pablo Pedro Rafael Ramiro Raúl Ricardo Roberto Rodrigo
Rubén Samuel Santiago Sergio Simón Tomás Ulises Víctor Wilson Abel Aníbal Bayron Darío Edgar Efraín Fredy Gerardo
Horacio Jairo Leandro Marino Nelson Octavio Reinaldo Saúl Teodoro Wilmer Yesid""".split()
APELLIDOS = """Acevedo Aguilar Alvarado Amaya Aranda Arboleda Ávila Barrera Bedoya Benítez Bermúdez Bonilla Bustos Cabrera
Caicedo Calderón Camacho Cárdenas Carmona Carrillo Castillo Cifuentes Contreras Córdoba Cortés Cuéllar Delgado Duque
Echeverri Escobar Espinosa Estrada Figueroa Fonseca Franco Fuentes Galindo Gallego Garzón Gaviria Giraldo Guerrero
Guzmán Henao Hurtado Ibarra Jaramillo Lara Leal Londoño Lozano Mejía Medina Mendoza Molina Montoya Mora Moreno Muñoz
Navarro Nieto Núñez Ochoa Orozco Ortega Ospina Pacheco Palacios Parra Peña Pineda Plata Posada Prieto Quintero
Quiroga Restrepo Reyes Rincón Rivas Rojas Román Rueda Salazar Salcedo Sandoval Serna Sierra Solano Suárez Tamayo
Toro Trujillo Uribe Valencia Vanegas Vargas Vélez Villamil Villegas Zambrano Zapata Zuluaga Agudelo Alzate Bernal
Cano Chaparro Durán Forero Gil Granados Hoyos Lemus Marín Niño Olarte Patiño Quevedo Rendón Sabogal Tobón Urrego""".split()


def h(*parts) -> int:
    msg = "\x1f".join(str(p) for p in parts).encode()
    return int.from_bytes(hmac.new(SALT, msg, hashlib.sha256).digest(), "big")


def sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def slug(s: str) -> str:
    return "".join(c for c in sin_tildes(s).lower() if c.isalnum() or c in " .").replace(" ", ".")


def nombre(key: str, genero: str, attempt: int = 0, partes: int = 3) -> str:
    """Nombre completo: nombre + 2 apellidos (o nombre + segundo nombre + apellido)."""
    pool = NOMBRES_F if genero == "F" else NOMBRES_M
    x = h("name", key, attempt)
    n1 = pool[x % len(pool)]; x //= len(pool)
    a1 = APELLIDOS[x % len(APELLIDOS)]; x //= len(APELLIDOS)
    a2 = APELLIDOS[x % len(APELLIDOS)]; x //= len(APELLIDOS)
    if a2 == a1:
        a2 = APELLIDOS[(APELLIDOS.index(a1) + 7) % len(APELLIDOS)]
    if partes <= 2:
        return f"{n1} {a1}"
    return f"{n1} {a1} {a2}"


def digitos(key: str, kind: str, n: int, primero: str = "") -> str:
    x = h(kind, key)
    out = primero
    while len(out) < n:
        out += str(x % 10); x //= 10
        if x == 0:
            x = h(kind, key, len(out))
    return out


def cedula(key: str, original: str) -> str:
    d = "".join(c for c in original if c.isdigit())
    n = len(d) if 6 <= len(d) <= 10 else 10
    primero = str(1 + h("ced0", key) % 9)
    nueva = digitos(key, "cedula", n, primero)
    return reformatear(original, nueva)


def celular(key: str, original: str) -> str:
    d = "".join(c for c in original if c.isdigit())
    pref = d[:2] if d.startswith("57") and len(d) == 12 else ""
    nueva = pref + "3" + str(h("celp", key) % 3 + 0) + digitos(key, "cel", 8)
    nueva = pref + "3" + "012"[h("celp", key) % 3] + digitos(key, "cel", 8)
    return reformatear(original, nueva)


def reformatear(original: str, nuevos: str) -> str:
    """Copia el formato (puntos, espacios, guiones) del original sobre los dígitos nuevos."""
    d = [c for c in original if c.isdigit()]
    if len(d) != len(nuevos):
        return nuevos
    it = iter(nuevos)
    return "".join(next(it) if c.isdigit() else c for c in original)


def placa(key: str, original: str) -> str:
    letras = "BCDFGHJKLMNPRSTVWXYZ"
    x = h("placa", key)
    l = "".join(letras[(x >> (5 * i)) % len(letras)] for i in range(3))
    tail = digitos(key, "placa", 3)
    sep = "-" if "-" in original else (" " if " " in original.strip() else "")
    if original[-1:].isalpha():  # motos: ABC12D
        return f"{l}{sep}{tail[:2]}{letras[x % len(letras)]}"
    return f"{l}{sep}{tail}"


def pasaporte(key: str, original: str) -> str:
    letras = "ABCDEFGHJKLMNPRSTUVWXYZ"
    out = []
    for i, c in enumerate(original):
        if c.isdigit():
            out.append(str(h("pas", key, i) % 10))
        elif c.isalpha():
            ch = letras[h("pas", key, i) % len(letras)]
            out.append(ch if c.isupper() else ch.lower())
        else:
            out.append(c)
    return "".join(out)


def generico_alnum(key: str, original: str, kind: str) -> str:
    out = []
    for i, c in enumerate(original):
        if c.isdigit():
            out.append(str(h(kind, key, i) % 10))
        else:
            out.append(c)
    return "".join(out)
