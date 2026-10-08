#!/usr/bin/env bash
# #47: Ubuntu 24.04 ARM64 nuevo; Docker y acceso por clave. No despliega la app.
set -Eeuo pipefail
[[ $(id -u) == 0 ]] || { echo 'Ejecutar como root'; exit 1; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(dpkg --print-architecture) == arm64 ]] || {
  echo 'Se requiere Ubuntu 24.04 ARM64'; exit 1;
}
# La fuente IPv4 administrativa llega validada por el aprovisionador.
python3 - "$NUEVAMENTE_ADMIN_CIDR" <<'CHECK_CIDR'
import ipaddress, sys
net = ipaddress.ip_network(sys.argv[1], strict=True)
assert net.version == 4 and net.prefixlen == 32 and net.network_address.is_global
CHECK_CIDR
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get install -y ca-certificates curl unattended-upgrades iptables iproute2
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
AddressFamily inet
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
# INPUT protege al host; DOCKER-USER protege el tráfico reenviado de Docker.
# Solo Caddy (en el host, #48) podrá atender tráfico público de aplicación.
printf 'NUEVAMENTE_ADMIN_CIDR=%s\n' "$NUEVAMENTE_ADMIN_CIDR" > /etc/nuevamente-firewall.conf
chmod 0600 /etc/nuevamente-firewall.conf
cat > /usr/local/sbin/nuevamente-firewall <<'FIREWALL'
#!/usr/bin/env bash
set -Eeuo pipefail
iface=$(ip -o -4 route show default | awk 'NR==1 {print $5}')
[[ -n $iface && -n $NUEVAMENTE_ADMIN_CIDR ]]
ipt() { iptables -w 10 "$@"; }
for chain in NUEVAMENTE-IN NUEVAMENTE-FWD; do
  ipt -N "$chain" 2>/dev/null || ipt -L "$chain" -n >/dev/null
  ipt -F "$chain"
done
ipt -A NUEVAMENTE-IN -i lo -j ACCEPT
ipt -A NUEVAMENTE-IN -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ipt -A NUEVAMENTE-IN -p udp --sport 67 --dport 68 -j ACCEPT
ipt -A NUEVAMENTE-IN -p tcp -s "$NUEVAMENTE_ADMIN_CIDR" --dport 22 -j ACCEPT
ipt -A NUEVAMENTE-IN -p tcp -m multiport --dports 80,443 -j ACCEPT
ipt -A NUEVAMENTE-IN -p icmp --icmp-type fragmentation-needed -j ACCEPT
ipt -A NUEVAMENTE-IN -j DROP
ipt -C INPUT -j NUEVAMENTE-IN 2>/dev/null || ipt -I INPUT 1 -j NUEVAMENTE-IN
ipt -A NUEVAMENTE-FWD -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
ipt -A NUEVAMENTE-FWD -i "$iface" -j DROP
ipt -A NUEVAMENTE-FWD -j RETURN
ipt -C DOCKER-USER -j NUEVAMENTE-FWD 2>/dev/null || ipt -I DOCKER-USER 1 -j NUEVAMENTE-FWD
FIREWALL
chmod 0700 /usr/local/sbin/nuevamente-firewall
cat > /etc/systemd/system/nuevamente-firewall.service <<'FIREWALL_SERVICE'
[Unit]
Description=NuevaMente host and Docker ingress firewall
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service
PartOf=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=/etc/nuevamente-firewall.conf
ExecStart=/usr/local/sbin/nuevamente-firewall
[Install]
WantedBy=docker.service
FIREWALL_SERVICE
systemctl daemon-reload
systemctl enable --now nuevamente-firewall.service
systemctl enable --now unattended-upgrades
# El administrador conserva sudo; no se agrega al grupo docker (equivale a root).
docker info --format '{{.Architecture}}' | grep -Eq '^(aarch64|arm64)$'
docker compose version
docker run --rm hello-world
# El volumen de Compose queda en disco local; no crear/eliminar datos de una app.
install -d -m 0750 -o ubuntu -g ubuntu /opt/nuevamente
printf '%s\n' 'bootstrap-completed' > /opt/nuevamente/issue47-ready
