#!/usr/bin/env bash
set -euo pipefail

mirror=
for candidate in https://archive.ubuntu.com/ubuntu https://mirrors.edge.kernel.org/ubuntu https://security.ubuntu.com/ubuntu; do
  if curl --head --fail --silent --show-error --max-time 8 --output /dev/null "$candidate/dists/noble-updates/InRelease" &&
     curl --head --fail --silent --show-error --max-time 8 --output /dev/null "$candidate/dists/noble-security/InRelease"; then
    mirror="$candidate"
    break
  fi
done
if [[ -z "$mirror" ]]; then
  echo '::error::Aucun miroir Ubuntu signé accessible.'
  exit 1
fi
echo "Dépendances système via $mirror"
apt_sources=/etc/apt/zefacture-ci.sources
apt_config=/etc/apt/apt.conf.d/99-zefacture-ci
trap 'sudo rm -f "$apt_config" "$apt_sources"' EXIT
sudo tee "$apt_sources" > /dev/null <<EOF
Types: deb
URIs: $mirror/
Suites: noble noble-updates noble-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
sudo tee "$apt_config" > /dev/null <<'EOF'
Dir::Etc::sourcelist "/etc/apt/zefacture-ci.sources";
Dir::Etc::sourceparts "-";
Acquire::Retries "2";
Acquire::https::Timeout "20";
EOF
"$@"
