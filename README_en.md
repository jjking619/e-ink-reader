# E-Ink Reader

[中文](README.md) | English

## Project Overview

This is an intelligent e-ink reader project integrated with **eye-tracking page turning technology**, specifically designed for **Linux embedded platform** (Quectel-Pi-H1). The project combines low-level hardware control in C with Python-based computer vision technology to achieve a truly **hands-free reading experience**.

![Interface Preview](assets/main_reader.png)

## 🌟 Core Features

| Feature | Description |
|---------|-------------|
| **Eye-tracking Page Turning** | Page turning is achieved by detecting eye movement direction. When reaching the bottom of the reader, simply looking towards the top of the screen triggers the page turn |
| **Smart Screen Off** | Automatically turns off the screen after 4 seconds of no face detection to protect privacy and save power |
| **Multi-language Support** | Supports correct rendering of pure English, pure Chinese (GB2312), and mixed Chinese-English text |
| **Automatic Typesetting** | No character cropping, automatic line wrapping, supports cross-page content continuation, Chinese first-line indentation |
| **Page Memory** | Supports returning to previous pages with pixel-level consistency, accurately recording reading position |
| **Multi-book Management** | Supports switching between different books via long-press physical buttons |
| **Efficient Refresh** | Uses partial refresh technology to reduce flickering and improve refresh speed |

## 👁️ Eye-tracking Control Instructions

