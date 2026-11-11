"""The production Nginx site must forward every API route to the backend.

Nginx only proxies the path prefixes it lists; any other path falls through to
the single-page app, which answers with index.html. A router missing from that
list therefore "works" in development (Vite proxies everything) but breaks in
production with HTML where the frontend expects JSON.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.ssl_monitor.api.app import app

NGINX_CONF = Path(__file__).resolve().parents[3] / "deploy" / "nginx.conf"


def _api_prefixes() -> set[str]:
    return {path.split("/")[1] for path in app.openapi()["paths"]}


def _proxied_prefixes() -> set[str]:
    match = re.search(r"location ~ \^/\(([^)]*)\)\(/\|\$\)", NGINX_CONF.read_text("utf-8"))
    assert match, "API location block not found in deploy/nginx.conf"
    return set(match.group(1).split("|"))


def test_nginx_forwards_every_api_route() -> None:
    missing = _api_prefixes() - _proxied_prefixes()
    assert not missing, f"deploy/nginx.conf does not proxy: {sorted(missing)}"


DEPLOY_DIR = NGINX_CONF.parent


def _units_installed_by_scripts() -> set[str]:
    lib = (DEPLOY_DIR / "lib.sh").read_text("utf-8")
    block = re.search(r"CERTWATCH_UNITS=\(\n(.*?)\n\)", lib, re.S)
    assert block, "CERTWATCH_UNITS list not found in deploy/lib.sh"
    return {line.strip() for line in block.group(1).splitlines() if line.strip()}


def test_setup_and_update_install_every_shipped_systemd_unit() -> None:
    shipped = {p.name for p in DEPLOY_DIR.iterdir() if p.suffix in {".service", ".timer"}}
    assert _units_installed_by_scripts() == shipped


def test_production_env_template_uses_postgres() -> None:
    env = (DEPLOY_DIR / "certwatch.env.example").read_text("utf-8")
    assert re.search(r"^STORAGE=postgres$", env, re.M)
    assert re.search(r"^DATABASE_URL=postgresql://", env, re.M)


def test_vite_dev_proxy_forwards_every_api_route() -> None:
    vite = (DEPLOY_DIR.parent / "frontend" / "vite.config.ts").read_text("utf-8")
    proxied = set(re.findall(r'"/([a-z-]+)":\s*DEV_API', vite))
    missing = _api_prefixes() - proxied
    assert not missing, f"frontend/vite.config.ts does not proxy: {sorted(missing)}"
