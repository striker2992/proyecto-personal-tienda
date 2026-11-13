# ContainerPro

Tienda inicial construida con Django. Los productos se guardan en la base de datos; SQLite sirve para desarrollo local y MySQL se configura mediante variables de entorno.

## Preparar el entorno en Windows

Desde PowerShell, en la carpeta del proyecto:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Este workspace ya tiene un `.env` local de desarrollo con una clave aleatoria; no lo compartas ni lo subas al repositorio. En una instalación nueva, genera una clave privada para `DJANGO_SECRET_KEY` y cópiala dentro de `.env`:

```powershell
py -c "import secrets; print(secrets.token_urlsafe(50))"
```

Deja `DJANGO_DEBUG=True` y `DB_ENGINE=sqlite` para trabajar localmente. Luego ejecuta:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

Abre `http://127.0.0.1:8000`. La cuenta creada con `createsuperuser` tiene permiso de administrador. Los visitantes pueden crear cuentas de cliente desde **Crear cuenta**; el formulario público nunca permite asignarse permisos administrativos.

El panel privado está en `/panel/productos/`. Permite añadir y editar productos (incluida una imagen PNG, JPEG o WebP de hasta 5 MB), configurar existencias y publicarlos u ocultarlos. También permite responder y eliminar consultas y elegir el correo receptor. El panel `/panel/ventas/` muestra los pedidos y estadísticas; ambas áreas requieren una cuenta marcada como administradora. Para cerrar sesión se usa un formulario POST protegido por CSRF.

## MySQL

Configura MySQL, crea una base de datos y un usuario con permisos limitados solo sobre esa base. En `.env`, establece `DB_ENGINE=mysql` y los valores `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` y `DB_PORT`. Después aplica las migraciones con `manage.py migrate`. PyMySQL proporciona el controlador de desarrollo; para una instalación productiva conviene revisar el controlador recomendado por el proveedor de despliegue.

## Qué incluye y qué falta

- Registro e inicio de sesión con el sistema de usuarios y hash de contraseñas de Django.
- Roles diferenciados por `user.is_staff`; las rutas de administración también comprueban el rol en el servidor.
- Bloqueo temporal de una hora tras tres intentos fallidos para la misma combinación de usuario e IP mediante django-axes; los errores al entrar a otra cuenta no bloquean toda la IP.
- Formularios POST con CSRF, cookies de sesión seguras fuera de modo debug, validación de contraseñas y configuración de seguridad HTTPS.
- Catálogo persistido en la base de datos con imágenes e inventario por producto y cuatro productos de ejemplo con existencias iniciales.
- Formulario público de consultas y reclamos; cada mensaje queda guardado y se envía al correo elegido por el administrador.
- Perfil editable con nombre, correo, teléfono, RUT chileno y dirección habitual. El RUT se normaliza y se comprueba su dígito verificador; esto no acredita que el número esté asignado ni que pertenezca a quien lo introduce. El teléfono debe verificarse por SMS antes de comprar. Los pedidos guardan una copia histórica de los datos utilizados; el cliente consulta su historial por separado en **Mis compras** (`/cuenta/compras/`).
- En el checkout, el correo, RUT y teléfono se toman de Mi cuenta; no se aceptan cambios de esos datos desde el formulario del pedido. El servidor valida productos, cantidades y stock, calcula los precios desde la base de datos y crea un pedido pendiente con el detalle histórico. Las existencias se reservan de forma transaccional; cancelar el pedido las devuelve una sola vez. Un pedido cancelado no se puede reabrir.
- Panel de ventas con conteos, total de pedidos confirmados y hasta 100 pedidos recientes; el administrador puede actualizar su estado.
- El carrito local no deja añadir más unidades que las indicadas en existencias y elimina o limita selecciones guardadas si cambia el inventario. La validación definitiva ocurre en el servidor al enviar el pedido.

