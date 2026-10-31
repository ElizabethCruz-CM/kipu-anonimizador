"""Listas curadas: nombres de pila, cosas que se conservan, ocupaciones -> sector."""

GIVEN_F = set("""Adela Adriana Alejandra Alicia Amalia Amparo Ana Andrea Ángela Antonia Aurora Beatriz Bibiana Camila
Carmenza Carolina Catalina Cecilia Celeste Clara Claudia Constanza Cristina Daniela Diana Dora Eliana Elena Elisa Elvira
Emilia Esperanza Estela Eugenia Fabiola Fernanda Gabriela Gloria Graciela Helena Inés Ingrid Irene Isabel Isabella
Ivonne Jimena Johana Josefina Juana Juanita Juliana Julieta Julieth Karina Katherine Kelly Laura Leonor Liliana Lina
Lizeth Lorena Lucía Luisa Luz Magdalena Magnolia Manuela Marcela Margarita María Mariana Marta Martina Mercedes Milena
Mónica Natalia Nathalia Nidia Nohora Norma Ofelia Olga Pamela Paola Patricia Paula Paz Pilar Raquel Renata Rocío Rosa
Salomé Sandra Sara Silvia Sofía Susana Tatiana Teresa Valentina Valeria Verónica Victoria Viviana Ximena Yesenia
Yolanda Yuliana Zoraida Lucero Maritza Nubia Omaira Piedad Rebeca Rubiela Soledad Yaneth Zulma Mariela Marisol Yamile
Luz Stella Doris Gladys Myriam Miriam Esther Lucy Angie Leidy Wendy Jessica Vanessa Melissa Lady Erika Karen
Daniella Gabriella Antonella Emma Lucila Rosalba Carmen Dolores Consuelo""".split())

GIVEN_M = set("""Adolfo Adrián Agustín Alberto Alejandro Alexander Álex Alex Alfonso Álvaro Andrés Aníbal Antonio Armando
Arturo Benjamín Bernardo Brayan Camilo Carlos César Cristian Cristóbal Dagoberto Daniel Darío David Diego Eduardo Emilio
Enrique Ernesto Esteban Fabián Farid Federico Felipe Fernando Francisco Gabriel Germán Gilberto Gonzalo Guillermo
Héctor Hernán Hernando Hugo Ignacio Iván Jaime Javier Jerónimo Jesús Joaquín Jorge José Juan Julián Kevin Leonardo
Lisandro Luis Manuel Marco Mario Martín Mateo Mauricio Miguel Milton Nicolás Norberto Octavio Óscar Pablo Pedro Rafael
Ramiro Raúl Ricardo Roberto Rodolfo Rodrigo Rubén Samuel Santiago Saúl Sebastián Sergio Simón Teodoro Tomás Vicente
Víctor Wilson Yamid Abel Bayron Edgar Efraín Fredy Gerardo Horacio Jairo Leandro Marino Nelson Reinaldo Wilmer Yesid
Jhon John Jhonatan Andrey Duván Edwin Fabio Harold Hollman Jairo Jefferson Juancho Oswaldo Orlando Rigoberto Alonso""".split())

GIVEN = GIVEN_F | GIVEN_M

# Palabras capitalizadas que nunca empiezan un nombre de persona aunque coincidan con uno
NOT_NAME_AFTER = set("""de del la las los el y e o u a en con por para que sin""".split())

