# Sprint 3 — plan de trabajo

Este documento está pensado para repartir el trabajo entre el equipo. Cada
bloque es un frente distinto, con lo que hay que hacer y por qué. Lo técnico
queda en segundo plano: primero está **qué se necesita** y después **cómo**.

---

## Dónde estamos

- El **back** ya tiene toda la API de la clínica funcionando: 9 modelos, 23
  servicios y 159 pruebas en verde.
- El **front** ya tiene los cuatro paneles (admin, recepción, médico y
  paciente), pero **sin pruebas automáticas**.
- Falta cerrar dos cosas externas (el correo y la base en la nube) y algunos
  huecos del día a día: mover turnos, avisar al paciente y guardar la consulta
  médica.

## Objetivo del sprint 3

Que la clínica se pueda **operar de principio a fin sin salir de la app**:
mover un turno, avisar por correo, registrar la atención del médico y cubrir
todo con pruebas. Y dejar cerradas las decisiones de infraestructura.

---

## Back — lo que pasa del lado del servidor

| Tarea | En palabras simples | Prioridad |
|---|---|---|
| Avisar por correo | Cuando alguien pide, cambia o cancela un turno, que le llegue un correo de confirmación. Requiere tener el correo configurado. | Alta |
| Mover un turno | Hoy un turno solo se puede **cancelar o marcar como atendido**. Recepción necesita poder **cambiar el médico o el horario** de un turno ya dado, sin borrarlo y volver a crearlo. | Alta |
| Recuperar contraseña | No existe el "olvidé mi contraseña". Hay que agregarlo (correo con enlace para crear una nueva). | Media |
| Avisar al que espera | Cuando se libera un horario, hoy el sistema deja a la persona en la cola pero **nadie la llama**. Debe avisar al siguiente y ofrecerle el horario. | Media |
| Guardar la consulta | El médico hoy no registra nada de la atención. Falta un espacio para anotar **qué se le hizo al paciente** (diagnóstico, indicaciones) para que el "historial" sea real. | Alta |

**Depende de:** la configuración del correo (tarea de infraestructura) para
poder mandar cualquier notificación.

---

## Front — lo que ve el usuario

| Tarea | En palabras simples | Prioridad |
|---|---|---|
| Mover turno desde pantalla | En el panel de recepción, un botón para **cambiar médico u horario** de un turno existente. | Alta |
| Historial del paciente | En el panel del médico y del paciente, una sección para **ver las atenciones anteriores**. | Alta |
| Recuperar contraseña | Pantalla de "olvidé mi contraseña" y la de "crear contraseña nueva" (llega por correo). | Media |
| Avisos y listas largas | Cuando hay muchos turnos o pacientes, hoy se corta en los primeros. Hay que **paginar** (botón "ver más") y avisar bien cuando algo sale mal. | Media |
| Mensajes de correo | Avisar en pantalla cuándo se envió un correo ("te mandamos el código a tu email"). | Baja |

**Depende de:** las tareas de Back para tener los datos y las acciones.

---

## Base de datos (DB) — decisiones e infraestructura

| Tarea | En palabras simples | Prioridad |
|---|---|---|
| Decidir la base | Resolver si la base en la nube (**Neón**) reemplaza a la base local o son dos distintas. Hoy se prueba contra la local. | Alta |
| Rotar la credencial | La clave de la base que se compartió en el chat **hay que cambiarla** antes de publicar el proyecto. | Alta |
| Historia clínica | Preparar la estructura para guardar las atenciones del médico (es la que habilita la tarea "Guardar la consulta" de Back). | Alta |
| Limpiar lo que no se usa | Existe un modelo (`PerfilPaciente`) que **no lo usa nadie**. Decidir: se borra, o se conecta al médico para tener "mis pacientes". | Media |

**Depende de:** la decisión de negocio sobre Neón.
**Bloquea a:** Back (historia clínica) y a Testers (contra qué base se prueba).

---

## Testers — cómo comprobamos que está bien

| Tarea | En palabras simples | Prioridad |
|---|---|---|
| Pruebas de pantallas | Hoy **no hay ninguna prueba automática del front**. Empezar con pruebas de las funciones básicas y de las pantallas de cada rol. | Alta |
| Prueba de punta a punta | Automatizar el recorrido real: entrar, pedir turno, moverlo, cancelarlo, con los cuatro roles. Ya existe un borrador (`verificar:paneles`) que sirve de guion. | Alta |
| Casos difíciles | Probar lo que suele romperse: dos turnos en el mismo horario, turno cancelado, sin cupos, permisos cruzados entre roles, listas largas. | Alta |
| Correos reales | Cuando el correo esté configurado, verificar que **llegan de verdad** (alta, recuperación, avisos de turno). | Media |

**Depende de:** Front y Back para poder automatizar; DB para saber contra qué base
se corre.

---

## Orden sugerido de trabajo

1. **Decisiones e infraestructura**: correo, Neón y rotar la credencial.
   Desbloquea todo lo demás.
2. **Mover turnos** (Back + Front) y sus pruebas: es lo que más falta en el
   día a día.
3. **Notificaciones por correo** (Back + Front + Testers).
4. **Historia clínica** (DB + Back + Front).
5. **Pruebas automáticas del front** y de punta a punta.
6. **Recuperar contraseña** y **avisar al que espera**.

## Definición de "terminado"

- Cada tarea tiene sus pruebas y todas pasan.
- El recorrido completo funciona con los cuatro roles contra el servidor real.
- Las decisiones de correo y base quedan cerradas y documentadas.
- Sin contraseñas ni claves en el código ni en el chat.

## Decisiones que hacen falta del negocio

| # | Pregunta | A quién bloquea |
|---|---|---|
| 1 | ¿Qué proveedor de correo usamos? | Back, Testers |
| 2 | ¿Neón reemplaza a la base local, o son dos? | DB, Testers |
| 3 | ¿"Mis pacientes" se maneja por asignación o por turnos? | DB, Back |
| 4 | ¿El paciente puede editar su ficha médica en cualquier momento? | Back, Front |
