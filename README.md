# Home Network Monitor

A lightweight, on-demand local Flask dashboard for the TP-Link router.

## Setup
1. Extract this folder.
2. Run `cp .env.example .env` only if `.env` does not already exist.
3. Edit `.env` and set `SNMP_COMMUNITY` to your **read-only** SNMP community.
4. Run `chmod +x run.sh && ./run.sh`.
5. Open `http://127.0.0.1:5000`.
6. Stop with `Ctrl+C`.

The dashboard shows WAN rates and known LAN neighbors from the standard SNMP ARP table. You can assign friendly names to MAC addresses; names are saved in SQLite.

**Per-device bandwidth/data usage is not included yet.** The standard ARP table only provides IP/MAC/interface information. The SNMP enterprise subtree tested on this TL-WR850N returned no objects, so per-device counters need to be discovered from the router's Statistics page/API or another exposed MIB. The app deliberately does not invent per-device rates or totals.

The app binds to localhost only. Keep SNMP WAN access disabled and use only the read-only community string. The WAN rate calculation uses 32-bit interface counters and is intended for live polling.