### Startup Process
1. After running [startup.sh](file:///home/pi/e-ink-reader/startup.sh), the system simultaneously launches the eye-tracking script and e-ink display program
2. The camera automatically detects available devices and begins monitoring eye movements
3. Initialization takes 4 seconds - maintain normal reading posture during this period

### Page Turning Operations
- **Next Page**: Maintain reading posture, look upward (raise head) to trigger next page
- **Previous Page**: Requires physical button operation
- **Page Turn Cooldown**: 1-second cooldown between page turns to prevent accidental triggers

### Screen Off/Wake-up Function
- **Auto Screen Off**: Automatically sends screen-off signal after 4 seconds of no face detection
- **Auto Wake-up**: Automatically wakes the screen when face is detected again
- **Event Cleanup**: Clears input events during screen-off period upon wake-up to prevent accidental page turns

## ⌨️ Physical Button Functions

- **Short Press Button A**: Turn to next page
- **Short Press Button B**: Turn to previous page
- **Long Press Button A**: Switch to next book
- **Long Press Button B**: Switch to previous book

## 🛠️ Hardware Requirements

### Main Hardware
- **Main Controller**: Quectel-Pi-H1
- **Display**: Waveshare 7.5" Black and White E-Ink Display
- **Camera**: OV5693 USB Camera (for eye tracking)
- **Input Devices**: At least two physical buttons (mapped to `/dev/input/eventX` devices)

### E-Ink Display Pin Connections
| EPD Pin | BCM2835 Numbering | Board Physical Pin |
|---------|-------------------|--------------------|
| VCC     | 3.3V              | 3.3V               |
| GND     | GND               | GND                |
| DIN     | MOSI              | 19                 |
| CLK     | SCLK              | 23                 |
| CS      | CE0               | 24                 |
| DC      | 25                | 22                 |
| RST     | 17                | 11                 |
| BUSY    | 24                | 18                 |
| PWR     | 18                | 12                 |

## 📋 Software Dependencies

### System Requirements
- Python version: Python 3.9~3.12

### System Dependencies
- OpenCV-Python == 4.8.1.78
- MediaPipe == 0.10.9
- evdev == 1.9.2
- numpy == 1.24.3

## 📚 Preparing Book Files

Place your .txt book files in [e-ink-reader/src/tools/books](file:///home/pi/e-ink-reader/src/tools/books).

- Place your .txt files in this directory with GB2312 encoding
- Windows users: Notepad → Save As → Encoding select "ANSI" (which is GB2312)

## 🚀 Complete Deployment Guide

### Step 1: Get Project Source Code
1. Create e-ink-reader folder in single-board computer terminal:
```bash
mkdir -p /home/pi/e-ink-reader
cd /home/pi/e-ink-reader
```

2. Clone project source code to this directory

3. Modify file permissions:
```bash
sudo chmod -R 755 /home/pi/e-ink-reader
```

### Step 2: Compile LG Library
In e-ink-reader directory:
```bash
cd lg-master
sudo apt update && sudo apt install python3-setuptools 
make
sudo make install
```

### Step 3: Configure Python Environment
System default Python is 3.13, but MediaPipe requires Python 3.9-3.12 (Python 3.10 is pre-installed):

1. Backup current Python link:
```bash
sudo cp /usr/bin/python3 /usr/bin/python3.backup
```

2. Remove current Python link:
```bash
sudo rm /usr/bin/python3
```

3. Create new link to Python 3.10:
```bash
sudo ln -s /usr/bin/python3.10 /usr/bin/python3
```

4. Verify modification:
```bash
ls -l /usr/bin/python3
python3 --version
```

### Step 4: Activate Python Virtual Environment
```bash
python3.10 -m venv ~/mediapipe_env
source ~/mediapipe_env/bin/activate
```

### Step 5: Install Python Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Install evdev separately:
```bash
sudo ln -s /usr/bin/aarch64-linux-gnu-gcc /usr/bin/aarch64-qcom-linux-gcc
CPPFLAGS="-I/usr/include/python3.13 -I/usr/include/python3.10" CFLAGS="-I/usr/include/python3.13 -I/usr/include/python3.10" pip3 install --no-binary evdev evdev==1.9.2
```

### Step 6: Compile E-Ink Display Driver
```bash
cd /home/pi/e-ink-reader/src/c
make CC=gcc EPD=epd7in5V2
```

### Step 7: Create udev Rules File
```bash
sudo nano /etc/udev/rules.d/99-uinput.rules
```

Add this line:
```
KERNEL=="uinput", MODE="0660", GROUP="input"
```

### Step 8: Add input Group
```bash
sudo usermod -aG input pi 
```

### Step 9: Enable SPI Function
```bash
sudo qpi-config 40pin set
```

### Step 10: Verify Configuration
1. After restarting the system, enter the following command in the terminal to verify whether the user is in the input group and the udev rule configuration.:
```bash
ls -l /dev/uinput
groups
```

2. Verify SPI functionality:
```bash
ls /dev/spi*
```

### Step 11: Configure Password-less Execution
```bash
echo "pi ALL=(ALL) NOPASSWD: /home/pi/e-ink-reader/src/c/epd" | sudo tee /etc/sudoers.d/ebook-reader
```

### Step 12: Run Project
```bash
cd /home/pi/e-ink-reader
./startup.sh
```

## 🔧 Technical Details

### Eye-tracking Algorithm
- Uses MediaPipe face mesh recognition to precisely locate eye and iris landmarks
- Implements exponential smoothing algorithm to reduce image jitter
- 70% consecutive frame judgment mechanism to prevent false triggers
- Adaptive reference position to accommodate different reading postures

### E-Ink Display Optimization
- **Partial Refresh**: Only refreshes text content and page number area, keeping header unchanged
- **Three-layer Architecture**: Manages header, content, and footer separately
- **Chinese Optimization**: Supports Chinese first-line indentation for better reading experience
- **Quick Wake-up**: Uses fast initialization mode to quickly resume from sleep state

### Power Management
- Smart screen-off function enters low-power mode when no reading activity
- Upon wake-up, only necessary UI elements are refreshed to reduce power consumption
- Optimized driver delays for faster response

## ⚠️ Important Notes

1. **Text Encoding**: TXT files must use GB2312 encoding, otherwise Chinese characters may display incorrectly
2. **Camera Placement**: Position camera near the screen to clearly capture user's face
3. **Lighting Conditions**: Use in well-lit environments to ensure clear capture of eye features
4. **Permissions**: Program requires access to camera and input devices, may need sudo privileges
5. **Hardware Connection**: Ensure e-ink display is correctly connected to SPI interface with proper GPIO configuration

## 🔍 Troubleshooting

| Issue | Solution |
|-------|----------|
| Camera cannot open | Check device permissions, use `ls /dev/video*` to confirm device node exists |
| Eye-tracking unresponsive | Check if camera is occupied by other programs, verify MediaPipe installation |
| Screen no display or abnormal | Check SPI connection stability and GPIO configuration |
| Chinese characters garbled | Confirm TXT file encoding is GB2312 |
| Buttons not working | Use `cat /proc/bus/input/devices` to find event device and check permissions |
| Compilation failure | Check cross-compilation toolchain existence and path |

## Reporting Issues
We welcome Issues and Pull Requests to improve this project.