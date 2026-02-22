"""Traffic generator — sends realistic traffic to simulated microservices
and periodically injects anomalies (latency spikes, error bursts, resource surges).

Runs as a long-lived process inside Docker. Controlled via env vars:
- SERVICES: comma-separated list of service base URLs
- TRAFFIC_INTERVAL: seconds between normal traffic bursts (default: 5)
- ANOMALY_INTERVAL: seconds between anomaly injections (default: 120)
- ANOMALY_DURATION: how long each anomaly lasts in seconds (default: 60)
"""

import os
import sys
import time
import random
import logging
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("traffic-gen")

# ── Configuration ────────────────────────────────────────────────────

SERVICES = [s.strip() for s in os.getenv("SERVICES", "").split(",") if s.strip()]
TRAFFIC_INTERVAL = float(os.getenv("TRAFFIC_INTERVAL", "5"))
ANOMALY_INTERVAL = float(os.getenv("ANOMALY_INTERVAL", "120"))
ANOMALY_DURATION = float(os.getenv("ANOMALY_DURATION", "60"))

if not SERVICES:
    logger.error("No SERVICES configured. Set SERVICES env var.")
    sys.exit(1)


# ── Anomaly scenarios ────────────────────────────────────────────────

ANOMALY_SCENARIOS = [
    {
        "name": "Latency Spike",
        "description": "Sudden p95 latency increase — simulates downstream timeout or DB lock contention",
        "params": {"latency_spike_ms": 800, "error_probability": -1},
    },
    {
        "name": "Error Burst",
        "description": "Spike in 5xx errors — simulates a bad deploy or dependency failure",
        "params": {"error_probability": 0.35, "latency_spike_ms": 100},
    },
    {
        "name": "CPU Surge",
        "description": "CPU goes to 85%+ — simulates runaway process or crypto-mining",
        "params": {"cpu_base": 88, "latency_spike_ms": 200},
    },
    {
        "name": "Memory Leak",
        "description": "Memory climbs to 900MB+ — simulates a memory leak",
        "params": {"memory_base": 920, "latency_spike_ms": 50},
    },
    {
        "name": "Cascading Failure",
        "description": "High latency + high errors — simulates cascading service failure",
        "params": {"latency_spike_ms": 1500, "error_probability": 0.5, "cpu_base": 75},
    },
    {
        "name": "Gradual Degradation",
        "description": "Moderate latency increase + slight error uptick — simulates slow leak",
        "params": {"latency_spike_ms": 300, "error_probability": 0.08},
    },
]


def send_normal_traffic(client: httpx.Client):
    """Send a burst of normal traffic to all services."""
    for service_url in SERVICES:
        try:
            count = random.randint(5, 20)
            resp = client.post(
                f"{service_url}/simulate",
                params={"count": count},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                svc_name = service_url.split("//")[1].split(":")[0]
                logger.debug(
                    f"  {svc_name}: {data['processed']} reqs, "
                    f"{data['errors']} errors, "
                    f"{data['avg_latency_ms']:.1f}ms avg"
                )
        except Exception as e:
            logger.warning(f"Traffic to {service_url} failed: {e}")


def inject_anomaly(client: httpx.Client):
    """Pick a random service and inject a random anomaly."""
    target = random.choice(SERVICES)
    scenario = random.choice(ANOMALY_SCENARIOS)
    svc_name = target.split("//")[1].split(":")[0]

    logger.warning(
        f"🔥 INJECTING ANOMALY: '{scenario['name']}' on {svc_name} "
        f"for {ANOMALY_DURATION}s — {scenario['description']}"
    )

    try:
        params = {**scenario["params"], "duration_seconds": ANOMALY_DURATION}
        resp = client.post(
            f"{target}/inject-anomaly",
            params=params,
            timeout=10.0,
        )
        if resp.status_code == 200:
            logger.info(f"  Anomaly injected: {resp.json()}")
        else:
            logger.error(f"  Anomaly injection failed: {resp.status_code}")
    except Exception as e:
        logger.error(f"  Anomaly injection error: {e}")


def main():
    logger.info(f"Traffic Generator starting")
    logger.info(f"  Services: {SERVICES}")
    logger.info(f"  Traffic interval: {TRAFFIC_INTERVAL}s")
    logger.info(f"  Anomaly interval: ~{ANOMALY_INTERVAL}s (±30%)")
    logger.info(f"  Anomaly duration: {ANOMALY_DURATION}s")

    # Wait for services to come up
    logger.info("Waiting 10s for services to start...")
    time.sleep(10)

    client = httpx.Client()
    last_anomaly_time = time.time()
    # Randomize first anomaly (don't inject immediately)
    next_anomaly_in = ANOMALY_INTERVAL * random.uniform(0.5, 1.0)
    cycle = 0

    try:
        while True:
            cycle += 1

            # Normal traffic
            send_normal_traffic(client)

            # Check if it's time for an anomaly
            elapsed = time.time() - last_anomaly_time
            if elapsed >= next_anomaly_in:
                inject_anomaly(client)
                last_anomaly_time = time.time()
                # Randomize next anomaly interval ±30%
                next_anomaly_in = ANOMALY_INTERVAL * random.uniform(0.7, 1.3)

            # Log progress every 20 cycles
            if cycle % 20 == 0:
                until_anomaly = max(0, next_anomaly_in - (time.time() - last_anomaly_time))
                logger.info(
                    f"[cycle {cycle}] Normal traffic OK — "
                    f"next anomaly in ~{until_anomaly:.0f}s"
                )

            time.sleep(TRAFFIC_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Traffic generator stopped")
    finally:
        client.close()


if __name__ == "__main__":
    main()
