import socket
import threading
import json
import time
import uuid
import os
import sys
import random
import argparse
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

from strategies import DefaultStrategy, LLMStrategy

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
    def __init__(self, port=50000, name=None):
        self.node_id = str(uuid.uuid4())[:8]
        self.node_name = name or self.node_id
        self.port = port
        self.peers = {} # {node_id: {"ip": ip, "name": name, "last_seen": timestamp}}
        self.tasks = [] # List of task strings for logs
        self.active_elections = {} # {task_id: {"bids": {node_id: score}, "start_time": time.time()}}
        self.known_tasks = {} # {task_id: task_data} - Cache for tasks we have seen
        self.running = True
        self.input_buffer = ""
        self.strategy = None # Set externally
        
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
        s_name = self.strategy.get_name() if self.strategy else "None"
        subtitle = Text(f"Serverless P2P Node Network - Name: {self.node_name} (ID: {self.node_id}) | Strategy: {s_name}", style="yellow")
        return Panel(text, subtitle=subtitle, expand=True, border_style="blue")

    def get_peer_table(self):
        table = Table(title="Connected Peers", style="green", expand=True)
        table.add_column("Node Name/ID", style="cyan")
        table.add_column("IP Address", style="magenta")
        table.add_column("Status", style="green")
        
        current_time = time.time()
        for p_id, info in list(self.peers.items()):
            if current_time - info["last_seen"] > 10:
                del self.peers[p_id]
                self.add_log(f"[bold red]Peer Lost:[/bold red] {info.get('name', p_id)}")
                continue
            table.add_row(info.get("name", p_id), info["ip"], "Online")
            
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
        return Panel(text, title="Input (Type 'task <desc>', 'taskllm <model> <prompt>', 'mock'/'mockllm', 'quit')", border_style="magenta")

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
                self.udp_socket.settimeout(1.0)
                data, addr = self.udp_socket.recvfrom(2048)
                message = json.loads(data.decode('utf-8'))
                msg_type = message.get("type")
                sender_id = message.get("node_id")
                
                if sender_id == self.node_id:
                    continue # Ignore our own messages
                    
                elif msg_type == "task_result":
                    res_task_id = message.get("task_id")
                    requester = message.get("requester_id")
                    result_text = message.get("result")
                    
                    if requester == self.node_id:
                        self.add_log(f"[bold magenta]🎯 Cevap Geldi (ID:{res_task_id}):[/bold magenta] {result_text}")
                        # In the future, we could save this to a file or trigger an event.
                        
                elif msg_type == "discovery":
                    if sender_id not in self.peers:
                        sender_name = message.get("node_name", sender_id)
                        self.add_log(f"[bold green]New Peer:[/bold green] {sender_name} at {addr[0]}")
                    self.peers[sender_id] = {
                        "name": message.get("node_name", sender_id),
                        "ip": addr[0],
                        "last_seen": time.time()
                    }
                elif msg_type == "new_task":
                    task_data = message.get("task_data")
                    task_id = task_data.get("id")
                    task_desc = task_data.get("desc")
                    
                    self.known_tasks[task_id] = task_data # Cache it
                    
                    self.add_log(f"[bold cyan]Task Received:[/bold cyan] ID:{task_id} - {task_desc}")
                    
                    if self.strategy:
                        score = self.strategy.calculate_score(task_data)
                        self.add_log(f"[bold magenta]Qualify Score:[/bold magenta] {score}% for ID:{task_id}")
                        self.send_bid(task_id, score)
                    
                elif msg_type == "task_bid":
                    bid_task_id = message.get("task_id")
                    bid_score = message.get("score")
                    self.add_log(f"[bold yellow]Bid Received:[/bold yellow] Node {sender_id} bid {bid_score}% for ID:{bid_task_id}")
                    if bid_task_id in self.active_elections:
                        self.active_elections[bid_task_id]["bids"][sender_id] = bid_score
                        
                elif msg_type == "election_result":
                    res_task_id = message.get("task_id")
                    res_winner_id = message.get("winner_id")
                    if res_winner_id == self.node_id:
                        self.add_log(f"[bold green]🏆 I WON task ID:{res_task_id}![/bold green] Executing...")
                        task_data = self.known_tasks.get(res_task_id, {})
                        if self.strategy:
                            res = self.strategy.on_task_won(task_data)
                            if res:
                                self.broadcast_task_result(res_task_id, task_data.get("requester_id"), res)
                    else:
                        self.add_log(f"[dim]Node {res_winner_id} won task ID:{res_task_id}[/dim]")
                    
            except socket.timeout:
                pass
            except Exception as e:
                pass

    def broadcast_presence(self):
        while self.running:
            message = {
                "type": "discovery",
                "node_id": self.node_id,
                "node_name": self.node_name
            }
            try:
                self.udp_socket.sendto(json.dumps(message).encode('utf-8'), ('<broadcast>', self.port))
            except Exception:
                pass
            time.sleep(2)

    def broadcast_task(self, task_data):
        task_id = task_data["id"]
        # Include requester_id so the winner knows where to send the result
        task_data["requester_id"] = self.node_id
        
        message = {
            "type": "new_task",
            "node_id": self.node_id,
            "task_data": task_data
        }
        try:
            self.known_tasks[task_id] = task_data
            self.udp_socket.sendto(json.dumps(message).encode('utf-8'), ('<broadcast>', self.port))
            self.add_log(f"[bold blue]Task Sent:[/bold blue] ID:{task_id} - {task_data.get('desc')}")
            self.active_elections[task_id] = {
                "bids": {},
                "start_time": time.time()
            }
        except Exception as e:
            self.add_log(f"[bold red]Error sending task:[/bold red] {e}")

    def broadcast_task_result(self, task_id, requester_id, result):
        message = {
            "type": "task_result",
            "node_id": self.node_id,
            "task_id": task_id,
            "requester_id": requester_id,
            "result": result
        }
        try:
            self.udp_socket.sendto(json.dumps(message).encode('utf-8'), ('<broadcast>', self.port))
        except:
            pass

    def send_bid(self, task_id, score):
        message = {
            "type": "task_bid",
            "node_id": self.node_id,
            "task_id": task_id,
            "score": score
        }
        try:
            self.udp_socket.sendto(json.dumps(message).encode('utf-8'), ('<broadcast>', self.port))
        except Exception:
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
                        self.add_log(f"[bold red]No bids for task ID:{task_id}. Task failed.[/bold red]")
                    else:
                        winner_id = max(bids, key=bids.get)
                        highest_score = bids[winner_id]
                        self.add_log(f"[bold magenta]Election finished for ID:{task_id}[/bold magenta]. Winner: {winner_id} ({highest_score}%)")
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
        elif cmd.lower() == "mockllm":
            models = ["llama3", "mistral", "gemma"]
            t_model = random.choice(models)
            task_data = {
                "id": str(uuid.uuid4())[:6],
                "desc": f"Generate text using {t_model}",
                "model": t_model,
                "task_type": "llm_task"
            }
            self.broadcast_task(task_data)
        elif cmd.lower().startswith("taskllm "):
            parts = cmd[8:].strip().split(" ", 1)
            if len(parts) < 2:
                self.add_log("[bold red]Format: taskllm <model> <prompt>[/bold red]")
                return
            t_model = parts[0]
            t_prompt = parts[1]
            task_data = {
                "id": str(uuid.uuid4())[:6],
                "desc": t_prompt,
                "model": t_model,
                "task_type": "llm_task"
            }
            self.broadcast_task(task_data)
        elif cmd.lower().startswith("task "):
            task_desc = cmd[5:].strip()
            task_data = {
                "id": str(uuid.uuid4())[:6],
                "desc": task_desc,
                "difficulty": random.randint(1, 5),
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
    parser = argparse.ArgumentParser(description="DRIFT P2P Serverless Framework")
    parser.add_argument("--llm-backend", choices=["ollama", "lmstudio", "mock"], help="Backend to use for LLM tasks")
    parser.add_argument("--llm-url", help="Custom URL for the LLM backend API")
    args = parser.parse_args()

    os.system("cls" if os.name == "nt" else "clear")
    console.print(Text(DRIFT_BANNER, style="bold cyan"))
    
    node_name = console.input("[bold yellow]Name your Node (press Enter for random ID):[/bold yellow] ").strip()
    
    console.print("\n[bold green]Available Strategies:[/bold green]")
    console.print("1 - Default Strategy (CPU/RAM scoring)")
    console.print("2 - LLM Strategy (GPU/Model focused)")
    strat_choice = console.input("Select strategy [1/2]: ").strip()
    
    node = DriftNode(name=node_name if node_name else None)
    
    if strat_choice == "2":
        backend = args.llm_backend
        if not backend:
            console.print("\n[bold yellow]Select LLM Backend:[/bold yellow]")
            console.print("1 - Ollama (Default URL: http://localhost:11434/api/generate)")
            console.print("2 - LM Studio (Default URL: http://localhost:1234/v1/chat/completions)")
            console.print("3 - Mock (Simulation)")
            b_choice = console.input("Select backend [1/2/3]: ").strip()
            if b_choice == "1": backend = "ollama"
            elif b_choice == "2": backend = "lmstudio"
            else: backend = "mock"
            
        node.strategy = LLMStrategy(node, backend=backend, backend_url=args.llm_url)
    else:
        node.strategy = DefaultStrategy(node)
        
    node.start()
