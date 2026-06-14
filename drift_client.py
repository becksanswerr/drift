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
    
    print(f"⏳ Ağdaki en uygun Node seçiliyor (3 saniye ihale süresi)...\n")
    
    bids = {}
    election_start = time.time()
    election_finished = False
    winner_id = None
    
    while True:
        try:
            sock.settimeout(0.5)
            data, addr = sock.recvfrom(65535)
            try:
                msg = json.loads(data.decode('utf-8'))
                
                # İhale (Bidding) aşaması
                if not election_finished and msg.get("type") == "task_bid" and msg.get("task_id") == task_id:
                    bidder = msg.get("node_id")
                    score = msg.get("score")
                    bids[bidder] = score
                    print(f"   [Bid] Node {bidder} teklif verdi: {score}%")
                    
                # Sonuç bekleme aşaması
                elif election_finished and msg.get("type") == "task_result" and msg.get("task_id") == task_id and msg.get("requester_id") == client_id:
                    print("\n" + "=" * 60)
                    print(f"🎯 CEVAP GELDİ! (İşleyen Node: {msg.get('node_id')} - {addr[0]})")
                    print("-" * 60)
                    print(msg.get("result"))
                    print("=" * 60)
                    break
                    
            except json.JSONDecodeError:
                pass
        except socket.timeout:
            pass
        except KeyboardInterrupt:
            print("\nİşlem iptal edildi.")
            break
            
        # İhaleyi sonlandır ve kazananı belirle
        if not election_finished and (time.time() - election_start > 3.0):
            election_finished = True
            if not bids:
                print("❌ İhaleye hiç teklif gelmedi. Ağda aktif Node yok veya model bulunamadı.")
                break
                
            winner_id = max(bids, key=bids.get)
            print(f"🏆 İhale bitti! Kazanan Node: {winner_id} ({bids[winner_id]}% puan ile). İşlem bekleniyor...")
            
            # Kazananı ağa duyur
            election_msg = {
                "type": "election_result",
                "node_id": client_id,
                "task_id": task_id,
                "winner_id": winner_id
            }
            sock.sendto(json.dumps(election_msg).encode('utf-8'), ('<broadcast>', 50000))
            
            # Artık cevap için beklemeye geçiyoruz, timeout uzatılır
            sock.settimeout(60.0)

if __name__ == "__main__":
    main()
