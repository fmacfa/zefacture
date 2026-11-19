#!/usr/bin/env bash
set -euo pipefail

mirrors=(
  https://mirror.umd.edu/ubuntu
  https://mirrors.mit.edu/ubuntu
  https://mirror.math.princeton.edu/pub/ubuntu
  https://ubuntu.osuosl.org/ubuntu
  https://archive.ubuntu.com/ubuntu
  https://security.ubuntu.com/ubuntu
)
apt_sources=/etc/apt/zefacture-ci.sources
apt_config=/etc/apt/apt.conf.d/99-zefacture-ci
trap 'sudo rm -f "$apt_config" "$apt_sources"' EXIT
sudo tee "$apt_config" > /dev/null <<'APT'
Dir::Etc::sourcelist "/etc/apt/zefacture-ci.sources";
Dir::Etc::sourceparts "-";
Acquire::Retries "1";
Acquire::https::Timeout "15";
Acquire::ForceIPv4 "true";
Acquire::Languages "none";
APT

probe=(curl --ipv4 --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --connect-timeout 5 --max-time 12 --output /dev/null)
for mirror in "${mirrors[@]}"; do
  if ! "${probe[@]}" "$mirror/dists/noble-updates/InRelease" ||
     ! "${probe[@]}" "$mirror/dists/noble-security/InRelease"; then
    continue
  fi
  sudo tee "$apt_sources" > /dev/null <<APT
Types: deb
URIs: $mirror/
Suites: noble noble-updates noble-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
APT
  echo "Dépendances système via $mirror"
  if "$@"; then
    exit 0
  fi
  echo "::warning::Installation interrompue depuis $mirror. Essai du miroir suivant."
done
echo '::error::Installation impossible depuis les miroirs Ubuntu signés.'
exit 1
