#!/home/pi/mediapipe_env/bin/python3
import os
import cv2
import time
from evdev import UInput, ecodes
import mediapipe as mp

# ======================
# 参数区（工程关键）
# ======================
FRAME_WIDTH = 640
FRAME_HEIGHT = 480

# 虹膜位置变化阈值（相对变化）
IRIS_CHANGE_THRESHOLD = 0.002  # 虹膜位置显著变化的阈值

# 翻页冷却时间
COOLDOWN_TIME = 1.0

# 平滑参数
SMOOTHING_FACTOR = 0.3  # 指数平滑系数 (0-1，越小越平滑)

# 初始化等待时间（防止启动时误触发）
INITIALIZATION_PERIOD = 4.0  # 启动后等待4秒再开始响应翻页

# 状态变量
last_action_time = 0
read_to_bottom = False  # 标记是否已经阅读到屏幕底部
smooth_iris_y = None    # 平滑后的虹膜Y位置
reference_iris_y = None # 参考虹膜Y位置（静止时的位置）
eye_movement_buffer = []  # 眼球运动方向缓冲区
BUFFER_SIZE = 5          # 缓冲区大小

# 初始化相关变量
initialization_start_time = time.time()
is_initialized = False  # 是否已完成初始化

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
# 创建一个标准的键盘设备，模拟按键事件
ui = UInput({
    ecodes.EV_KEY: [
        ecodes.KEY_PAGEDOWN,  # 下一页
    ]
}, name="eye_page_turner", version=0x3)

def send_key(key):
    ui.write(ecodes.EV_KEY, key, 1)
    ui.write(ecodes.EV_KEY, key, 0)
    ui.syn()
    print(f"[DEBUG] Sent key: {key}")

