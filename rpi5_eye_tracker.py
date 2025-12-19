# rpi5_eye_tracker.py
import cv2
import mediapipe as mp
import numpy as np
import time
from collections import deque
import threading
import os
from wayland_input import RPiWaylandInput
import subprocess

class RPi5EyeTracker:
    """树莓派5优化的眼动追踪"""
    
    def __init__(self):
        print("初始化树莓派5眼动追踪...")
        
        # 初始化MediaPipe（树莓派优化设置）
        self.mp_face_mesh = mp.solutions.face_mesh
        self.mp_drawing = mp.solutions.drawing_utils
        
        # 针对树莓派5的优化配置
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=1,           # 只追踪一张脸
            refine_landmarks=True,     # 使用虹膜精炼
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
            static_image_mode=False    # 视频模式，性能更好
        )
        
        # 树莓派相机设置（使用较低分辨率以提高性能）
        self.cap = cv2.VideoCapture(2)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)   # 降低分辨率
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        self.cap.set(cv2.CAP_PROP_FPS, 30)            # 降低帧率
        
        # 获取屏幕信息
        self.screen_width, self.screen_height = self.get_screen_size()
        print(f"屏幕尺寸: {self.screen_width}x{self.screen_height}")
        
        # 初始化Wayland输入
        self.input_simulator = RPiWaylandInput()
        
        # 眼动追踪参数
        self.gaze_history = deque(maxlen=10)
        self.page_ready = False
        self.last_page_turn = 0
        self.cooldown_time = 2.0
        
        # 区域定义
        self.top_region = 0.15     # 顶部15%
        self.bottom_region = 0.85  # 底部15%
        
        # 状态变量
        self.bottom_start_time = None
        self.bottom_dwell_time = 0.8  # 底部停留时间（秒）
        
        # 性能统计
        self.frame_count = 0
        self.start_time = time.time()
        
        # 虹膜关键点索引
        self.LEFT_IRIS = [474, 475, 476, 477]
        self.RIGHT_IRIS = [469, 470, 471, 472]
        self.LEFT_EYE = [362, 382, 381, 380, 374, 373, 390, 249, 263]
        self.RIGHT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133]
        
    def get_screen_size(self):
        """获取树莓派屏幕尺寸"""
        try:
            # 尝试多种方法获取屏幕尺寸
            # 方法1：通过环境变量
            if 'WAYLAND_DISPLAY' in os.environ:
                # 使用wlr-randr获取信息
                try:
                    import subprocess
                    result = subprocess.run(
                        ['wlr-randr'],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    for line in result.stdout.split('\n'):
                        if 'current' in line and 'x' in line:
                            parts = line.strip().split()
                            for part in parts:
                                if 'x' in part and part[0].isdigit():
                                    w, h = map(int, part.split('x'))
                                    return w, h
                except:
                    pass
            
            # 方法2：通过配置文件
            config_paths = [
                '/boot/config.txt',
                '/boot/firmware/config.txt'
            ]
            
            for path in config_paths:
                if os.path.exists(path):
                    with open(path, 'r') as f:
                        for line in f:
                            if line.startswith('framebuffer_width'):
                                w = int(line.split('=')[1].strip())
                            elif line.startswith('framebuffer_height'):
                                h = int(line.split('=')[1].strip())
                                return w, h
            
            # 默认值
            return 1920, 1080
            
        except Exception as e:
            print(f"获取屏幕尺寸失败: {e}")
            return 1920, 1080
    
    def calculate_iris_center(self, landmarks, iris_indices):
        """计算虹膜中心"""
        iris_points = []
        for idx in iris_indices:
            landmark = landmarks.landmark[idx]
            x = landmark.x
            y = landmark.y
            iris_points.append((x, y))
        
        if not iris_points:
            return None
        
        center_x = sum([p[0] for p in iris_points]) / len(iris_points)
        center_y = sum([p[1] for p in iris_points]) / len(iris_points)
        
        return center_x, center_y
    
    def calculate_eye_center(self, landmarks, eye_indices):
        """计算眼睛中心"""
        eye_points = []
        for idx in eye_indices:
            landmark = landmarks.landmark[idx]
            x = landmark.x
            y = landmark.y
            eye_points.append((x, y))
        
        if not eye_points:
            return None
        
        center_x = sum([p[0] for p in eye_points]) / len(eye_points)
        center_y = sum([p[1] for p in eye_points]) / len(eye_points)
        
        return center_x, center_y
    
    def estimate_gaze_direction(self, iris_center, eye_center):
        """估算注视方向"""
        if iris_center is None or eye_center is None:
            return 0.5, 0.5  # 默认中心位置
        
        # 计算虹膜相对于眼睛中心的偏移
        dx = iris_center[0] - eye_center[0]
        dy = iris_center[1] - eye_center[1]
        
        # 归一化到屏幕坐标
        # 注意：这需要根据个人校准调整
        gaze_x = 0.5 + dx * 3.0  # 调整系数
        gaze_y = 0.5 + dy * 3.0
        
        # 限制在有效范围内
        gaze_x = np.clip(gaze_x, 0.0, 1.0)
        gaze_y = np.clip(gaze_y, 0.0, 1.0)
        
        return gaze_x, gaze_y
    
    def detect_blink(self, landmarks):
        """检测眨眼"""
        try:
            # 计算眼睛纵横比
            def eye_aspect_ratio(eye_points):
                # 垂直距离
                A = np.linalg.norm(
                    np.array([landmarks.landmark[eye_points[1]].x, 
                             landmarks.landmark[eye_points[1]].y]) -
                    np.array([landmarks.landmark[eye_points[5]].x, 
                             landmarks.landmark[eye_points[5]].y])
                )
                B = np.linalg.norm(
                    np.array([landmarks.landmark[eye_points[2]].x, 
                             landmarks.landmark[eye_points[2]].y]) -
                    np.array([landmarks.landmark[eye_points[4]].x, 
                             landmarks.landmark[eye_points[4]].y])
                )
                # 水平距离
                C = np.linalg.norm(
                    np.array([landmarks.landmark[eye_points[0]].x, 
                             landmarks.landmark[eye_points[0]].y]) -
                    np.array([landmarks.landmark[eye_points[3]].x, 
                             landmarks.landmark[eye_points[3]].y])
                )
                
                ear = (A + B) / (2.0 * C)
                return ear
            
            left_ear = eye_aspect_ratio(self.LEFT_EYE[:6])
            right_ear = eye_aspect_ratio(self.RIGHT_EYE[:6])
            
            # 眨眼阈值
            return left_ear < 0.2 and right_ear < 0.2
            
        except:
            return False
    
    def run(self):
        """主循环"""
        print("开始眼动追踪...")
        print("按 'q' 键退出")
        print("按 'c' 键校准")
        print("按 'r' 键重置状态")
        
        # 性能监控
        fps_update_interval = 30
        
        while True:
            ret, frame = self.cap.read()
            if not ret:
                print("无法读取摄像头")
                break
            
            # 性能统计
            self.frame_count += 1
            if self.frame_count % fps_update_interval == 0:
                elapsed = time.time() - self.start_time
                fps = fps_update_interval / elapsed
                print(f"FPS: {fps:.1f}")
                self.start_time = time.time()
            
            # 图像预处理
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame_rgb.flags.writeable = False
            
            # 面部检测
            results = self.face_mesh.process(frame_rgb)
            
            # 恢复可写状态
            frame_rgb.flags.writeable = True
            frame_display = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
            
            current_time = time.time()
            gaze_detected = False
            
            if results.multi_face_landmarks:
                for face_landmarks in results.multi_face_landmarks:
                    # 跳过眨眼时刻
                    if self.detect_blink(face_landmarks):
                        continue
                    
                    # 计算左眼注视
                    left_iris = self.calculate_iris_center(
                        face_landmarks, self.LEFT_IRIS
                    )
                    left_eye_center = self.calculate_eye_center(
                        face_landmarks, self.LEFT_EYE
                    )
                    
                    # 计算右眼注视
                    right_iris = self.calculate_iris_center(
                        face_landmarks, self.RIGHT_IRIS
                    )
                    right_eye_center = self.calculate_eye_center(
                        face_landmarks, self.RIGHT_EYE
                    )
                    
                    if (left_iris and left_eye_center and 
                        right_iris and right_eye_center):
                        
                        # 计算双眼平均注视
                        left_gaze = self.estimate_gaze_direction(
                            left_iris, left_eye_center
                        )
                        right_gaze = self.estimate_gaze_direction(
                            right_iris, right_eye_center
                        )
                        
                        gaze_x = (left_gaze[0] + right_gaze[0]) / 2
                        gaze_y = (left_gaze[1] + right_gaze[1]) / 2
                        
                        # 添加到历史记录
                        self.gaze_history.append((gaze_x, gaze_y))
                        gaze_detected = True
                        
                        # 平滑处理
                        if len(self.gaze_history) >= 3:
                            smooth_x = np.mean([g[0] for g in list(self.gaze_history)[-3:]])
                            smooth_y = np.mean([g[1] for g in list(self.gaze_history)[-3:]])
                            
                            # 转换为像素坐标
                            pixel_x = int(smooth_x * self.screen_width)
                            pixel_y = int(smooth_y * self.screen_height)
                            
                            # 在画面上显示注视点
                            display_x = int(smooth_x * frame.shape[1])
                            display_y = int(smooth_y * frame.shape[0])
                            cv2.circle(frame_display, (display_x, display_y), 
                                      5, (0, 255, 0), -1)
                            
                            # 检测注视区域
                            if smooth_y > self.bottom_region:
                                if self.bottom_start_time is None:
                                    self.bottom_start_time = current_time
                                elif (current_time - self.bottom_start_time > 
                                      self.bottom_dwell_time):
                                    if not self.page_ready:
                                        self.show_prompt("向上看，翻下一页")
                                        self.page_ready = True
                                        self.bottom_start_time = None
                            else:
                                self.bottom_start_time = None
                            
                            # 检测顶部注视并翻页
                            if (smooth_y < self.top_region and 
                                self.page_ready and 
                                current_time - self.last_page_turn > self.cooldown_time):
                                
                                self.trigger_page_turn()
                                self.page_ready = False
                                self.last_page_turn = current_time
            
            # 绘制状态信息
            self.draw_status(frame_display)
            
            # 显示帧
            cv2.imshow('RPi5 Eye Tracker', frame_display)
            
            # 键盘控制
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('c'):
                self.calibrate()
            elif key == ord('r'):
                self.page_ready = False
                print("状态已重置")
            elif key == ord('t'):
                # 手动测试翻页
                self.input_simulator.press_key('PAGE_DOWN')
        
        self.cleanup()
    
    def show_prompt(self, message):
        """显示提示信息"""
        print(f"\n{'='*50}")
        print(f"提示: {message}")
        print(f"{'='*50}\n")
        
        # 发送系统通知
        try:
            subprocess.run(['notify-send', '眼动控制', message])
        except:
            pass
    
    def trigger_page_turn(self):
        """触发翻页"""
        print("翻页！")
        self.input_simulator.press_key('PAGE_DOWN')
    
    def calibrate(self):
        """简单校准"""
        print("校准：请注视屏幕中心，然后按 Enter")
        input("准备好后按 Enter...")
        
        if len(self.gaze_history) > 0:
            # 取最近5个点的平均值作为校准参考
            recent_gazes = list(self.gaze_history)[-5:]
            avg_x = np.mean([g[0] for g in recent_gazes])
            avg_y = np.mean([g[1] for g in recent_gazes])
            
            # 计算校准偏移
            offset_x = 0.5 - avg_x
            offset_y = 0.5 - avg_y
            
            # 应用校准（这里简单打印，实际可以保存和应用）
            print(f"校准偏移: X={offset_x:.3f}, Y={offset_y:.3f}")
            print("注意：完整校准需要更复杂的多点校准")
    
    def draw_status(self, frame):
        """在画面上绘制状态信息"""
        status = "准备翻页" if self.page_ready else "阅读中"
        color = (0, 255, 0) if self.page_ready else (0, 165, 255)
        
        cv2.putText(frame, f"状态: {status}", (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        
        if self.page_ready:
            cv2.putText(frame, "向上看以翻页", (10, 60),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        
        # 显示帧率
        elapsed = time.time() - self.start_time
        if elapsed > 0:
            fps = self.frame_count / elapsed
            cv2.putText(frame, f"FPS: {fps:.1f}", (10, 90),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    
    def cleanup(self):
        """清理资源"""
        self.cap.release()
        cv2.destroyAllWindows()
        self.face_mesh.close()
        print("眼动追踪已停止")

# 安装和运行脚本
def install_requirements():
    """安装所需依赖"""
    print("安装树莓派5眼动控制依赖...")
    
    commands = [
        "sudo apt update",
        "sudo apt install -y python3-pip python3-opencv wtype wev",
        "sudo apt install -y python3-evdev libinput-tools",
        "sudo apt install -y libnotify-bin",  # 用于通知
        "pip3 install mediapipe numpy opencv-python"
    ]
    
    for cmd in commands:
        print(f"执行: {cmd}")
        os.system(cmd)
    
    print("安装完成！")
    print("注意：可能需要重新登录或重启以使权限生效")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--install":
        install_requirements()
    else:
        tracker = RPi5EyeTracker()
        tracker.run()