**Checkout Pro está preparado para pruebas de Mercado Pago, pero no puede cobrar hasta configurar credenciales y una URL pública HTTPS.** El sitio nunca recibe ni guarda números de tarjeta. El estado “pagado” solo se actualiza al verificar la firma del webhook y consultar el pago directamente a Mercado Pago; la página de retorno del navegador no confirma el pago.

### Probar Mercado Pago Checkout Pro

1. Crea una aplicación en [Tus integraciones de Mercado Pago](https://www.mercadopago.cl/developers/panel/app) y usa las credenciales de prueba del vendedor y un usuario comprador de prueba.
2. Expón el servidor local con un túnel HTTPS temporal. En `.env`, establece `MP_PUBLIC_BASE_URL` con esa URL pública y agrega su dominio a `DJANGO_ALLOWED_HOSTS`. Mercado Pago no puede notificar a `localhost` ni a `127.0.0.1`.
3. Configura en `.env` el `MP_ACCESS_TOKEN` de prueba, el `MP_WEBHOOK_SECRET` de la aplicación y `MP_USE_SANDBOX=True`. No compartas ni subas esos valores.
4. En la configuración de Webhooks de Mercado Pago, activa el evento de pagos y usa `https://<tu-dominio>/pagos/mercadopago/notificacion/`. La URL también se envía al crear la preferencia.
5. Inicia Django y haz un pedido de prueba. En el detalle del pedido aparecerá **Pagar con Mercado Pago**; después de volver, espera la notificación para ver el estado actualizado.
6. Para producción, cambia a credenciales de producción, configura el dominio definitivo con HTTPS y ajusta `MP_USE_SANDBOX=False` solo después de comprobar pagos aprobados, rechazados y notificaciones repetidas.

Los pedidos reservan stock al crearse. Si cancelas un pedido mientras un pago sigue pendiente, revisa su estado en Mercado Pago: un pago aprobado tardío requiere revisión y posible reembolso manual. La integración no realiza reembolsos automáticamente. Las pruebas automatizadas simulan la API; todavía falta probar con las credenciales y cuenta reales del negocio.

### Verificación de teléfono por SMS

La aplicación envía códigos de 6 dígitos mediante Twilio. Crea una cuenta y configura en el `.env` local `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` y uno de `TWILIO_FROM_NUMBER` o `TWILIO_MESSAGING_SERVICE_SID`. El teléfono se guarda en formato internacional (por ejemplo, `+56912345678`). El código vence en 10 minutos, permite hasta 5 intentos y limita los envíos a 3 por hora; se almacena como hash y nunca se muestra en la página. Twilio puede cobrar por los mensajes y recibe el teléfono y el texto del SMS. No publiques sus credenciales.

El chequeo local del RUT valida formato y dígito verificador, no consulta una base oficial ni confirma identidad. Para demostrar que un RUT existe o pertenece a la persona se necesita una verificación autorizada de identidad; no se debe inferir solo a partir del checksum.

## Correo

En desarrollo, el correo se imprime en la consola del servidor. Para el despliegue configura las variables SMTP (`EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` y `DEFAULT_FROM_EMAIL`) con las credenciales de un proveedor de correo. El administrador elige el correo destinatario desde el panel; el formulario web no permite a visitantes cambiar esa dirección.

## Antes de publicar

- Usa MySQL administrado, crea una base de datos/usuario dedicados y protege las credenciales; no subas `.env`.
- Genera una `DJANGO_SECRET_KEY` nueva, única y secreta. Pon `DJANGO_DEBUG=False`.
- Establece `DJANGO_ALLOWED_HOSTS` y `DJANGO_CSRF_TRUSTED_ORIGINS` con el dominio real.
- Activa HTTPS en el hosting y `DJANGO_SECURE_SSL_REDIRECT=True` solo cuando el proxy TLS esté correctamente configurado.
- Ejecuta `manage.py check --deploy` y `manage.py collectstatic`, configura copias de seguridad y revisa privacidad, términos, correo y soporte del negocio.
- No despliegues el servidor de desarrollo `runserver`.
