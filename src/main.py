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

# 调整阈值，使小幅移动也能检测到
GAZE_THRESHOLD_X = 0.10     
GAZE_THRESHOLD_Y = 0.05     # 降低垂直方向阈值，使小幅上下移动更容易被检测
HOLD_TIME = 0.3             # 减少持续凝视时间，增加响应速度
COOLDOWN_TIME = 0.5         # 翻页冷却时间

# 眼球运动参数 - 重点优化小幅快速移动检测
last_gaze_position = None
gaze_change_time = 0
QUICK_GAZE_THRESHOLD = 0.08  # 降低快速眼球运动阈值，小幅移动也能触发
QUICK_GAZE_TIME = 0.3        # 快速眼球运动判断时间

# 存储参考位置的变量
reference_eye_y = None
reference_update_counter = 0
REFERENCE_UPDATE_INTERVAL = 20  # 更频繁地更新参考位置

# 添加专门用于检测从下到上移动的变量
was_looking_down_recently = False
LOOK_DOWN_THRESHOLD = 0.12  # 向下看的阈值
LOOK_UP_THRESHOLD = -0.08   # 向上看的阈值（负值表示向上）

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

# 面部关键点：左右眼角
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

# ======================
# 状态机变量
# ======================
last_action_time = 0
gaze_start_time = 0
gaze_direction = None
reset_required = False  # 是否需要复位到中心区域

def get_landmark_coords(landmarks, landmark_idx, frame_width, frame_height):
    """获取特定关键点的坐标"""
    x = landmarks[landmark_idx].x * frame_width
    y = landmarks[landmark_idx].y * frame_height
    return x, y

def iris_center(landmarks, idxs):
    pts = np.array([(landmarks[i].x, landmarks[i].y) for i in idxs])
    return pts.mean(axis=0)

