# Kipu Pay · anonimizador del corpus de soporte

Servicio HTTP que regenera `output/` (chats, docs, tables, entities) desde el API de fuentes del reto
"Vende los datos sin vender a la gente" y anonimiza registros nuevos en vivo con los mismos seudónimos.

## Correr

```bash
./run.sh                       # levanta el servicio en el puerto $PORT (9000 por defecto)
curl localhost:9000/v1/health
curl -X POST localhost:9000/v1/run -H 'content-type: application/json' \
     -d '{"sources_url":"https://reto-pii-fuentes-production.up.railway.app/v1","token":"<token>"}'
```

También se puede desplegar con el `Dockerfile` (Railway, Render, Fly).

## Cómo funciona

1. **Ingesta** (`app/sources.py`): baja chats, documentos (PDF, HTML, Markdown con metadatos) y tablas (CSV Latin-1 y UTF-8, XML).
2. **Registro de personas** (`app/anonymizer.py`): clientes, empleados y usuarios del chat se unen por correo, cédula,
   celular, id de cliente y evidencia en sus hilos. Los homónimos se separan por esos identificadores, nunca por el nombre.
3. **Personas solo-texto**: nombres completos de terceros que no están en ninguna tabla.
4. **Seudónimos deterministas**: HMAC con sal sobre una llave estable (id de cliente, id de empleado, uid). Mismo género,
   mismo formato (cédula, celular, correo, placa, pasaporte, dirección, fecha), estables entre corridas y cuando crecen las fuentes.
5. **Reemplazo** (`app/replacer.py`): sobre texto plegado (sin tildes, minúsculas) para atrapar MAYÚSCULAS SIN TILDES,
   correos deletreados, menciones de Slack, nombres en archivos, últimos 4 dígitos, y cuasi-identificadores
   (ocupación → sector, barrio → ciudad, año de vinculación → "hace unos años").
6. **Servicio** (`app/server.py`): `/v1/health`, `/v1/run`, `/v1/run/{job_id}`, `/v1/output/{archivo}`, `/v1/anonymize`, `/v1/entities`.

Sin LLM: todo es reglas, diccionarios y resolución de identidad. Corre en ~5 segundos.
