"""CertWatch SSL Agent — runs inside private network, pushes results outbound."""

from __future__ import annotations

import logging
import os
import time

import yaml

from probe import is_private_host, probe
from reporter import push

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("agent")


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run_once(config: dict) -> None:
    agent_id = config["agent_id"]
    api_url = config["api_url"].rstrip("/")
    api_key = config["api_key"]
    timeout = config.get("tls_timeout", 10.0)

    for entry in config["domains"]:
        host = entry["host"]
        port = entry.get("port", 443)
        private = is_private_host(host)
        logger.info("probing %s:%s (private=%s)", host, port, private)
        result = probe(host, port, timeout)
        push(result, api_url, agent_id, api_key)


def main() -> None:
    config_path = os.environ.get("AGENT_CONFIG", "config.yaml")
    config = load_config(config_path)
    interval = config.get("interval_seconds", 300)

    logger.info(
        "CertWatch Agent starting | id=%s | %d domain(s) | interval=%ss",
        config["agent_id"],
        len(config["domains"]),
        interval,
    )

    while True:
        try:
            run_once(config)
        except Exception as e:
            logger.error("run failed: %s", e)
        time.sleep(interval)


if __name__ == "__main__":
    main()
