# 墨水屏阅读器

[English](README_en.md) | 中文

## 项目概述

本项目是一款基于 Quectel Pi H1 智能主控板的智能电子墨水屏阅读器。系统结合电子墨水屏低功耗显示特性与基于摄像头的眼动追踪技术，实现了无需手动操作的自然翻页阅读方式。通过检测用户眼球视线变化完成翻页控制，并配合物理按键作为辅助输入，提升系统可靠性。

在显示方面，系统采用局部刷新与分区渲染策略，支持中英文混排文本的自动排版与连续阅读，同时具备页面记忆与快速唤醒功能，适用于长时间阅读及嵌入式智能终端应用场景。

![界面预览](assets/main_reader.png)

## 🌟 核心功能特性

| 功能项 | 描述 |
|--------|------|
| **眼动控制翻页** | 通过检测眼球移动方向实现翻页，当观看到阅读器底部时，只需将目光移至屏幕顶部，即可触发翻页操作 |
| **智能息屏** | 检测不到人脸超过设定时间后自动息屏，保护隐私并节省电量 |
| **多语言支持** | 支持纯英文、纯中文（GB2312）、中英混合文本的正确渲染 |
| **自动排版** | 不裁剪字符，自动换行，支持跨页内容延续，中文首行缩进 |
| **页面记忆** | 支持返回前一页并保证像素级一致，精准记录阅读位置 |
| **多书管理** | 支持物理按键长按切换不同书籍 |
| **高效刷新** | 采用局部刷新技术，减少闪烁并提高刷新速度 |

## 👁️ 眼动控制使用方法

