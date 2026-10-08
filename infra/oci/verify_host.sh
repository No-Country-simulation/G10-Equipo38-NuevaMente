#!/usr/bin/env bash
# Ejecutar en la VM: sudo bash infra/oci/verify_host.sh
set -Eeuo pipefail
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == aarch64 ]]
test -f /opt/nuevamente/issue47-ready
cloud-init status --wait
/usr/sbin/sshd -t
/usr/sbin/sshd -T | grep -qx 'passwordauthentication no'
/usr/sbin/sshd -T | grep -qx 'kbdinteractiveauthentication no'
/usr/sbin/sshd -T | grep -qx 'permitrootlogin no'
systemctl is-active --quiet docker
systemctl is-enabled --quiet docker
systemctl is-active --quiet unattended-upgrades
apt-config dump | grep -q 'APT::Periodic::Unattended-Upgrade "1"'
docker compose version
docker info --format '{{.Architecture}}' | grep -Eq '^(aarch64|arm64)$'
docker run --rm hello-world
# Revisar los listeners reales; 8000/8501 pueden existir solo en loopback en #48.
python3 - <<'PY'
import subprocess
for line in subprocess.check_output(['ss','-H','-lnt'],text=True).splitlines():
    local=line.split()[3]
    if local.rsplit(':',1)[-1] in ('8000','8501'):
        assert local.startswith(('127.0.0.1:','[::1]:')), 'Puerto de app expuesto'
print('Host ARM64, SSH y servicios verificados. Revisar aparte reglas OCI y prueba desde otra IP.')
PY