# Organizaciones, lugares, marcas y figuras públicas que se conservan (se declaran kept en entities.json)
KEPT = [
    "Kipu Pay", "Kipi", "Bancolombia", "Davivienda", "Banco de Bogotá", "Banco de Occidente", "BBVA", "Banco Popular",
    "Banco Caja Social", "Scotiabank Colpatria", "Itaú", "Santander", "Nequi", "Daviplata", "PSE", "DataCrédito",
    "Datacrédito", "TransUnion", "CIFIN", "Superfinanciera", "Superintendencia Financiera", "SFC", "DIAN", "Registraduría",
    "Carulla", "Éxito", "Terpel", "Farmatodo", "Juan Valdez", "Rappi", "Spotify", "Netflix", "Tigo", "Movistar",
    "EPM", "Enel", "Emcali", "Avianca", "D1", "Unicentro", "Seguros Bolívar", "Sura", "Colsanitas", "Andes Capital",
    "Los Andes", "Universidad de los Andes", "Mensajería Veloz", "Mensajeria Veloz", "Veloz", "Visa", "Mastercard",
    "Google", "WhatsApp", "Slack",
    "Plaza Botero", "Parque Lleras", "Parque de Bolívar", "Parque Simón Bolívar", "Carrera Séptima", "Avenida El Dorado",
    "Shakira", "Gabriel García Márquez", "Jorge Isaacs", "Egan Bernal", "Fernando Botero", "Juanes", "Carlos Vives",
    "Nairo Quintana", "James Rodríguez", "Gustavo Petro", "Simón Bolívar",
    "Bogotá", "Medellín", "Cali", "Barranquilla", "Cartagena", "Bucaramanga", "Pereira", "Lisboa", "Miami", "Colombia",
]

OCUPACION_SECTOR = {
    "comerciante": "profesional del comercio",
    "contadora": "profesional de finanzas", "contador": "profesional de finanzas",
    "auxiliar contable": "profesional de finanzas",
    "abogada": "profesional del derecho", "abogado": "profesional del derecho",
    "estudiante de derecho": "estudiante universitario", "estudiante de medicina": "estudiante universitario",
    "docente": "profesional de la educación", "profesora": "profesional de la educación", "profesor": "profesional de la educación",
    "mecánico": "profesional del transporte", "conductor de plataforma": "profesional del transporte",
    "piloto comercial": "profesional del transporte", "conductor": "profesional del transporte",
    "periodista": "profesional de medios", "community manager": "profesional de medios",
    "fotógrafo": "profesional de las industrias creativas", "fotógrafa": "profesional de las industrias creativas",
    "diseñador gráfico": "profesional de las industrias creativas", "diseñadora gráfica": "profesional de las industrias creativas",
    "panadero": "profesional de alimentos", "panadera": "profesional de alimentos", "chef": "profesional de alimentos",
    "administrador": "profesional de servicios", "administradora": "profesional de servicios",
    "estilista": "profesional de servicios",
    "ingeniero de sistemas": "profesional de tecnología", "ingeniera de sistemas": "profesional de tecnología",
    "arquitecto": "profesional de la construcción", "arquitecta": "profesional de la construcción",
    "ingeniero civil": "profesional de la construcción", "ingeniera civil": "profesional de la construcción",
    "electricista": "profesional de la construcción",
    "fisioterapeuta": "profesional de la salud", "enfermera": "profesional de la salud", "enfermero": "profesional de la salud",
    "odontóloga": "profesional de la salud", "odontólogo": "profesional de la salud",
    "psicóloga": "profesional de la salud", "psicólogo": "profesional de la salud",
    "médica": "profesional de la salud", "médico": "profesional de la salud",
    "veterinario": "profesional de la salud animal", "veterinaria": "profesional de la salud animal",
    "diseñadora": "profesional de las industrias creativas", "diseñador": "profesional de las industrias creativas",
    "ingeniera": "profesional de ingeniería", "ingeniero": "profesional de ingeniería",
    "estudiante": "estudiante universitario", "auxiliar de enfermería": "profesional de la salud",
    "nutricionista": "profesional de la salud", "dentista": "profesional de la salud",
}


def sector(ocupacion: str) -> str:
    o = (ocupacion or "").strip().lower()
    if not o:
        return ocupacion
    if o in OCUPACION_SECTOR:
        return OCUPACION_SECTOR[o]
    for k, v in OCUPACION_SECTOR.items():
        if k in o:
            return v
    return "profesional independiente"
