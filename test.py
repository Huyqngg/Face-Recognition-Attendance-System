import cv2
import numpy as np
import requests
import face_recognition

# Thay IP của bạn vào
IP_CAM = "172.20.10.14" 
URL_CAPTURE = f"http://{IP_CAM}/capture"

print(f"--- ĐANG KIỂM TRA CAMERA TẠI: {URL_CAPTURE} ---")

try:
    # 1. Tải ảnh về
    print("1. Đang gửi lệnh lấy ảnh...")
    resp = requests.get(URL_CAPTURE, timeout=5.0)
    print(f"   -> Trạng thái HTTP: {resp.status_code}")
    print(f"   -> Kích thước dữ liệu tải về: {len(resp.content)} bytes")

    if resp.status_code == 200 and len(resp.content) > 0:
        # 2. Giải mã ảnh
        print("2. Đang giải mã ảnh bằng OpenCV...")
        img_arr = np.array(bytearray(resp.content), dtype=np.uint8)
        frame = cv2.imdecode(img_arr, cv2.IMREAD_COLOR) # Bắt buộc đọc màu

        if frame is None:
            print("   [LỖI] Dữ liệu tải về không phải là ảnh hợp lệ!")
            exit()
            
        print(f"   -> Kích thước ảnh: {frame.shape}")
        print(f"   -> Kiểu dữ liệu: {frame.dtype}")

        # 3. Thử đưa vào Face Recognition (để xem có lỗi không)
        print("3. Đang test thử Face Recognition...")
        
        # Chuyển sang RGB chuẩn
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # In thử thông tin mảng RGB
        print(f"   -> RGB Shape: {rgb_frame.shape}")
        
        # Gọi hàm gây lỗi
        locs = face_recognition.face_locations(rgb_frame)
        print(f"   -> KẾT QUẢ: Tìm thấy {len(locs)} khuôn mặt.")
        
        # Hiển thị ảnh
        cv2.imshow("TEST CAMERA", frame)
        print("4. Thành công! Bấm phím bất kỳ trên ảnh để thoát.")
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    else:
        print("   [LỖI] Không tải được ảnh từ ESP32.")

except Exception as e:
    print("\n========================================")
    print("PHÁT HIỆN LỖI CHI TIẾT:")
    print(e)
    print("========================================")