# ======================
# MediaPipe 初始化
# ======================
mp_face = mp.solutions.face_mesh
face_mesh = mp_face.FaceMesh(
    static_image_mode=False,
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

# 虹膜关键点索引
LEFT_IRIS = [474, 475, 476, 477]
RIGHT_IRIS = [469, 470, 471, 472]

# 眼睛轮廓关键点（用于计算眼睛中心）
LEFT_EYE = [33, 133, 160, 159, 158, 144, 145, 153]
RIGHT_EYE = [362, 263, 387, 386, 385, 373, 374, 380]

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

def eye_center(landmarks, idxs):
    """计算眼睛中心点"""
    x_sum = sum(landmarks[i].x for i in idxs)
    y_sum = sum(landmarks[i].y for i in idxs)
    count = len(idxs)
    return x_sum/count, y_sum/count

def iris_center(landmarks, idxs):
    """计算虹膜中心点"""
    x_sum = sum(landmarks[i].x for i in idxs)
    y_sum = sum(landmarks[i].y for i in idxs)
    count = len(idxs)
    return x_sum/count, y_sum/count

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

    # 检查是否完成初始化
    if not is_initialized:
        if now - initialization_start_time >= INITIALIZATION_PERIOD:
            is_initialized = True
            print(f"[INFO] Initialization completed after {INITIALIZATION_PERIOD} seconds")
        else:
            # 显示初始化倒计时
            remaining_time = INITIALIZATION_PERIOD - (now - initialization_start_time)
            cv2.putText(frame, f"INITIALIZING... {remaining_time:.1f}s", (10, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            cv2.imshow("Eye Control - Iris Movement Detection (Improved)", frame)
            
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            continue

    if result.multi_face_landmarks:
        lm = result.multi_face_landmarks[0].landmark
        
        # 获取双眼中心点
        left_eye_x, left_eye_y = eye_center(lm, LEFT_EYE)
        right_eye_x, right_eye_y = eye_center(lm, RIGHT_EYE)
        
        # 获取虹膜中心点
        left_iris_x, left_iris_y = iris_center(lm, LEFT_IRIS)
        right_iris_x, right_iris_y = iris_center(lm, RIGHT_IRIS)
        
        # 计算双眼虹膜的平均Y坐标（归一化值）
        avg_iris_y = (left_iris_y + right_iris_y) / 2.0
        
        # 应用指数平滑
        if smooth_iris_y is None:
            smooth_iris_y = avg_iris_y
        else:
            smooth_iris_y = smooth_iris_y * (1 - SMOOTHING_FACTOR) + avg_iris_y * SMOOTHING_FACTOR
        
        # 绘制眼睛中心点（绿色）
        cv2.circle(frame, (int(left_eye_x * FRAME_WIDTH), int(left_eye_y * FRAME_HEIGHT)), 5, (0, 255, 0), -1)
        cv2.circle(frame, (int(right_eye_x * FRAME_WIDTH), int(right_eye_y * FRAME_HEIGHT)), 5, (0, 255, 0), -1)
        
        # 绘制虹膜中心点（红色）
        cv2.circle(frame, (int(left_iris_x * FRAME_WIDTH), int(left_iris_y * FRAME_HEIGHT)), 3, (0, 0, 255), -1)
        cv2.circle(frame, (int(right_iris_x * FRAME_WIDTH), int(right_iris_y * FRAME_HEIGHT)), 3, (0, 0, 255), -1)
        
        # 绘制眼睛到虹膜的连线
        cv2.line(frame, 
                (int(left_eye_x * FRAME_WIDTH), int(left_eye_y * FRAME_HEIGHT)),
                (int(left_iris_x * FRAME_WIDTH), int(left_iris_y * FRAME_HEIGHT)),
                (255, 255, 0), 1)
        cv2.line(frame, 
                (int(right_eye_x * FRAME_WIDTH), int(right_eye_y * FRAME_HEIGHT)),
                (int(right_iris_x * FRAME_WIDTH), int(right_iris_y * FRAME_HEIGHT)),
                (255, 255, 0), 1)
        
        # 初始化参考位置（当头部稳定时）
        if reference_iris_y is None:
            reference_iris_y = smooth_iris_y
            print(f"[INFO] Initial reference iris Y: {reference_iris_y:.4f}")
        
        # 计算相对变化（相对于参考位置）
        relative_change = smooth_iris_y - reference_iris_y
        
        # 将变化方向添加到缓冲区
        if len(eye_movement_buffer) >= BUFFER_SIZE:
            eye_movement_buffer.pop(0)
        
        # 判断当前帧的运动方向
        if relative_change > IRIS_CHANGE_THRESHOLD:
            eye_movement_buffer.append("DOWN")
        elif relative_change < -IRIS_CHANGE_THRESHOLD:
            eye_movement_buffer.append("UP")
        else:
            eye_movement_buffer.append("NEUTRAL")
        
        # 分析缓冲区中的运动模式
        down_count = eye_movement_buffer.count("DOWN")
        up_count = eye_movement_buffer.count("UP")
        
        # 检测向下看模式（连续多帧向下看）
        if down_count >= BUFFER_SIZE * 0.7:  # 70%的帧都是向下看
            read_to_bottom = True
            reference_iris_y = smooth_iris_y  # 更新参考位置
            cv2.putText(frame, "READING DOWN", (10, 60), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        
        # 检测向上看模式（连续多帧向上看）
        elif up_count >= BUFFER_SIZE * 0.7 and read_to_bottom:
            if now - last_action_time >= COOLDOWN_TIME:
                print("[ACTION] NEXT PAGE (Look Up)")
                send_key(ecodes.KEY_PAGEDOWN)
                last_action_time = now
                read_to_bottom = False  # 重置状态
                reference_iris_y = smooth_iris_y  # 更新参考位置
                eye_movement_buffer.clear()  # 清空缓冲区
            else:
                # 显示冷却状态
                remaining_time = COOLDOWN_TIME - (now - last_action_time)
                cv2.putText(frame, f"Cooldown: {remaining_time:.1f}s", (10, 150), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        
        # 如果眼睛回到中性位置且没有阅读到底部，更新参考位置
        elif up_count >= BUFFER_SIZE * 0.7 and not read_to_bottom:
            reference_iris_y = smooth_iris_y
        
        # 显示当前状态
        if read_to_bottom:
            status = "READ TO BOTTOM - LOOK UP TO TURN PAGE"
            status_color = (0, 255, 255)
        else:
            status = "READING"
            status_color = (255, 255, 255)
        
        cv2.putText(frame, f"Status: {status}", (10, 30), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)
        
        # 显示虹膜位置信息
        cv2.putText(frame, f"Smooth Iris Y: {smooth_iris_y:.4f}", (10, 90), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(frame, f"Relative Change: {relative_change:.4f}", (10, 120), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        # 显示阈值
        cv2.putText(frame, f"Threshold: +/-{IRIS_CHANGE_THRESHOLD:.4f}", (10, 180), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 100, 255), 1)
        
        # 显示缓冲区状态
        buffer_str = "Buffer: " + " ".join(eye_movement_buffer)
        cv2.putText(frame, buffer_str, (10, 210), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        
    else:
        # 如果没有检测到面部，显示提示
        cv2.putText(frame, "NO FACE DETECTED", (10, 30), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        # 重置参考位置
        reference_iris_y = None

    # 调试窗口
    cv2.imshow("Eye Control - Iris Movement Detection (Improved)", frame)
    
    # 按'q'退出，按'r'重置状态
    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('r'):
        read_to_bottom = False
        reference_iris_y = None
        eye_movement_buffer.clear()
        print("[INFO] State reset")

cap.release()
ui.close()
cv2.destroyAllWindows()