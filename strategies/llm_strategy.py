from .base_strategy import BaseStrategy

import json
import urllib.request
import urllib.error

class LLMStrategy(BaseStrategy):
    def __init__(self, node, backend="mock", backend_url=None):
        super().__init__(node)
        self.node = node
        self.backend = backend # "ollama", "lmstudio", or "mock"
        self.backend_url = backend_url or ("http://localhost:11434/api/generate" if backend == "ollama" else "http://localhost:1234/v1/chat/completions")
        self.loaded_models = [] # Installed on disk
        self.active_vram_models = [] # Currently loaded in VRAM
        self.active_tasks = {} # {task_id: model_name}
        
        if self.backend in ["ollama", "lmstudio"]:
            import threading
            threading.Thread(target=self._fetch_available_models, daemon=True).start()
            if self.backend == "ollama":
                threading.Thread(target=self._poll_vram, daemon=True).start()

    def _poll_vram(self):
        import time
        while True:
            try:
                url = self.backend_url.replace("/api/generate", "/api/ps")
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    self.active_vram_models = [m.get("name") for m in result.get("models", [])]
            except Exception:
                self.active_vram_models = []
            time.sleep(5)

    def _fetch_available_models(self):
        import urllib.request
        import json
        self.node.add_log(f"[dim]Auto-detecting available models on {self.backend}...[/dim]")
        try:
            if self.backend == "ollama":
                url = self.backend_url.replace("/api/generate", "/api/tags")
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    self.loaded_models = [m.get("name") for m in result.get("models", [])]
            elif self.backend == "lmstudio":
                url = self.backend_url.replace("/chat/completions", "/models")
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    self.loaded_models = [m.get("id") for m in result.get("data", [])]
                    
            if self.loaded_models:
                self.node.add_log(f"[bold green]✅ Detected Models:[/bold green] {', '.join(self.loaded_models)}")
            else:
                self.node.add_log(f"[bold yellow]⚠️ No models found on {self.backend}.[/bold yellow]")
        except Exception as e:
            self.node.add_log(f"[bold red]Failed to detect models on {self.backend}: {e}[/bold red]")

    def get_name(self):
        models_str = ", ".join(self.loaded_models) if self.loaded_models else "None"
        vram_str = f" (VRAM: {', '.join(self.active_vram_models)})" if self.active_vram_models else ""
        return f"LLM Management (Disk: {models_str}){vram_str}"

    def calculate_score(self, task_data):
        # 1. Yetenek Kontrolü (Capability Check)
        required_cap = task_data.get("required_cap")
        if required_cap and required_cap not in self.node.caps:
            return 0
            
        if task_data.get("task_type") != "llm_task":
            # Eğer bu bir LLM görevi değilse (örn: memory, tts veya genel görev)
            # DefaultStrategy gibi CPU/RAM bazlı genel bir puan ver.
            import psutil
            cpu_usage = psutil.cpu_percent(interval=0.1)
            score = 100 - cpu_usage
            return max(0, min(100, int(score)))
            
        required_model = task_data.get("model")
        
        # Base score
        score = 0
        
        if required_model in self.active_vram_models:
            # Zaten VRAM'e yüklü (Sıcak). Anında cevap verir.
            score = 100
        elif required_model in self.loaded_models:
            # Diskte yüklü ama VRAM'de değil (Soğuk). Yükleme süresi gerekecek.
            score = 80
        else:
            # Model bu makinede yok. İhaleye girmemeli.
            return 0
            
        # Parallel Task Penalty
        # If the node is currently running other tasks, it will be slower due to shared resources
        current_parallel_tasks = len(self.active_tasks)
        if current_parallel_tasks > 0:
            # Drop score by 20 points for each active task
            score -= (current_parallel_tasks * 20)
            
        return max(0, min(100, int(score)))

    def on_task_won(self, task_data):
        task_id = task_data.get("id")
        model = task_data.get("model", "gemma:2b")
        prompt = task_data.get("desc", "Hello!")
        
        self.active_tasks[task_id] = model
        
        import threading
        
        def run_llm():
            self.node.add_log(f"[dim]LLM Strategy: Generating response for ID:{task_id} using {model} on {self.backend}...[/dim]")
            reply = None
            try:
                if self.backend == "ollama":
                    data = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode('utf-8')
                    req = urllib.request.Request(self.backend_url, data=data, headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(req) as response:
                        result = json.loads(response.read().decode('utf-8'))
                        reply = result.get("response", "").strip()
                        
                elif self.backend == "lmstudio":
                    data = json.dumps({
                        "model": model, 
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.7
                    }).encode('utf-8')
                    req = urllib.request.Request(self.backend_url, data=data, headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(req) as response:
                        result = json.loads(response.read().decode('utf-8'))
                        reply = result["choices"][0]["message"]["content"].strip()
                        
                else: # mock
                    import time
                    time.sleep(4)
                    reply = "This is a simulated response for: " + prompt
                    
                display_reply = reply if len(reply) < 50 else reply[:47] + "..."
                self.node.add_log(f"[bold green]LLM Reply (ID:{task_id}):[/bold green] {display_reply}")
                
            except Exception as e:
                self.node.add_log(f"[bold red]LLM Error (ID:{task_id}):[/bold red] {e}")
                reply = f"Hata Oluştu: {e}"
                
            self.on_task_completed(task_id, reply, task_data.get("requester_id"))
            
        threading.Thread(target=run_llm, daemon=True).start()

    def on_task_completed(self, task_id, reply=None, requester_id=None):
        if task_id in self.active_tasks:
            del self.active_tasks[task_id]
            if reply and requester_id:
                # Broadcast the final result back to the network (and the requester)
                self.node.broadcast_task_result(task_id, requester_id, reply)
