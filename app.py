import os, re, sqlite3, subprocess, time
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request

BASE = Path(__file__).resolve().parent
DB = BASE / "router_history.db"
HOST = os.getenv("ROUTER_HOST", "192.168.0.1")
COMMUNITY = os.getenv("SNMP_COMMUNITY", "")
VERSION = os.getenv("SNMP_VERSION", "2c")
INTERFACE = int(os.getenv("WAN_IFINDEX", "4"))
POLL = max(5, int(os.getenv("POLL_SECONDS", "10")))
ARP_OID = "1.3.6.1.2.1.4.22"
app = Flask(__name__)
previous = {"t": None, "in": None, "out": None}


def connect_db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with connect_db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS samples(
            id INTEGER PRIMARY KEY, ts TEXT, uptime REAL, download REAL, upload REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS device_names(
            mac TEXT PRIMARY KEY, name TEXT NOT NULL)""")


def snmp_command(base, oid):
    if not COMMUNITY:
        raise RuntimeError("Set SNMP_COMMUNITY in your .env file.")
    cmd = [base, "-v" + VERSION, "-c", COMMUNITY, "-On", "-t", "2", "-r", "1", HOST, oid]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    if p.returncode and not p.stdout.strip():
        raise RuntimeError(p.stderr.strip() or "SNMP query failed")
    return p.stdout


def get_oid(oid):
    output = snmp_command("snmpget", oid)
    if "No Such" in output or "No more variables" in output:
        raise RuntimeError(output.strip())
    # Net-SNMP prints typed values, e.g. "Counter32: 1687600668",
    # "Gauge32: 123", "INTEGER: 6", or "Timeticks: (64779200) ...".
    m = re.search(r"Timeticks:\s*\((-?\d+)\)", output)
    if not m:
        m = re.search(r"(?:Counter32|Counter64|Gauge32|Gauge|INTEGER|INTEGER32|Unsigned32):\s*(-?\d+)", output)
    if not m:
        raise RuntimeError("Unexpected SNMP response: " + output.strip())
    return int(m.group(1))


def collect_wan():
    global previous
    uptime = get_oid("1.3.6.1.2.1.1.3.0") / 100
    incoming = get_oid(f"1.3.6.1.2.1.2.2.1.10.{INTERFACE}")
    outgoing = get_oid(f"1.3.6.1.2.1.2.2.1.16.{INTERFACE}")
    now = time.time()
    down = up = 0.0
    if previous["t"] is not None:
        elapsed = now - previous["t"]
        if elapsed > 0:
            down = ((incoming - previous["in"]) % (2**32)) * 8 / elapsed / 1e6
            up = ((outgoing - previous["out"]) % (2**32)) * 8 / elapsed / 1e6
    previous = {"t": now, "in": incoming, "out": outgoing}
    sample = {"timestamp": datetime.now(timezone.utc).isoformat(), "uptime": uptime,
              "download": round(down, 3), "upload": round(up, 3)}
    with connect_db() as c:
        c.execute("INSERT INTO samples(ts,uptime,download,upload) VALUES(?,?,?,?)",
                  (sample["timestamp"], uptime, sample["download"], sample["upload"]))
    return sample


def collect_devices():
    """Read the standard IP-MIB ipNetToMediaTable (ARP cache)."""
    output = snmp_command("snmpwalk", ARP_OID)
    # OIDs: ...1.1.<ifIndex>.<IPv4>, ...1.2.<ifIndex>.<IPv4>, ...1.3..., ...1.4...
    entries = {}
    for line in output.splitlines():
        m = re.match(r"\.?(?:1\.3\.6\.1\.2\.1\.4\.22\.1)\.(\d+)\.(\d+)\.(\d+)\.(\d+)\.(\d+)\.(\d+)\s*=\s*(.*)", line.strip())
        if not m:
            continue
        column, ifindex = int(m.group(1)), int(m.group(2))
        ip = ".".join(m.group(i) for i in range(3, 7))
        value = m.group(7).strip()
        item = entries.setdefault((ifindex, ip), {"ip": ip, "ifindex": ifindex, "mac": None, "type": None})
        if column == 2:
            hx = re.search(r"Hex-STRING:\s*((?:[0-9A-Fa-f]{2}\s*)+)", value)
            if hx:
                item["mac"] = ":".join(hx.group(1).split()).upper()
        elif column == 4:
            n = re.search(r"(-?\d+)", value)
            if n:
                item["type"] = int(n.group(1))
    with connect_db() as c:
        names = {r["mac"]: r["name"] for r in c.execute("SELECT mac,name FROM device_names")}
    devices = []
    for item in entries.values():
        if not item["mac"]:
            continue
        item["name"] = names.get(item["mac"], "")
        item["entry_type"] = {1: "Other", 2: "Invalid", 3: "Dynamic", 4: "Static", 5: "Local"}.get(item["type"], "Unknown")
        # ARP cache entries identify neighbors, not proof of active traffic.
        devices.append(item)
    devices.sort(key=lambda x: (x["ifindex"], x["ip"]))
    return devices


@app.route("/")
def home():
    return render_template("index.html", host=HOST, poll=POLL)


@app.route("/api/status")
def status():
    try:
        sample = collect_wan()
        return jsonify(ok=True, sample=sample, host=HOST, interface=f"ppp0 (ifIndex {INTERFACE})")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 503


@app.route("/api/devices")
def devices():
    try:
        return jsonify(ok=True, devices=collect_devices(), note="ARP cache discovery only. This router has not yet exposed per-device byte counters through the SNMP tables tested.")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 503


@app.route("/api/device-name", methods=["POST"])
def save_device_name():
    data = request.get_json(silent=True) or {}
    mac = str(data.get("mac", "")).upper().strip()
    name = str(data.get("name", "")).strip()[:40]
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
        return jsonify(ok=False, error="Invalid MAC address"), 400
    with connect_db() as c:
        if name:
            c.execute("INSERT INTO device_names(mac,name) VALUES(?,?) ON CONFLICT(mac) DO UPDATE SET name=excluded.name", (mac, name))
        else:
            c.execute("DELETE FROM device_names WHERE mac=?", (mac,))
    return jsonify(ok=True)


@app.route("/api/history")
def history():
    with connect_db() as c:
        rows = c.execute("SELECT ts,download,upload FROM samples ORDER BY id DESC LIMIT 60").fetchall()
    return jsonify([dict(r) for r in reversed(rows)])


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
