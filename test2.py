import cv2
import face_recognition
import requests
import numpy as np
import time
from datetime import datetime
from threading import Thread

# ================= CẤU HÌNH =================
IP_CAM = "172.20.10.14"       
IP_CONTROL = "172.20.10.2"  

# Link Stream video (Cổng 81)
URL_STREAM = f"http://{IP_CAM}:81/stream"

URL_CHECK_PIR = f"http://{IP_CONTROL}/check_pir"
URL_OPEN = f"http://{IP_CONTROL}/open"
URL_FAIL = f"http://{IP_CONTROL}/fail"
URL_SCAN = f"http://{IP_CONTROL}/scan"



# ================= CLASS ĐA LUỒNG (QUAN TRỌNG) =================
class VideoStream:
    def __init__(self, src=0):
        self.stream = cv2.VideoCapture(src)
        (self.grabbed, self.frame) = self.stream.read()
        self.stopped = False

    def start(self):
        Thread(target=self.update, args=()).start()
        return self

    def update(self):
        while True:
            if self.stopped:
                self.stream.release()
                return
            (self.grabbed, self.frame) = self.stream.read()

    def read(self):
        return self.frame

    def stop(self):
        self.stopped = True

# ================= LOAD DỮ LIỆU =================
known_encodings = []
known_names = []
import os
dataset_path = "dataset"

print("[INIT] Đang học dữ liệu khuôn mặt...")
if os.path.exists(dataset_path):
    for file in os.listdir(dataset_path):
        if file.endswith(".jpg") or file.endswith(".png"):
            path = os.path.join(dataset_path, file)
            try:
                img = face_recognition.load_image_file(path)
                enc = face_recognition.face_encodings(img)[0]
                known_encodings.append(enc)
                name = os.path.splitext(file)[0]
                known_names.append(name)
                print(f"   + Đã học: {name}")
            except: pass
else:
    print("[ERROR] Thiếu thư mục dataset!")
    exit()

def send_command(url, params=None):
    try: requests.get(url, params=params, timeout=0.5)
    except: pass

# ================= VÒNG LẶP CHÍNH =================
print("[SYSTEM] Sẵn sàng! Đang chạy chế độ STREAM ĐA LUỒNG...")

while True:
    try:
        try:
            # 1. Mặc định phải là False (Coi như không có người)
            pir_active = False 
            
            # 2. Hỏi ESP32
            r = requests.get(URL_CHECK_PIR, timeout=1)
            
            # 3. Chỉ khi nào ESP32 trả về đúng số "1" thì mới bật lên
            if r.text.strip() == "1": 
                pir_active = True
                
        except: 
            # Nếu mất mạng hoặc lỗi kết nối thì coi như không có người (để tránh lỗi)
            pir_active = False

        if pir_active:
            print("\n>>> BẮT ĐẦU QUÉT STREAM...")
            send_command(URL_SCAN)
            
            # KHỞI ĐỘNG STREAM
            # Code sẽ tự chạy ngầm để lấy ảnh về liên tục
            video_getter = VideoStream(URL_STREAM).start()
            time.sleep(1.0) # Đợi 1s cho cam khởi động
            
            start_time = time.time()
            found = False
            
            while (time.time() - start_time) < 15:
                # Lấy frame mới nhất từ luồng
                frame = video_getter.read()
                
                if frame is None: continue

                # --- XỬ LÝ ẢNH AN TOÀN ---
                try:
                    # Resize
                    small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                    
                    # Chuyển màu
                    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                    
                    # *** QUAN TRỌNG: SỬA LỖI CRASH DLIB ***
                    # Dòng này sắp xếp lại bộ nhớ để tránh lỗi in loằng ngoằng
                    rgb = np.ascontiguousarray(rgb)

                    # Tìm mặt
                    locs = face_recognition.face_locations(rgb)
                    encs = face_recognition.face_encodings(rgb, locs)

                    # Vẽ khung
                    for (t, r, b, l) in locs:
                        t*=2; r*=2; b*=2; l*=2
                        cv2.rectangle(frame, (l, t), (r, b), (0, 255, 0), 2)
                    
                    cv2.imshow("STREAM CAM", frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break

                    if len(locs) > 0:
                        for enc in encs:
                            matches = face_recognition.compare_faces(known_encodings, enc, tolerance=0.5)
                            if True in matches:
                                idx = matches.index(True)
                                name = known_names[idx]
                                print(f"   => OK! Mở cửa cho: {name}")
                                
                                now = datetime.now().strftime("%H:%M")
                                send_command(URL_OPEN, params={"name": f"{name} {now}"})
                                found = True
                                break
                    if found: break

                except Exception as e:
                    # Nếu lỗi thì in ra và bỏ qua frame này, KHÔNG DỪNG CHƯƠNG TRÌNH
                    print(f"Lỗi xử lý frame: {e}")
                    continue
            
            
            # Dừng Stream để giải phóng Camera
            video_getter.stop()
            cv2.destroyAllWindows()
            
            if not found:
                print(">>> Hết giờ. Không nhận diện được.")
                send_command(URL_FAIL)
                print("--- Nghỉ 3 giây trước khi quét lại ---")
                time.sleep(3)
            else:
                # NẾU ĐÃ MỞ CỬA THÌ NGHỈ LÂU HƠN (QUAN TRỌNG)
                # Để chờ bạn đi vào hẳn và chờ PIR hết tín hiệu
                print(">>> ĐÃ MỞ CỬA....")
                time.sleep(15) 


    except KeyboardInterrupt:
        break
    except Exception as e:
        print("Lỗi tổng:", e)