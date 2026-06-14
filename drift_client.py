import socket
import json
import uuid
import sys
import time

def main():
    if len(sys.argv) < 3:
        print("Kullanım: python drift_client.py <model> <prompt>")
        print("Örnek: python drift_client.py gemma:2b Bana kısa bir şiir yaz")
        sys.exit(1)
        
    model = sys.argv[1]
    prompt = " ".join(sys.argv[2:])
    
    client_id = "client-" + str(uuid.uuid4())[:4]
    task_id = str(uuid.uuid4())[:6]
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    
    # Port 50000 dinliyoruz ki ağa atılan broadcast task_result'ı görebilelim
    sock.bind(('', 50000))
    
    task_data = {
        "id": task_id,
        "desc": prompt,
        "model": model,
        "task_type": "llm_task",
        "requester_id": client_id
    }
    
    message = {
        "type": "new_task",
        "node_id": client_id,
        "task_data": task_data
    }
    
    print(f"🚀 DRIFT Ağına Görev Gönderiliyor (Task ID: {task_id})")
    print(f"Model: {model} | Soru: {prompt}")
    
    sock.sendto(json.dumps(message).encode('utf-8'), ('<broadcast>', 50000))
    
    print(f"⏳ Ağdaki en uygun Node seçiliyor ve işleniyor. Lütfen bekleyin...\n")
    
    while True:
        try:
            sock.settimeout(60.0) # 60 saniye bekle
            data, addr = sock.recvfrom(65535)
            try:
                msg = json.loads(data.decode('utf-8'))
                
                if msg.get("type") == "task_result" and msg.get("task_id") == task_id and msg.get("requester_id") == client_id:
                    print("=" * 60)
                    print(f"🎯 CEVAP GELDİ! (İşleyen Node: {msg.get('node_id')} - {addr[0]})")
                    print("-" * 60)
                    print(msg.get("result"))
                    print("=" * 60)
                    break
                    
            except json.JSONDecodeError:
                pass
        except socket.timeout:
            print("❌ Zaman aşımı (60 sn). Ağda boşta bir Node yok veya işlem çok uzun sürdü.")
            break
        except KeyboardInterrupt:
            print("\nİşlem iptal edildi.")
            break

if __name__ == "__main__":
    main()
