import streamlit as st
import cv2
import numpy as np
import pandas as pd
import requests
import os
import time
from datetime import datetime
from insightface.app import FaceAnalysis

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
# Cooldown để tránh quét liên tục khi PIR vẫn đang HIGH
if 'cooldown_until' not in st.session_state:
    st.session_state.cooldown_until = 0.0

# ================= 3. KHỞI TẠO INSIGHTFACE =================
@st.cache_resource
def load_model():
    # Sử dụng model buffalo_s (chứa MobileFaceNet) siêu nhẹ cho CPU
    print("[INIT] Loading InsightFace Model...")
    app = FaceAnalysis(name='buffalo_s', providers=['CPUExecutionProvider'])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return app

# Hàm tính độ tương đồng Cosine
def compute_sim(feat1, feat2):
    denom = np.linalg.norm(feat1) * np.linalg.norm(feat2)
    return float(np.dot(feat1, feat2) / denom) if denom else 0.0

@st.cache_resource
def load_database(_model):
    known_embeddings = []
    known_names = []
    print("[DATA] Loading Database...")
    if os.path.exists(DATASET_DIR):
        for file in sorted(os.listdir(DATASET_DIR)):
            if file.lower().endswith((".jpg", ".png", ".jpeg")):
                path = os.path.join(DATASET_DIR, file)
                try:
                    # InsightFace đọc ảnh BGR (OpenCV mặc định)
                    img = cv2.imread(path)
                    if img is None: continue

                    faces = _model.get(img)
                    if len(faces) > 0:
                        # Lấy khuôn mặt lớn nhất trong ảnh
                        face = sorted(faces, key=lambda x: (x.bbox[2]-x.bbox[0]) * (x.bbox[3]-x.bbox[1]))[-1]
                        known_embeddings.append(face.embedding)
                        name = dataset_identity(file)
                        known_names.append(name)
                except Exception as e:
                    print(f"Error loading {file}: {e}")
    return known_embeddings, known_names

def reload_data(model):
    load_database.clear()
    return load_database(model)

# ================= 4. CÁC HÀM XỬ LÝ LOGIC =================

def send_to_screen_success(name):
    now_str = datetime.now().strftime("%H:%M")
    return notify_controller(URL_OPEN, {"name": f"{name}  {now_str}"})

def send_to_screen_fail():
    return notify_controller(URL_FAIL)

