#!/bin/bash

# ebook reader 启动脚本
# 同时运行Python眼动控制脚本和电子墨水屏程序

# 定义变量存储子进程PID
PYTHON_PID=0
EPD_PID=0

# 退出处理函数
cleanup() {
    echo ""
    echo "接收到退出信号，正在终止所有子进程..."
    
    # 终止Python进程
    if [ $PYTHON_PID -ne 0 ] && kill -0 $PYTHON_PID 2>/dev/null; then
        echo "终止Python眼动控制脚本 (PID: $PYTHON_PID)"
        kill -TERM $PYTHON_PID 2>/dev/null
        wait $PYTHON_PID 2>/dev/null
    fi
    
    # 终止EPD进程
    if [ $EPD_PID -ne 0 ] && kill -0 $EPD_PID 2>/dev/null; then
        echo "终止电子墨水屏程序 (PID: $EPD_PID)"
        kill -TERM $EPD_PID 2>/dev/null
        wait $EPD_PID 2>/dev/null
    fi
    
    echo "所有程序已终止"
    exit 0
}

# 捕获退出信号
trap cleanup SIGINT SIGTERM

echo "正在启动ebook reader系统..."

# 获取脚本所在目录的绝对路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 进入ebook-reader目录
cd "$SCRIPT_DIR"

# 创建日志目录
mkdir -p logs

# 启动Python眼动控制脚本 (后台运行)
echo "启动Python眼动控制脚本..."
python3 main.py > logs/python_eye_control.log 2>&1 &
PYTHON_PID=$!

# 检查Python进程是否成功启动
if ! kill -0 $PYTHON_PID 2>/dev/null; then
    echo "错误：无法启动Python眼动控制脚本"
    exit 1
fi

# 启动电子墨水屏程序 (后台运行)
echo "启动电子墨水屏程序..."
sudo ./src/c/epd > logs/epd_program.log 2>&1 &
EPD_PID=$!

# 检查EPD进程是否成功启动
if ! kill -0 $EPD_PID 2>/dev/null; then
    echo "错误：无法启动电子墨水屏程序"
    kill $PYTHON_PID 2>/dev/null
    exit 1
fi

echo "程序已启动:"
echo "  - Python眼动控制脚本 PID: $PYTHON_PID"
echo "  - 电子墨水屏程序 PID: $EPD_PID"
echo "按 Ctrl+C 退出程序"

# 等待所有子进程完成
wait $PYTHON_PID $EPD_PID 2>/dev/null

echo "所有程序已完成"