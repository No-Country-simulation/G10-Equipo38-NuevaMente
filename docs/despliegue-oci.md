# Instalación y operación de NuevaMente en OCI — Issue 58

## Alcance y reglas de ejecución

Guía documental de instalación y operación, **etapas 2–4**, contrastada por
lectura el **17/10/2026** contra la base integrada de `develop`
`5988244a40fb1f3061c0bafc1fc106eb6e592a0a` (#137/#138/#140).
El merge local de #58 es `8b97c6b5970cfcb0bae87cc7c7d8923baa40aa9d`.
Las pruebas y revisiones del 16/10 conservan su base `f292415`; no se atribuyen a
esta integración. El contraste nuevo es documental, sin nuevas pruebas funcionales.
No se ejecutó sobre una VM.
El resultado local volvió al alcance exclusivamente documental: código, tests y
manifiesto de smoke coinciden con `5988244`. Las implementaciones experimentales
de 17.1 y 17.4 fueron retiradas y respaldadas fuera del checkout. Sus ensayos
son históricos, no validación de las capacidades disponibles en esta receta.
El [registro de verificación](verificacion-issue-58.md) distingue lectura de código,
evidencia histórica, revisión humana de #58 y reproducción real pendiente en
[#133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133).

Todos los comandos operativos siguientes son para una **ejecución posterior
expresamente autorizada**. Esta documentación no autoriza aprovisionamiento,
proveedores, DNS ni gasto. No ejecutar bloques completos sin satisfacer sus
prerrequisitos. Ante una condición de parada, conservar datos y evidencias y
detener el procedimiento; no omitir guardas para continuar.

La topología vigente es React → Caddy en el host → FastAPI; Compose aloja backend
y servidor de archivos React. Sólo backend monta datos y credenciales. No se usa
Streamlit: fue retirado en #139, integrado por #140. El proyecto productivo se llama `nuevamente`,
el volumen `nuevamente_backend_data` y el checkout de esta receta
`/opt/nuevamente/checkout`. Todas las sesiones Bash indicadas abajo son en la VM,
salvo las identificadas como consola del operador. No usar `set -x`, registrar
terminales con secretos ni publicar `docker inspect`/Compose resuelto.
En cada sesión Bash operativa habilitar `set -Eeuo pipefail`: un comando que
falla debe detener el bloque. No pegar los ejemplos PowerShell en Bash ni viceversa.
En PowerShell comprobar también `$LASTEXITCODE` después de cada programa nativo
(`git`, `ssh`, `scp`, Python o `curl.exe`): un valor distinto de 0 exige parar,
aunque PowerShell permita continuar con la línea siguiente.

Las secciones 1–12 conservan la instalación de etapa 2; las secciones 13–19 agregan
la operación de etapa 3. Sólo se ensayó copia/restauración con datos sintéticos,
según el registro de verificación. La etapa 4 registra comprobaciones locales y
sus límites; la revisión humana y la reproducción real siguen pendientes.
Los huecos operativos de 17.1/17.4 afectan la suficiencia local de recuperación
de #58: requieren decisión/implementación del equipo, no sólo una prueba de #133.
Esta receta no acredita la aceptación de ninguno de esos pendientes.

## 1. Prerrequisitos y verificación de costo cero

**Máquina/shell:** PC del operador, PowerShell, y consola web OCI/Google/DNS.
**Previo:** autorización operativa separada, cuenta OCI, home region y compartimento
identificados; Git, OpenSSH y Python 3.11 con `oci==2.187.2` del
[requirements backend](../backend/requirements.txt). Si falta ese entorno, preparar
las dependencias fijadas fuera de esta revisión documental antes de continuar.

Se necesitan tres identidades/credenciales diferentes:

| Uso | Custodia y permisos |
| --- | --- |
| Operador OCI | Config y signing key fuera del repo y de la VM; permisos de inventario/provisión Compute/red. No copiar al backend |
| SSH | Par Ed25519 del administrador, privada en su PC; OCI recibe sólo `.pub`. Usuario `ubuntu`, sudo, sin contraseña/root SSH |
| Backend OCI y Gemini | Usuario técnico OCI ajeno a Administrators, limitado al bucket; API key Gemini y cuotas verificadas. Sólo servidor/backend; nunca React ni variables `VITE_*` |

Comprobar la versión **antes** de producir el archivo de despliegue:

```powershell
$ErrorActionPreference = 'Stop'
$revisionDespliegue = '5988244a40fb1f3061c0bafc1fc106eb6e592a0a'
git status --short --branch
git rev-parse HEAD develop origin/develop
git ls-remote origin refs/heads/develop
git show --no-patch --format=fuller $revisionDespliegue
```

Estos comandos inspeccionan, no actualizan refs. Si `develop` remoto cambió,
revisar y registrar una nueva base de la receta antes de desplegarla; no cambiar
silenciosamente la revisión. Los cambios locales no entran en `git archive`.
La fuente operativa fijada aquí es el segundo padre del merge; el HEAD de la
rama documental puede diferir, al igual que la referencia local `develop`.
Registrar las referencias por separado, sin actualizarlas para aparentar igualdad.

En la consola OCI, registrar fecha y evidencia no secreta de los controles:

| Control manual | Qué debe verificarse antes de confirmar Always Free |
| --- | --- |
| Cuenta/región | Tipo de cuenta, home region y asignación efectiva gratuita; no depender de créditos trial ni de una cuota de servicio mayor |
| A1 agregado | Inventario y consumo mensual de la tenancy, incluidas instancias detenidas. La receta fija 2 OCPU/12 GB; decisiones §9 cita 1.500 OCPU-h/9.000 GB-h, sujetos a verificación efectiva |
| Discos | Boot de esta VM 50 GB Balanced/10 VPU por GB; boot y block agregados dentro de los 200 GB de la receta. Contar discos huérfanos y backups existentes por separado; no activar políticas nuevas |
| Otros recursos | Revisar recursos y consumo agregados de la tenancy, transferencia y cualquier servicio ajeno. No crear balanceadores, Marketplace, replicación u otros servicios para sortear un bloqueo |
| Object Storage | Bucket privado `nuevamente-contenidos-educativos`, Standard, home region, versionado/replicación/auto-tiering desactivados y sin transiciones habilitadas. Ver [preparación e IAM](oci-storage.md#2-permisos-mínimos) |
| Presupuestos | Máximo propio 1 GB y 20.000 solicitudes/mes, 2.000 reservadas para limpieza; conservar ledger/consumo previo. Las asignaciones Oracle dependen de la cuenta y consumo global; una alerta Budgets no corta gasto |
| Gemini | Modelos accesibles y cuotas RPM/TPM/RPD verificadas, consumo previo y autorización acotada para precarga/arranque. No activar facturación ni repetir pruebas para obtener verde |

Consultar [asignaciones oficiales OCI](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
y [gobernanza del proyecto](gobernanza-costos.md). La bandera
`OCI_ALWAYS_FREE_CONFIRMED=1` declara esta revisión humana; no comprueba precios.
Preparar un dominio existente o gratuito aprobado, sin compras automáticas.

**Esperado:** revisión del código y recursos/consumo firmada por el operador.
**Consumo posible:** lecturas de GitHub y consolas; todavía no se ejecuta provisión.
**Parar si:** faltan permisos, asignación gratuita o margen; hay consumo no
reconciliado, credenciales administrativas usadas como técnicas o base divergente.

## 2. Aprovisionar A1 o registrar falta de capacidad

**Máquina/shell:** PC del operador, PowerShell en la raíz del repo.
**Previo:** sección 1 aprobada, config del operador válido y clave pública Ed25519.
Los valores `REEMPLAZAR` son campos a completar, nunca credenciales reales aquí.

```powershell
$configuracionOci = 'C:/ruta/fuera-del-repo/.oci/config'
$compartimentoOci = 'ocid1.compartment.oc1..REEMPLAZAR'
.venv/Scripts/python.exe -B infra/oci/provision_vm.py --config-file $configuracionOci --compartment-id $compartimentoOci
```

La consulta escribe reportes `.data/oci47/preflight-*.json`; no crea infraestructura.
Conservar el diario `.data/oci47/state.json` si existe: no reemplazarlo por un
reporte ni un snapshot anterior. Cada intento reconsulta capacidad e inventario.
Cuando la consulta y la revisión humana lo permitan:

```powershell
$ipAdministracion = 'TU_IPV4_PUBLICA/32'
$clavePublicaSsh = 'C:/ruta/fuera-del-repo/id_ed25519.pub'
.venv/Scripts/python.exe -B infra/oci/provision_vm.py --config-file $configuracionOci --compartment-id $compartimentoOci --admin-cidr $ipAdministracion --ssh-public-key $clavePublicaSsh --confirm-always-free --apply
```

El [aprovisionador](../infra/oci/provision_vm.py) selecciona Ubuntu 24.04 ARM64
oficial, A1 2 OCPU/12 GB, boot 50 GB y red restringida en home region; retoma
recursos de su diario con comprobaciones de pertenencia/configuración e idempotencia.
No asumir que una VM ya existente es adoptable por borrar el diario.

**Esperado:** salida de IDs/estado y diario de la configuración autorizada;
`RUNNING` todavía no acredita cloud-init ni Docker. Código 2 comunica bloqueo,
3 error OCI/configuración; salida 0 de la consulta tampoco acredita capacidad.
**Consumo posible:** consultas OCI y, con `--apply`, recursos Compute/red/disco.
**Parar si:** `OUT_OF_HOST_CAPACITY`, cuota insuficiente, diario incompatible o
token incierto/vencido. Registrar intentos manuales acotados con fecha/resultado,
esperar decisión operativa y no cambiar región/shape ni contratar recursos pagos.
Los bloqueos históricos del 8/12 de octubre no prueban capacidad actual.

## 3. SSH, cloud-init y fuente desplegada

**Máquina/shell:** consola OCI de confianza, PC/PowerShell y VM/Bash como `ubuntu`.
**Previo:** IP pública/identidad de la VM contrastadas con OCI y /32 administrativo.
Obtener la huella de host desde un canal OCI de confianza, por ejemplo la consola
serial autorizada, ejecutando dentro de la VM:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Comparar esa huella al conectar desde la PC; no aceptar ciegamente la primera clave:

```powershell
$ipVm = 'IPV4_PUBLICA_VERIFICADA'
$claveSsh = 'C:/ruta/fuera-del-repo/id_ed25519'
ssh -o StrictHostKeyChecking=ask -i $claveSsh "ubuntu@$ipVm"
```

En la VM:

```bash
set -Eeuo pipefail
cloud-init status --wait --long
uname -m
dpkg --print-architecture
sudo stat -c '%U:%G %a %n' /opt/nuevamente /opt/nuevamente/issue47-ready
sudo /usr/sbin/sshd -t
sudo /usr/sbin/sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin|addressfamily) '
sudo systemctl is-active docker nuevamente-firewall unattended-upgrades
sudo systemctl is-enabled docker nuevamente-firewall unattended-upgrades
sudo docker compose version
sudo docker info --format '{{.Architecture}}'
sudo iptables -w 10 -S
sudo iptables -w 10 -t filter -S DOCKER-USER
sudo ss -H -lntu
```

Esperar `done`, Ubuntu 24.04/aarch64/arm64, marcador root:root 0644 bajo
`/opt/nuevamente` ubuntu:ubuntu 0750; SSH sin root/contraseña e IPv4; servicios
activos/habilitados. El bootstrap ya instala Docker Engine/Compose desde el
repositorio oficial, actualizaciones automáticas y firewall. No repetir bootstrap
en una instalación con datos. Caddy exige Compose **>=2.24.4**, Python 3 del sistema,
`systemd-analyze`, `nft`, `iptables`, `ip`, `ss` y `flock`.
Si falta `nft`, instalar su paquete como preparación explícita de la VM:

```bash
sudo apt-get update
sudo apt-get install -y nftables
```

SSH sólo TCP/22 desde /32 administrativo; TCP/80 y 443 públicos; ICMP 3/4 para
MTU; 8000/8501 sólo loopback. La receta no habilita IPv6, UDP/443 ni administración
Caddy TCP. No cambiar reglas para ocultar un fallo. La comprobación desde otra IP
y la correlación OCI/host quedan para ejecución autorizada/evidencia de #133.

En la PC crear un archivo de fuentes versionadas, sin incluir `.env` ni cambios
sin commit. Requiere `.data` como directorio local de artefactos:

```powershell
New-Item -ItemType Directory -Force .data | Out-Null
if (Test-Path .data/nuevamente-5988244.tar) { throw 'Archivo previo: conservarlo y revisar su procedencia' }
git archive --format=tar --output=.data/nuevamente-5988244.tar $revisionDespliegue
if ($LASTEXITCODE -ne 0) { throw 'git archive fallo' }
Get-FileHash .data/nuevamente-5988244.tar -Algorithm SHA256
scp -i $claveSsh .data/nuevamente-5988244.tar "ubuntu@${ipVm}:/opt/nuevamente/"
```

Comparar en la VM el SHA256 con el de la PC y, **sólo si el destino no existe**:

```bash
set -euo pipefail
sha256sum /opt/nuevamente/nuevamente-5988244.tar
test ! -e /opt/nuevamente/checkout
test ! -L /opt/nuevamente/checkout
mkdir -m 0750 /opt/nuevamente/checkout
tar -xf /opt/nuevamente/nuevamente-5988244.tar -C /opt/nuevamente/checkout
cd /opt/nuevamente/checkout
sudo bash infra/oci/verify_host.sh
```

`checkout` es aquí la extracción de `git archive` del commit indicado; no contiene
`.git`. Registrar commit y SHA256 del tar. Si ya existe un checkout, comprobar su
procedencia y contenido antes de reutilizarlo; no extraer encima ni reemplazarlo.

**Esperado:** fuente exacta y host preparado. `verify_host.sh` incluye `hello-world`;
no es un control sin efectos. **Consumo posible:** descargas apt/Docker, tráfico
SSH y espacio del archivo; no Gemini. **Parar si:** huella inesperada, cloud-init
con error, arquitectura/servicios/permisos distintos, Compose viejo o SHA divergente.

## 4. Construir imágenes ARM64 sin arrancar la aplicación

**Máquina/shell:** VM/Bash, `/opt/nuevamente/checkout`.
**Previo:** fuente revisada, margen de disco/memoria, Docker ARM64 y autorización
de descargas. Ésta es la primera construcción; actualizar imágenes ya desplegadas
se desarrolla en la sección 13. No sobrescribir tags de una instalación activa con esta receta.

```bash
cd /opt/nuevamente/checkout
sudo docker buildx build --platform linux/arm64 --load --tag nuevamente-backend:latest ./backend
sudo docker buildx build --platform linux/arm64 --load --tag nuevamente-frontend:latest ./frontend
sudo docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}} {{.Config.User}}' nuevamente-backend:latest nuevamente-frontend:latest
```

**Esperado:** ambas `linux/arm64`, usuario `10001:10001`; anotar IDs `sha256:...`.
Son los tags que `freeze_model()` inspecciona; `--activate` fija esos IDs en su
modelo privado. No se usa un registry de imágenes de aplicación inexistente.
**Consumo posible:** registry/repositorios pip/npm/apt, CPU/RAM/disco de builds;
no llamadas OCI/Gemini desde la aplicación. **Parar si:** fallo de build, plataforma
o usuario distintos; no cambiar a amd64 ni omitir dependencias/pruebas para continuar.

## 5. Preparar Caddy sin publicar

**Máquina/shell:** VM/Bash, checkout revisado.
**Previo:** host listo y sin instalación Caddy ajena. Si hay archivos/usuario/unidad
Caddy previos, el instalador los verifica o rechaza; no borrarlos ni sobrescribirlos.
Los hashes/versiones fijados están en [verify_deployment.py](../infra/caddy/verify_deployment.py).

Sólo para directorios de artefactos nuevos:

```bash
sudo bash <<'BASH'
set -Eeuo pipefail
umask 077
test ! -e /root/issue48-artifacts && test ! -L /root/issue48-artifacts
test ! -e /root/issue48-verifiers && test ! -L /root/issue48-verifiers
install -d -m 0700 -o root -g root /root/issue48-artifacts /root/issue48-verifiers
for archivo in caddy_2.11.7_linux_arm64.tar.gz caddy_2.11.7_linux_arm64.pem caddy_2.11.7_linux_arm64.tar.gz.sig caddy_2.11.7_checksums.txt caddy_2.11.7_checksums.txt.pem caddy_2.11.7_checksums.txt.sig; do
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    "https://github.com/caddyserver/caddy/releases/download/v2.11.7/$archivo" \
    --output "/root/issue48-artifacts/$archivo"
done
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  https://github.com/sigstore/cosign/releases/download/v2.6.5/cosign-linux-arm64 \
  --output /root/issue48-verifiers/cosign
chmod 0755 /root/issue48-verifiers/cosign
BASH
sudo bash infra/caddy/install.sh --artifacts /root/issue48-artifacts --cosign /root/issue48-verifiers/cosign
```

El instalador comprueba primero hash del Cosign 2.6.5, versiones, SHA256/SHA512
del tar, manifiesto y ambas firmas Caddy 2.11.7. Requiere red Sigstore/TUF/Rekor;
no hay modo offline que omita transparencia. Prepara usuario, binario, helper,
unidad notify, drop-in y directorios, sin activar sitio ni arrancar Caddy.

**Esperado:** `installation-prepared; service-not-activated`, o comprobación
idempotente de una instalación idéntica. **Consumo posible:** descargas y consultas
de firmas, archivos locales/systemd; sin aplicación/proveedores ni ACME todavía.
**Parar si:** firma/hash/versión, instalación parcial o permisos incompatibles.

## 6. Archivos canónicos y credenciales técnicas

**Máquina/shell:** VM/Bash con sudo; edición privada sin volcado a terminal.
**Previo:** Caddy preparado, credenciales técnicas emitidas por el equipo, cuotas
verificadas. No usar aquí config/signing key del operador de la sección 2.

| Ruta | Dueño / modo | Contenido |
| --- | --- | --- |
| `/etc/nuevamente` | root:root 0755 en esta instalación | Padre protegido, sin escritura de grupo/otros |
| `/etc/nuevamente/secrets` | root:root 0700 | Fuente privada del secreto de origen |
| `/etc/nuevamente/secrets/origin.env` | root:root 0600 | Sólo `TRUSTED_ORIGIN_SECRET`, 64 hex minúsculas |
| `/etc/nuevamente/backend.env` | root:root 0600 | Gemini/configuración backend; no secreto de origen |
| `/etc/nuevamente/deployment.env` | root:root 0600 | Interpolación Compose; sin claves Gemini/origen |
| `/etc/nuevamente/oci` | 10001:10001 0700 | Sólo `config` y `private_key.pem` |
| Ambos archivos OCI | 10001:10001 0600 | Perfil técnico `DOCKER` y signing key correspondiente |
| `/etc/caddy/caddy.env` | root:root 0600 | Dominio y rutas públicas de Caddy |
| `/etc/caddy` | root:caddy 0750 | Lo prepara el instalador; no cambiarlo |

El helper rechaza symlinks, ACL extendidas y padres inseguros. No usar `chmod -R`
ni `chown -R`. Para una instalación nueva, el bloque siguiente crea exclusivamente
directorios/archivos nuevos; **si existen, parar y revisar**, no regenerar secretos:

```bash
sudo python3 - <<'PY'
import os
import secrets
from pathlib import Path
os.umask(0o077)
root = Path('/opt/nuevamente/checkout')
for name, mode, uid, gid in (
    ('/etc/nuevamente', 0o755, 0, 0),
    ('/etc/nuevamente/secrets', 0o700, 0, 0),
    ('/etc/nuevamente/oci', 0o700, 10001, 10001),
):
    p = Path(name)
    if os.path.lexists(p):
        raise SystemExit('Directorio previo: revisar sin normalizar permisos')
    p.mkdir(mode=mode)
    os.chown(p, uid, gid)
    os.chmod(p, mode)
def nuevo(name, value):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(value)
backend = '\n'.join(line for line in (root / '.env.example').read_text().splitlines()
                    if not line.startswith('TRUSTED_ORIGIN_SECRET=')) + '\nMOCK_GEMINI=0\n'
backend = backend.replace('OCI_CONFIG_PROFILE=DEFAULT', 'OCI_CONFIG_PROFILE=DOCKER')
nuevo('/etc/nuevamente/backend.env', backend)
nuevo('/etc/nuevamente/deployment.env', (root / '.env.production.example').read_text())
nuevo('/etc/nuevamente/secrets/origin.env', 'TRUSTED_ORIGIN_SECRET=' + secrets.token_hex(32) + '\n')
nuevo('/etc/caddy/caddy.env', (root / 'infra/caddy/caddy.env.example').read_text())
print('Archivos preparados; completar valores privados sin imprimirlos')
PY
sudoedit /etc/nuevamente/backend.env /etc/nuevamente/deployment.env /etc/caddy/caddy.env
```

Completar `backend.env` sin añadir claves desconocidas ni duplicadas:

- `APP_ENV=production`, `MOCK_OCI=0`, `MOCK_GEMINI=0`, `DATA_DIR=/app/.data`.
- `GOOGLE_API_KEY` real; modelos accesibles, dimensiones 768 y parámetros vigentes.
- Descomentar las doce cuotas positivas: `EMBEDDING_QUOTA_{RPM,TPM,RPD}`,
  `VISION_QUOTA_{RPM,TPM,RPD}`, `GENERATION_QUOTA_{RPM,TPM,RPD}` y
  `VERIFICATION_QUOTA_{RPM,TPM,RPD}`. Son nombres expandidos, no sintaxis literal
  para el env. Si comparten modelo, las triples deben coincidir.
- `OCI_BUCKET_NAME=nuevamente-contenidos-educativos`, compartimento/home region
  reales, `OCI_AUTH_TYPE=api_key`, `OCI_CONFIG_PROFILE=DOCKER`,
  `OCI_CONFIG_FILE=/run/oci/config`. Sólo después del control de la sección 1,
  cambiar `OCI_ALWAYS_FREE_CONFIRMED=1`; reducir presupuestos si corresponde.
- No reiniciar contadores ni copiar un ledger vacío sobre datos previos.

En `deployment.env`, conservar rutas canónicas y poner el mismo
`NUEVAMENTE_DOMAIN` que en `caddy.env`; inicialmente `FORWARDED_ALLOW_IPS=` vacío.
En `caddy.env`, conservar `/var/lib/caddy` y `/var/log/caddy`.
Los env son asignaciones literales `CLAVE=valor`, sin `export`, comillas,
expansión `$`, backticks, escapes, espacios exteriores ni `#` dentro de valores.
No copiar `TRUSTED_ORIGIN_SECRET` a ningún otro archivo.

Transferir por SSH los **dos archivos técnicos** a un directorio temporal privado
del usuario `ubuntu`, fuera del checkout. No se prescribe trasladar `.oci` completo.
El archivo `config` transferido debe contener exclusivamente esta estructura,
completada por el operador con identidad técnica, no administrativa:

```ini
[DOCKER]
user=OCID_DEL_USUARIO_TECNICO
tenancy=OCID_DE_LA_TENANCY
fingerprint=HUELLA_DE_LA_CLAVE_TECNICA
key_file=/run/oci/private_key.pem
region=HOME_REGION_REAL
```

Ejemplo de staging nuevo en la VM y transferencia desde la PC:

```bash
test ! -e /home/ubuntu/nuevamente-credenciales
test ! -L /home/ubuntu/nuevamente-credenciales
install -d -m 0700 /home/ubuntu/nuevamente-credenciales
```

```powershell
$directorioTecnico = 'C:/ruta/fuera-del-repo/credenciales-tecnicas'
scp -i $claveSsh "$directorioTecnico/config" "$directorioTecnico/private_key.pem" "ubuntu@${ipVm}:/home/ubuntu/nuevamente-credenciales/"
```

En la VM, sólo para destinos nuevos:

```bash
sudo test ! -e /etc/nuevamente/oci/config
sudo test ! -e /etc/nuevamente/oci/private_key.pem
sudo install -o 10001 -g 10001 -m 0600 /home/ubuntu/nuevamente-credenciales/config /etc/nuevamente/oci/config
sudo install -o 10001 -g 10001 -m 0600 /home/ubuntu/nuevamente-credenciales/private_key.pem /etc/nuevamente/oci/private_key.pem
```

Custodiar y retirar el staging por el procedimiento privado del equipo; no dejar
copias en carpetas compartidas. El helper valida configuración, clave/fingerprint
en memoria y permisos. El proveedor genérico admite instance principal, pero esta
receta y su helper productivo exigen API key técnica/perfil `DOCKER`.

**Esperado:** archivos completos y permisos de la tabla; ningún secreto en React,
imágenes, repo o salida pública. **Consumo posible:** archivos locales/transferencia;
sin solicitudes OCI/Gemini al prepararlos. **Parar si:** credenciales de operador,
destinos previos, placeholders, cuotas inconsistentes o necesidad de rotar una clave.

## 7. Volumen inicial o datos existentes

**Máquina/shell:** VM/Bash con sudo.
**Previo:** imágenes revisadas, archivos canónicos, ventana exclusiva sin otro
operador ni proceso que cree/borre volúmenes o arranque escritores.
Inspeccionar primero todos los volúmenes y contenedores, incluidos detenidos:

```bash
sudo docker volume ls --format '{{.Name}}'
sudo docker ps -a --format '{{.ID}} {{.Names}} {{.Label "com.docker.compose.project"}}'
sudo test ! -e /var/lib/nuevamente-deploy/pending.json
```

Si hay datos previos, comprobar su ubicación real, pertenencia y custodia antes
de continuar. Un volumen con otro nombre, contenedores de otro proyecto o estado
de despliegue ajeno exige adopción/migración revisada fuera de esta instalación.
No crear uno vacío para reemplazarlo. Si `current.json` existe, no repetir la
preparación inicial ni `--observe-peer`; usar únicamente la precarga/activación
de una instalación administrada ya identificada, sin modificar su secreto/config.

### 7.1 Sólo instalación nueva sin datos previos

El bloque rechaza volúmenes `*_backend_data` y contenedores con `/app/.data`.
Después de la comprobación humana de que tampoco hay datos fuera de ese inventario:

```bash
sudo python3 - <<'PY'
import fcntl
import json
import os
import stat
import subprocess
from pathlib import Path
def ejecutar(*args):
    return subprocess.check_output(args, text=True).strip()
with open('/run/lock/nuevamente-caddy.lock', 'a', opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW, 0o600)) as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    s = os.fstat(lock.fileno())
    assert (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) == (0, 0, 0o600)
    nombres = ejecutar('docker', 'volume', 'ls', '--format', '{{.Name}}').splitlines()
    if any(n.endswith('_backend_data') for n in nombres):
        raise SystemExit('Volumen previo: no crear ni cambiar sus permisos')
    ids = ejecutar('docker', 'ps', '-aq').splitlines()
    contenedores = json.loads(ejecutar('docker', 'inspect', *ids)) if ids else []
    if any(m.get('Destination') == '/app/.data' for c in contenedores for m in c.get('Mounts', [])):
        raise SystemExit('Datos/contenedores previos: revisar adopcion')
    ejecutar('docker', 'volume', 'create', '--driver', 'local', 'nuevamente_backend_data')
    volumen = json.loads(ejecutar('docker', 'volume', 'inspect', 'nuevamente_backend_data'))[0]
    assert volumen['Driver'] == 'local' and not volumen.get('Options')
    ruta = Path(volumen['Mountpoint'])
    assert ruta.resolve() == ruta and stat.S_ISDIR(ruta.lstat().st_mode)
    assert not any(ruta.iterdir()), 'El volumen nuevo no esta vacio'
    os.chown(ruta, 10001, 10001)
    os.chmod(ruta, 0o700)
    print('Volumen nuevo vacio preparado; aprobar identidad en el paso siguiente')
PY
```

Sólo cambia dueño/modo de la **raíz vacía recién creada**, nunca contenidos ni
datos existentes. Si el proceso se interrumpe, conserva el volumen y requiere
inspección; no ejecutar de nuevo fingiendo que sigue siendo una instalación vacía.

### 7.2 Aprobar identidad sin modificar los datos

Para volumen nuevo o existente ya revisado. Exige driver local sin opciones,
raíz 10001:10001/0700 y ausencia de otros volúmenes legacy. No corrige discrepancias:

```bash
sudo python3 - <<'PY'
import json
import os
import stat
import subprocess
from pathlib import Path
volumen = json.loads(subprocess.check_output(['docker', 'volume', 'inspect', 'nuevamente_backend_data'], text=True))[0]
assert volumen['Driver'] == 'local' and not volumen.get('Options')
ruta = Path(volumen['Mountpoint'])
assert ruta.resolve() == ruta
s = ruta.lstat()
assert stat.S_ISDIR(s.st_mode) and (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) == (10001, 10001, 0o700)
identidad = {k: volumen[k] for k in ('Name', 'Driver', 'Mountpoint', 'CreatedAt', 'Scope')}
destino = Path('/etc/nuevamente/volume.json')
if os.path.lexists(destino):
    assert not destino.is_symlink() and json.loads(destino.read_text()) == identidad
else:
    fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(identidad, f)
        f.write('\n')
print('Identidad conservada; el helper comprobara permisos y escritores')
PY
```

`volume.json` debe ser root:root 0600; no publicarlo. Los preflights posteriores
rechazan symlinks/ACL, sustitución, writers múltiples o proyectos ajenos.
**Esperado:** identidad exacta aprobada sin alterar datos existentes.
**Consumo posible:** volumen/disco local sólo en 7.1; ninguna llamada de proveedor.
**Parar si:** hay datos no identificados, permisos distintos, volumen remoto,
identidad reemplazada o despliegue pendiente. No usar `down --volumes`, prune,
copy-up de una imagen ni cambios recursivos de permisos.

## 8. Evidencia de red y DNS antes de observar el peer

**Máquina/shell:** operador/consola OCI y DNS, después VM/Bash.
**Previo:** revisión reciente de **todas** las asociaciones VNIC/subred/security
lists/NSG; identidad/IP de esta VM y dominio aprobado.
El repo no exporta automáticamente la red para Caddy: el administrador debe
obtener/revisar los objetos nativos de todas las listas/reglas asociadas mediante
su herramienta OCI autorizada. No inventar un resumen que omita reglas.

Preparar `/etc/nuevamente/issue47-network.json`, root:root 0600, con este esquema:

```json
{
  "schema": 1,
  "all_attached_reviewed": true,
  "reviewed_at": 0,
  "host_machine_id": "REEMPLAZAR",
  "instance_id": "ocid1.instance.REEMPLAZAR",
  "admin_cidr": "REEMPLAZAR/32",
  "public_ipv4": "REEMPLAZAR",
  "security_lists": [],
  "nsg_rules": []
}
```

Este ejemplo **no pasa**. `security_lists` contiene los objetos nativos completos
con `ingress-security-rules`; `nsg_rules`, todas las reglas nativas de NSG con sus
campos `direction`, `source-type`, `is-stateless`, `tcp-options`/`icmp-options`.
Puede quedar `nsg_rules=[]` sólo si se comprobó que no hay NSG asociados.
La receta #47 usa exclusivamente su lista restringida en la subred, sin NSG;
si aparecen otras asociaciones también hay que examinarlas. Las reglas admitidas
son las de la sección 3. `all_attached_reviewed` es una declaración del operador,
no una prueba de completitud realizada por el helper.

```bash
date -u +%s
cat /etc/machine-id
sudoedit /etc/nuevamente/issue47-network.json
sudo chmod 0600 /etc/nuevamente/issue47-network.json
sudo stat -c '%U:%G %a %n' /etc/nuevamente/issue47-network.json
```

`reviewed_at` es el instante real de revisión, epoch UTC, máximo una hora; no
cambiar sólo el timestamp para eludir caducidad. Repetir revisión/exportación
si vence antes de otro helper. `host_machine_id` debe coincidir con la VM.

En el proveedor DNS, crear/verificar A al IPv4 de la VM, sin proxy/CDN. No publicar
AAAA: esta receta no admite IPv6 global. Revisar también CAA y que el emisor
Let's Encrypt pueda emitir, y que TCP/80/443 lleguen a Caddy. Desde la PC:

```powershell
$dominioDespliegue = 'dominio.aprobado.example'
Resolve-DnsName -Name $dominioDespliegue -Type A
Resolve-DnsName -Name $dominioDespliegue -Type AAAA
```

El dominio del ejemplo debe sustituirse: no usar `.example` para emitir. La
ausencia de AAAA debe confirmarse, no confundirse con un fallo del resolver.
**Esperado:** dominio/archivos coherentes, evidencia nativa reciente y revisada.
**Consumo posible:** lecturas OCI/DNS y cambio DNS autorizado; sin prueba pública
automática en esta etapa documental. **Parar si:** asociaciones sin revisar,
SSH amplio, IPv6 publicado/global, permisos/fecha incorrectos, DNS/CAA incompatibles.

## 9. Observar el peer inicial y validar

**Máquina/shell:** VM/Bash en el checkout.
**Previo:** secciones 1–8 completas; imágenes, volumen y archivos privados
aprobados. Para primera instalación: sin `current.json`, Caddyfile activo ni
contenedores previos del proyecto; `FORWARDED_ALLOW_IPS` vacío. Si es un despliegue
ya administrado, conservar su peer/configuración y omitir la observación inicial.

```bash
cd /opt/nuevamente/checkout
sudo bash infra/caddy/deploy.sh --checkout /opt/nuevamente/checkout --observe-peer
```

Leer privadamente `/var/lib/nuevamente-deploy/observed-peer.json` mediante un editor
root y completar **sólo su IP `peer` literal** en `FORWARDED_ALLOW_IPS`; no copiar
el fingerprint. No mostrar ese archivo ni otros modelos privados:

```bash
sudoedit /var/lib/nuevamente-deploy/observed-peer.json
```

Usar este último comando sólo para consultar y salir sin guardar. La observación
usa backend en cuarentena, no importa la aplicación ni contacta proveedores;
conserva red/volumen y retira el contenedor de prueba. No suponer gateway, usar
`*`, CIDR ni IP de otro host. Después:

```bash
sudoedit /etc/nuevamente/deployment.env
sudo bash infra/caddy/deploy.sh --checkout /opt/nuevamente/checkout --validate
```

**Esperado:** `initial-peer-observed...` y luego
`configuration-validated; activation-required; peer-observation-will-precede-uvicorn`.
La validación usa Compose real y contenedores efímeros sin red para config/crypto;
no basta `compose config -q`. Aún no publica la aplicación.
**Consumo posible:** archivos de despliegue, red Docker y contenedores locales;
sin OCI/Gemini en estas validaciones. **Parar si:** cualquier rechazo, volumen,
modelo, origen o peer inconsistente; no editar el JSON de observación ni omitir
la compuerta con `compose up`.

## 10. Precarga productiva con un solo escritor

**Máquina/shell:** VM/Bash con sudo.
**Previo:** validación anterior aprobada, presupuesto/quota Gemini/OCI disponibles,
archivos productivos completos y volumen aprobado. En una instalación inicial,
precargar antes de la primera activación pública. No generar ejemplos de #55
de nuevo: la precarga sólo prepara fuentes y su índice/interpretaciones.

Si la aplicación administrada ya estaba activa, cerrar primero admisiones públicas
con `sudo systemctl stop caddy`, coordinar el drenaje de actividad y detener los
contenedores **existentes** de frontend/backend, sin eliminarlos. Obtener sus IDs:

```bash
sudo docker ps -aq --filter label=com.docker.compose.project=nuevamente --filter label=com.docker.compose.service=frontend
sudo docker ps -aq --filter label=com.docker.compose.project=nuevamente --filter label=com.docker.compose.service=backend
```

Debe haber exactamente uno por servicio. Verificar su pertenencia al despliegue
aprobado y sustituir los marcadores. El control SQLite sólo lee el número de
trabajos queued/running; si no es cero, no detener aún el backend ni precargar:

```bash
sudo docker exec --user 10001:10001 ID_BACKEND_VERIFICADO python -c "import sqlite3; db=sqlite3.connect('file:/app/.data/operativo.db?mode=ro', uri=True); n=db.execute(\"SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')\").fetchone()[0]; db.close(); assert n == 0, 'Quedan trabajos activos'; print('Sin trabajos queued/running')"
sudo docker stop --time 70 ID_FRONTEND_VERIFICADO ID_BACKEND_VERIFICADO
```

La confirmación de drenaje es administrativa; no se inventa un endpoint de pausa.
No retirar contenedores, redes, journal ni `current.json`. Si no puede garantizarse
un único escritor detenido, no ejecutar precarga. La CLI toma además `runtime.lock`;
no borrar ese archivo para eludir exclusividad.

El bloque siguiente usa el volumen por **bind de su Mountpoint aprobado**, sin
creación implícita/copy-up, credenciales RO y los dos env canónicos. Usa la red
`nuevamente_aplicacion` creada por la observación/deploy, con salida a proveedores;
no publica puertos ni arranca Uvicorn. No cambiar imágenes/env durante la operación.
En mantenimiento posterior a una actualización, sustituir el origen del bind
`/opt/nuevamente/checkout/documents` por `documents` de la fuente desplegada
registrada; comprobar antes su revisión/hash. No precargar desde el checkout viejo
con una imagen nueva sin verificar esa correspondencia.

```bash
sudo bash <<'BASH'
set -Eeuo pipefail
umask 077
exec 9>/run/lock/nuevamente-caddy.lock
flock -n 9
test ! -e /var/lib/nuevamente-deploy/pending.json
test -z "$(docker ps -q --filter label=com.docker.compose.project=nuevamente)"
python3 - <<'PY'
import json
import subprocess
from pathlib import Path
v = json.loads(subprocess.check_output(['docker', 'volume', 'inspect', 'nuevamente_backend_data'], text=True))[0]
assert {k: v[k] for k in ('Name', 'Driver', 'Mountpoint', 'CreatedAt', 'Scope')} == json.loads(Path('/etc/nuevamente/volume.json').read_text())
assert v['Driver'] == 'local' and not v.get('Options')
ids = subprocess.check_output(['docker', 'ps', '-q'], text=True).splitlines()
running = json.loads(subprocess.check_output(['docker', 'inspect', *ids], text=True)) if ids else []
assert not any(m.get('Name') == v['Name'] or m.get('Source') == v['Mountpoint'] or m.get('Destination') == '/app/.data' for c in running for m in c.get('Mounts', [])), 'Escritor activo'
PY
puntoDatos=$(docker volume inspect --format '{{.Mountpoint}}' nuevamente_backend_data)
imagenBackend=$(docker image inspect --format '{{.Id}}' nuevamente-backend:latest)
docker run --rm --pull=never --name nuevamente-demo-preload \
  --user 10001:10001 --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m,mode=1777 --log-driver none \
  --network nuevamente_aplicacion \
  --env-file /etc/nuevamente/backend.env \
  --env-file /etc/nuevamente/secrets/origin.env \
  --mount "type=bind,src=$puntoDatos,dst=/app/.data" \
  --mount type=bind,src=/etc/nuevamente/oci,dst=/run/oci,readonly \
  --mount type=bind,src=/opt/nuevamente/checkout/documents,dst=/app/documents,readonly \
  --entrypoint python "$imagenBackend" \
  -m app.documents.preload_cli --documents-dir /app/documents
BASH
```

La ejecución debe terminar antes de reactivar. El gestor común ejecuta las fuentes
en serie y conserva su registro en `/app/.data/demo-mantenimiento.db`; comparte
ledger OCI con la aplicación, pero no fabrica admisiones privadas. Reutiliza
originales/índices/interpretaciones compatibles; puede consumir embeddings y visión.

**Esperado:** código 0 y tres registros `fuente_demo` `ready`, ambos mocks false.
Código 2 indica visión pendiente; 1, fallo técnico. Conservar salida saneada y
consumo por modelo/operación. **Consumo posible:** solicitudes reales OCI y Gemini,
incluidos controles/reintentos admitidos; los contadores previos se conservan.
**Parar si:** salida distinta de 0, `processing`, cuota agotada, original cambiado,
lápida/conflicto, lock ocupado o escritor vivo. No ejecutar en bucle ni degradar
a mock. No presentar un catálogo fallido como vacío aprobado.

## 11. Activar o reactivar administrativamente

**Máquina/shell:** VM/Bash.
**Previo:** precarga finalizada, sin proceso/contenedor de precarga activo, identidad
del volumen sin cambios, evidencia de red aún dentro de una hora y DNS preparado.
Revisar de nuevo la red si caducó. Conservar imágenes, archivos y peer aprobados.

```bash
cd /opt/nuevamente/checkout
sudo bash infra/caddy/deploy.sh --checkout /opt/nuevamente/checkout --activate
sudo bash infra/caddy/deploy.sh --checkout /opt/nuevamente/checkout --validate
```

Después de detener/reiniciar un backend existente, su observación anterior puede
estar invalidada: usar `--activate` antes de exigir la validación final. El helper
recrea backend en cuarentena, observa de nuevo como Caddy y sólo entonces libera
Uvicorn, inicia UI, comprueba salud y publica Caddy. Primera instalación y
reactivación conservan volumen y credenciales. No ejecutar `compose up` paralelo.

**Esperado:** `deployment-activated-and-locally-verified; public-acceptance-pending`
y validación final aprobada. `current.json` queda privado; no mostrarlo ni adjuntarlo.
El helper verifica estado efectivo; salida 0 de `systemctl` sola no es suficiente.
**Consumo posible:** arranque backend consulta OCI, puede reconciliar obligaciones
durables o reconstrucciones pendientes y consumir cuotas; Caddy solicita ACME.
No asumir que activar sólo consume un healthcheck.
**Parar si:** journal pendiente, fingerprint no verificable, peer distinto, salud,
secreto o volumen incompatibles. No borrar estado ni ampliar confianza; la
recuperación/diagnóstico detallados están en las secciones 17–19. No se promete reapertura
automática después de restart/reboot ni continuidad de trabajos interrumpidos.

## 12. Salud de API/UI y comprobación de la biblioteca

### 12.1 Salud local y HTTPS

**Máquina/shell:** VM/Bash para salud local; navegador/PowerShell desde red externa
para web pública. **Previo:** activación aprobada, dominio real configurado.

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/api/health
curl --fail --silent --show-error http://127.0.0.1:8501/healthz
sudo docker ps --filter label=com.docker.compose.project=nuevamente --format '{{.Names}} {{.Status}}'
sudo systemctl is-active caddy
sudo ss -H -lnt
```

API debe dar HTTP 200 y `status=ok`; UI `/healthz`, 200/`ok`; ambos contenedores
`healthy`, listeners internos sólo loopback. Health no llama por sí mismo a Gemini
ni acredita persistencia real. Desde la PC, usando `$dominioDespliegue` real:

```powershell
curl.exe --silent --show-error --head "http://$dominioDespliegue/"
curl.exe --fail --silent --show-error --head "https://$dominioDespliegue/"
Invoke-RestMethod "https://$dominioDespliegue/api/health"
```

**Esperado:** HTTP 308 al mismo dominio HTTPS, HTTPS válido/200 y API `ok`, sin
`-k`, CA de staging ni excepción del navegador. Abrir React y comprobar una
interacción visible; inspeccionar certificado, origen y ausencia de secretos.
**Consumo posible:** tráfico web, sin generación LLM por estas sondas.
**Parar si:** error TLS, redirect incorrecto, app expuesta directamente, UI/assets
inaccesibles o salud degradada. La reproducción pública con fecha, URL, versión
y revisores es evidencia de #133, todavía no ejecutada por esta documentación.

### 12.2 Tres fuentes demo y ejemplos guardados

**Máquina/shell:** navegador externo para sesión; PC/PowerShell para lectura API.
**Previo:** sesión válida propia creada o recuperada en React, token guardado sólo
privadamente; crear/recuperar sesión puede consumir OCI y no genera contenido.
Para sondas API ingresar el Bearer mediante prompt seguro, no escribirlo en
historial ni mostrar respuestas completas. Ejecutar en una consola privada:

```powershell
$tokenDemo = Read-Host 'Bearer de la sesion propia' -AsSecureString
$cabecerasDemo = @{ Authorization = ('Bearer ' + [System.Net.NetworkCredential]::new('', $tokenDemo).Password) }
$baseDemo = "https://$dominioDespliegue"
$catalogoFuentes = Invoke-RestMethod -Headers $cabecerasDemo "$baseDemo/api/demo/documents?limit=100"
$idsEsperados = @('demo-redes-vcn', 'demo-pagos-fintech', 'demo-salud-gobernanza')
foreach ($idDemo in $idsEsperados) {
    if (@($catalogoFuentes.items | Where-Object document_id -EQ $idDemo).Count -ne 1) { throw 'Falta una fuente demo' }
    $detalleDemo = Invoke-RestMethod -Headers $cabecerasDemo "$baseDemo/api/demo/documents/$idDemo"
    if ($detalleDemo.status -ne 'ready') { throw 'Fuente demo no ready' }
}
'Tres fuentes encontradas y ready; revisar cobertura y fuentes en React'
$ejemplosGuardados = Invoke-RestMethod -Headers $cabecerasDemo "$baseDemo/api/demo/generations"
'Entradas de ejemplos guardados: ' + @($ejemplosGuardados.items).Count
$cabecerasDemo.Clear()
Remove-Variable tokenDemo, cabecerasDemo, detalleDemo, catalogoFuentes, ejemplosGuardados
```

En React, seleccionar cada fuente y verificar estado/cobertura, una cita y la
interpretación VCN diferenciada del texto. La ruta autenticada
`GET /api/demo/documents/{document_id}/sources/{chunk_id}` existe; usar IDs
efectivos, sin inventarlos. No imprimir Bearer, códigos de recuperación ni HAR.

El segundo catálogo corresponde a **resultados guardados de #55**, independiente
de precargar fuentes. Debe contrastarse con los ocho materiales aprobados y
complementos de la [evidencia v2](evidencia/issue-55/v2/README.md), sus hashes,
etiqueta de ejemplo previo, fecha y parámetros. `items=[]` puede ser un catálogo
no publicado, no equivale a la biblioteca de entrega verificada. No ejecutar
`app.demo.prepare` para regenerarlos como efecto de instalar; su eventual
publicación exige autorización y el procedimiento de #55.

**Esperado:** tres fuentes vigentes `ready`, cobertura/interpretación comprobadas
y biblioteca de ejemplos recuperable según evidencia, registradas por separado.
**Consumo posible:** lecturas OCI, incluida validación de fuentes/paquetes; no
embeddings ni generación LLM desde estas rutas. **Parar si:** fuente ausente,
fallo de integridad, 401/503, versión/hash incompatible o ejemplo incompleto.

**Pendiente funcional demo→generación:** la UI y la autorización backend revisadas
no habilitan generar desde una fuente demo compartida; el
[registro de la contradicción](verificacion-issue-58.md#contradicción-demo-generación)
conserva el requisito y la decisión/seguimiento del equipo. No usar una carga
privada ni estos ejemplos guardados como sustituto. Las comprobaciones de salud,
lectura de fuentes y recuperación de paquetes no prueban generación nueva,
SSE real por Caddy ni completan #133.

### 12.3 Modos, recuperación de materiales y descargas

**Máquina/shell:** navegador React, sesión privada propia; sin comandos de generación.
**Previo:** material ya `completed` y guardado, fuente vigente y autorización de
lectura/descarga. Si falta material de un modo, registrar esa comprobación pendiente;
no generar como efecto de verificar salud o instalar la demo.

El selector ofrece multiagente (predeterminado) y básico, sobre el mismo documento
preparado. Una generación nueva que omite `modo_generacion` conserva el paquete
1.0 multiagente; explicitar el modo produce 1.1. Los lectores actuales admiten 1.0
y las variantes 1.1 multiagente/básico. `completed` exige persistencia confirmada:
en básico no acredita evaluación independiente. Su paquete es `no_evaluado`, con
evaluación `no_realizada`, sin score ni modelo juez; no mostrarlo como aprobado.
Ver [contrato y límites de modos](modos-generacion.md).

Abrir el resultado y luego el mismo material desde historial; comprobar el modo
guardado y sus avisos, incluso si el selector actual indica otro modo. Al recuperar
el espacio con su código, comprobar esa misma identidad/modo. Recuperar desde OCI
también conserva el modo de los paquetes confirmados; 1.0 se interpreta como
multiagente. No restaura sesiones/tokens ni trabajos pendientes. Esto no resuelve
los huecos de 17.1/17.4 ni autoriza reabrir espacios retirados.

| Formato pedagógico guardado | Descargas desde resultado e historial |
| --- | --- |
| Tutorial, quiz, resumen, guion | JSON, Markdown y PDF |
| Flashcards | JSON, Markdown, PDF, CSV, TSV y APKG |

Los botones incompatibles permanecen deshabilitados. Las descargas requieren
sesión/propiedad/estado/fuente válidos y no vuelven a llamar al LLM. JSON conserva
el canónico completo (incluidas soluciones del quiz); la vista estudiante no
las muestra anticipadamente. Las exportaciones 1.1 identifican el modo y no
inventan una revisión para básico; los derivados 1.0 conservan su compatibilidad
histórica. «Guardado en OCI» exige `provider=oci` y `status_upload=completado`;
un mock confirmado sólo acredita almacenamiento local. No confundir estas
descargas privadas con abrir ejemplos compartidos guardados de #55.

**Esperado:** mismo material/modo en resultado, historial y recuperación, descargas
compatibles legibles, sin exponer secretos. **Consumo posible:** tráfico/lecturas
OCI y almacenamiento de derivados, CPU/disco de exportación; no generación LLM.
**Parar si:** 401/404/409/422/503, modo distinto, aprobación ficticia, lector
incompatible, fuente retirada o credenciales en archivos. La lectura de código
de esta sección no acredita que se haya ejecutado el recorrido; su reproducción
real y evidencia siguen pendientes en #133.

## 13. Actualización con interrupción breve y versiones conservadas

**Máquina/shell:** operador/PowerShell para preparar la fuente, VM/Bash para
operar. **Previo:** ventana acordada, respaldo consistente de la sección 16 y
compatibilidad de esquema y lectores de paquetes revisada antes de admitir
escrituras de la versión nueva. No hay actualizaciones rolling ni promesa de cero downtime.

Registrar commit desplegado, SHA del archivo de fuentes, IDs de ambas imágenes,
versión del esquema SQLite, versiones/modos de paquetes locales y confirmados en
OCI, lectores admitidos, modelos/dimensiones/preparación del índice y fecha.
Los modelos privados de despliegue contienen secretos: extraer sólo los IDs,
nunca copiarlos al informe público:

```bash
sudo python3 - <<'PY'
import json
from pathlib import Path
p = json.loads(Path('/var/lib/nuevamente-deploy/current.json').read_text())
for servicio in ('backend', 'frontend'):
    print(servicio, p['model']['services'][servicio]['image'])
PY
```

Guardar esos IDs en el registro privado y darles tags de retención antes de
construir otra imagen. Sustituir los marcadores por los IDs reales revisados:

```bash
sudo docker image tag sha256:ID_BACKEND_ANTERIOR nuevamente-backend:conservar-REVISION_ANTERIOR
sudo docker image tag sha256:ID_FRONTEND_ANTERIOR nuevamente-frontend:conservar-REVISION_ANTERIOR
```

Un tag no reemplaza el ID inmutable. Conservar ambas imágenes y la fuente anterior;
no ejecutar prune. No hay publicación/pull de imágenes de aplicación en un registry
del proyecto: la fuente nueva se prepara con `git archive` como en la sección 3,
usando otro commit aprobado, nombre de tar y directorio nuevos. Extraerla como
`/opt/nuevamente/releases/REVISION_NUEVA`, sin escribir sobre el checkout anterior.
Revisar primero el espacio necesario para archivo, dos pares de imágenes y backup.

Comprobar que la actualización ordinaria no modifica el instalador/helper de
systemd de #48. Cambiarlo exige una revisión específica, no una reinstalación
automática encima del existente:

```bash
sudo cmp /opt/nuevamente/releases/REVISION_NUEVA/infra/caddy/verify_deployment.py /usr/local/lib/nuevamente/verify_deployment.py
sudo cmp /opt/nuevamente/releases/REVISION_NUEVA/infra/caddy/caddy.service.d/nuevamente.conf /etc/systemd/system/caddy.service.d/nuevamente.conf
```

Construir las imágenes nuevas en ese directorio con los comandos ARM64 de la
sección 4, conservando las anteriores. La construcción se puede preparar antes de
la pausa, si el margen de CPU/RAM/disco lo permite; no arrancar un segundo backend.
Registrar los nuevos IDs. Mantener archivos canónicos, secreto y volumen aprobados.
Revisar migraciones en `backend/app/jobs/store.py`: abrir `RegistroOperativo` aplica
migraciones y marca trabajos interrumpidos, por lo que **no se lo instancia para
inspeccionar una copia**. La versión del código puede leerse sin importar la app:

```bash
sudo docker run --rm --pull=never --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --log-driver none --entrypoint python nuevamente-backend:latest -c "import ast,pathlib; t=ast.parse(pathlib.Path('/app/app/jobs/store.py').read_text()); print(next(ast.literal_eval(n.value) for n in t.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='VERSION_ESQUEMA' for x in n.targets)))"
```

Ese número sólo identifica el esquema del código, no demuestra compatibilidad
hacia atrás. Compararlo con el esquema del backup y revisar SQL/contratos reales.
Revisar además `cargar_paquete_json` y sus variantes en
[responses.py](../backend/app/schemas/responses.py): esta base lee 1.0 y 1.1,
mientras que `f292415` sólo leía 1.0. El esquema SQLite sigue siendo 11; esa
igualdad no permite volver al lector antiguo después de guardar paquetes 1.1.
La compatibilidad debe abarcar estado local, objetos OCI y exportaciones de
resultado/historial, incluidos ambos modos. Restaurar un backup local anterior
no elimina paquetes nuevos ni efectos externos en OCI.
Si una migración impide volver a la imagen anterior, acordar antes de actualizar
el procedimiento de recuperación y la pérdida aceptable; no confiar en rollback
automático como protección de datos.

Cerrar admisiones y drenar/detener la app como en la sección 10; conservar los
contenedores detenidos, red y `current.json`. Ejecutar desde la fuente nueva:

```bash
sudo bash /opt/nuevamente/releases/REVISION_NUEVA/infra/caddy/deploy.sh --checkout /opt/nuevamente/releases/REVISION_NUEVA --activate
sudo bash /opt/nuevamente/releases/REVISION_NUEVA/infra/caddy/deploy.sh --checkout /opt/nuevamente/releases/REVISION_NUEVA --validate
```

Repetir salud y comprobaciones funcionales de la sección 12 sin generar material
nuevo por defecto. Registrar la revisión y los IDs que quedaron activos.
**Esperado:** interrupción acotada a parada/recreación/salud, mismo volumen y
origen verificado. **Consumo:** builds y disco local; al activar, OCI/ACME y posibles
reconciliaciones/reconstrucciones. **Parar si:** esquema o lectores/paquetes incompatibles, margen
insuficiente, helper distinto, journal pendiente, peer/volumen/configuración no
verificables o salud fallida.

**Volver a imágenes/configuración:** con esquema y paquetes locales/OCI compatibles
con los lectores anteriores, y sin journal pendiente,
reetiquetar los IDs anteriores como `nuevamente-backend:latest` y
`nuevamente-frontend:latest`, y ejecutar `--activate`/`--validate` desde la fuente
anterior conservada. No restaurar `current.json` a mano. Si hay journal, usar el
caso 17.2. **Volver a datos** es una restauración de backup distinta: no la hace
el helper y puede perder escrituras y revocaciones posteriores.

## 14. Reinicio de servicios o de la VM

**Máquina/shell:** VM/Bash y reconexión SSH desde el operador. **Previo:** ventana
de interrupción, datos/configuración custodiados, IDs y fuente desplegada anotados,
sin journal pendiente; cerrar admisiones y drenar como en la sección 10.

```bash
sudo systemctl stop caddy
sudo docker stop --time 70 ID_FRONTEND_VERIFICADO ID_BACKEND_VERIFICADO
```

Para reiniciar la VM, sólo después de la parada confirmada:

```bash
sudo systemctl reboot
```

Para volver a los servicios sin reboot no hace falta `docker start`: el helper
recrea los contenedores conservados. Tras reconectar a la misma VM y comprobar su
identidad, esperar cloud-init/servicios y actualizar la revisión real de red
`issue47-network.json` si caducó; no actualizar sólo su timestamp. Después:

```bash
sudo systemctl is-active docker nuevamente-firewall unattended-upgrades
sudo bash /RUTA_FUENTE_DESPLEGADA/infra/caddy/deploy.sh --checkout /RUTA_FUENTE_DESPLEGADA --activate
sudo bash /RUTA_FUENTE_DESPLEGADA/infra/caddy/deploy.sh --checkout /RUTA_FUENTE_DESPLEGADA --validate
```

Sustituir `/RUTA_FUENTE_DESPLEGADA` por el checkout/release registrado, no por la
rama más reciente. El tmpfs/marcador de la compuerta no es durable: restart/reboot,
cambio de red o namespace invalidan la observación. La activación comprueba otra
vez el peer, sin ampliar a CIDR o `*`. No borrar/recrear el volumen ni regenerar
credenciales. Si se perdió la IP pública, revisar DNS y la evidencia de red.

**Esperado:** reactivación administrativa y salud posterior, no continuidad de
trabajos; queued/running interrumpidos se identifican como `INTERRUPTED`, no
`completed`. **Consumo:** reconexión/arranque y lecturas OCI, posible trabajo de
reconciliación; no autoriza generación nueva. **Parar si:** volumen ausente,
credenciales cambiadas, journal, reglas/peer o identidad inesperados.

## 15. Monitoreo mínimo sin secretos

**Máquina/shell:** VM/Bash, navegador externo y consolas del operador.
**Previo:** acceso administrativo y versión de la fuente identificada. No enviar
estas salidas administrativas a una API pública ni capturar HAR/entornos.

```bash
df -h / /var/lib/docker /var/backups
df -i / /var/lib/docker
free -h
sudo systemctl is-active docker nuevamente-firewall unattended-upgrades caddy
sudo docker ps --filter label=com.docker.compose.project=nuevamente --format '{{.Names}} {{.Status}}'
curl --fail --silent --show-error http://127.0.0.1:8000/api/health
curl --fail --silent --show-error http://127.0.0.1:8501/healthz
```

Revisar espacio para crecimiento del volumen, imágenes y backup+restauración de
prueba; no responder al disco lleno con prune o borrado de WAL/locks. Definir los
umbrales y responsable con el equipo: esta guía no inventa una política acordada.
Desde fuera, comprobar HTTPS estricto, emisor/SAN/vigencia y renovación observada;
`curl` sin `-k` valida confianza/fecha, y el visor del certificado del navegador
permite revisar expiración sin abrir las claves ACME.

Para leer ledger y obligaciones sin inicializar proveedores, entrar en el backend
existente sólo como lector SQLite. Reemplazar su ID verificado; el bloque no
importa `app.main`, `RegistroOperativo` ni el SDK:

```bash
sudo docker exec --user 10001:10001 -i ID_BACKEND_VERIFICADO python - <<'PY'
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
def leer(nombre, consulta, parametros=()):
    p = Path('/app/.data') / nombre
    db = sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return db.execute(consulta, parametros).fetchall()
    finally:
        db.close()
mes = datetime.now(timezone.utc).strftime('%Y-%m')
print(json.dumps({
    'mes': mes,
    'solicitudes_registradas': leer('oci_budget.sqlite3', 'SELECT usadas FROM solicitudes WHERE mes=?', (mes,)),
    'operaciones_registradas': leer('oci_budget.sqlite3', 'SELECT tipo, usadas FROM operaciones WHERE mes=?', (mes,)),
    'reservas_inciertas': leer('oci_budget.sqlite3', 'SELECT COUNT(*) FROM reservas'),
    'limpieza_espacios': leer('operativo.db', 'SELECT estado, COUNT(*) FROM workspace_cleanup GROUP BY estado'),
    'limpieza_documentos': leer('operativo.db', 'SELECT estado, COUNT(*) FROM document_cleanup GROUP BY estado'),
    'lapidas_sin_confirmar': leer('operativo.db', 'SELECT COUNT(*) FROM pending_deletes WHERE manifest_confirmado_en IS NULL'),
}, ensure_ascii=True))
PY
```

Una fila/tabla ausente o un error de lectura no es consumo cero ni limpieza
completada. Comparar con límites efectivos del archivo privado backend.env,
alerta al 80%, margen de limpieza y métricas/consumo agregado OCI. Las cuotas
Gemini del proceso se reinician con él: contrastarlas también con el proveedor,
sin usar el reinicio para evadir consumo previo. El inspector existente
`app.tools.inspect_storage --local` muestra diagnóstico de espacios; sin `--local`
inicializa el proveedor real y consume solicitudes OCI aunque no escriba objetos.
No ejecutarlo como supuesto health barato ni repetirlo automáticamente.

Los errores de limpieza conservan intentos/seguimiento; una reserva ambigua no
libera presupuesto. El sincronizador existente reintenta, sin necesidad de cron
nuevo. Revisar únicamente logs filtrados de Caddy en `/var/log/caddy` y códigos
administrativos; no volcar `journalctl`/Docker crudos, config resuelto, argumentos
o respuestas SDK en incidencias públicas. Los contenedores del helper usan logging
Docker `none`; ausencia de logs Docker no acredita ausencia de fallos.

**Esperado:** servicios sanos, certificado válido y margen/obligaciones conocidos.
**Consumo:** lecturas locales; web externa y consolas pueden consumir solicitudes.
**Parar nuevas admisiones si:** espacio insuficiente, alerta/bloqueo sin revisar,
reservas/limpieza no explicadas, certificado/salud fallidos o consumo desconocido.
No borrar ledger/lápidas ni aumentar límites para salir de ese estado.

## 16. Backup físico consistente del volumen completo

### 16.1 Preparar la parada y distinguir tipos de respaldo

**Máquina/shell:** VM/Bash; posteriormente PC/PowerShell para copia externa.
**Previo:** ventana exclusiva, espacio suficiente para archivo y restauración de
prueba, destino protegido fuera del volumen y autorización de la custodia externa.
Frecuencia, retención, responsable, ubicación y cifrado externo son **decisiones
operativas pendientes**. No existe aún un calendario acordado ni backup automático.

Una copia en el mismo disco no protege de pérdida de VM/disco. Este procedimiento
produce un archivo de **todo** `/app/.data`: `operativo.db`, WAL/SHM presentes,
Chroma y su catálogo, `oci_budget.sqlite3`, mantenimiento, temporales y demás
estado durable. No usar `cp operativo.db` ni un checkpoint aislado como respaldo
del conjunto. No abrir la base de origen para hacer `integrity_check`: se verificará
una restauración separada y se preservarán los bytes originales.

Un **backup de boot/block volume OCI** es otro recurso, no este tar ni una copia
del bucket. Requiere control agregado de capacidad, número/retención y asignación
gratuita efectiva, y parada coherente de escritores igualmente. Restaurarlo crea
o conserva discos que también consumen capacidad; no lo provisiona esta guía.
Una copia del tar en Object Storage consume bytes/solicitudes/transferencia y puede
duplicar los datos; las operaciones externas al backend no aparecen por sí solas
en su ledger. No subir respaldos al bucket de contenidos saltando su presupuesto,
IAM o retención. Contabilizar todas las copias, staging, ensayos y restauraciones.

Detener Caddy, drenar y parar frontend/backend con la sección 10. Confirmar que
no quedan escritores ni contenedores de precarga, y no ejecutar otro helper.
Conservar contenedores, volumen, red y locks. El bloque de copia toma además el
mismo `flock` administrativo de Caddy. No basta que `/api/health` deje de responder.

### 16.2 Crear archivo e inventario de integridad

El siguiente bloque es una instrucción Python estándar, no un nuevo comando del
repositorio. Reemplazar `REVISION_DESPLEGADA` por el commit del registro operativo.
Ejecutarlo en la VM sólo después de la parada. Si encuentra enlaces, archivos
especiales, hardlinks o xattrs/ACL no contemplados, se detiene sin normalizarlos.
Conservar el directorio parcial para revisión; sin manifiesto final no hay backup válido.
Usar el intérprete normal, sin `-O` ni `PYTHONOPTIMIZE`: las guardas usan `assert`.

```bash
sudo python3 - REVISION_DESPLEGADA <<'PY'
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

def crear_copia(datos, destino, version):
    datos, destino = Path(datos), Path(destino)
    assert datos.is_dir() and datos.resolve() == datos
    assert not os.path.lexists(destino) and destino.parent.resolve() == destino.parent
    def huella(p):
        with p.open('rb') as f:
            return hashlib.file_digest(f, 'sha256').hexdigest()
    def inventario():
        resultado = []
        for p in [datos, *sorted(datos.rglob('*'))]:
            s = p.lstat()
            assert stat.S_ISDIR(s.st_mode) or (stat.S_ISREG(s.st_mode) and s.st_nlink == 1)
            assert not stat.S_IMODE(s.st_mode) & 0o7000
            assert not hasattr(os, 'listxattr') or not os.listxattr(p)
            fila = {'path': p.relative_to(datos).as_posix(), 'type': 'dir' if p.is_dir() else 'file',
                    'uid': s.st_uid, 'gid': s.st_gid, 'mode': stat.S_IMODE(s.st_mode), 'mtime_ns': s.st_mtime_ns}
            if p.is_file():
                fila.update(size=s.st_size, sha256=huella(p))
            resultado.append(fila)
        return resultado
    antes = inventario()
    destino.mkdir(mode=0o700)
    archivo = destino / 'volume.tar.gz'
    with archivo.open('xb') as raw:
        with tarfile.open(fileobj=raw, mode='w:gz', format=tarfile.PAX_FORMAT) as tar:
            for fila in antes:
                tar.add(datos / fila['path'], arcname=fila['path'], recursive=False)
        raw.flush()
        os.fsync(raw.fileno())
    os.chmod(archivo, 0o600)
    assert antes == inventario(), 'El origen cambio durante la copia'
    manifiesto = {'schema': 1, 'version': version, 'files': antes,
                  'archive_sha256': huella(archivo)}
    with (destino / 'manifest.json').open('x', encoding='utf-8') as f:
        json.dump(manifiesto, f, ensure_ascii=True, indent=2)
        f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.chmod(destino / 'manifest.json', 0o600)
    if hasattr(os, 'O_DIRECTORY'):
        fd = os.open(destino, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return manifiesto

os.umask(0o077)
assert re.fullmatch('[0-9a-f]{40}', sys.argv[1]), 'Falta commit completo'
with open('/run/lock/nuevamente-caddy.lock', 'a', opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW, 0o600)) as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not Path('/var/lib/nuevamente-deploy/pending.json').exists()
    v = json.loads(subprocess.check_output(['docker', 'volume', 'inspect', 'nuevamente_backend_data'], text=True))[0]
    identidad = {k: v[k] for k in ('Name', 'Driver', 'Mountpoint', 'CreatedAt', 'Scope')}
    assert identidad == json.loads(Path('/etc/nuevamente/volume.json').read_text())
    assert v['Driver'] == 'local' and not v.get('Options')
    ids = subprocess.check_output(['docker', 'ps', '-q'], text=True).splitlines()
    vivos = json.loads(subprocess.check_output(['docker', 'inspect', *ids], text=True)) if ids else []
    assert not any(m.get('Name') == v['Name'] or m.get('Source') == v['Mountpoint'] or m.get('Destination') == '/app/.data' for c in vivos for m in c.get('Mounts', [])), 'Escritor activo'
    padre = Path('/var/backups/nuevamente')
    if padre.exists():
        s = padre.lstat()
        assert not padre.is_symlink() and (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) == (0, 0, 0o700)
    else:
        padre.mkdir(mode=0o700)
    estado = json.loads(Path('/var/lib/nuevamente-deploy/current.json').read_text())
    version = {'commit': sys.argv[1], 'fecha_utc': datetime.now(timezone.utc).isoformat(),
               'images': {s: estado['model']['services'][s]['image'] for s in ('backend', 'frontend')}}
    destino = padre / ('snapshot-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex)
    crear_copia(Path(v['Mountpoint']), destino, version)
    print('Copia fisica creada; verificar restauracion y custodia externa:', destino)
PY
```

El manifiesto incluye cada ruta, tipo, dueño/grupo, modo, tamaño/hash de archivos,
mtime y versiones/IDs. SHA256 detecta cambios accidentales; no autentica un backup
si un atacante pudo cambiar también su manifiesto. La custodia es parte de la
confianza. La comparación antes/después ayuda a detectar actividad, pero no
sustituye la parada de productores. No se fuerza un checkpoint ni se omite WAL/SHM.

**Esperado:** tar e inventario final privados. **Consumo:** lectura/escritura local,
capacidad para copias; cero OCI/Gemini en la copia. **Parar si:** origen cambia,
volumen/identidad no coincide, escritor activo, disco insuficiente, tipos/permisos
no contemplados o directorio de backup inseguro. No borrar ni retocar el origen.

### 16.3 Restaurar en destino aislado y verificar antes de adoptar

**Máquina/shell:** host de verificación con Python 3.11 o posterior; en producción VM/Bash con
sudo. **Previo:** par tar/manifiesto custodiado, ruta de destino nueva o raíz vacía
sin datos ajenos, espacio libre y sin escritor. Nunca usar como prueba el volumen
activo ni restaurar encima de datos existentes. El bloque comprueba rutas y tipos
antes de crear archivos; no usa extracción genérica que acepte enlaces o `..`.

```bash
sudo python3 - /RUTA_COPIA /RUTA_DESTINO_NUEVO <<'PY'
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tarfile
import shutil
from pathlib import Path, PurePosixPath

def restaurar_copia(copia, destino, preservar_propietarios=False):
    copia, destino = Path(copia), Path(destino)
    assert copia.resolve() == copia and destino.resolve() == destino
    assert not destino.exists() or (destino.is_dir() and not any(destino.iterdir()))
    m = json.loads((copia / 'manifest.json').read_text(encoding='utf-8'))
    archivo = copia / 'volume.tar.gz'
    assert all(stat.S_ISREG(p.lstat().st_mode) and p.lstat().st_nlink == 1 for p in (archivo, copia / 'manifest.json'))
    with archivo.open('rb') as f:
        assert m['schema'] == 1 and hashlib.file_digest(f, 'sha256').hexdigest() == m['archive_sha256']
    esperados = {f['path']: f for f in m['files']}
    assert len(esperados) == len(m['files']) and esperados['.']['type'] == 'dir'
    if preservar_propietarios:
        assert hasattr(os, 'geteuid') and os.geteuid() == 0
    with tarfile.open(archivo, 'r:gz') as tar:
        miembros = tar.getmembers()
        assert len({x.name for x in miembros}) == len(miembros)
        assert {x.name for x in miembros} == set(esperados)
        for x in miembros:
            p = PurePosixPath(x.name)
            assert not p.is_absolute() and '..' not in p.parts and '\\' not in x.name and ':' not in x.name
            assert x.name == p.as_posix() and not x.mode & 0o7000
            f = esperados[x.name]
            assert (x.isdir() and f['type'] == 'dir') or (x.isfile() and f['type'] == 'file')
            assert (x.uid, x.gid, x.mode) == (f['uid'], f['gid'], f['mode'])
            if x.isfile():
                with tar.extractfile(x) as cuerpo:
                    assert x.size == f['size'] and hashlib.file_digest(cuerpo, 'sha256').hexdigest() == f['sha256']
        destino.mkdir(mode=0o700, exist_ok=True)
        for f in sorted(m['files'], key=lambda f: len(PurePosixPath(f['path']).parts)):
            p = destino / f['path']
            if f['type'] == 'dir':
                p.mkdir(mode=0o700, exist_ok=True)
            else:
                with p.open('xb') as out:
                    with tar.extractfile(f['path']) as source:
                        shutil.copyfileobj(source, out)
                    out.flush(); os.fsync(out.fileno())
        for f in sorted(m['files'], key=lambda f: len(PurePosixPath(f['path']).parts), reverse=True):
            p = destino / f['path']
            if preservar_propietarios:
                os.chown(p, f['uid'], f['gid'])
            os.chmod(p, f['mode'])
            os.utime(p, ns=(f['mtime_ns'], f['mtime_ns']))
        assert {'.', *(p.relative_to(destino).as_posix() for p in destino.rglob('*'))} == set(esperados)
        for f in m['files']:
            p = destino / f['path']
            if f['type'] == 'file':
                with p.open('rb') as source:
                    assert hashlib.file_digest(source, 'sha256').hexdigest() == f['sha256']
            if preservar_propietarios:
                s = p.stat()
                assert (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) == (f['uid'], f['gid'], f['mode'])
    return m

def verificar_sqlite_restaurado(destino):
    resultados = []
    for p in sorted(Path(destino).rglob('*')):
        if not p.is_file():
            continue
        with p.open('rb') as f:
            if f.read(16) != b'SQLite format 3\x00':
                continue
        db = sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
            esquema = None
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone():
                esquema = db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
            resultados.append({'archivo': p.relative_to(destino).as_posix(), 'integridad': 'ok', 'esquema': esquema})
        finally:
            db.close()
    return resultados

os.umask(0o077)
m = restaurar_copia(Path(sys.argv[1]), Path(sys.argv[2]), preservar_propietarios=True)
print(json.dumps({'version': m['version'], 'sqlite': verificar_sqlite_restaurado(Path(sys.argv[2]))}, ensure_ascii=True))
PY
```

Los hashes se comparan **antes de abrir SQLite**. Su apertura puede reconstruir
SHM o modificar/eliminar sidecars en la copia restaurada: no confundir eso con
cambio del archivo de backup ni repetir hashes del manifiesto después esperando
los mismos bytes. No usar `immutable=1`: puede ignorar WAL. Una base principal sin
su WAL puede dar `integrity_check=ok` y aun así haber perdido commits.

Contrastar además versión/migraciones con la imagen registrada, ledger mensual,
reservas, revocaciones y obligaciones restauradas. No publicar tablas ni valores
privados. Esta validación física/SQLite no demuestra que Chroma pueda abrirse con
otra versión, ni que la aplicación/OCI complete su recuperación. Conservar el
backup original intacto y separar una copia de prueba de la que se adoptará.

**Esperado:** inventario/hash/permisos coherentes y cada SQLite íntegra, con esquema
identificado. **Consumo:** disco/CPU local; sin proveedores. **Parar si:** checksum,
ruta/tipo, duplicado, propietario/modo, integridad o esquema incompatibles, destino
no vacío o backup antiguo sin reconciliar. Un fallo deja un destino parcial que
no se adopta; no sobreescribirlo para repetir ni ocultar el resultado.

### 16.4 Copia protegida fuera de la VM y secretos por separado

**Máquina/shell:** VM/Bash y operador/PowerShell. **Previo:** restauración aislada
verificada, destino externo cifrado/restringido aprobado por el equipo; registrar
custodio, fecha y hash. En Windows verificar ACL del destino; los modos POSIX no
demuestran protección de una carpeta Windows. No copiar al repo, nube personal
sin aprobación ni directorio compartido.

Para permitir `scp` con el usuario administrativo sin aflojar permisos del backup,
crear **una copia de exportación nueva**, con espacio contabilizado. Reemplazar
la ruta por el snapshot real; no usar globs ni otro directorio de datos:

```bash
copiaOrigen='/var/backups/nuevamente/snapshot-FECHA-ID_VERIFICADO'
test ! -e /home/ubuntu/nuevamente-backup-export
test ! -L /home/ubuntu/nuevamente-backup-export
sudo install -d -o ubuntu -g ubuntu -m 0700 /home/ubuntu/nuevamente-backup-export
sudo install -o ubuntu -g ubuntu -m 0600 "$copiaOrigen/volume.tar.gz" /home/ubuntu/nuevamente-backup-export/volume.tar.gz
sudo install -o ubuntu -g ubuntu -m 0600 "$copiaOrigen/manifest.json" /home/ubuntu/nuevamente-backup-export/manifest.json
```

En la PC, usando las variables SSH de instalación y un destino nuevo protegido:

```powershell
$destinoBackup = 'C:/RESPALDO_PROTEGIDO/NuevaMente/FECHA-ID'
if (Test-Path $destinoBackup) { throw 'Destino previo: no sobrescribir' }
New-Item -ItemType Directory -Path $destinoBackup | Out-Null
scp -i $claveSsh "ubuntu@${ipVm}:/home/ubuntu/nuevamente-backup-export/volume.tar.gz" "$destinoBackup/volume.tar.gz"
if ($LASTEXITCODE -ne 0) { throw 'Transferencia del volumen fallida' }
scp -i $claveSsh "ubuntu@${ipVm}:/home/ubuntu/nuevamente-backup-export/manifest.json" "$destinoBackup/manifest.json"
if ($LASTEXITCODE -ne 0) { throw 'Transferencia del manifiesto fallida' }
$manifestBackup = Get-Content "$destinoBackup/manifest.json" -Raw | ConvertFrom-Json
if ((Get-FileHash "$destinoBackup/volume.tar.gz" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifestBackup.archive_sha256) { throw 'Checksum externo incorrecto' }
```

Comprobar restauración también desde la copia externa, en otro destino aislado.
Custodiar fuente/IDs de imágenes de forma recuperable: un ID sin sus bytes/imagen
local no asegura reconstrucción futura si desaparecen dependencias externas.
No se instala ni contrata almacenamiento externo mediante esta guía.

El tar de datos **no respalda** `/etc/nuevamente`, las claves OCI/Gemini/origen,
`/etc/caddy`, ACME `/var/lib/caddy`, ni los modelos de `/var/lib/nuevamente-deploy`.
Definir su custodia privada separada, con dueño/modos y versión de la sección 6.
Los modelos/journals contienen secretos aunque sean JSON: no adjuntarlos al
reporte ni mezclarlos con el inventario saneado. Una recuperación en otro host
recrea instalación/red/peer y aprobación de volumen; no reusa fingerprints ni
`current.json` del host perdido. No restaurar automáticamente secretos antiguos
ni rotar una sola parte de Caddy/backend.

**Esperado:** copia externa verificada y custodios identificados, sin secretos en
evidencia pública. **Consumo:** espacio para staging/copia, tráfico SSH/egreso; si
se acuerda almacenamiento cloud, contar su capacidad y solicitudes por separado.
**Parar si:** falta destino protegido/custodio, error de transferencia/integridad,
capacidad o política de secretos. No considerar suficiente el backup local.

## 17. Recuperación por escenario

Todos estos procedimientos reales quedan sujetos a autorización operativa futura.
En una pérdida de datos, cerrar admisiones antes de explorar alternativas y
preservar lo sobreviviente. No inicializar un volumen vacío para esconder el fallo.

### 17.1 Contenedores perdidos o recreados, volumen conservado

**Máquina/shell:** VM/Bash. **Previo:** comprobar el volumen/credenciales/fuente e
imágenes aprobadas, sin journal. Un restart con contenedores presentes sigue la
sección 14. Si los contenedores fueron eliminados, el helper necesita un backend
único para observar el fingerprint anterior: no asumir que `--activate` lo resolverá.

```bash
sudo docker volume inspect --format '{{.Name}} {{.Driver}} {{.CreatedAt}}' nuevamente_backend_data
sudo docker ps -aq --filter label=com.docker.compose.project=nuevamente --filter label=com.docker.compose.service=backend
sudo test -e /var/lib/nuevamente-deploy/current.json
```

**Esperado:** identidad conservada. Con contenedores presentes, activar/validar con
la fuente registrada y sección 12. Si falta el backend y existe `current.json`,
**parar**: no hay modo CLI del helper para adoptar ese estado como primera
instalación ni se autoriza borrar su modelo para simularlo. Revisar recuperación
administrativa con el responsable; `--recover` requiere journal y no lo fabrica.
**Consumo:** inspección local; al reactivar, el indicado en sección 11.

### 17.2 Despliegue interrumpido con journal

**Máquina/shell:** VM/Bash. **Previo:** `pending.json` real, instalación gestionada,
credenciales/volumen intactos, fuente del helper compatible e imágenes previas
presentes. No editar el journal ni restaurarlo desde una copia vieja.

```bash
sudo test -e /var/lib/nuevamente-deploy/pending.json
sudo bash /RUTA_FUENTE_DESPLEGADA/infra/caddy/deploy.sh --checkout /RUTA_FUENTE_DESPLEGADA --recover
```

**Esperado:** restauración verificada del modelo/configuración anterior, con
reobservación de peer. Sin versión anterior, el helper detiene los servicios
nuevos y retira la publicación, conservando datos/ACME: no hay aplicación previa
que validar. `--recover` **no restaura la VM, secretos, SQLite, migraciones ni
efectos externos**. Un resultado `prior-service-state-restored` no revierte lo
que ya escribió la versión fallida. Antes de volver al modelo anterior, revisar
también su compatibilidad con paquetes locales/OCI 1.0/1.1 y sus modos: un journal
no convierte un lector 1.0 en lector 1.1, aunque SQLite conserve el esquema 11.

Sólo si existía una versión anterior y se restauró correctamente:

```bash
sudo bash /RUTA_FUENTE_DESPLEGADA/infra/caddy/deploy.sh --checkout /RUTA_FUENTE_DESPLEGADA --validate
```

**Consumo:** contenedores/salud, posibles OCI/ACME al arrancar. **Parar si:** imagen
ausente, esquema/paquetes/lector incompatibles, volumen/secretos cambiados o recuperación fallida;
conservar journal y servicios cerrados. No borrar estado para conseguir verde.

### 17.3 Pérdida de VM con backup

**Máquina/shell:** operador y VM nueva Ubuntu ARM64/Bash. **Previo:** backup externo
verificado, fuente/imágenes compatibles, custodia de secretos y análisis de la
antigüedad de la sección 18. Revisar inventario OCI: instancias detenidas, discos
huérfanos y restauraciones cuentan; no se presupone capacidad A1 libre ni se
descarta el diario #47 para crear recursos duplicados.

Secuencia operativa: secciones 1–6 para el nuevo host, sin publicar; sección 7.1
sólo para un volumen realmente nuevo/vacío; transferencia privada del backup y
restauración de 16.3 al Mountpoint inspeccionado de ese volumen. **No escribir en
un volumen que contenga datos**. El bloque restaura propietarios sólo sobre
elementos nuevos; no es `chown -R` de datos ajenos. Luego aprobar la identidad
actual mediante 7.2, no copiar `volume.json` del host anterior.

```bash
sudo docker volume inspect --format '{{.Mountpoint}}' nuevamente_backend_data
sudo stat -c '%u:%g %a %n' /RUTA_DESTINO_RESTAURADO
```

Usar la ruta real inspeccionada en 16.3, no el marcador literal. Comprobar SQLite,
versión del esquema y compatibilidad de lectores con paquetes locales/OCI y modos,
permisos, ledger, obligaciones y vigencia de los recursos. La restauración conserva
el modo guardado; no transforma básicos en aprobados ni restaura trabajos pendientes.
Preparar archivos/credenciales autorizados y DNS/red del nuevo host; resolver
revocaciones o consumo posteriores al backup **antes** de abrir la app. Conservar
Caddy detenido mientras no esté resuelto ese análisis. No copiar el estado de
instalación systemd, journal, peer o modelo Compose de la VM anterior.
Repetir observación/validación/activación según secciones 8–12 sólo cuando sea
seguro; H02 no convierte automáticamente una base restaurada antigua en una base
reconciliada con todos los cambios remotos posteriores.

**Esperado:** estado hasta el punto respaldado y nueva identidad de despliegue
verificada. **Consumo:** VM/discos dentro de asignación verificada, tráfico y espacio
de restauración, OCI/Gemini potenciales al reactivar; el ejercicio real va a #133.
**Parar si:** no hay capacidad gratuita, backup/esquema incompleto, recursos
anteriores sin resolver, custodia dudosa o revocaciones/consumo inciertos.

### 17.4 Pérdida o corrupción del índice con SQLite conservada

**Máquina/shell:** VM/Bash. **Previo:** app detenida, backup de lo sobreviviente,
modelo/dimensiones/preparación identificados y sin cambios destructivos al índice.
Preferir restauración coherente del conjunto según 16.3/17.3 tras revisar su edad;
no mezclar un catálogo viejo con archivos Chroma nuevos ni borrar sólo
`catalogo.sqlite3`, donde también viven lápidas.

Para fuentes demo existe la CLI real de sección 10, con un único escritor. Puede
reconstruir desde originales e interpretaciones compatibles; no elimina conflictos
de una colección incompatible o corrupción estructural. Parar si no puede abrirla.

Para índice privado existen las funciones Python
`reconstruir_workspace(storage, indice, contexto=...)` y
`reconstruir_indice(storage, indice, contextos=...)` en
[rebuild.py](../backend/app/core/rag/rebuild.py). No tienen entrada `python -m`
operativa ni reparan SQLite. Exigen StorageProvider, contextos autorizados con
cuotas compartidas/deadline y control de versiones/lápidas. No son una CLI que
acepte un path y no acreditan por sí solas `ready` operativo.

La recuperación H02 disponible se activa para documentos acreditados tras
restaurar un **registro nuevo o pendiente**, no simplemente por perder Chroma con
SQLite conservada. No borrar `operativo.db`, editar `recovery_state` ni fabricar
admisiones para dispararla. Si no hay backup coherente y se necesita una reparación
selectiva, la integración administrativa de esas funciones y la reconciliación de
lápidas locales requieren decisión/seguimiento técnico del equipo; esta guía no
inventa un comando ni declara resuelto ese hueco.
**Esperado:** índice restaurado/reprecarga demo verificados o limitación explícita.
**Consumo:** restauración local; reconstrucción autorizada consume OCI/embeddings
y eventualmente visión. **Parar si:** preparación incompatible, lápidas sin
sincronizar, corrupción no recuperable o falta del procedimiento seguro del caso.

### 17.5 Pérdida total sin backup

**Máquina/shell:** operador/VM nueva, Bash. **Previo:** evaluación explícita de
pérdida, capacidad gratuita y consumo remanente, sin prometer restauración total.
Reconstruir infraestructura/configuración e identidad del volumen con 1–9; no
copiar credenciales del operador ni reducir seguridad para recuperar servicio.

Sólo después de resolver cuotas y riesgos de revocación/consumo de sección 18,
activar mediante el helper de sección 11. El lifespan llama a
`session.recovery.restaurar_registro` sobre el registro nuevo: lee manifiestos
privados, originales y paquetes confirmados, verifica procedencia/hash y respeta
expiración/lápidas remotas. H02 reserva y despacha nuevos trabajos de reconstrucción
en el gestor común, con cuotas configuradas. No hay otra CLI de restauración que
deba ejecutarse. Preparar demo con sección 10; recuperar espacio con su código
vigente y verificar fuentes/paquetes, sin publicar códigos en evidencia.

**Esperado:** recuperación parcial verificable de recursos confirmados; las fuentes
no pasan a `ready` hasta confirmar índice/cobertura y visión. H02 limita intentos;
un fallo no autoriza bucles ni nueva generación pedagógica para sustituir datos.
No se recuperan sesiones, jobs/eventos históricos, idempotencia, consumo mensual
perdido ni revocaciones/intenciones que nunca llegaron a OCI. Si tampoco se conserva
el código de recuperación, no hay bypass administrativo inventado para entrar al
espacio. **Consumo:** lecturas/listados OCI, escrituras de control y embeddings/
visión de reconstrucción; registrar presupuesto real. **Parar si:** no se puede
establecer un margen seguro o contener el riesgo de cambios no sincronizados.

## 18. Límites de antigüedad y reapertura segura

**Máquina/shell:** operador/consolas y VM/Bash sobre copia aislada.
**Previo:** fecha/versión del backup conocidas. El punto de recuperación es el
snapshot, no la hora de restaurar. Conservar el original e identificar la diferencia
temporal: documentos, aprobados, sesiones, revocaciones, cuotas, retiros y reservas
posteriores pueden faltar. Un backup íntegro y viejo sigue siendo viejo.

Los comandos locales de 15 y 16.3 identifican contadores/esquema/obligaciones de
la copia; contrastarlos con evidencia posterior y consumo agregado del proveedor.
No hay en el repo una herramienta universal que mezcle una base antigua con
todas las mutaciones posteriores. No borrar sesiones/lápidas ni editar tablas
para simular esa reconciliación. Si un retiro sólo existió en el disco perdido,
OCI no puede acreditarlo por sí solo: mantener cerrado el acceso afectado y pedir
decisión del equipo; la ausencia de evidencia no autoriza resucitarlo.

Para consumo, un ledger restaurado registra sólo lo que conocía al copiarse;
uno nuevo empieza sin ese historial. Antes de contactar proveedores, determinar
un saldo adicional seguro, incluidas lecturas de control y reserva de limpieza.
Reducir `OCI_MAX_REQUESTS_PER_MONTH` puede limitar nuevas operaciones sin reescribir
contadores: el techo configurado debe ser como máximo las solicitudes registradas
del mes UTC restaurado **más** el saldo adicional efectivamente autorizado, y nunca
superar 20.000. Con ledger nuevo, no se suma consumo histórico inexistente.
Esto es un cálculo preventivo, no corrección del historial ni permiso para usar
la franquicia restante de la tenancy. Revisar también bytes/inventario y Gemini.
Si no puede verificarse un saldo seguro, no arrancar el backend real: incluso
su constructor OCI consume solicitudes. No tomar el último snapshot público de
#55 como lectura de consumo actual.

**Esperado:** límite y riesgo residual decididos y registrados por el responsable,
sin afirmar consumo cero ni recuperación de cambios ausentes. **Consumo:** las
consultas externas futuras también cuentan. **Parar si:** saldo, retención,
revocaciones o custodia no pueden verificarse; no reabrir por tener health verde.

## 19. Diagnóstico y condiciones de parada

**Máquina/shell:** VM/Bash, operador y navegador según el síntoma.
**Previo:** identificar fase, versión, estado del journal y observaciones saneadas.
Usar sólo herramientas existentes y no ejecutar reparaciones automáticas:

```bash
if sudo test -e /var/lib/nuevamente-deploy/pending.json; then
  printf '%s\n' 'Journal pendiente: aplicar 17.2, sin activar otro despliegue'
else
  printf '%s\n' 'Sin journal pendiente'
fi
sudo docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}} {{.Config.User}}' nuevamente-backend:latest nuevamente-frontend:latest
sudo docker ps -a --filter label=com.docker.compose.project=nuevamente --format '{{.ID}} {{.Names}} {{.Status}}'
sudo systemctl is-active caddy docker nuevamente-firewall
sudo ss -H -lntu
```

La comprobación del journal sólo informa su presencia y no ejecuta una mutación.
Los demás controles no imprimen env ni cuerpos.
El helper `--validate` comprueba configuración/estado sin publicar, pero después
de un reinicio puede informar observación invalidada: ésa es condición para la
reactivación revisada, no para ampliar confianza. Un `--recover` sin journal no
es un mecanismo alternativo de instalación.

| Síntoma | Comprobación y parada |
| --- | --- |
| Falta capacidad A1 | Revisión del reporte #47; registrar bloqueo/manuales acotados. Sin shape/región paga |
| Firma/config/permisos | Revisar artefactos, tabla de sección 6 y código fijo del helper. Sin omitir firma/ACL ni chmod recursivo |
| Volumen no aprobado | Contrastar identidad con `volume.json` privadamente. Sin reemplazar con uno vacío |
| Peer/rutas cambiados | Revisar reglas OCI/host y nueva observación admitida por el helper. Sin CIDR, `*` ni edición de fingerprints |
| Journal pendiente | 17.2, conservar imágenes/estado. Si falla, mantener cerrado y escalar; nunca borrar journal |
| 502/unhealthy/servicio detenido | Salud de 12, servicios/listeners, IDs/versiones y códigos filtrados. No concluir que OCI/Gemini están sanos por health aislado |
| 401/404/503 de biblioteca | Sesión propia, fuente vigente, presupuesto y registro de precarga. No forzar permisos ni convertir error en vacío |
| Disco/cuotas/limpieza | Controles de 15; conservar reservas/lápidas y margen de limpieza. No reiniciar ledger ni repetir en bucle |
| Backup/SQLite/esquema | Validar 16.3 en copia aislada. Sin WAL borrado, migración inversa inventada ni restauración encima |
| Índice privado perdido | 17.4; no confundir funciones de biblioteca con una CLI operativa existente |

Registrar fecha UTC, versión, fase, código/resultado y limitaciones sin claves,
tokens, códigos de recuperación, URLs firmadas, manifests privados ni prompts.
El verificador `infra/caddy/verify_public.py` tiene protocolo de autorización propio
para sondas externas; escribir esta guía no autoriza ejecutarlo ni escanear puertos.
**Esperado:** causa localizada o límite documentado, nunca éxito inferido de un
timeout. **Consumo:** inspección local; cualquier sonda/proveedor futuro debe
declararse y presupuestarse. **Parar si:** seguridad, datos, integridad, cuota o
identidad no pueden acreditarse. No improvisar una reparación destructiva.

## Estado para auditoría final y aceptación

La validación local de etapa 4 y sus límites están registrados en la matriz;
la revisión humana independiente de #58 y la reproducción real de #133 siguen
pendientes. También requieren decisión del equipo
la frecuencia/custodia/retención del backup y los huecos operativos señalados en
17.1/17.4; no se amplía el código ni se recortan requisitos para ocultarlos.
Los ensayos sintéticos de etapa 3 y su consolidación reproducible de etapa 4 se
registran en [verificación #58](verificacion-issue-58.md) y en la
[evidencia sintética](evidencia/issue-58/README.md): no prueban permisos POSIX, Chroma real,
systemd/Docker, una VM pública, OCI/Gemini ni recuperación de datos no respaldados.
