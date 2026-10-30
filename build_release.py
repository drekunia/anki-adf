"""Build a clean Anki upload archive using an explicit runtime-file allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

FILES = (
    "__init__.py", "browser_window.py", "config_model.py", "duplicate_core.py",
    "live_index.py", "preferences.py", "results_ui.py", "scan_history.py",
    "scan_service.py", "config.json", "manifest.json", "LICENSE", "README.md",
)


def build(version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError("Use a release version such as 0.1.0")
    root = Path(__file__).resolve().parent
    for name in FILES:
        if not (root / name).is_file():
            raise FileNotFoundError(name)
        if name.endswith(".py"):
            compile((root / name).read_text(encoding="utf-8-sig"), name, "exec")
    defaults = json.loads((root / "config.json").read_text(encoding="utf-8-sig"))
    if defaults.get("last") or defaults.get("presets") or defaults.get("profiles"):
        raise ValueError("config.json must contain shared defaults, not personal settings")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    if not manifest.get("package") or not manifest.get("name"):
        raise ValueError("manifest.json needs package and name")
    output = root / "dist" / f"advanced-duplicate-finder-{version}.ankiaddon"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            data = (root / name).read_bytes()
            if name == "README.md":
                # The installed package has user documentation, not developer-only links.
                text = data.decode("utf-8-sig").split("## Development and testing", 1)[0]
                text += ("## Source and license\n\n"
                         "[Source code and complete documentation](https://github.com/drekunia/anki-adf).\n\n"
                         "GNU Affero General Public License v3.0. See [LICENSE](LICENSE).\n")
                data = text.encode("utf-8")
            archive.writestr(name, data)
    with zipfile.ZipFile(output) as archive:
        if set(archive.namelist()) != set(FILES) or archive.testzip():
            raise ValueError("Release archive verification failed")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    print(f"Built {output} ({output.stat().st_size:,} bytes, {len(FILES)} files)")
    print(f"SHA-256: {digest}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.1.0")
    build(parser.parse_args().version)
