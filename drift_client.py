import socket
import json
import uuid
import time
import threading

BROADCAST_PORT = 50000
LISTEN_PORT = 50001


class DriftClient:
    def __init__(self, broadcast_port=BROADCAST_PORT, listen_port=LISTEN_PORT):
        self.broadcast_port = broadcast_port
        self.client_id = str(uuid.uuid4())[:8]

        self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.recv_sock.bind(("", self.broadcast_port))

    def _listen(self):
        while True:
            try:
                data, _ = self.recv_sock.recvfrom(4096)
                msg = json.loads(data.decode())
                if msg.get("type") == "start_job":
                    print(f"\n✅ [UPDATE] Node {msg.get('node_id')} won job {msg.get('job_id')}")
                    print(f"   VRAM: {msg.get('vram')}GB | Score: {msg.get('score'):.2f}")
                    print("\nEnter command (1: LLM, 2: TTS, 3: Exit): ", end="", flush=True)
            except Exception:
                pass

    def send_job(self, task_type: str, payload: dict):
        job_id = str(uuid.uuid4())[:8]
        msg = {
            "type": "job",
            "job_id": job_id,
            "task_type": task_type,
            "payload": payload,
            "client_id": self.client_id,
        }
        print(f"\n[Client-{self.client_id}] Broadcasting {task_type.upper()} job (ID: {job_id})...")
        self.send_sock.sendto(json.dumps(msg).encode(), ("<broadcast>", self.broadcast_port))
        return job_id

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()
        time.sleep(0.3)

        print("=== DRIFT CLIENT ===")
        print(f"ID: {self.client_id}")
        print("=" * 20)

        while True:
            try:
                cmd = input("\nEnter command (1: LLM, 2: TTS, 3: Exit): ").strip()
                if cmd == "1":
                    self.send_job("llm", {"prompt": "Write a poem about space."})
                elif cmd == "2":
                    self.send_job("tts", {"text": "Hello from the DRIFT network."})
                elif cmd == "3":
                    break
            except KeyboardInterrupt:
                break

        print(f"\n[Client-{self.client_id}] Disconnected.")


if __name__ == "__main__":
    DriftClient().start()