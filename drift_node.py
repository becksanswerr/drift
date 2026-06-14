import socket
import threading
import json
import time
import uuid
import os
import sys
import random
import psutil

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from rich.live import Live
from rich.table import Table

# For non-blocking input
if os.name == 'nt':
    import msvcrt
else:
    import select
    import tty
    import termios

console = Console()

DRIFT_BANNER = """
 ██████╗  ██████╗ ██╗███████╗████████╗
 ██╔══██╗██╔══██╗██║██╔════╝╚══██╔══╝
 ██║  ██║██████╔╝██║█████╗     ██║   
 ██║  ██║██╔══██╗██║██╔══╝     ██║   
 ██████╔╝██║  ██║██║██║        ██║   
 ╚═════╝ ╚═╝  ╚═╝╚═╝╚═╝        ╚═╝   
"""

class DriftNode:
    def __init__(self, port=50000):
        self.node_id = str(uuid.uuid4())[:8]
        self.port = port
        self.peers = {} # {node_id: {"ip": ip, "last_seen": timestamp}}
        self.tasks = [] # List of task strings for logs
        self.active_elections = {} # {task_id: {"bids": {node_id: score}, "start_time": time.time()}}
        self.running = True
        self.input_buffer = ""
        
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.udp_socket.bind(('', self.port))
        
    def add_log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.tasks.append(f"[{timestamp}] {message}")
        if len(self.tasks) > 15: # Keep log size manageable
            self.tasks.pop(0)

    def get_header(self):
        text = Text(DRIFT_BANNER, style="bold cyan")
        subtitle = Text(f"Serverless P2P Node Network - ID: {self.node_id}", style="yellow")
        return Panel(text, subtitle=subtitle, expand=True, border_style="blue")

    def get_peer_table(self):
        table = Table(title="Connected Peers", style="green", expand=True)
        table.add_column("Node ID", style="cyan")
        table.add_column("IP Address", style="magenta")
        table.add_column("Status", style="green")
        
        current_time = time.time()
        for p_id, info in list(self.peers.items()):
            if current_time - info["last_seen"] > 10:
                del self.peers[p_id]
                self.add_log(f"[bold red]Peer Lost:[/bold red] {p_id}")
                continue
            table.add_row(p_id, info["ip"], "Online")
            
        if not self.peers:
            table.add_row("No peers found", "-", "-")
            
        return Panel(table, title="Network", border_style="green")

    def get_logs_panel(self):
        log_text = Text.from_markup("\n".join(self.tasks))
        return Panel(log_text, title="Task & Activity Logs", border_style="yellow")

    def get_input_panel(self):
        # Blinking cursor effect
        cursor = "█" if int(time.time() * 2) % 2 == 0 else " "
        text = Text(f"> {self.input_buffer}{cursor}", style="bold white")
        return Panel(text, title="Input (Type 'task <desc>', 'mock' for random tasks, 'quit' to exit)", border_style="magenta")

    def calculate_qualify_score(self, difficulty, duration_sec):
        cpu_usage = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        ram_avail_gb = ram.available / (1024**3)
        
        score = 100 - cpu_usage
        if difficulty > 7 and ram_avail_gb < 4.0:
            score -= 40
        elif difficulty < 4:
            score += 10
            
        return max(0, min(100, int(score)))

    def generate_layout(self):
        layout = Layout(name="root")
        layout.split(
            Layout(name="header", size=9),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3)
        )
        layout["main"].split_row(
            Layout(name="peers", ratio=1),
            Layout(name="logs", ratio=2)
        )
        
        layout["header"].update(self.get_header())
        layout["peers"].update(self.get_peer_table())
        layout["logs"].update(self.get_logs_panel())
        layout["footer"].update(self.get_input_panel())
        
        return layout

    def listen_for_broadcasts(self):
        while self.running:
            try:
                self.udp_socket.settimeout(1.0) # non-blocking so thread can exit
                data, addr = self.udp_socket.recvfrom(2048)
                message = json.loads(data.decode('utf-8'))
                msg_type = message.get("type")
                sender_id = message.get("node_id")
                
                if sender_id == self.node_id:
                    continue # Ignore our own messages
                    
                if msg_type == "discovery":
                    if sender_id not in self.peers:
                        self.add_log(f"[bold green]New Peer:[/bold green] {sender_id} at {addr[0]}")
                    self.peers[sender_id] = {
                        "ip": addr[0],
                        "last_seen": time.time()
                    }
                elif msg_type == "new_task":
                    task_data = message.get("task_data")
                    task_id = task_data.get("id")
                    task_desc = task_data.get("desc")
                    diff = task_data.get("difficulty")
                    self.add_log(f"[bold cyan]Task Received:[/bold cyan] [{task_id}] {task_desc} (Diff: {diff})")
                    
                    # Calculate and show qualify score
                    score = self.calculate_qualify_score(diff, task_data.get("duration_sec"))
                    self.add_log(f"[bold magenta]Qualify Score:[/bold magenta] {score}% for [{task_id}]")
                    self.send_bid(task_id, score)
                    
                elif msg_type == "task_bid":
                    bid_task_id = message.get("task_id")
                    bid_score = message.get("score")
                    self.add_log(f"[bold yellow]Bid Received:[/bold yellow] Node {sender_id} bid {bid_score}% for [{bid_task_id}]")
                    if bid_task_id in self.active_elections:
                        self.active_elections[bid_task_id]["bids"][sender_id] = bid_score
                        
                elif msg_type == "election_result":
                    res_task_id = message.get("task_id")
                    res_winner_id = message.get("winner_id")
                    if res_winner_id == self.node_id:
                        self.add_log(f"[bold green]🏆 I WON task [{res_task_id}]![/bold green] Executing...")
                    else:
                        self.add_log(f"[dim]Node {res_winner_id} won task [{res_task_id}][/dim]")
                    
            except socket.timeout:
                pass
            except Exception as e:
                if self.running:
                    pass

    def broadcast_presence(self):
        while self.running:
            message = {
                "type": "discovery",
                "node_id": self.node_id
            }
            try:
                self.udp_socket.sendto(json.dumps(message).encode('utf-8'), ('<broadcast>', self.port))
            except Exception as e:
                pass
            time.sleep(2)

    def broadcast_task(self, task_data):
        message = {
            "type": "new_task",
            "node_id": self.node_id,
            "task_data": task_data
        }
        try:
            self.udp_socket.sendto(json.dumps(message).encode('utf-8'), ('<broadcast>', self.port))
            self.add_log(f"[bold blue]Task Sent:[/bold blue] [{task_data['id']}] {task_data['desc']}")
            self.active_elections[task_data["id"]] = {
                "bids": {},
                "start_time": time.time()
            }
        except Exception as e:
            self.add_log(f"[bold red]Error sending task:[/bold red] {e}")

    def send_bid(self, task_id, score):
        message = {
            "type": "task_bid",
            "node_id": self.node_id,
            "task_id": task_id,
            "score": score
        }
        try:
            self.udp_socket.sendto(json.dumps(message).encode('utf-8'), ('<broadcast>', self.port))
        except Exception as e:
            pass

    def broadcast_election_result(self, task_id, winner_id):
        message = {
            "type": "election_result",
            "node_id": self.node_id,
            "task_id": task_id,
            "winner_id": winner_id
        }
        try:
            self.udp_socket.sendto(json.dumps(message).encode('utf-8'), ('<broadcast>', self.port))
        except:
            pass

    def manage_elections(self):
        while self.running:
            current_time = time.time()
            for task_id, election in list(self.active_elections.items()):
                if current_time - election["start_time"] > 3.0: # 3 second bidding window
                    bids = election["bids"]
                    if not bids:
                        self.add_log(f"[bold red]No bids for task [{task_id}]. Task failed.[/bold red]")
                    else:
                        winner_id = max(bids, key=bids.get)
                        highest_score = bids[winner_id]
                        self.add_log(f"[bold magenta]Election finished for [{task_id}][/bold magenta]. Winner: {winner_id} ({highest_score}%)")
                        self.broadcast_election_result(task_id, winner_id)
                    del self.active_elections[task_id]
            time.sleep(0.5)

    def handle_command(self, cmd):
        cmd = cmd.strip()
        if not cmd: return
        
        if cmd.lower() == "quit" or cmd.lower() == "exit":
            self.running = False
        elif cmd.lower() == "mock":
            mock_tasks = [
                {"desc": "Process Video Frame", "difficulty": random.randint(5, 10), "duration_sec": 30, "urgency": "High"},
                {"desc": "Train Mini Neural Net", "difficulty": random.randint(7, 10), "duration_sec": 120, "urgency": "Normal"},
                {"desc": "Scrape Website Data", "difficulty": random.randint(1, 4), "duration_sec": 10, "urgency": "Low"},
                {"desc": "Render 3D Object", "difficulty": random.randint(6, 9), "duration_sec": 45, "urgency": "High"}
            ]
            t = random.choice(mock_tasks)
            task_data = {
                "id": str(uuid.uuid4())[:6],
                "desc": t["desc"],
                "difficulty": t["difficulty"], # 1-10
                "duration_sec": t["duration_sec"],
                "urgency": t["urgency"]
            }
            self.broadcast_task(task_data)
        elif cmd.lower().startswith("task "):
            task_desc = cmd[5:].strip()
            task_data = {
                "id": str(uuid.uuid4())[:6],
                "desc": task_desc,
                "difficulty": random.randint(1, 5), # Default simple difficulty
                "duration_sec": 10,
                "urgency": "Normal"
            }
            self.broadcast_task(task_data)
        else:
            self.add_log(f"[yellow]Unknown command:[/yellow] {cmd}")

    def input_loop(self):
        if os.name == 'nt':
            while self.running:
                if msvcrt.kbhit():
                    char = msvcrt.getwche()
                    if char in ('\r', '\n'):
                        self.handle_command(self.input_buffer)
                        self.input_buffer = ""
                    elif char == '\x08': # backspace
                        self.input_buffer = self.input_buffer[:-1]
                    elif char == '\x03': # Ctrl+C
                        self.running = False
                    else:
                        self.input_buffer += char
                time.sleep(0.05)
        else:
            # POSIX non-blocking input for Ubuntu/Linux
            old_settings = termios.tcgetattr(sys.stdin)
            try:
                tty.setcbreak(sys.stdin.fileno())
                while self.running:
                    if select.select([sys.stdin], [], [], 0)[0]:
                        char = sys.stdin.read(1)
                        if char in ('\r', '\n'):
                            self.handle_command(self.input_buffer)
                            self.input_buffer = ""
                        elif char in ('\x08', '\x7f'): # backspace variants
                            self.input_buffer = self.input_buffer[:-1]
                        elif char == '\x03': # Ctrl+C
                            self.running = False
                        else:
                            self.input_buffer += char
                    time.sleep(0.05)
            finally:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    def start(self):
        os.system("cls" if os.name == "nt" else "clear")
        self.add_log("Node started. Listening for peers...")
        
        listener_thread = threading.Thread(target=self.listen_for_broadcasts, daemon=True)
        broadcaster_thread = threading.Thread(target=self.broadcast_presence, daemon=True)
        election_thread = threading.Thread(target=self.manage_elections, daemon=True)
        input_thread = threading.Thread(target=self.input_loop, daemon=True)
        
        listener_thread.start()
        broadcaster_thread.start()
        election_thread.start()
        input_thread.start()
        
        try:
            with Live(self.generate_layout(), refresh_per_second=10, screen=True) as live:
                while self.running:
                    time.sleep(0.1)
                    live.update(self.generate_layout())
        except KeyboardInterrupt:
            self.running = False

        self.udp_socket.close()
        os.system("cls" if os.name == "nt" else "clear")
        console.print("[bold red]DRIFT Node shut down successfully.[/bold red]")

if __name__ == "__main__":
    node = DriftNode()
    node.start()
