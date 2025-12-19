# wayland_input.py - 专门针对树莓派Wayland的输入模拟
import subprocess
import time
import os
from pathlib import Path

class RPiWaylandInput:
    """树莓派5 Wayland输入模拟器"""
    
    def __init__(self):
        self.methods = []
        self.detect_available_methods()
        
    def detect_available_methods(self):
        """检测可用的输入方法"""
        # 检查 wtype
        if self.check_command('wtype'):
            self.methods.append('wtype')
            print("✓ 检测到 wtype")
        
        # # 检查通过 evdev 直接写入设备
        # if self.check_evdev_access():
        #     self.methods.append('evdev')
        #     print("✓ 检测到 evdev 访问权限")
        
        # # 检查 KWin 的 D-Bus 接口
        # if self.check_kwin_dbus():
        #     self.methods.append('kwin_dbus')
        #     print("✓ 检测到 KWin D-Bus 接口")
        
        if not self.methods:
            print("⚠ 未找到可用的输入模拟方法")
    
    def check_command(self, cmd):
        """检查命令是否存在"""
        try:
            subprocess.run(['which', cmd], capture_output=True, check=True)
            return True
        except:
            return False
    
    def check_evdev_access(self):
        """检查 evdev 设备访问权限"""
        try:
            # 尝试读取输入设备列表
            result = subprocess.run(
                ['libinput', 'list-devices'],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except:
            return False
    
    # def check_kwin_dbus(self):
    #     """检查 KWin D-Bus 接口"""
    #     try:
    #         import dbus
    #         bus = dbus.SessionBus()
    #         # 尝试获取 KWin 接口
    #         bus.get_object('org.kde.KWin', '/KWin')
    #         return True
    #     except:
    #         return False
    
    def press_key(self, key):
        """模拟按键"""
        if not self.methods:
            print("错误：没有可用的输入方法")
            return False
        
        # 尝试各种方法
        for method in self.methods:
            success = False
            
            if method == 'wtype':
                success = self.press_key_wtype(key)
            # elif method == 'evdev':
            #     success = self.press_key_evdev(key)
            # elif method == 'kwin_dbus':
            #     success = self.press_key_kwin_dbus(key)
            
            if success:
                return True
        
        return False
    
    def press_key_wtype(self, key):
        """使用 wtype 模拟按键"""
        key_map = {
            'PAGE_DOWN': 'Page_Down',
            'PAGE_UP': 'Page_Up',
            'RIGHT': 'Right',
            'LEFT': 'Left',
            'SPACE': 'space',
            'ENTER': 'Return',
            'ESCAPE': 'Escape',
            'TAB': 'Tab'
        }
        
        key_name = key_map.get(key.upper(), key)
        
        try:
            subprocess.run(['wtype', key_name], check=True)
            print(f"使用 wtype 模拟按键: {key_name}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"wtype 失败: {e}")
            return False
        except FileNotFoundError:
            return False
    
    # def press_key_evdev(self, key):
    #     """使用 evdev 模拟按键"""
    #     try:
    #         from evdev import UInput, ecodes
    #         import time
            
    #         # 键映射
    #         key_map = {
    #             'PAGE_DOWN': ecodes.KEY_PAGEDOWN,
    #             'PAGE_UP': ecodes.KEY_PAGEUP,
    #             'RIGHT': ecodes.KEY_RIGHT,
    #             'LEFT': ecodes.KEY_LEFT,
    #             'SPACE': ecodes.KEY_SPACE,
    #             'ENTER': ecodes.KEY_ENTER
    #         }
            
    #         key_code = key_map.get(key.upper())
    #         if key_code is None:
    #             return False
            
    #         # 创建虚拟输入设备
    #         with UInput() as ui:
    #             # 按下键
    #             ui.write(ecodes.EV_KEY, key_code, 1)
    #             ui.syn()
    #             time.sleep(0.01)
    #             # 释放键
    #             ui.write(ecodes.EV_KEY, key_code, 0)
    #             ui.syn()
            
    #         print(f"使用 evdev 模拟按键: {key}")
    #         return True
            
    #     except ImportError:
    #         print("未安装 python3-evdev")
    #         return False
    #     except Exception as e:
    #         print(f"evdev 失败: {e}")
    #         return False
    
    # def press_key_kwin_dbus(self, key):
    #     """使用 KWin D-Bus 接口模拟按键"""
    #     try:
    #         import dbus
            
    #         key_map = {
    #             'PAGE_DOWN': 'PgDown',
    #             'PAGE_UP': 'PgUp',
    #             'RIGHT': 'Right',
    #             'LEFT': 'Left'
    #         }
            
    #         key_name = key_map.get(key.upper())
    #         if not key_name:
    #             return False
            
    #         bus = dbus.SessionBus()
    #         kwin = bus.get_object('org.kde.KWin', '/KWin')
            
    #         # 调用 KWin 的按键模拟方法
    #         kwin.pressKey(key_name, dbus_interface='org.kde.KWin')
    #         time.sleep(0.05)
    #         kwin.releaseKey(key_name, dbus_interface='org.kde.KWin')
            
    #         print(f"使用 KWin D-Bus 模拟按键: {key_name}")
    #         return True
            
    #     except Exception as e:
    #         print(f"KWin D-Bus 失败: {e}")
    #         return False