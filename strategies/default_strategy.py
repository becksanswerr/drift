import psutil
from .base_strategy import BaseStrategy

class DefaultStrategy(BaseStrategy):
    def get_name(self):
        return "Default (CPU/RAM)"

    def calculate_score(self, task_data):
        # 1. Yetenek Kontrolü (Capability Check)
        required_cap = task_data.get("required_cap")
        if required_cap and required_cap not in self.node.caps:
            # İstenen yetenek bu PC'de yoksa direkt elenir (Puan: 0)
            return 0
            
        difficulty = task_data.get("difficulty", 1)
        
        cpu_usage = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        ram_avail_gb = ram.available / (1024**3)
        
        # 2. Bilişsel Yük / Doluluk Hesaplaması
        # CPU ne kadar boşsa, skor o kadar 100'e yaklaşır.
        score = 100 - cpu_usage
        
        if difficulty > 7 and ram_avail_gb < 4.0:
            score -= 40
        elif difficulty < 4:
            score += 10
            
        return max(0, min(100, int(score)))