### 启动流程
1. 运行 [startup.sh](file:///home/pi/e-ink-reader/startup.sh) 后，系统会同时启动眼动控制脚本和电子墨水屏程序
2. 摄像头会自动检测可用设备并开始监测用户的眼球运动
3. 初始化时间为4秒，期间请保持正常阅读姿势

### 翻页操作
- **向下翻页**：保持阅读姿势，向上看（抬头）触发下一页
- **向上翻页**：向后翻页需通过物理按键操作
- **翻页冷却**：两次翻页间有1秒冷却时间，防止误触

### 息屏/唤醒功能
- **自动息屏**：检测不到人脸4秒后自动发送息屏信号
- **自动唤醒**：重新检测到人脸时自动唤醒屏幕
- **事件清理**：唤醒时会清理息屏期间的输入事件，防止误翻页

## ⌨️ 物理按键功能

- **短按按键A**：向下翻页
- **短按按键B**：向上翻页
- **长按按键A**：切换到下一本书
- **长按按键B**：切换到上一本书

## 🛠️ 硬件配置要求

### 主要硬件
- **主控板**：Quectel-Pi-H1 
- **显示屏**：Waveshare 7.5" 黑白电子墨水屏
- **摄像头**：OV5693 USB摄像头（用于眼动追踪）
- **输入设备**：至少两个物理按键（映射为 `/dev/input/eventX` 设备）

### 电子墨水屏连接引脚
| EPD 引脚 | BCM2835编码 | Board物理引脚序号 |
|----------|-------------|-------------------|
| VCC      | 3.3V        | 3.3V              |
| GND      | GND         | GND               |
| DIN      | MOSI        | 19                |
| CLK      | SCLK        | 23                |
| CS       | CE0         | 24                |
| DC       | 25          | 22                |
| RST      | 17          | 11                |
| BUSY     | 24          | 18                |
| PWR      | 18          | 12                |

## 📋 软件依赖

### 系统要求
- Python版本：Python 3.9~3.12

### 系统依赖
- OpenCV-Python == 4.8.1.78
- MediaPipe == 0.10.9
- evdev == 1.9.2
-numpy==1.24.3

## 📚 准备书籍文件

在[e-ink-reader/src/tools/books](file:///home/pi/e-ink-reader/src/tools/books)下放入.txt书籍文件即可。

- 将您的 .txt 文件放入此目录，并确保编码为 GB2312。
- Windows 用户操作路径：记事本 → 另存为 → 编码选"ANSI"（即 GB2312）。

## 🚀 完整部署指南

### 步骤 1：获取项目源码
1. 在单板电脑终端下新建e-ink-reader文件夹存放项目源码
```bash
mkdir -p /home/pi/e-ink-reader
cd /home/pi/e-ink-reader
```

2. 克隆项目源码至该目录下

3. 在该文件夹路径下打开终端运行以下命令修改文件权限
```bash
sudo chmod -R 755 /home/pi/e-ink-reader
```

### 步骤 2：编译LG库
在e-ink-reader目录下依次执行下面命令：
```bash
cd lg-master
sudo apt update && sudo apt install python3-setuptools 
make
sudo make install
```

### 步骤 3：配置Python环境
系统默认的python版本为3.13，而MediaPipe模型需要Python 3.9-3.12，需要重新指定python路径（系统中已安装python3.10）：

1. 备份当前Python路径链接：
```bash
sudo cp /usr/bin/python3 /usr/bin/python3.backup
```

2. 删除当前Python路径链接：
```bash
sudo rm /usr/bin/python3
```

3. 创建新的路径链接指向Python 3.10：
```bash
sudo ln -s /usr/bin/python3.10 /usr/bin/python3
```

4. 验证修改：
```bash
ls -l /usr/bin/python3
python3 --version
```

### 步骤 4：激活Python虚拟环境
在根目录下执行下面命令激活Python虚拟环境：
```bash
python3.10 -m venv ~/mediapipe_env
source ~/mediapipe_env/bin/activate
```

### 步骤 5：安装Python依赖项
在e-ink-reader目录下安装Python依赖项：
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

单独安装evdev库：
```bash
sudo ln -s /usr/bin/aarch64-linux-gnu-gcc /usr/bin/aarch64-qcom-linux-gcc
CPPFLAGS="-I/usr/include/python3.13 -I/usr/include/python3.10" CFLAGS="-I/usr/include/python3.13 -I/usr/include/python3.10" pip3 install --no-binary evdev evdev==1.9.2
```

### 步骤 6：编译墨水屏驱动程序
在e-ink-reader/src/c目录下编译墨水屏阅读器程序，若该目录出现epd文件则证明编译成功：
```bash
cd /home/pi/e-ink-reader/src/c
make CC=gcc EPD=epd7in5V2
```

### 步骤 7：创建udev规则文件
先输入下面命令创建并打开udev规则文件：
```bash
sudo nano /etc/udev/rules.d/99-uinput.rules
```

在文件中添加下面语句，按下"ctrl + o" + Enter保存编辑内容，然后按下"ctrl + x"退出编辑：
```
KERNEL=="uinput", MODE="0660", GROUP="input"
```

### 步骤 8：添加input组
将用户添加到input组：
```bash
sudo usermod -aG input pi 
```

### 步骤 9：开启SPI功能
在终端输入下面命令开启SPI功能：
```bash
sudo qpi-config 40pin set
```

### 步骤 10：验证配置
1. 重启系统后在终端输入下面命令验证用户是否在input组以及udev规则配置：
```bash
ls -l /dev/uinput
groups
```

2. 验证SPI功能是否开启：
```bash
ls /dev/spi*
```

### 步骤 11：配置免密运行程序
在终端输入下面命令配置免密运行`epd`程序：
```bash
echo "pi ALL=(ALL) NOPASSWD: /home/pi/e-ink-reader/src/c/epd" | sudo tee /etc/sudoers.d/ebook-reader
```

### 步骤 12：运行项目
在e-ink-reader文件夹下执行[startup.sh](file:///home/pi/e-ink-reader/startup.sh)脚本运行项目：
```bash
cd /home/pi/e-ink-reader
./startup.sh
```

## 🔧 技术特点详解

### 眼动追踪算法
- 使用MediaPipe的人脸网格识别技术，精确定位眼部和虹膜关键点
- 实现指数平滑算法，减少画面抖动
- 70%的连续帧判断机制，防止误触发
- 自适应参考位置，适应不同的阅读姿势

### 电子墨水屏优化
- **局部刷新**：仅刷新文本内容和页码区域，保留标题栏不变
- **三层架构**：标题(header)、正文(content)、页码(footer)分区管理
- **中文优化**：支持中文首行缩进，符合阅读习惯
- **快速唤醒**：使用快速初始化模式，迅速从息屏恢复阅读状态

### 电源管理
- 智能息屏功能，无人阅读时自动进入低功耗模式
- 唤醒时仅刷新必要的UI元素，减少功耗
- 优化驱动延时，加快响应速度

## ⚠️ 注意事项

1. **文本编码**：TXT文件必须使用GB2312编码，否则中文可能出现乱码
2. **摄像头位置**：摄像头应放置在屏幕附近，确保能清晰拍摄到用户的面部
3. **光线条件**：在光线充足的环境下使用，确保摄像头能够清晰捕捉眼部特征
4. **权限设置**：程序需要访问摄像头和输入设备的权限，可能需要sudo运行
5. **硬件连接**：确保电子墨水屏正确连接到SPI接口，GPIO配置正确

## 🔍 故障排除

| 问题 | 解决方案 |
|------|----------|
| 摄像头无法打开 | 检查设备权限，使用 `ls /dev/video*` 确认设备节点存在 |
| 眼动控制无响应 | 检查摄像头是否被其他程序占用，确认MediaPipe安装正确 |
| 屏幕无显示或异常 | 检查SPI连接是否牢固，GPIO配置是否正确 |
| 中文显示乱码 | 确认TXT文件编码为GB2312 |
| 按键无效 | 使用 `cat /proc/bus/input/devices` 查找event设备并确认权限 |
| 编译失败 | 检查交叉编译工具链是否存在且路径正确 |

## 报告问题
欢迎提交Issue和Pull Request来改进此项目。