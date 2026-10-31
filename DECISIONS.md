# Decisiones

## Qué consideramos PII
- **Identificadores directos** de personas naturales: nombre completo y sus variantes (MAYÚSCULAS sin tildes, nombre + primer
  apellido, nombre de pila y apodos dentro del hilo, "señor + apellido"), cédula (con y sin puntos, pegada a letras en nombres
  de archivo, últimos 4 dígitos), celular (+57, con espacios o guiones), correo (también deletreado con "arroba" y "punto"),
  usuario corporativo (`fernanda.vargas`, `@emilia.arango`), menciones de Slack `<@UID>`, dirección, fecha de nacimiento,
  placa, pasaporte y número de cuenta.
- **Dónde**: texto de chats, nombres de adjuntos, cuerpo de documentos, metadatos de PDF (`author`, `keywords`, `subject`),
  `meta` de HTML, front matter de Markdown, comentarios HTML y todas las columnas de las tablas.
- **Cuasi-identificadores**: la combinación ocupación + barrio + año de vinculación. Se generaliza, no se borra:
  ocupación → sector (en la fila del maestro y en el texto), barrio → ciudad en el texto, barrio de la fila → otro barrio
  de la misma ciudad, año de vinculación mencionado → "hace unos años".

## Qué conservamos a propósito
- Organizaciones, marcas, lugares y figuras públicas (Bancolombia, Nequi, Santander como banco, Carulla, Juan Valdez,
  Superfinanciera, Plaza Botero, Shakira...). Se declaran en `entities.json` con `kept: true`.
- Llaves de negocio (`CL-…`, `TX-…`, `PQR-…`, `KP-…`, números de crédito), montos, fechas de transacción, ciudad y fecha de
  vinculación en el maestro: son la utilidad del corpus y permiten unir tablas.
- Un apellido que también es lugar o marca (Santander, Carulla, Castilla, Bolívar) se trata como apellido solo cuando va
  dentro de un nombre de persona.

## Decisiones clave
| Elegimos | En vez de | Por qué |
|---|---|---|
| Reglas + diccionario de identidades | LLM por registro | Determinista, barato (0 tokens) y rápido (~5 s); el enunciado reporta ~88 puntos para reglas sólidas |
| Seudónimo con HMAC de una llave estable | Tabla aleatoria | Misma persona, mismo seudónimo en todas las fuentes, en `/anonymize` y después de que crecen las fuentes |
| Identidad por correo, cédula, celular, id de cliente y evidencia del hilo | Identidad por nombre | Separa homónimos (dos Camila Arango, dos Juliana Mejía, agente y clienta Laura Ospina) |
| Reemplazar, no reescribir | Reescribir con un modelo | Conserva el texto de negocio y el parecido con la salida ideal |

## Costo
- Tokens de LLM: 0.
- Tiempo de una corrida completa (8.297 registros): ~5 segundos más la descarga de fuentes.
- `/anonymize`: < 1 segundo por lote de 50.
