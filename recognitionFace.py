import streamlit as st
import cv2
import face_recognition
import numpy as np
import pandas as pd
import requests
import os
import time
from datetime import datetime

# ================= 1. CẤU HÌNH KẾT NỐI =================
from project_config import (
    SETTINGS, dataset_identity, validate_identity, best_distance_match,
    notify_controller, reset_registration,
)

URL_STREAM = SETTINGS.camera_stream_url
URL_CHECK_PIR = SETTINGS.controller_url + "/check_pir"
URL_OPEN = SETTINGS.controller_url + "/open"
URL_FAIL = SETTINGS.controller_url + "/fail"
DATASET_DIR = str(SETTINGS.dataset_dir)
TOLERANCE = SETTINGS.face_distance_threshold
SIMILARITY_THRESHOLD = SETTINGS.similarity_threshold
SETTINGS.dataset_dir.mkdir(parents=True, exist_ok=True)

# ================= 2. QUẢN LÝ SESSION STATE =================
if 'system_state' not in st.session_state:
    st.session_state.system_state = "IDLE"
if 'temp_reg_name' not in st.session_state:
    st.session_state.temp_reg_name = ""
if 'reg_step' not in st.session_state:
    st.session_state.reg_step = 0
if 'attendance_log' not in st.session_state:
    st.session_state.attendance_log = pd.DataFrame(columns=["Thời gian", "Họ tên", "Trạng thái"])

# ================= 3. HÀM XỬ LÝ =================

@st.cache_resource
def load_database():
    known_encodings = []
    known_names = []
    if os.path.exists(DATASET_DIR):
        for file in sorted(os.listdir(DATASET_DIR)):
            if file.lower().endswith((".jpg", ".png", ".jpeg")):
                path = os.path.join(DATASET_DIR, file)
                try:
                    image = face_recognition.load_image_file(path)
                    encodings = face_recognition.face_encodings(image)
                    if len(encodings) > 0:
                        known_encodings.append(encodings[0])
                        name = dataset_identity(file)
                        known_names.append(name)
                except Exception: pass
    return known_encodings, known_names

def reload_data():
    load_database.clear()
    return load_database()

def send_to_screen_success(name):
    now_str = datetime.now().strftime("%H:%M")
    return notify_controller(URL_OPEN, {"name": f"{name}  {now_str}"})

def send_to_screen_fail():
    return notify_controller(URL_FAIL)

