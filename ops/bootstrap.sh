#!/usr/bin/env bash
# Primo avvio della VPS (Ubuntu 24.04), una volta sola, dall'utente iniziale del provider
# (Contabo: "admin" con sudo senza password via cloud-init):
#   ssh admin@<IP> 'sudo bash -s' < ops/bootstrap.sh
# Crea l'utente "deploy" con la stessa chiave SSH dell'utente iniziale, installa Docker,
# chiude tutto tranne SSH (il web entra dal Cloudflare Tunnel), poi disattiva
# password e login di root. Idempotente: si può rilanciare.
set -euo pipefail
KEYS=$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)/.ssh/authorized_keys
[ -s "$KEYS" ] || { echo "manca $KEYS: prima installa la chiave SSH" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl git ufw unattended-upgrades rclone postgresql-client

# Infisical CLI usato dal deploy per leggere i secret prod senza mantenere il .env sul server.
curl -1sLf 'https://artifacts-cli.infisical.com/setup.deb.sh' | bash
apt-get update -q
apt-get install -yq infisical

# Docker dal repo ufficiale
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Utente deploy con la stessa chiave
id deploy >/dev/null 2>&1 || adduser --disabled-password --gecos "" deploy
usermod -aG sudo,docker deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
install -m 600 -o deploy -g deploy "$KEYS" /home/deploy/.ssh/authorized_keys
echo "deploy ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/deploy && chmod 440 /etc/sudoers.d/deploy
install -d -o deploy -g deploy /opt/zenbree

# Firewall: solo SSH. Docker pubblica le porte su 127.0.0.1 (compose.yml), quindi niente buchi.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

# SSH solo a chiave, niente root. "00-": sshd tiene la PRIMA occorrenza, e cloud-init scrive 50-cloud-init.conf con PasswordAuthentication yes
cat > /etc/ssh/sshd_config.d/00-zenbree.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
CONF
rm -f /etc/ssh/sshd_config.d/99-zenbree.conf
sshd -t && systemctl reload ssh
sshd -T | grep -q "^passwordauthentication no" || { echo "password SSH ancora attiva" >&2; exit 1; }

dpkg-reconfigure -f noninteractive unattended-upgrades
echo "ok: entra con ssh deploy@$(curl -fsS -4 ifconfig.me)"
