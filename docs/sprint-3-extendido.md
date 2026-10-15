# Sprint 3 — plan de trabajo

Objetivo: que la clínica se pueda usar de punta a punta sin salir de la app: mover turnos, avisar por correo, guardar la consulta del médico y cubrir todo con pruebas.

## BACK (servidor)

| Tarea | En palabras simples | Prioridad |
|---|---|---|
| 1. Avisar por correo | Cuando alguien pide, cambia o cancela un turno, que le llegue un correo de confirmación. Requiere tener el correo configurado. | Alta |
| 2. Mover un turno | Hoy solo se puede cancelar o marcar atendido. Falta poder cambiar de médico u horario sin borrarlo y crearlo de nuevo. | Alta |
| 3. Guardar la consulta | El médico no registra nada de la atención. Falta un espacio para anotar qué se le hizo (diagnóstico, indicaciones) para que el historial sea real. | Alta |
| 4. Recuperar contraseña | No existe el "olvidé mi contraseña". Hay que agregarlo (correo con enlace para crear una nueva contraseña). | Media |
| 5. Avisar al que espera | Cuando se libera un horario, el que está en la cola hoy no se entera. Hay que avisarle que hay un hueco y ofrecerle el horario. | Media |
| 6. Verificación de cuenta más clara | Mejorar el mensaje de error cuando el código de verificación vence o es incorrecto, para que el usuario sepa qué hacer (reenviar código). | Media |

## FRONT (pantallas)

| Tarea | En palabras simples | Prioridad |
|---|---|---|
| 1. Mover turno desde pantalla | En el panel de recepción, agregar un botón para cambiar médico u horario de un turno ya existente. | Alta |
| 2. Historial del paciente | Mostrar las atenciones anteriores en el panel del médico y en el panel del paciente. | Alta |
| 3. Recuperar contraseña | Agregar la pantalla "Olvidé mi contraseña" y la pantalla para crear una nueva contraseña (con el enlace que llega por correo). | Alta |
| 4. Avisos y listas largas | Paginar cuando hay muchos turnos o pacientes (botón "ver más") y mostrar mensajes de error claros cuando algo falla. | Media |
| 5. Avisar en pantalla cuando se manda un correo | Mostrar un mensaje tipo "Te enviamos un correo a tu email" después de pedir un código o recuperar contraseña. | Media |
| 6. Mostrar estado de envío | En los formularios que envían correos, mostrar "Enviando..." mientras se procesa y evitar enviar dos veces el mismo pedido. | Media |

## BASE DE DATOS (DB)

| Tarea | En palabras simples | Prioridad |
|---|---|---|
| 1. Decidir la base | Resolver si la base en la nube (Neón) reemplaza a la base local, o si van a ser dos bases distintas. | Alta |
| 2. Rotar la credencial | Cambiar la clave de la base que se compartió en el chat, antes de publicar el proyecto. | Alta |
| 3. Preparar la estructura para guardar las atenciones del médico | Crear lo necesario para que cada atención quede guardada (lo que habilita "Guardar la consulta" del Back). | Alta |
| 4. Limpiar lo que no se usa | Decidir qué hacer con el modelo `PerfilPaciente` (que hoy no usa nadie): borrarlo, o conectarlo al médico para tener "mis pacientes". | Media |
| 5. Revisar datos de prueba | Ajustar los datos de demo para que tengan turnos, consultas y colas coherentes entre todos los roles. | Media |
| 6. Mantener coherencia entre datos | Revisar que los IDs/relaciones no queden sueltos cuando se borra o mueve algo (evitar datos huérfanos). | Baja |

## TESTERS

| Tarea | En palabras simples | Prioridad |
|---|---|---|
| 1. Pruebas automáticas de pantallas | Empezar a probar el front con pruebas automáticas (Vitest). Probar al menos login, registro y los 4 paneles básicos. | Alta |
| 2. Prueba de punta a punta | Automatizar el recorrido real con los 4 roles: entrar, pedir turno, moverlo, cancelarlo. Usar el script `verificar:paneles` como base para hacerlo automático. | Alta |
| 3. Casos difíciles | Probar lo que suele romperse: dos turnos en el mismo horario, turno cancelado, sin cupos, permisos cruzados entre roles y listas largas. | Alta |
| 4. Probar correos | Cuando el correo esté configurado, verificar que lleguen de verdad (alta de cuenta, recuperación de contraseña y avisos de turno). | Media |
| 5. Probar la cola de espera | Ver que cuando se libera un turno, la persona en cola puede recibir la oferta y agendarse sin errores. | Media |
| 6. Probar con datos reales de demo | Correr todas las pruebas con el comando `datos_demo` cargado, para asegurar que todo funciona con datos como los de producción. | Media |

## Orden sugerido

1. **Decisiones e infraestructura (DB + Back):** correo, Neón y rotar la credencial.
2. **Mover turnos (Back + Front + Testers):** lo más necesario para el día a día.
3. **Historia clínica (DB + Back + Front):** guardar las atenciones del médico.
4. **Notificaciones por correo (Back + Front + Testers):** confirmaciones y recuperación de contraseña.
5. **Pruebas automáticas del front y de punta a punta (Testers):** cubrir lo ya hecho.
6. **Avisar al que espera (Back + Front + Testers):** cerrar el ciclo completo de la cola.

## Para cerrar el sprint

- Cada tarea tiene sus pruebas y todas pasan.
- El recorrido completo funciona con los cuatro roles contra el servidor real.
- Las decisiones de correo y base quedan cerradas y documentadas.
- No quedan claves, contraseñas o datos sensibles en el código ni en el chat.
- Todo lo nuevo está verificado con los datos de demo.

## Decisiones que hacen falta del negocio

| # | Pregunta | A quién bloquea |
|---|---|---|
| 1 | ¿Qué proveedor de correo usamos? | Back, Front, Testers |
| 2 | ¿Neón reemplaza a la base local, o son dos bases distintas? | DB, Testers |
| 3 | ¿"Mis pacientes" se maneja por asignación (vincular paciente a médico) o por turnos (se calcula)? | DB, Back |
| 4 | ¿El paciente puede editar su ficha médica en cualquier momento, o queda pendiente de revisión por un profesional? | Back, Front |
| 5 | ¿Queremos que los correos salgan solo en desarrollo (consola) o también en producción? | Back, Testers |