def auto_capture_stream(cam_placeholder, status_placeholder, step, name, model):
    cap = cv2.VideoCapture(URL_STREAM)
    if not cap.isOpened():
        st.error("❌ Không thể kết nối Camera ESP32!")
        cap.release()
        return False, None

    try:
        # Định nghĩa hướng dẫn cho từng bước
        steps_info = {
            1: "Nhin thang vao Camera",
            2: "Quay mat sang TRAI",
            3: "Quay mat sang PHAI"
        }
        msg = steps_info.get(step, "")

        stable_count = 0
        REQUIRED_STABLE = 8  # Giảm xuống chút cho dễ chụp sau khi đã chờ
        captured_frame = None
        SCALE_FACTOR = 0.25
        frame_count = 0
        SKIP_FRAMES = 3
        last_faces = []

        # --- GIAI ĐOẠN 1: ĐẾM NGƯỢC (ĐỂ BẠN KỊP QUAY ĐẦU) ---
        # Thời gian chờ: 3 giây cho bước 1, 4 giây cho bước 2,3 (để kịp xoay)
        wait_time = 3 if step == 1 else 4
        start_time = time.time()

        while time.time() - start_time < wait_time:
            ret, frame = cap.read()
            if not ret: break

            # Chỉ hiển thị đếm ngược, không xử lý AI
            countdown = wait_time - int(time.time() - start_time)
            display_frame = frame.copy()

            # Vẽ màn hình tối đi một chút để tập trung
            overlay = display_frame.copy()
            cv2.rectangle(overlay, (0, 0), (frame.shape[1], frame.shape[0]), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.3, display_frame, 0.7, 0, display_frame)

            # In chữ to giữa màn hình
            text = f"BUOC {step}: {msg}"
            cv2.putText(display_frame, text, (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.putText(display_frame, f"CHUAN BI... {countdown}", (150, 250), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 4)

            cam_placeholder.image(cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB), channels="RGB", use_container_width=True)
            time.sleep(0.05)

        # --- GIAI ĐOẠN 2: BẮT ĐẦU QUÉT VÀ CHỤP ---
        deadline = time.monotonic() + SETTINGS.capture_timeout_seconds
        while time.monotonic() < deadline:
            ret, frame = cap.read()
            if not ret: break

            display_frame = frame.copy()
            h, w, _ = display_frame.shape
            frame_count += 1

            # Frame Skipping cho mượt
            if frame_count % SKIP_FRAMES == 0:
                small_frame = cv2.resize(frame, (0, 0), fx=SCALE_FACTOR, fy=SCALE_FACTOR)
                last_faces = model.get(small_frame)

            # Hiển thị hướng dẫn liên tục
            cv2.putText(display_frame, f"B{step}: {msg}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)

            if len(last_faces) == 1:
                if frame_count % SKIP_FRAMES == 0:
                    stable_count += 1

                face = last_faces[0]
                box = (face.bbox / SCALE_FACTOR).astype(int)

                # Vẽ khung xanh
                cv2.rectangle(display_frame, (box[0], box[1]), (box[2], box[3]), (0, 255, 0), 2)

                # Thanh tiến trình
                progress_width = int((stable_count / REQUIRED_STABLE) * w)
                cv2.rectangle(display_frame, (0, h-20), (progress_width, h), (0, 255, 0), -1)
                cv2.putText(display_frame, f"GIU YEN... {int((stable_count/REQUIRED_STABLE)*100)}%", (20, h-40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

                if stable_count >= REQUIRED_STABLE:
                    captured_frame = frame
                    break
            else:
                if frame_count % SKIP_FRAMES == 0:
                    stable_count = 0

                status_text = "KHONG THAY MAT" if len(last_faces) == 0 else "CHI 1 NGUOI THOI"
                cv2.putText(display_frame, status_text, (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

            cam_placeholder.image(cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB), channels="RGB", use_container_width=True)
            time.sleep(0.01)

        return captured_frame is not None, captured_frame
    finally:
        cap.release()

# --- HÀM QUÉT CHẤM CÔNG (INSIGHTFACE) ---
def scan_face_slowly(cam_ph, status_ph, known_embeddings, known_names, model):
    if not known_names:
        return None
    cap = cv2.VideoCapture(URL_STREAM)
    if not cap.isOpened():
        cap.release()
        return None

    try:
        found_name = None
        max_attempts = 3

        for i in range(max_attempts):
            # Đếm ngược và hiển thị stream
            start_time = time.time()
            while time.time() - start_time < 5:
                ret, frame = cap.read()
                if ret:
                    cv2.putText(frame, f"QUET LAN {i+1}...", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,255), 2)
                    countdown = 5 - int(time.time() - start_time)
                    cv2.putText(frame, str(countdown), (300, 240), cv2.FONT_HERSHEY_SIMPLEX, 3, (0,255,255), 5)
                    cam_ph.image(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), channels="RGB", use_container_width=True)
                time.sleep(0.05)

            # Chụp ảnh để xử lý
            ret, frame = cap.read()
            if not ret: continue

            # Nhận diện
            faces = model.get(frame)

            if len(faces) > 0:
                # Lấy mặt lớn nhất
                face = sorted(faces, key=lambda x: (x.bbox[2]-x.bbox[0]) * (x.bbox[3]-x.bbox[1]))[-1]

                # So sánh với database (Cosine Similarity)
                max_score = 0
                best_idx = -1

                for idx, embed in enumerate(known_embeddings):
                    score = compute_sim(face.embedding, embed)
                    if score > max_score:
                        max_score = score
                        best_idx = idx

                # Kiểm tra ngưỡng
                if max_score > SIMILARITY_THRESHOLD:
                    found_name = known_names[best_idx]
                    break

            if not found_name:
                status_ph.warning(f"⚠️ Lần {i+1}: Không khớp!")

        return found_name
    finally:
        cap.release()

# ================= 5. GIAO DIỆN CHÍNH =================
st.set_page_config(page_title="InsightFace Attendance", layout="wide")
st.title("🛡️ Hệ Thống Chấm Công (InsightFace MobileNet)")

# Load Model & Data
face_app = load_model()
encodings, names = load_database(face_app)

col_L, col_R = st.columns([0.65, 0.35])

with col_L:
    st.subheader("🔴 Camera Monitor")
    cam_ph = st.empty()
    status_ph = st.empty()
    control_container = st.container()

with col_R:
    st.subheader("📋 Lịch sử Điểm danh")
    if st.button("🗑️ Xóa Lịch Sử"):
        st.session_state.attendance_log = pd.DataFrame(columns=["Thời gian", "Họ tên", "Trạng thái"])
        st.rerun()

    st.dataframe(
        st.session_state.attendance_log,
        use_container_width=True,
        hide_index=True,
        height=400
    )

with st.sidebar:
    st.header("⚙️ Điều khiển")
    if st.button("🔄 RESET"):
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

if st.session_state.system_state == "IDLE":
    status_ph.info("💤 Đang chờ cảm biến chuyển động...")
    cam_ph.info("Chờ chuyển động hoặc chọn Đăng ký khuôn mặt ở thanh bên.")

    # Nếu đang trong cooldown thì không gọi PIR (tránh kích quét liên tục)
    if time.time() < st.session_state.cooldown_until:
        time.sleep(0.3)
        st.rerun()

    try:
        r = requests.get(URL_CHECK_PIR, timeout=0.5)
        if r.text.strip() == "1":
            st.session_state.system_state = "SCANNING"
            st.rerun()
    except: time.sleep(1)
    time.sleep(1)
    st.rerun()

elif st.session_state.system_state == "SCANNING":
    # Truyền thêm face_app model vào hàm
    name = scan_face_slowly(cam_ph, status_ph, encodings, names, face_app)

    if name:
        status_ph.success(f"✅ Xác nhận: {name}")
        command_accepted = send_to_screen_success(name)
        if not command_accepted:
            st.warning("Đã nhận diện, nhưng bộ điều khiển chưa nhận được lệnh mở cửa.")

        row = {"Thời gian": datetime.now().strftime("%H:%M:%S"), "Họ tên": name, "Trạng thái": "Thành công" if command_accepted else "Lỗi điều khiển"}
        st.session_state.attendance_log = pd.concat([pd.DataFrame([row]), st.session_state.attendance_log], ignore_index=True)

        # Thời gian giữ trạng thái thành công + cooldown để PIR không kích lại ngay
        time.sleep(8)
        st.session_state.cooldown_until = time.time() + 5  # chờ PIR hạ xuống / người rời khỏi vùng

        st.session_state.system_state = "IDLE"
        st.rerun()
    else:
        send_to_screen_fail()
        row = {"Thời gian": datetime.now().strftime("%H:%M:%S"), "Họ tên": "Unknown", "Trạng thái": "Thất bại"}
        st.session_state.attendance_log = pd.concat([pd.DataFrame([row]), st.session_state.attendance_log], ignore_index=True)

        # Nghỉ vài giây để người dùng đọc thông báo / OLED kịp hiển thị
        time.sleep(8)
        st.session_state.cooldown_until = time.time() + 2
        st.session_state.system_state = "FAIL_OPT"
        st.rerun()

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

elif st.session_state.system_state == "REGISTER":
    if not st.session_state.temp_reg_name:
        cam_ph.empty()
        status_ph.info("Nhập tên nhân viên mới:")
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
        name = st.session_state.temp_reg_name
        step = st.session_state.reg_step
        msgs = {
            1: "📸 BƯỚC 1: Nhin thang vao Camera",
            2: "⬅️ BƯỚC 2: Quay mat sang TRAI (Khoang 30-45 đo)",
            3: "➡️ BƯỚC 3: Quay mat sang PHAI (Khoang 30-45 đo)"
        }
        status_ph.markdown(f"### {msgs[step]}")
        ok, frame = auto_capture_stream(cam_ph, status_ph, step, name, face_app)

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
                reload_data(face_app)
                st.success("🎉 Đăng ký thành công!")

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
