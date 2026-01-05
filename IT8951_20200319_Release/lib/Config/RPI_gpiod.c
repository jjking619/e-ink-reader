/*****************************************************************************
* | File        :   GPIOD.c
* | Author      :   Waveshare team
* | Function    :   Drive GPIO using sysfs interface
* | Info        :   Read and write gpio via /sys/class/gpio/
*----------------
* |	This version:   V1.0
* | Date        :   2024-05-15
* | Info        :   Basic version using sysfs
*
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documnetation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to  whom the Software is
# furished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS OR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
******************************************************************************/
#include "RPI_gpiod.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <errno.h>

// 定义 GPIO 导出路径
#define SYSFS_GPIO_DIR "/sys/class/gpio"

// 内部函数声明
static int write_to_file(const char *filename, const char *value);
static int read_from_file(const char *filename, char *buffer, size_t size);

// 导出 GPIO 引脚
int GPIOD_Export(void)
{
    // sysfs GPIO 不需要全局初始化
    // 每个引脚在使用时单独导出
    GPIOD_Debug("Sysfs GPIO initialized (no global init needed)\n");
    return 0;
}

// 导出指定的 GPIO 引脚
int GPIOD_Export_Pin(int Pin)
{
    char path[64];
    char value[10];
    
    // 检查是否已经导出
    snprintf(path, sizeof(path), "%s/gpio%d", SYSFS_GPIO_DIR, Pin);
    if (access(path, F_OK) == 0) {
        GPIOD_Debug("Pin%d already exported\n", Pin);
        return 0;
    }
    
    // 导出引脚
    snprintf(value, sizeof(value), "%d", Pin);
    if (write_to_file("/sys/class/gpio/export", value) < 0) {
        GPIOD_Debug("Failed to export Pin%d: %s\n", Pin, strerror(errno));
        return -1;
    }
    
    // 等待文件系统创建完成
    usleep(100000); // 100ms
    
    GPIOD_Debug("Exported Pin%d\n", Pin);
    return 0;
}

// 取消导出 GPIO 引脚
int GPIOD_Unexport(int Pin)
{
    char value[10];
    
    snprintf(value, sizeof(value), "%d", Pin);
    if (write_to_file("/sys/class/gpio/unexport", value) < 0) {
        GPIOD_Debug("Failed to unexport Pin%d: %s\n", Pin, strerror(errno));
        return -1;
    }
    
    GPIOD_Debug("Unexported Pin%d\n", Pin);
    return 0;
}

// 取消导出所有 GPIO 引脚（清理函数）
int GPIOD_Unexport_GPIO(void)
{
    // sysfs GPIO 不需要全局清理
    // 每个引脚在使用后单独取消导出
    return 0;
}

// 设置 GPIO 方向
int GPIOD_Direction(int Pin, int Dir)
{
    char path[64];
    
    // 先导出引脚
    if (GPIOD_Export_Pin(Pin) < 0) {
        return -1;
    }
    
    // 设置方向
    snprintf(path, sizeof(path), "%s/gpio%d/direction", SYSFS_GPIO_DIR, Pin);
    
    if (Dir == GPIOD_IN) {
        if (write_to_file(path, "in") < 0) {
            GPIOD_Debug("Failed to set Pin%d as input: %s\n", Pin, strerror(errno));
            return -1;
        }
        GPIOD_Debug("Pin%d: Input mode\n", Pin);
    } else {
        if (write_to_file(path, "out") < 0) {
            GPIOD_Debug("Failed to set Pin%d as output: %s\n", Pin, strerror(errno));
            return -1;
        }
        GPIOD_Debug("Pin%d: Output mode\n", Pin);
    }
    
    return 0;
}

// 读取 GPIO 值
int GPIOD_Read(int Pin)
{
    char path[64];
    char value[3];
    
    // 先确保方向是输入
    if (GPIOD_Direction(Pin, GPIOD_IN) < 0) {
        return -1;
    }
    
    snprintf(path, sizeof(path), "%s/gpio%d/value", SYSFS_GPIO_DIR, Pin);
    
    if (read_from_file(path, value, sizeof(value)) < 0) {
        GPIOD_Debug("Failed to read Pin%d: %s\n", Pin, strerror(errno));
        return -1;
    }
    
    return atoi(value);
}

// 写入 GPIO 值
int GPIOD_Write(int Pin, int value)
{
    char path[64];
    char val_str[3];
    
    // 先确保方向是输出
    if (GPIOD_Direction(Pin, GPIOD_OUT) < 0) {
        return -1;
    }
    
    snprintf(path, sizeof(path), "%s/gpio%d/value", SYSFS_GPIO_DIR, Pin);
    snprintf(val_str, sizeof(val_str), "%d", value);
    
    if (write_to_file(path, val_str) < 0) {
        GPIOD_Debug("Failed to write Pin%d: %s\n", Pin, strerror(errno));
        return -1;
    }
    
    return 0;
}

// 辅助函数：写入文件
static int write_to_file(const char *filename, const char *value)
{
    int fd;
    ssize_t bytes_written;
    int len;
    
    fd = open(filename, O_WRONLY);
    if (fd < 0) {
        return -1;
    }
    
    len = strlen(value);
    bytes_written = write(fd, value, len);
    close(fd);
    
    if (bytes_written != len) {
        return -1;
    }
    
    return 0;
}

// 辅助函数：读取文件
static int read_from_file(const char *filename, char *buffer, size_t size)
{
    int fd;
    ssize_t bytes_read;
    
    fd = open(filename, O_RDONLY);
    if (fd < 0) {
        return -1;
    }
    
    bytes_read = read(fd, buffer, size - 1);
    close(fd);
    
    if (bytes_read < 0) {
        return -1;
    }
    
    buffer[bytes_read] = '\0';
    
    // 移除换行符（如果有）
    if (buffer[bytes_read - 1] == '\n') {
        buffer[bytes_read - 1] = '\0';
    }
    
    return 0;
}