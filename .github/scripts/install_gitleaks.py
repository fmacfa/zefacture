"""Install the reviewed Gitleaks release, verifying its archive before extraction."""

import argparse
import hashlib
import io
import platform
import tarfile
from pathlib import Path
from urllib.request import urlopen

VERSION = "8.30.1"
ARCHIVES = {
    ("Linux", "x86_64"): (
        "linux_x64",
        "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
    ),
    ("Darwin", "arm64"): (
        "darwin_arm64",
        "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5",
    ),
}


def install(output):
    selected = ARCHIVES.get((platform.system(), platform.machine()))
    if selected is None:
        raise SystemExit("Unsupported platform; use a reviewed Gitleaks binary.")
    suffix, expected = selected
    url = f"https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{suffix}.tar.gz"
    with urlopen(url, timeout=60) as response:
        raw = response.read(50_000_001)
    if len(raw) > 50_000_000 or hashlib.sha256(raw).hexdigest() != expected:
        raise SystemExit("Gitleaks archive checksum mismatch.")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        member = archive.getmember("gitleaks")
        if not member.isfile() or member.size > 100_000_000:
            raise SystemExit("Invalid Gitleaks archive member.")
        binary = archive.extractfile(member).read()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(binary)
    output.chmod(0o755)
    print(f"Gitleaks {VERSION}: checksum verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    install(parser.parse_args().output)
