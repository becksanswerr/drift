import psutil
from .base_strategy import BaseStrategy

class DefaultStrategy(BaseStrategy):
    def get_name(self):
        return "Default (CPU/RAM)"

    def calculate_score(self, task_data):
        difficulty = task_data.get("difficulty", 1)
        
        cpu_usage = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        ram_avail_gb = ram.available / (1024**3)
        
        score = 100 - cpu_usage
        if difficulty > 7 and ram_avail_gb < 4.0:
            score -= 40
        elif difficulty < 4:
            score += 10
            
        return max(0, min(100, int(score)))