def auto_capture_stream(cam_placeholder, status_placeholder, step, name):
    """
    Hàm này stream camera liên tục.
    Tối ưu hóa: Resize ảnh nhỏ để detect nhanh -> Video mượt.
    """
    cap = cv2.VideoCapture(URL_STREAM)
    if not cap.isOpened():
        st.error("❌ Không thể kết nối Camera ESP32!")
        cap.release()
        return False, None

    try:
        msg = {1: "Nhìn THẲNG", 2: "Quay nhẹ TRÁI", 3: "Quay nhẹ PHẢI"}.get(step, "")

        stable_count = 0
        REQUIRED_STABLE = 10 # Số frame cần giữ yên (khoảng 1-2 giây)
        captured_frame = None

        # Nút hủy (đặt bên ngoài loop bằng trick container, nhưng ở đây ta dùng logic loop)
        # Streamlit hơi khó bắt sự kiện nút trong vòng lặp while, nên ta dùng Auto-Capture là chính.

        deadline = time.monotonic() + SETTINGS.capture_timeout_seconds
        while time.monotonic() < deadline:
            ret, frame = cap.read()
            if not ret:
                break

            # 1. Resize siêu nhỏ để nhận diện cho nhanh (Tăng FPS)
            # Giảm xuống 1/4 kích thước để AI xử lý mượt
            small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

            # 2. Detect khuôn mặt
            faces = face_recognition.face_locations(rgb_small)

            # 3. Vẽ giao diện lên ảnh gốc (Ảnh to)
            display_frame = frame.copy()
            h, w, _ = display_frame.shape

            if len(faces) == 1:
                stable_count += 1
                # Vẽ thanh tiến trình màu xanh
                progress_width = int((stable_count / REQUIRED_STABLE) * w)
                cv2.rectangle(display_frame, (0, h-20), (progress_width, h), (0, 255, 0), -1)
                cv2.putText(display_frame, f"GIU YEN... {int((stable_count/REQUIRED_STABLE)*100)}%", (20, h-40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

                # Nếu giữ yên đủ lâu -> Chụp
                if stable_count >= REQUIRED_STABLE:
                    captured_frame = frame # Lưu frame gốc sắc nét
                    break
            else:
                stable_count = 0 # Reset nếu mất mặt hoặc quay quá nhanh
                color = (0, 255, 255) # Vàng
                if len(faces) == 0:
                    txt = "KHONG THAY MAT"
                    color = (0, 0, 255) # Đỏ
                elif len(faces) > 1:
                    txt = "CHI 1 NGUOI THOI"
                    color = (0, 0, 255)
                else:
                    txt = msg

                cv2.putText(display_frame, f"B{step}: {msg}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2)
                cv2.putText(display_frame, txt, (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

            # 4. Đẩy ảnh lên giao diện ngay lập tức
            # Dùng use_container_width=True để ảnh to rõ
            cam_placeholder.image(cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB), channels="RGB", use_container_width=True)

            # Sleep cực ngắn để nhường CPU
            time.sleep(0.01)

        return captured_frame is not None, captured_frame

    finally:
        cap.release()

def scan_face_slowly(cam_ph, status_ph, encodings, names):
    if not names:
        return None
    cap = cv2.VideoCapture(URL_STREAM)
    if not cap.isOpened():
        cap.release()
        return None

    try:
        found = None
        max_attempts = 3

        for i in range(max_attempts):
            # Đếm ngược 3s (Vừa đếm vừa stream để không bị đứng hình)
            start_time = time.time()
            while time.time() - start_time < 2: # Đếm 2 giây
                ret, frame = cap.read()
                if ret:
                    cv2.putText(frame, f"QUET LAN {i+1}...", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,255), 2)
                    countdown = 2 - int(time.time() - start_time)
                    cv2.putText(frame, str(countdown), (300, 240), cv2.FONT_HERSHEY_SIMPLEX, 3, (0,255,255), 5)
                    cam_ph.image(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), channels="RGB", use_container_width=True)
                time.sleep(0.05)

            # Chụp thật
            ret, frame = cap.read()
            if not ret: continue

            # Nhận diện (Resize nhỏ cho nhanh)
            small = cv2.resize(frame, (0,0), fx=0.5, fy=0.5)
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            locs = face_recognition.face_locations(rgb)
            encs = face_recognition.face_encodings(rgb, locs)

            if encs:
                dists = face_recognition.face_distance(encodings, encs[0])
                found = best_distance_match(dists, names, TOLERANCE)
                if found is not None:
                    break

            if not found:
                status_ph.warning(f"⚠️ Lần {i+1}: Không khớp!")

        return found
    finally:
        cap.release()

# ================= 4. GIAO DIỆN CHÍNH =================
st.set_page_config(page_title="Hệ Thống Chấm Công AI", layout="wide")
st.title("🛡️ Hệ Thống Chấm Công & Điểm Danh")

# Load dữ liệu
encodings, names = load_database()

# Layout chính: 2 Cột (Camera - Lịch sử)
col_L, col_R = st.columns([0.65, 0.35])

with col_L:
    st.subheader("🔴 Camera Monitor")
    cam_ph = st.empty() # Placeholder cho Camera
    status_ph = st.empty() # Placeholder cho thông báo

    # Khu vực điều khiển (ẩn hiện linh hoạt)
    control_container = st.container()

with col_R:
    st.subheader("📋 Lịch sử Điểm danh")
    # Nút xóa log
    if st.button("🗑️ Xóa Lịch Sử"):
        st.session_state.attendance_log = pd.DataFrame(columns=["Thời gian", "Họ tên", "Trạng thái"])
        st.rerun()

    # Hiển thị bảng log (Tự động cập nhật)
    st.dataframe(
        st.session_state.attendance_log,
        use_container_width=True,
        hide_index=True,
        height=400
    )

# Sidebar điều khiển
with st.sidebar:
    st.header("⚙️ Điều khiển")
    if st.button("🔄 RESET VỀ MẶC ĐỊNH"):
        reset_registration(st.session_state)
        st.session_state.system_state = "IDLE"
        st.rerun()
    st.info(f"Đã học: {len(names)} khuôn mặt")
    if st.button("📝 Đăng ký khuôn mặt"):
        reset_registration(st.session_state)
        st.session_state.system_state = "REGISTER"
        st.session_state.reg_step = 1
        st.rerun()


# --- STATE MACHINE ---

# 1. TRẠNG THÁI IDLE (CHỜ)
if st.session_state.system_state == "IDLE":
    status_ph.info("💤 Đang chờ cảm biến chuyển động...")
    cam_ph.info("Chờ chuyển động hoặc chọn Đăng ký khuôn mặt ở thanh bên.")

    # Check PIR
    try:
        r = requests.get(URL_CHECK_PIR, timeout=0.5)
        if r.text.strip() == "1":
            st.session_state.system_state = "SCANNING"
            st.rerun()
    except: time.sleep(1)
    time.sleep(1)
    st.rerun()

# 2. TRẠNG THÁI QUÉT (SCANNING)
elif st.session_state.system_state == "SCANNING":
    name = scan_face_slowly(cam_ph, status_ph, encodings, names)

    if name:
        status_ph.success(f"✅ Xác nhận: {name}")
        command_accepted = send_to_screen_success(name)
        if not command_accepted:
            st.warning("Đã nhận diện, nhưng bộ điều khiển chưa nhận được lệnh mở cửa.")

        # Ghi Log
        row = {"Thời gian": datetime.now().strftime("%H:%M:%S"), "Họ tên": name, "Trạng thái": "Thành công" if command_accepted else "Lỗi điều khiển"}
        st.session_state.attendance_log = pd.concat([pd.DataFrame([row]), st.session_state.attendance_log], ignore_index=True)

        time.sleep(5)
        st.session_state.system_state = "IDLE"
        st.rerun()
    else:
        send_to_screen_fail()
        # Ghi Log thất bại
        row = {"Thời gian": datetime.now().strftime("%H:%M:%S"), "Họ tên": "Unknown", "Trạng thái": "Thất bại"}
        st.session_state.attendance_log = pd.concat([pd.DataFrame([row]), st.session_state.attendance_log], ignore_index=True)

        st.session_state.system_state = "FAIL_OPT"
        st.rerun()

# 3. TRẠNG THÁI HỎI (FAIL OPTION)
elif st.session_state.system_state == "FAIL_OPT":
    status_ph.error("❌ Không nhận diện được!")
    cam_ph.info("Bạn có muốn đăng ký khuôn mặt mới không?")

    with control_container:
        c1, c2 = st.columns(2)
        if c1.button("📝 Đăng ký ngay"):
            st.session_state.system_state = "REGISTER"
            st.session_state.reg_step = 1
            st.rerun()
        if c2.button("➡️ Bỏ qua"):
            st.session_state.system_state = "IDLE"
            st.rerun()

# 4. TRẠNG THÁI ĐĂNG KÝ (REGISTER)
elif st.session_state.system_state == "REGISTER":
    if not st.session_state.temp_reg_name:
        cam_ph.empty()
        status_ph.info("Nhập tên nhân viên mới để bắt đầu:")
        with control_container:
            val = st.text_input("Họ và Tên (Viết liền, không dấu):")
            if st.button("📸 Bắt đầu chụp") and val:
                try:
                    val = validate_identity(val)
                except ValueError as exc:
                    st.error(str(exc))
                    st.stop()
                st.session_state.temp_reg_name = val
                st.rerun()
    else:
        # Vào quy trình chụp Auto Stream
        name = st.session_state.temp_reg_name
        step = st.session_state.reg_step

        # Gọi hàm stream liên tục
        ok, frame = auto_capture_stream(cam_ph, status_ph, step, name)

        if ok and frame is not None:
            suffix = ["front", "left", "right"][step-1]
            saved = cv2.imwrite(os.path.join(DATASET_DIR, f"{name}_{suffix}.jpg"), frame)
            if not saved:
                st.error("Không lưu được ảnh. Kiểm tra quyền ghi thư mục dataset.")
                st.stop()

            st.toast(f"✅ Đã lưu góc {suffix}!", icon="💾")

            if step < 3:
                st.session_state.reg_step += 1
                st.rerun()
            else:
                reload_data()
                st.success("🎉 Đăng ký thành công! Đang tải lại dữ liệu...")

                # Ghi Log đăng ký
                row = {"Thời gian": datetime.now().strftime("%H:%M:%S"), "Họ tên": name, "Trạng thái": "Đăng ký mới"}
                st.session_state.attendance_log = pd.concat([pd.DataFrame([row]), st.session_state.attendance_log], ignore_index=True)

                st.session_state.temp_reg_name = ""
                st.session_state.reg_step = 0
                st.session_state.system_state = "IDLE"
                time.sleep(3)
                st.rerun()
        else:
            status_ph.error("Không chụp được ảnh hợp lệ trong thời gian chờ.")
            if st.button("Thử chụp lại"):
                st.rerun()
