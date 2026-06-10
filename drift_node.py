import socket
import threading
import json
import time
import uuid
import psutil
import argparse

try:
    import GPUtil
    HAS_GPUTIL = True
except ImportError:
    HAS_GPUTIL = False

BROADCAST_PORT = 50000
BIDDING_WINDOW = 2.0
JOB_TTL = 30.0
ANNOUNCE_INTERVAL = 5.0
CAPABILITY_MATCH_BONUS = 1.5


class DriftNode:
    def __init__(self, capabilities=None, port=BROADCAST_PORT):
        self.node_id = str(uuid.uuid4())[:8]
        self.port = port
        self.capabilities = capabilities if capabilities else ["*"]
        self.is_sdnode = "*" in self.capabilities
        self.hive = {}
        self.active_jobs = {}
        self.lock = threading.Lock()

        self._detect_hardware()

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", self.port))

    def _detect_hardware(self):
        self.cpu_cores = psutil.cpu_count(logical=False)
        self.total_ram_gb = psutil.virtual_memory().total / (1024 ** 3)

        if HAS_GPUTIL:
            gpus = GPUtil.getGPUs()
            if gpus:
                self.vram_gb = gpus[0].memoryTotal / 1024
                self.gpu_name = gpus[0].name
            else:
                self.vram_gb = 0
                self.gpu_name = "CPU Only"
        else:
            self.vram_gb = 8.0
            self.gpu_name = "Simulated GPU"

    def _compute_score(self, task_type=None):
        cpu_load = psutil.cpu_percent()
        ram_avail = psutil.virtual_memory().available / (1024 ** 3)
        score = (self.vram_gb * 10) + (ram_avail * 2) - cpu_load

        if task_type and task_type in self.capabilities:
            score *= CAPABILITY_MATCH_BONUS

        return max(0.1, score)

    def _broadcast(self, msg: dict):
        try:
            self.sock.sendto(json.dumps(msg).encode(), ("<broadcast>", self.port))
        except Exception:
            pass

    def _schedule_job_cleanup(self, job_id):
        def cleanup():
            time.sleep(JOB_TTL)
            with self.lock:
                self.active_jobs.pop(job_id, None)
        threading.Thread(target=cleanup, daemon=True).start()

    def _listen(self):
        print(f"[*] Listening on port {self.port}...")
        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
                msg = json.loads(data.decode())
                sender = msg.get("node_id") or msg.get("client_id")
                if sender == self.node_id:
                    continue
                mtype = msg.get("type")
                if mtype == "discovery":
                    self._on_discovery(msg, addr)
                elif mtype == "job":
                    self._on_job(msg)
                elif mtype == "bid":
                    self._on_bid(msg)
            except Exception:
                pass

    def _on_discovery(self, msg, addr):
        sender_id = msg.get("node_id")
        caps = msg.get("capabilities", [])
        with self.lock:
            is_new = sender_id not in self.hive
            self.hive[sender_id] = {
                "ip": addr[0],
                "capabilities": caps,
                "vram": msg.get("vram"),
                "last_seen": time.time(),
            }
        if is_new:
            label = "SDNODE" if "*" in caps else f"DNODE {caps}"
            print(f"\n👋 [DISCOVERY] {label} joined — ID: {sender_id}, VRAM: {msg.get('vram')}GB")

    def _on_job(self, msg):
        job_id = msg.get("job_id")
        task_type = msg.get("task_type")

        can_handle = task_type in self.capabilities or self.is_sdnode

        if not can_handle:
            print(f"\n❌ [JOB] {job_id} ({task_type.upper()}) — capability mismatch, skipping.")
            return

        with self.lock:
            if job_id in self.active_jobs:
                return
            self.active_jobs[job_id] = {"bids": {}, "status": "bidding", "task_type": task_type}

        self._schedule_job_cleanup(job_id)

        score = self._compute_score(task_type)
        print(f"\n📩 [JOB] {job_id} ({task_type.upper()}) received — score: {score:.2f}")

        self._broadcast({
            "type": "bid",
            "job_id": job_id,
            "node_id": self.node_id,
            "score": score,
            "vram": self.vram_gb,
        })

        threading.Thread(target=self._resolve, args=(job_id, score), daemon=True).start()

    def _on_bid(self, msg):
        job_id = msg.get("job_id")
        with self.lock:
            if job_id not in self.active_jobs:
                return
            if self.active_jobs[job_id]["status"] != "bidding":
                return
            self.active_jobs[job_id]["bids"][msg.get("node_id")] = msg.get("score")
        print(f"   ↳ Bid from {msg.get('node_id')}: {msg.get('score'):.2f}")

    def _resolve(self, job_id, my_score):
        time.sleep(BIDDING_WINDOW)

        with self.lock:
            if job_id not in self.active_jobs:
                return
            job = self.active_jobs[job_id]
            if job["status"] != "bidding":
                return
            job["bids"][self.node_id] = my_score
            bids = dict(job["bids"])

        print(f"\n⚖️  [CONSENSUS] Job {job_id} — {len(bids)} bid(s): {bids}")
        winner_id = max(bids, key=bids.get)

        if winner_id != self.node_id:
            print(f"😞 Lost to {winner_id} ({bids[winner_id]:.2f}). Standing by.")
            with self.lock:
                if job_id in self.active_jobs:
                    self.active_jobs[job_id]["status"] = "lost"
            return

        with self.lock:
            if job_id not in self.active_jobs:
                return
            self.active_jobs[job_id]["status"] = "running"

        print(f"🎉 [WON] Job {job_id} — executing in VRAM...")

        self._broadcast({
            "type": "start_job",
            "job_id": job_id,
            "node_id": self.node_id,
            "score": my_score,
            "vram": self.vram_gb,
        })

        time.sleep(3)
        print(f"✅ [DONE] Job {job_id} completed.")

        with self.lock:
            if job_id in self.active_jobs:
                self.active_jobs[job_id]["status"] = "done"

    def _announce(self):
        while True:
            self._broadcast({
                "type": "discovery",
                "node_id": self.node_id,
                "capabilities": self.capabilities,
                "vram": self.vram_gb,
            })
            time.sleep(ANNOUNCE_INTERVAL)

    def start(self):
        label = "SDNODE (Joker)" if self.is_sdnode else f"DNODE {self.capabilities}"
        print(f"\n=== DRIFT {label} ===")
        print(f"ID       : {self.node_id}")
        print(f"GPU      : {self.gpu_name} ({self.vram_gb}GB VRAM)")
        print(f"RAM      : {self.total_ram_gb:.1f}GB | Cores: {self.cpu_cores}")
        print(f"DCaps    : {self.capabilities}")
        print("=" * 36)

        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._announce, daemon=True).start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print(f"\n[{self.node_id}] Shutting down.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start a DRIFT Node")
    parser.add_argument("--caps", type=str, help="Comma-separated DCaps (e.g. llm,tts). Omit for SDNODE.")
    args = parser.parse_args()
    caps = args.caps.split(",") if args.caps else ["*"]
    DriftNode(capabilities=caps).start()