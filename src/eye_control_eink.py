#!/home/pi/mediapipe_env/bin/python3
import os
import cv2
import time
import numpy as np
from evdev import UInput, ecodes
import mediapipe as mp

# ======================
# 参数区（工程关键）
# ======================
FRAME_WIDTH = 640
FRAME_HEIGHT = 480

GAZE_THRESHOLD = 0.10     # 头部移动阈值（越大越不敏感）
HOLD_TIME = 0.5            # 注视持续时间（秒）
COOLDOWN_TIME = 1.0         # 翻页冷却时间（秒）

# 存储参考位置的变量
reference_nose_x = None
reference_update_counter = 0
REFERENCE_UPDATE_INTERVAL = 30  # 每30帧更新一次参考位置

# ======================
# 自动检测摄像头函数
# ======================
def find_available_camera():
    """自动检测可用的摄像头设备"""
    print("[INFO] Searching for available cameras...")

    
    # 尝试索引0-9的摄像头
    for i in range(10):
        device_path = f"/dev/video{i}"
        if os.path.exists(device_path):
            print(f"[INFO] Device path exists: {device_path}")
            temp_cap = cv2.VideoCapture(i)
            if temp_cap.isOpened():
                ret, frame = temp_cap.read()
                if ret:
                    print(f"[INFO] Found available camera at index: {i}")
                    temp_cap.release()
                    return i
                else:
                    print(f"[WARNING] Camera {i} opened but failed to read frame")
                    temp_cap.release()
            else:
                print(f"[WARNING] Failed to open camera at index: {i}")
        else:
            print(f"[INFO] Device path does not exist: {device_path}")
    
    raise RuntimeError("No camera found")

# ======================
# 虚拟按键初始化
# ======================
ui = UInput({
    ecodes.EV_KEY: [
        ecodes.KEY_PAGEDOWN,  # 下一页
        ecodes.KEY_PAGEUP     # 上一页
    ]
}, name="eye_page_turner")

def send_key(key):
    ui.write(ecodes.EV_KEY, key, 1)
    ui.write(ecodes.EV_KEY, key, 0)
    ui.syn()
    print(f"[DEBUG] Sent key: {key}")

# ======================
# MediaPipe 初始化
# ======================
mp_face = mp.solutions.face_mesh
mp_drawing = mp.solutions.drawing_utils
drawing_spec = mp_drawing.DrawingSpec(thickness=1, circle_radius=1)
face_mesh = mp_face.FaceMesh(
    static_image_mode=False,
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

# 左右眼关键点（虹膜中心）
LEFT_IRIS = [474, 475, 476, 477]
RIGHT_IRIS = [469, 470, 471, 472]

# 面部关键点：鼻子尖
NOSE_TIP = 1

# ======================
# 摄像头初始化
# ======================
CAMERA_INDEX = find_available_camera()
cap = cv2.VideoCapture(CAMERA_INDEX)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

if not cap.isOpened():
    raise RuntimeError("Camera open failed")

print("[INFO] Eye control started")

# ======================
# 状态机变量
# ======================
last_action_time = 0
gaze_start_time = None
gaze_direction = None

def get_landmark_coords(landmarks, landmark_idx, frame_width, frame_height):
    """获取特定关键点的坐标"""
    x = landmarks[landmark_idx].x * frame_width
    y = landmarks[landmark_idx].y * frame_height
    return x, y

def iris_center(landmarks, idxs):
    pts = np.array([(landmarks[i].x, landmarks[i].y) for i in idxs])
    return pts.mean(axis=0)

# ======================
# 主循环
# ======================
while True:
    ret, frame = cap.read()
    if not ret:
        break

    frame = cv2.flip(frame, 1)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    result = face_mesh.process(rgb)

    now = time.time()

    if result.multi_face_landmarks:
        lm = result.multi_face_landmarks[0].landmark
        
        # 获取鼻子坐标
        nose_x, nose_y = get_landmark_coords(lm, NOSE_TIP, FRAME_WIDTH, FRAME_HEIGHT)

        # 绘制鼻子点
        cv2.circle(frame, (int(nose_x), int(nose_y)), 5, (0, 0, 255), -1)  # 鼻尖 - 红色

        # 更新参考位置（每30帧更新一次）
        if reference_update_counter % REFERENCE_UPDATE_INTERVAL == 0:
            reference_nose_x = nose_x
            reference_update_counter = 0
            print(f"[INFO] Reference position updated: nose_x={nose_x:.2f}")
        
        reference_update_counter += 1

        # 绘制面部网格
        mp_drawing.draw_landmarks(
            image=frame,
            landmark_list=result.multi_face_landmarks[0],
            connections=mp_face.FACEMESH_CONTOURS,
            landmark_drawing_spec=drawing_spec,
            connection_drawing_spec=drawing_spec
        )

        direction = None
        
        # 检测头部移动
        if reference_nose_x is not None:
            # 计算当前鼻子位置相对于参考位置的偏移
            nose_offset = nose_x - reference_nose_x
            
            # 如果偏移超过阈值，则认为是头部移动
            if nose_offset > GAZE_THRESHOLD * FRAME_WIDTH:
                direction = "LEFT"  # 头向右移，画面向左翻
            elif nose_offset < -GAZE_THRESHOLD * FRAME_WIDTH:
                direction = "RIGHT"   # 头向左移，画面向右翻

        # 注视逻辑
        if direction:
            if gaze_direction != direction:
                gaze_direction = direction
                gaze_start_time = now
                # 在画面上显示注视方向提示
                cv2.putText(frame, f"Head turning {direction}", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                print(f"[DEBUG] Started tracking direction: {direction}")
            else:
                elapsed_time = now - gaze_start_time
                # 显示注视计时
                cv2.putText(frame, f"Head turning {direction}: {elapsed_time:.1f}s", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 2)
                
                if elapsed_time >= HOLD_TIME:
                    if now - last_action_time >= COOLDOWN_TIME:
                        if direction == "RIGHT":
                            print("[ACTION] NEXT PAGE")
                            send_key(ecodes.KEY_PAGEDOWN)
                        else:
                            print("[ACTION] PREV PAGE")
                            send_key(ecodes.KEY_PAGEUP)

                        last_action_time = now
                        gaze_start_time = None
                        gaze_direction = None
                        print(f"[DEBUG] Action performed: {direction} page")
                    else:
                        remaining_time = COOLDOWN_TIME - (now - last_action_time)
                        cv2.putText(frame, f"Cooldown: {remaining_time:.1f}s", (10, 90), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        else:
            # 显示当前头部相对位置
            if reference_nose_x is not None:
                nose_offset = nose_x - reference_nose_x
                if abs(nose_offset) < GAZE_THRESHOLD * FRAME_WIDTH / 2:
                    side_indicator = "CENTER"
                elif nose_offset > 0:
                    side_indicator = "RIGHT (turning)"
                else:
                    side_indicator = "LEFT (turning)"
                
                cv2.putText(frame, f"Head: {side_indicator}", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                
                # 显示当前偏移量
                cv2.putText(frame, f"Offset: {nose_offset:.1f}px", (10, 60), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            
            gaze_start_time = None
            gaze_direction = None
    else:
        # 如果没有检测到面部，显示提示
        cv2.putText(frame, "NO FACE DETECTED", (10, 30), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

    # 调试窗口（显示特征点）
    cv2.imshow("eye", frame)
    if cv2.waitKey(1) & 0xFF == 27:
        break

cap.release()
ui.close()
cv2.destroyAllWindows()