def eye_center(landmarks, idxs):
    """计算眼睛中心点"""
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

    if result.multi_face_landmarks:
        lm = result.multi_face_landmarks[0].landmark
        
        # 获取双眼中心点
        left_eye_x, left_eye_y = eye_center(lm, LEFT_EYE)
        right_eye_x, right_eye_y = eye_center(lm, RIGHT_EYE)
        
        # 计算平均眼部Y坐标
        avg_eye_y = (left_eye_y + right_eye_y) / 2.0 * FRAME_HEIGHT
        
        # 绘制眼部中心点
        cv2.circle(frame, (int(left_eye_x * FRAME_WIDTH), int(left_eye_y * FRAME_HEIGHT)), 5, (255, 0, 0), -1)
        cv2.circle(frame, (int(right_eye_x * FRAME_WIDTH), int(right_eye_y * FRAME_HEIGHT)), 5, (255, 0, 0), -1)

        # 更新参考眼部位置（每20帧更新一次）
        if reference_update_counter % REFERENCE_UPDATE_INTERVAL == 0:
            reference_eye_y = avg_eye_y
            reference_update_counter = 0
        
        reference_update_counter += 1

        # 检测快速眼球运动
        if last_gaze_position is not None:
            # 计算眼球垂直移动距离
            eye_movement = avg_eye_y - last_gaze_position[1]
            movement_time = now - gaze_change_time
            
            # 如果移动足够快，触发翻页
            if movement_time < QUICK_GAZE_TIME and abs(eye_movement) > QUICK_GAZE_THRESHOLD * FRAME_HEIGHT:
                if now - last_action_time >= COOLDOWN_TIME:
                    if eye_movement < 0:  # 眼球快速由下到上（数值变小）
                        print("[QUICK GAZE] BACKWARD PAGE")
                        send_key(ecodes.KEY_PAGEUP)
                    else:  # 眼球快速由上到下（数值变大）
                        print("[QUICK GAZE] FORWARD PAGE")
                        send_key(ecodes.KEY_PAGEDOWN)
                    
                    last_action_time = now
                    # 重置参考位置
                    reference_eye_y = avg_eye_y
                    last_gaze_position = (avg_eye_y, avg_eye_y)
                else:
                    # 在冷却时间内，重置参考位置避免误操作
                    reference_eye_y = avg_eye_y
            else:
                # 检测持续凝视方向
                if reference_eye_y is not None:
                    eye_offset_y = avg_eye_y - reference_eye_y
                    
                    # 检查是否在中心区域（复位区域）
                    in_center_y = abs(eye_offset_y) < GAZE_THRESHOLD_Y * FRAME_HEIGHT / 2
                    
                    # 检测是否近期向下看过（表示正在阅读底部）
                    if eye_offset_y > LOOK_DOWN_THRESHOLD * FRAME_HEIGHT:
                        was_looking_down_recently = True
                    
                    # 如果需要复位，只有回到中心区域才能继续检测其他方向
                    if reset_required:
                        if in_center_y:
                            reset_required = False
                            print("[INFO] Reset to center area, ready for next action")
                        else:
                            # 仍在复位过程中，跳过方向检测
                            cv2.putText(frame, "Resetting...", (10, 120), 
                                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2)
                    else:
                        direction = None
                        
                        # 检测眼部位置变化
                        if eye_offset_y > GAZE_THRESHOLD_Y * FRAME_HEIGHT:
                            direction = "DOWN"   # 眼球向下，向前翻页
                        elif eye_offset_y < LOOK_UP_THRESHOLD * FRAME_HEIGHT:  # 使用不同的向上阈值
                            direction = "UP"     # 眼球向上，向后翻页

                        if direction:
                            if gaze_direction != direction:
                                gaze_direction = direction
                                gaze_start_time = now
                                # 在画面上显示注视方向提示
                                cv2.putText(frame, f"Eyes looking {direction}", (10, 30), 
                                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                            else:
                                elapsed_time = now - gaze_start_time
                                # 显示注视计时
                                cv2.putText(frame, f"Eyes looking {direction}: {elapsed_time:.1f}s", (10, 30), 
                                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 2)
                                
                                # 检测从下到上的特殊翻页模式：如果之前在底部，现在在顶部，则直接翻页
                                if was_looking_down_recently and direction == "UP":
                                    # 如果最近向下看过，现在向上看，可以更快触发翻页
                                    if elapsed_time >= HOLD_TIME * 0.6:  # 60% 的时间就可以触发
                                        if now - last_action_time >= COOLDOWN_TIME:
                                            print("[SPECIAL ACTION] NEXT PAGE (from bottom to top)")
                                            send_key(ecodes.KEY_PAGEDOWN)
                                            
                                            # 重置状态
                                            was_looking_down_recently = False
                                            reset_required = True
                                            last_action_time = now
                                            gaze_start_time = 0
                                            gaze_direction = None
                                
                                if elapsed_time >= HOLD_TIME:
                                    if now - last_action_time >= COOLDOWN_TIME:
                                        if direction == "DOWN":  # 眼球向下看，向前翻页
                                            print("[ACTION] NEXT PAGE")
                                            send_key(ecodes.KEY_PAGEDOWN)
                                        elif direction == "UP":  # 眼球向上看，向后翻页
                                            print("[ACTION] PREV PAGE")
                                            send_key(ecodes.KEY_PAGEUP)

                                        # 设置复位需求
                                        reset_required = True
                                        last_action_time = now
                                        gaze_start_time = 0
                                        gaze_direction = None
                                        print(f"[DEBUG] Action performed: {direction}, waiting for reset")
                                    else:
                                        remaining_time = COOLDOWN_TIME - (now - last_action_time)
                                        cv2.putText(frame, f"Cooldown: {remaining_time:.1f}s", (10, 90), 
                                                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                        else:
                            # 显示当前眼部相对位置
                            if reference_eye_y is not None:
                                eye_offset_y = avg_eye_y - reference_eye_y
                                
                                if reset_required:
                                    # 正在等待复位
                                    cv2.putText(frame, "RESET REQUIRED", (10, 30), 
                                               cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                                    
                                    # 显示当前偏移量
                                    cv2.putText(frame, f"Offset Y: {eye_offset_y:.1f}px", (10, 60), 
                                               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                                else:
                                    if abs(eye_offset_y) < GAZE_THRESHOLD_Y * FRAME_HEIGHT / 2:
                                        side_indicator = "CENTER"
                                    else:
                                        if eye_offset_y > 0:
                                            side_indicator = "DOWN (forward page)"
                                        else:
                                            side_indicator = "UP (backward page)"
                                        
                                    cv2.putText(frame, f"Eyes: {side_indicator}", (10, 30), 
                                               cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                                    
                                    # 显示当前偏移量
                                    cv2.putText(frame, f"Offset Y: {eye_offset_y:.1f}px", (10, 60), 
                                               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                            
                            gaze_start_time = 0
                            gaze_direction = None
        
        # 更新上次眼部位置
        last_gaze_position = (left_eye_x * FRAME_WIDTH, avg_eye_y)
        gaze_change_time = now
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