#!/usr/bin/env bash
# #47: Ubuntu 24.04 ARM64 nuevo; Docker y acceso por clave. No despliega la app.
set -Eeuo pipefail
[[ $(id -u) == 0 ]] || { echo 'Ejecutar como root'; exit 1; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(dpkg --print-architecture) == arm64 ]] || {
  echo 'Se requiere Ubuntu 24.04 ARM64'; exit 1;
}
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get install -y ca-certificates curl unattended-upgrades
install -m 0755 -d /etc/apt/keyrings
curl --fail --silent --show-error --location --proto '=https' \
  https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'DOCKER_REPO'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: arm64
Signed-By: /etc/apt/keyrings/docker.asc
DOCKER_REPO
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'AUTO_UPGRADES'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
AUTO_UPGRADES
cat > /etc/apt/apt.conf.d/52nuevamente-upgrades <<'SECURITY_UPGRADES'
Unattended-Upgrade::Automatic-Reboot "false";
SECURITY_UPGRADES
install -m 0755 -d /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/00-nuevamente.conf <<'SSH_CONFIG'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
MaxAuthTries 3
AllowUsers ubuntu
SSH_CONFIG
/usr/sbin/sshd -t
systemctl reload ssh
systemctl enable --now docker
systemctl enable --now unattended-upgrades
# El administrador conserva sudo; no se agrega al grupo docker (equivale a root).
docker info --format '{{.Architecture}}' | grep -Eq '^(aarch64|arm64)$'
docker compose version
docker run --rm hello-world
# El volumen de Compose queda en disco local; no crear/eliminar datos de una app.
install -d -m 0750 -o ubuntu -g ubuntu /opt/nuevamente
printf '%s\n' 'bootstrap-completed' > /opt/nuevamente/issue47-ready
