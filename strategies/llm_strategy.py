from .base_strategy import BaseStrategy

import json
import urllib.request
import urllib.error

class LLMStrategy(BaseStrategy):
    def __init__(self, node, preloaded_models=None, backend="mock", backend_url=None):
        super().__init__(node)
        self.loaded_models = preloaded_models if preloaded_models else []
        self.active_tasks = {} # {task_id: model_name}
        self.backend = backend # "ollama", "lmstudio", or "mock"
        
        if backend == "ollama":
            self.backend_url = backend_url or "http://localhost:11434/api/generate"
        elif backend == "lmstudio":
            self.backend_url = backend_url or "http://localhost:1234/v1/chat/completions"
        else:
            self.backend_url = None

    def get_name(self):
        models_str = ", ".join(self.loaded_models) if self.loaded_models else "None"
        return f"LLM Management (Preloaded: {models_str})"

    def calculate_score(self, task_data):
        if task_data.get("task_type") != "llm_task":
            # If it's not an LLM task, maybe we don't want it or we just give it a low generic score
            return 10
            
        required_model = task_data.get("model")
        
        # Base score
        score = 0
        
        if required_model in self.loaded_models:
            score = 100
        else:
            # Model not loaded, would require downloading/loading into VRAM
            score = 40
            
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
                    
                # Shorten reply for terminal log if it's too long
                display_reply = reply if len(reply) < 50 else reply[:47] + "..."
                self.node.add_log(f"[bold green]LLM Reply (ID:{task_id}):[/bold green] {display_reply}")
                
            except Exception as e:
                self.node.add_log(f"[bold red]LLM Error (ID:{task_id}):[/bold red] {e}")
                
            self.on_task_completed(task_id)
            
        threading.Thread(target=run_llm, daemon=True).start()

    def on_task_completed(self, task_id):
        if task_id in self.active_tasks:
            del self.active_tasks[task_id]
