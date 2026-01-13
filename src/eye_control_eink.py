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

GAZE_THRESHOLD_X = 0.10     # 头部左右移动阈值（越大越不敏感）
GAZE_THRESHOLD_Y = 0.10     # 头部上下移动阈值（越大越不敏感）
HOLD_TIME = 0.5            # 注视持续时间（秒）
COOLDOWN_TIME = 1.0         # 翻页/换书冷却时间（秒）

# 存储参考位置的变量
reference_nose_x = None
reference_nose_y = None
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
        ecodes.KEY_PAGEUP,    # 上一页
        ecodes.KEY_NEXT,      # 下一本书 (多媒体键)
        ecodes.KEY_PREVIOUS   # 上一本书 (多媒体键)
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
# mp_drawing = mp.solutions.drawing_utils
# drawing_spec = mp_drawing.DrawingSpec(thickness=1, circle_radius=1)
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
gaze_start_time = 0
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
            reference_nose_y = nose_y
            reference_update_counter = 0
            print(f"[INFO] Reference position updated: nose_x={nose_x:.2f}, nose_y={nose_y:.2f}")
        
        reference_update_counter += 1

        # 绘制面部网格
        # mp_drawing.draw_landmarks(
        #     image=frame,
        #     landmark_list=result.multi_face_landmarks[0],
        #     connections=mp_face.FACEMESH_CONTOURS,
        #     landmark_drawing_spec=drawing_spec,
        #     connection_drawing_spec=drawing_spec
        # )

        direction = None
        
        # 检测头部移动
        if reference_nose_x is not None and reference_nose_y is not None:
            # 计算当前鼻子位置相对于参考位置的偏移
            nose_offset_x = nose_x - reference_nose_x
            nose_offset_y = nose_y - reference_nose_y
            
            # 如果偏移超过阈值，则认为是头部移动
            if nose_offset_x > GAZE_THRESHOLD_X * FRAME_WIDTH:
                direction = "RIGHT"  # 头向右移，画面向左翻
            elif nose_offset_x < -GAZE_THRESHOLD_X * FRAME_WIDTH:
                direction = "LEFT"   # 头向左移，画面向右翻
            elif nose_offset_y > GAZE_THRESHOLD_Y * FRAME_HEIGHT:
                direction = "DOWN"   # 头向下移，切换到下一本书
            elif nose_offset_y < -GAZE_THRESHOLD_Y * FRAME_HEIGHT:
                direction = "UP"     # 头向上移，切换到上一本书

        # 注视逻辑
        if direction:
            if gaze_direction != direction:
                gaze_direction = direction
                gaze_start_time = now
                # 在画面上显示注视方向提示
                cv2.putText(frame, f"Head moving {direction}", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                print(f"[DEBUG] Started tracking direction: {direction}")
            else:
                elapsed_time = now - gaze_start_time
                # 显示注视计时
                cv2.putText(frame, f"Head moving {direction}: {elapsed_time:.1f}s", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 2)
                
                if elapsed_time >= HOLD_TIME:
                    if now - last_action_time >= COOLDOWN_TIME:
                        if direction == "RIGHT":
                            print("[ACTION] NEXT PAGE")
                            send_key(ecodes.KEY_PAGEDOWN)
                        elif direction == "LEFT":
                            print("[ACTION] PREV PAGE")
                            send_key(ecodes.KEY_PAGEUP)
                        elif direction == "DOWN":
                            print("[ACTION] NEXT BOOK")
                            send_key(ecodes.KEY_NEXT)
                        elif direction == "UP":
                            print("[ACTION] PREV BOOK")
                            send_key(ecodes.KEY_PREVIOUS)

                        last_action_time = now
                        gaze_start_time = 0
                        gaze_direction = None
                        print(f"[DEBUG] Action performed: {direction}")
                    else:
                        remaining_time = COOLDOWN_TIME - (now - last_action_time)
                        cv2.putText(frame, f"Cooldown: {remaining_time:.1f}s", (10, 90), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        else:
            # 显示当前头部相对位置
            if reference_nose_x is not None and reference_nose_y is not None:
                nose_offset_x = nose_x - reference_nose_x
                nose_offset_y = nose_y - reference_nose_y
                if abs(nose_offset_x) < GAZE_THRESHOLD_X * FRAME_WIDTH / 2 and abs(nose_offset_y) < GAZE_THRESHOLD_Y * FRAME_HEIGHT / 2:
                    side_indicator = "CENTER"
                elif abs(nose_offset_x) > abs(nose_offset_y):  # 水平移动更明显
                    if nose_offset_x > 0:
                        side_indicator = "RIGHT (turning)"
                    else:
                        side_indicator = "LEFT (turning)"
                else:  # 垂直移动更明显
                    if nose_offset_y > 0:
                        side_indicator = "DOWN (next book)"
                    else:
                        side_indicator = "UP (prev book)"
                
                cv2.putText(frame, f"Head: {side_indicator}", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                
                # 显示当前偏移量
                cv2.putText(frame, f"Offset X: {nose_offset_x:.1f}px", (10, 60), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                cv2.putText(frame, f"Offset Y: {nose_offset_y:.1f}px", (10, 80), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            
            gaze_start_time = 0
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