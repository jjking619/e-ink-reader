#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
眼动阅读器控制模块
基于main.py的界面设计风格，实现眼动控制文档翻页功能
"""

import sys
import os
import cv2
import numpy as np
from collections import deque
import time
from datetime import datetime

# 添加项目路径
sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout, 
    QHBoxLayout, QGroupBox, QFrame, QSplitter, QGridLayout, QMessageBox,QCheckBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QImage, QPixmap

from wayland_input import RPiWaylandInput

try:
    import mediapipe as mp
except ImportError:
    print("警告: 未安装MediaPipe，眼动追踪功能将不可用")
    mp = None


class EyeTrackingWorker(QObject):
    """
    眼动追踪工作线程
    """
    frame_ready = Signal(object)
    detection_status = Signal(dict)
    fps_updated = Signal(float)
    
    def __init__(self):
        super().__init__()
        self.running = False
        self.detecting = True
        self.show_landmarks = True
        self.cap = None
        self.fps = 0
        self.frame_count = 0
        self.last_fps_time = time.time()
        
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
        
        # 输入模拟器
        self.input_simulator = RPiWaylandInput()
        
        if mp:
            try:
                # 初始化MediaPipe Face Mesh
                self.mp_face_mesh = mp.solutions.face_mesh
                self.face_mesh = self.mp_face_mesh.FaceMesh(
                    max_num_faces=1,
                    refine_landmarks=True,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5,
                    static_image_mode=False
                )
                
                # 初始化绘图模块
                self.mp_drawing = mp.solutions.drawing_utils
                self.mp_drawing_styles = mp.solutions.drawing_styles
                
                # 虹膜关键点索引
                self.LEFT_IRIS = [474, 475, 476, 477]
                self.RIGHT_IRIS = [469, 470, 471, 472]
                self.LEFT_EYE = [362, 382, 381, 380, 374, 373, 390, 249, 263]
                self.RIGHT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133]
            except Exception as e:
                print(f"初始化MediaPipe绘图模块失败: {e}")
                self.mp_drawing = None
                self.mp_drawing_styles = None
        else:
            self.mp_drawing = None
            self.mp_drawing_styles = None
            self.face_mesh = None
    
    def start_capture(self):
        """启动摄像头捕获"""
        if self.cap is None:
            # 尝试不同的摄像头索引
            for i in range(3):
                try:
                    self.cap = cv2.VideoCapture(i)
                    if self.cap.isOpened():
                        # 设置较低的分辨率以提高性能
                        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
                        self.cap.set(cv2.CAP_PROP_FPS, 30)
                        print(f"成功打开摄像头 {i}")
                        break
                    else:
                        self.cap.release()
                        self.cap = None
                except Exception as e:
                    print(f"尝试打开摄像头 {i} 失败: {e}")
                    if self.cap:
                        self.cap.release()
                    self.cap = None
            
            if self.cap is None or not self.cap.isOpened():
                raise Exception("无法打开任何摄像头")
        
        self.running = True
        self.run()
    
    def stop_capture(self):
        """停止摄像头捕获"""
        self.running = False
        if self.cap:
            self.cap.release()
            self.cap = None
    
    def toggle_detection(self, detecting):
        """切换检测状态"""
        self.detecting = detecting
    
    def toggle_landmarks(self, show):
        """切换特征点显示"""
        self.show_landmarks = show
    
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
    
    def trigger_page_turn(self, direction="down"):
        """触发翻页"""
        print(f"翻页: {direction}")
        if direction == "down":
            self.input_simulator.press_key('PAGE_DOWN')
        elif direction == "up":
            self.input_simulator.press_key('PAGE_UP')
    
    def run(self):
        """主运行循环"""
        while self.running and self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.01)
                continue
            
            # 计算FPS
            self.frame_count += 1
            current_time = time.time()
            if current_time - self.last_fps_time >= 1.0:
                self.fps = self.frame_count / (current_time - self.last_fps_time)
                self.frame_count = 0
                self.last_fps_time = current_time
                self.fps_updated.emit(self.fps)
            
            processed_frame = frame.copy()
            detection_result = {"face_detected": False}
            
            if self.detecting and mp:
                try:
                    # 图像预处理
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    frame_rgb.flags.writeable = False
                    
                    # 面部检测
                    results = self.face_mesh.process(frame_rgb)
                    
                    # 恢复可写状态
                    frame_rgb.flags.writeable = True
                    
                    if results.multi_face_landmarks:
                        detection_result["face_detected"] = True
                        
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
                                
                                # 平滑处理
                                if len(self.gaze_history) >= 3:
                                    smooth_x = np.mean([g[0] for g in list(self.gaze_history)[-3:]])
                                    smooth_y = np.mean([g[1] for g in list(self.gaze_history)[-3:]])
                                    
                                    # 在画面上显示注视点
                                    if self.show_landmarks:
                                        display_x = int(smooth_x * frame.shape[1])
                                        display_y = int(smooth_y * frame.shape[0])
                                        cv2.circle(processed_frame, (display_x, display_y), 
                                                  5, (0, 255, 0), -1)
                                    
                                    # 检测注视区域
                                    if smooth_y > self.bottom_region:
                                        if self.bottom_start_time is None:
                                            self.bottom_start_time = current_time
                                        elif (current_time - self.bottom_start_time > 
                                              self.bottom_dwell_time):
                                            if not self.page_ready:
                                                print("准备翻页：向上看以翻页")
                                                self.page_ready = True
                                                self.bottom_start_time = None
                                    else:
                                        self.bottom_start_time = None
                                    
                                    # 检测顶部注视并翻页
                                    if (smooth_y < self.top_region and 
                                        self.page_ready and 
                                        current_time - self.last_page_turn > self.cooldown_time):
                                        
                                        self.trigger_page_turn("down")
                                        self.page_ready = False
                                        self.last_page_turn = current_time
                                        
                                        # 发送状态更新
                                        detection_result["page_turn"] = "down"
                            
                            # 绘制面部网格
                            if self.show_landmarks:
                                self.mp_drawing.draw_landmarks(
                                    image=processed_frame,
                                    landmark_list=face_landmarks,
                                    connections=self.mp_face_mesh.FACEMESH_TESSELATION,
                                    landmark_drawing_spec=None,
                                    connection_drawing_spec=self.mp_drawing_styles
                                    .get_default_face_mesh_tesselation_style())
                
                except Exception as e:
                    print(f"处理帧时出错: {e}")
            
            # 发送处理后的帧和检测结果
            self.frame_ready.emit(processed_frame)
            self.detection_status.emit(detection_result)


class EyeReaderControl(QMainWindow):
    """眼动阅读器控制主窗口"""
    
    def __init__(self):
        super().__init__()
        self.eye_worker = EyeTrackingWorker()
        self.camera_active = False
        
        # 连接信号
        self.eye_worker.frame_ready.connect(self.update_camera_frame)
        self.eye_worker.detection_status.connect(self.update_detection_status)
        self.eye_worker.fps_updated.connect(self.update_fps_display)
        
        self.setup_styles()
        self.init_ui()
        
        # 状态更新定时器
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(500)
        
        # # 显示窗口
        # self.show()
        
        # # 在窗口显示后再尝试启动摄像头，避免阻塞界面显示
        # QTimer.singleShot(100, self.auto_start_camera)
    
    def setup_styles(self):
        """设置界面样式"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e2e;
            }
            QLabel {
                color: #cdd6f4;
            }
            QGroupBox {
                color: #89b4fa;
                font-weight: bold;
                border: 2px solid #585b70;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: #313244;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 15px;
                padding: 0 5px 0 5px;
            }
            QPushButton {
                background-color: #585b70;
                color: #cdd6f4;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #6c7086;
            }
            QPushButton:pressed {
                background-color: #45475a;
            }
            QPushButton:disabled {
                background-color: #313244;
                color: #7f849c;
            }
            QCheckBox {
                color: #cdd6f4;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #585b70;
            }
            QCheckBox::indicator:checked {
                background-color: #89b4fa;
                border-color: #89b4fa;
            }
            QFrame#status_frame {
                background-color: #313244;
                border-radius: 8px;
                border: 1px solid #585b70;
            }
            QLabel#status_value {
                font-weight: bold;
                padding: 2px 8px;
                border-radius: 4px;
            }
            QProgressBar {
                border: 1px solid #585b70;
                border-radius: 4px;
                text-align: center;
                background-color: #313244;
            }
            QProgressBar::chunk {
                background-color: #89b4fa;
                border-radius: 4px;
            }
        """)
    
    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle('👁️ Eye Reader Control')
        self.setGeometry(100, 100, 1200, 800)
        
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # 标题栏
        title_frame = QFrame()
        title_frame.setFixedHeight(50)
        title_frame.setStyleSheet("background-color: #313244; border-radius: 8px;")
        
        title_layout = QHBoxLayout(title_frame)
        
        title_label = QLabel("👁️ Eye Reader Control - 眼动翻页阅读器")
        title_label.setStyleSheet("color: #89b4fa; font-size: 18px; font-weight: bold;")
        
        # 全屏按钮
        self.fullscreen_btn = QPushButton("全屏模式")
        self.fullscreen_btn.setFixedSize(120, 30)
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        self.fullscreen_btn.setStyleSheet("""
            QPushButton {
                background-color: #89b4fa;
                color: #1e1e2e;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #74c7ec;
            }
        """)
        
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        title_layout.addWidget(self.fullscreen_btn)
        main_layout.addWidget(title_frame)
        
        # 主内容区域 - 水平分割
        content_splitter = QSplitter(Qt.Horizontal)
        
        # 左侧 - 视频显示区域
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(10)
        
        # 摄像头显示区域
        camera_group = QGroupBox("📷 摄像头画面")
        camera_layout = QVBoxLayout()
        
        self.camera_display = QLabel("正在启动摄像头...")
        self.camera_display.setAlignment(Qt.AlignCenter)
        self.camera_display.setMinimumSize(640, 360)
        self.camera_display.setStyleSheet("""
            QLabel {
                background-color: #000000;
                border-radius: 8px;
                border: 2px solid #585b70;
                color: #ffffff;
                font-size: 14px;
            }
        """)
        
        camera_layout.addWidget(self.camera_display)
        camera_group.setLayout(camera_layout)
        left_layout.addWidget(camera_group)
        
        left_layout.addStretch()
        
        # 右侧 - 控制面板
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(15)
        
        # 实时状态显示
        status_group = QGroupBox("📊 系统状态")
        status_layout = QGridLayout()
        
        # 摄像头状态
        cam_status_label = QLabel("📷 摄像头:")
        cam_status_label.setStyleSheet("color: #a6adc8;")
        
        self.cam_status = QLabel("运行中")
        self.cam_status.setObjectName("status_value")
        self.cam_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
        self.cam_status.setFixedSize(120, 25)
        
        # FPS显示
        fps_label = QLabel("⚡ 帧率:")
        fps_label.setStyleSheet("color: #a6adc8;")
        
        self.fps_display = QLabel("0.0")
        self.fps_display.setObjectName("status_value")
        self.fps_display.setStyleSheet("background-color: #cba6f7; color: #000000;")
        self.fps_display.setFixedSize(120, 25)
        
        # 检测状态
        detect_status_label = QLabel("🔍 检测状态:")
        detect_status_label.setStyleSheet("color: #a6adc8;")
        
        self.detect_status = QLabel("检测中...")
        self.detect_status.setObjectName("status_value")
        self.detect_status.setStyleSheet("background-color: #f9e2af; color: #000000;")
        self.detect_status.setFixedSize(120, 25)
        
        # 眼睛状态
        eye_status_label = QLabel("👁️ 眼睛状态:")
        eye_status_label.setStyleSheet("color: #a6adc8;")
        
        self.eye_status = QLabel("未检测")
        self.eye_status.setObjectName("status_value")
        self.eye_status.setStyleSheet("background-color: #f9e2af; color: #000000;")
        self.eye_status.setFixedSize(120, 25)
        
        # 注视状态
        gaze_status_label = QLabel("🎯 注视状态:")
        gaze_status_label.setStyleSheet("color: #a6adc8;")
        
        self.gaze_status = QLabel("未注视")
        self.gaze_status.setObjectName("status_value")
        self.gaze_status.setStyleSheet("background-color: #f9e2af; color: #000000;")
        self.gaze_status.setFixedSize(120, 25)
        
        # 翻页状态
        page_status_label = QLabel("📄 翻页状态:")
        page_status_label.setStyleSheet("color: #a6adc8;")
        
        self.page_status = QLabel("就绪")
        self.page_status.setObjectName("status_value")
        self.page_status.setStyleSheet("background-color: #89dceb; color: #000000;")
        self.page_status.setFixedSize(120, 25)
        
        # 添加到网格布局
        status_layout.addWidget(cam_status_label, 0, 0)
        status_layout.addWidget(self.cam_status, 0, 1)
        status_layout.addWidget(fps_label, 0, 2)
        status_layout.addWidget(self.fps_display, 0, 3)
        
        status_layout.addWidget(detect_status_label, 1, 0)
        status_layout.addWidget(self.detect_status, 1, 1)
        status_layout.addWidget(eye_status_label, 1, 2)
        status_layout.addWidget(self.eye_status, 1, 3)
        
        status_layout.addWidget(gaze_status_label, 2, 0)
        status_layout.addWidget(self.gaze_status, 2, 1)
        status_layout.addWidget(page_status_label, 2, 2)
        status_layout.addWidget(self.page_status, 2, 3)
        
        status_group.setLayout(status_layout)
        right_layout.addWidget(status_group)
        
        # 控制说明
        instruction_group = QGroupBox("📋 控制说明")
        instruction_layout = QVBoxLayout()
        
        instructions = QLabel(
            "<b>眼动翻页控制:</b><br>"
            "• 注视屏幕底部区域 → 准备翻页<br>"
            "• 准备状态下注视屏幕顶部 → 执行翻页<br>"
            "• 眨眼不会触发翻页操作<br><br>"
            "<b>手动控制:</b><br>"
            "• 使用下方按钮手动翻页<br>"
            "• 可随时启用/禁用眼动检测"
        )
        instructions.setStyleSheet("color: #cdd6f4; padding: 5px;")
        instructions.setWordWrap(True)
        
        instruction_layout.addWidget(instructions)
        instruction_group.setLayout(instruction_layout)
        right_layout.addWidget(instruction_group)
        
        # 控制按钮
        control_group = QGroupBox("🎮 控制按钮")
        control_layout = QVBoxLayout()
        
        # 摄像头控制按钮
        self.camera_toggle_btn = QPushButton("关闭摄像头")
        self.camera_toggle_btn.clicked.connect(self.toggle_camera)
        self.camera_toggle_btn.setFixedHeight(35)
        
        # 眼动检测开关
        self.detect_checkbox = QCheckBox("启用眼动检测")
        self.detect_checkbox.setChecked(True)
        self.detect_checkbox.stateChanged.connect(self.toggle_detection)
        
        # 特征点显示开关
        self.landmarks_checkbox = QCheckBox("显示特征点")
        self.landmarks_checkbox.setChecked(True)
        self.landmarks_checkbox.stateChanged.connect(self.toggle_landmarks)
        
        # 手动翻页按钮
        page_control_layout = QHBoxLayout()
        self.prev_page_btn = QPushButton("上一页")
        self.prev_page_btn.clicked.connect(self.previous_page)
        self.prev_page_btn.setFixedHeight(40)
        
        self.next_page_btn = QPushButton("下一页")
        self.next_page_btn.clicked.connect(self.next_page)
        self.next_page_btn.setFixedHeight(40)
        
        page_control_layout.addWidget(self.prev_page_btn)
        page_control_layout.addWidget(self.next_page_btn)
        
        control_layout.addWidget(self.camera_toggle_btn)
        control_layout.addWidget(self.detect_checkbox)
        control_layout.addWidget(self.landmarks_checkbox)
        control_layout.addLayout(page_control_layout)
        
        control_group.setLayout(control_layout)
        right_layout.addWidget(control_group)
        
        right_layout.addStretch()
        
        # 添加到分割器
        content_splitter.addWidget(left_widget)
        content_splitter.addWidget(right_widget)
        content_splitter.setSizes([800, 400])
        
        main_layout.addWidget(content_splitter)
        
        # 状态栏
        self.statusBar().showMessage("就绪")
        
        # 设置全屏快捷键
        self.fullscreen_btn.setShortcut("F11")
    
    def auto_start_camera(self):
        """自动启动摄像头"""
        try:
            self.eye_worker.start_capture()
            self.camera_active = True
            self.camera_toggle_btn.setText("关闭摄像头")
            self.cam_status.setText("运行中")
            self.cam_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
        except Exception as e:
            self.camera_active = False  # 确保状态正确
            self.cam_status.setText("启动失败")
            self.cam_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
            self.camera_display.setText(f"摄像头启动失败: {str(e)}")
            # 不使用弹窗，避免干扰界面显示
            print(f"摄像头启动失败: {str(e)}")
    
    def toggle_camera(self):
        """切换摄像头开关"""
        if self.camera_active:
            self.stop_camera()
        else:
            self.start_camera()
    
    def start_camera(self):
        """启动摄像头"""
        try:
            self.eye_worker.start_capture()
            self.camera_active = True
            self.camera_toggle_btn.setText("关闭摄像头")
            self.cam_status.setText("运行中")
            self.cam_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
            # 启用检测时更新检测状态
            if self.detect_checkbox.isChecked():
                self.detect_status.setText("检测中")
                self.detect_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
        except Exception as e:
            self.camera_active = False  # 确保状态正确
            self.cam_status.setText("启动失败")
            self.cam_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
            # 不使用弹窗，避免干扰界面显示
            print(f"摄像头启动失败: {str(e)}")
    
    def stop_camera(self):
        """停止摄像头"""
        self.eye_worker.stop_capture()
        self.camera_active = False
        self.camera_toggle_btn.setText("启动摄像头")
        self.cam_status.setText("已停止")
        self.cam_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
        self.camera_display.setText("摄像头已关闭")
        self.camera_display.setPixmap(QPixmap())
        # 停止摄像头时更新检测状态
        self.detect_status.setText("摄像头关闭")
        self.detect_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
    
    def toggle_detection(self, state):
        """切换检测状态"""
        is_detecting = state == Qt.CheckState.Checked.value
        self.eye_worker.toggle_detection(is_detecting)
        if is_detecting:
            self.detect_status.setText("检测中")
            self.detect_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
        else:
            self.detect_status.setText("已禁用")
            self.detect_status.setStyleSheet("background-color: #f38ba8; color: #000000;")
    
    def toggle_landmarks(self, state):
        """切换特征点显示"""
        self.eye_worker.toggle_landmarks(state == Qt.CheckState.Checked.value)
    
    def next_page(self):
        """下一页"""
        self.eye_worker.trigger_page_turn("down")
        self.page_status.setText("下一页")
        self.page_status.setStyleSheet("background-color: #89b4fa; color: #000000;")
        # 1秒后恢复状态
        QTimer.singleShot(1000, lambda: (
            self.page_status.setText("就绪"),
            self.page_status.setStyleSheet("background-color: #89dceb; color: #000000;")
        ))
    
    def previous_page(self):
        """上一页"""
        self.eye_worker.trigger_page_turn("up")
        self.page_status.setText("上一页")
        self.page_status.setStyleSheet("background-color: #89b4fa; color: #000000;")
        # 1秒后恢复状态
        QTimer.singleShot(1000, lambda: (
            self.page_status.setText("就绪"),
            self.page_status.setStyleSheet("background-color: #89dceb; color: #000000;")
        ))
    
    def update_camera_frame(self, frame):
        """更新摄像头画面"""
        self.display_frame(self.camera_display, frame)
    
    def display_frame(self, label, frame):
        """在指定标签上显示帧"""
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qt_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qt_image)
        
        scaled_pixmap = pixmap.scaled(
            label.size(), 
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        
        label.setPixmap(scaled_pixmap)
    
    def update_detection_status(self, detection_result):
        """更新检测状态"""
        if detection_result.get("face_detected", False):
            self.eye_status.setText("已检测")
            self.eye_status.setStyleSheet("background-color: #a6e3a1; color: #000000;")
            
            # 检查是否有翻页操作
            if "page_turn" in detection_result:
                direction = detection_result["page_turn"]
                if direction == "down":
                    self.page_status.setText("下一页")
                    self.page_status.setStyleSheet("background-color: #89b4fa; color: #000000;")
                    # 1秒后恢复状态
                    QTimer.singleShot(1000, lambda: (
                        self.page_status.setText("就绪"),
                        self.page_status.setStyleSheet("background-color: #89dceb; color: #000000;")
                    ))
        else:
            self.eye_status.setText("未检测")
            self.eye_status.setStyleSheet("background-color: #a6adc8; color: #000000;")
        
        # 更新注视状态（简化处理）
        if self.detect_checkbox.isChecked():
            self.gaze_status.setText("检测中")
            self.gaze_status.setStyleSheet("background-color: #f9e2af; color: #000000;")
        else:
            self.gaze_status.setText("未检测")
            self.gaze_status.setStyleSheet("background-color: #a6adc8; color: #000000;")
    
    def update_fps_display(self, fps):
        """更新FPS显示"""
        self.fps_display.setText(f"{fps:.1f}")
    
    def update_status(self):
        """更新状态信息"""
        current_time = datetime.now().strftime("%H:%M:%S")
        self.statusBar().showMessage(f"眼动阅读器控制 | {current_time}")
    
    def toggle_fullscreen(self):
        """切换全屏模式"""
        if self.isFullScreen():
            self.showNormal()
            self.fullscreen_btn.setText("全屏模式")
        else:
            self.showFullScreen()
            self.fullscreen_btn.setText("退出全屏")
    
    def closeEvent(self, event):
        """窗口关闭事件"""
        # 停止摄像头
        try:
            self.eye_worker.stop_capture()
        except:
            pass
        
        # 停止定时器
        try:
            self.status_timer.stop()
        except:
            pass
        
        event.accept()


def main():
    """主函数"""
    # 确保在正确的目录中
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = EyeReaderControl()
     # 显示窗口
    window.show()
    # 窗口已在__init__中显示，无需再次调用
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()