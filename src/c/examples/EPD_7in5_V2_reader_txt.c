// examples/EPD_7in5_V2_reader_txt.c
#define _DEFAULT_SOURCE  // 必须在包含头文件前定义，以启用DT_REG等文件类型常量
#include "EPD_7in5_V2.h"
#include "GUI_Paint.h"
#include "fonts.h"
#include "GUI_BMPfile.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <linux/input.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <sys/time.h>
#include <lgpio.h>
#include <sys/ioctl.h>
#include "DEV_Config.h"
#include <sys/types.h>  // 添加此头文件以定义DT_REG
#include <poll.h>

// 定义自定义的息屏和唤醒信号
#define CUSTOM_SCREEN_OFF_BTN BTN_LEFT
#define CUSTOM_SCREEN_ON_BTN BTN_RIGHT

#define BOOK_PATH "./src/tools/books"
#define MAX_BOOK_SIZE (4* 1024 * 1024) // 4MB
#define MAX_BOOKS 20
#define MAX_HISTORY 500 // 最多记录500页历史

// 局部刷新区域定义
#define HEADER_HEIGHT   30
#define FOOTER_HEIGHT   30  // 增加页脚高度，为页码下移预留空间

#define CONTENT_Y_START   (HEADER_HEIGHT + 8)
#define FOOTER_Y_START    (EPD_7IN5_V2_HEIGHT - FOOTER_HEIGHT)

// 息屏相关定义
static int screen_off = 0;  // 是否处于息屏状态

static char current_file[256] = {0};
static UBYTE *g_frame_buffer = NULL;
static UBYTE *g_prev_frame_buffer = NULL; // 用于比较前后两帧差异，实现真正意义上的局部刷新
static int key1_fd = -1; // event3: next page / long press: next book
static int key2_fd = -1; // event1: prev page / long press: prev book
static int eye_key_fd = -1; // 新增: eye_page_turner 虚拟设备
static int first_display_done = 0;
static int book_changed = 0;  // 标记书籍是否已切换
static int header_drawn = 0;  // 新增: 标记Header区是否已绘制

// 多书支持
static char book_list[MAX_BOOKS][256];
static int book_count = 0;
static int current_book_index = 0;

// 全局文本
static char* g_full_text = NULL;
static size_t g_text_size = 0;
// 新增：处理后的纯文本内容，去除多余换行
static char* g_processed_text = NULL;
static size_t g_processed_text_size = 0;

// 当前页起始偏移（字节）
static size_t g_current_char_offset = 0;

// 历史栈：记录每一页的起始偏移（用于精确回退）
static size_t history_stack[MAX_HISTORY];
static int history_top = -1;

// 标记是否需要重绘书名
static int title_drawn = 0;

// 新增：用于准确计算当前页数
static size_t *page_offsets = NULL;  // 存储每一页的起始偏移
static int total_pages = 0;          // 总页数
static int current_page_index = 0;   // 当前页索引（从0开始）

// 函数声明
char* process_text_content(const char* raw_text, size_t raw_size);
void calculate_page_info();
int get_current_page_index(size_t offset);
int find_eye_control_device();
void init_eye_control_device();
void enter_screen_off_mode();
void exit_screen_off_mode();

const char* get_ext(const char* filename) {
    const char* dot = strrchr(filename, '.');
    return (dot && dot != filename) ? dot + 1 : "";
}

// 函数：查找名为eye_page_turner的输入设备
int find_eye_control_device() {
    char name[256] = {0};
    int fd;
    int i;
    char fname[64];  // 设备文件名模板
    
    // 遍历/dev/input/event*设备
    for (i = 0; i < 32; i++) {
        sprintf(fname, "/dev/input/event%d", i);
        fd = open(fname, O_RDONLY | O_NONBLOCK);
        if (fd >= 0) {
            // 读取设备名称
            ioctl(fd, EVIOCGNAME(sizeof(name) - 1), name);
            if (strstr(name, "eye_page_turner")) {
                printf("Found eye control device: %s (%s)\n", fname, name);
                return fd;
            }
            close(fd);
        }
    }
    return -1;
}

// 初始化虚拟设备，带重试机制
void init_eye_control_device() {
    int attempts = 0;
    const int max_attempts = 10; // 尝试10次，每次间隔1秒
    
    printf("Waiting for eye control device...\n");
    
    while (attempts < max_attempts) {
        eye_key_fd = find_eye_control_device();
        if (eye_key_fd >= 0) {
            printf("Successfully connected to eye control device!\n");
            return;
        }
        
        printf("Attempt %d/%d: Eye control device not found, waiting...\n", attempts+1, max_attempts);
        sleep(1); // 等待1秒后重试
        attempts++;
    }
    
    printf("Warning: Failed to connect to eye control device after %d attempts\n", max_attempts);
}

// 处理文本内容：合并段落，去除多余换行
char* process_text_content(const char* raw_text, size_t raw_size) {
    if (!raw_text || raw_size == 0) return NULL;

    // 创建临时缓冲区存储处理后的文本
    char* processed = malloc(raw_size + 1);  // 初始化为原大小，可能会略大一些
    if (!processed) return NULL;

    size_t src_idx = 0, dst_idx = 0;
    int in_paragraph = 0;  // 标记是否在段落中间

    while (src_idx < raw_size) {
        // 跳过连续的换行符和空白字符
        while (src_idx < raw_size && (raw_text[src_idx] == '\n' || raw_text[src_idx] == '\r')) {
            // 检查是否是段落分隔（连续两个换行符）
            size_t temp_idx = src_idx;
            int newline_count = 0;
            while (temp_idx < raw_size && (raw_text[temp_idx] == '\n' || raw_text[temp_idx] == '\r')) {
                if (raw_text[temp_idx] == '\n' || raw_text[temp_idx] == '\r') {
                    newline_count++;
                    // 跳过\r\n或\n\r序列
                    if (temp_idx+1 < raw_size && 
                        ((raw_text[temp_idx]=='\r' && raw_text[temp_idx+1]=='\n') ||
                         (raw_text[temp_idx]=='\n' && raw_text[temp_idx+1]=='\r'))) {
                        temp_idx += 2;
                    } else {
                        temp_idx++;
                    }
                }
            }
            
            // 如果是段落分隔（至少两个换行），则添加一个换行表示段落结束
            if (newline_count >= 2) {
                if (in_paragraph) {
                    processed[dst_idx++] = '\n';  // 段落结束标记
                    in_paragraph = 0;
                }
            } else if (in_paragraph) {
                // 行与行之间的换行替换为空格
                processed[dst_idx++] = ' ';
            }
            
            src_idx = temp_idx;
        }

        // 处理普通字符
        if (src_idx < raw_size && raw_text[src_idx] != '\n' && raw_text[src_idx] != '\r') {
            // 跳过开头的空白字符（如果不在段落中）
            if (!in_paragraph) {
                while (src_idx < raw_size && raw_text[src_idx] == ' ') src_idx++;
                if (src_idx >= raw_size) break;
            }

            // 复制字符
            unsigned char c = (unsigned char)raw_text[src_idx];
            int bytes = 1;
            // 检测中文字符（高位字节大于0x80）
            if (c > 0x80 && src_idx + 1 < raw_size) {
                // 检查下一个字节是否是有效的低位字节
                if ((unsigned char)raw_text[src_idx+1] > 0x40) {
                    bytes = 2;
                }
            }

            // 复制字符（跳过段落内的多余空格）
            if (c == ' ' && in_paragraph && dst_idx > 0 && processed[dst_idx-1] == ' ') {
                // 跳过多余的空格
            } else {
                for (int i = 0; i < bytes && src_idx+i < raw_size; i++) {
                    processed[dst_idx++] = raw_text[src_idx + i];
                }
                in_paragraph = 1;
            }

            src_idx += bytes;
        }
    }

    processed[dst_idx] = '\0';
    // 重新分配合适大小的内存
    char* result = realloc(processed, dst_idx + 1);
    if (!result) result = processed;  // 如果realloc失败，返回原来的指针
    return result;
}

// 计算总页数并存储每页的起始偏移
void calculate_page_info() {
    if (!g_processed_text) return;
    
    // 释放之前的页偏移数组
    if (page_offsets) {
        free(page_offsets);
        page_offsets = NULL;
    }
    
    // 临时存储页偏移，使用较大缓冲区
    size_t *temp_offsets = malloc(sizeof(size_t) * (g_processed_text_size / 1000 + 100));
    if (!temp_offsets) {
        printf("Error: Could not allocate memory for temporary page offsets\n");
        return;
    }
    
    int count = 0;
    size_t offset = 0;
    
    // 循环计算每一页的起始偏移
    while (offset < g_processed_text_size) {
        if(count >= (g_processed_text_size / 1000 + 100)) {
            // 如果数组容量不够，重新分配更大的空间
            size_t *new_temp_offsets = realloc(temp_offsets, sizeof(size_t) * (count + 1000));
            if(new_temp_offsets) {
                temp_offsets = new_temp_offsets;
            } else {
                printf("Warning: Could not expand memory for page offsets, stop calculation at page %d\n", count);
                break;
            }
        }
        
        temp_offsets[count++] = offset;
        
        // 使用相同的显示逻辑计算一页能容纳多少字符
        const int left_margin = 10;
        const int right_margin = 10;
        const int max_x = EPD_7IN5_V2_WIDTH - right_margin;
        
        const int lh_en = Font16.Height;
        const int lh_cn = Font12CN.Height;
        
        int y = CONTENT_Y_START+HEADER_HEIGHT +10 ;
        const int text_bottom = FOOTER_Y_START - 10;
        
        // 计算一页的内容
        int page_has_content = 0; // 标记这一页是否有内容
        
        while (offset < g_processed_text_size && y < text_bottom) {
            int x = left_margin;
            int has_cn = 0;

            // 构建一行文本，直到达到最大宽度或遇到段落结束标记
            while (offset < g_processed_text_size) {
                unsigned char c = (unsigned char)g_processed_text[offset];
                // 遇到段落结束标记，换行
                if (c == '\n') {
                    offset++;  // 跳过段落结束标记
                    // 段落后第一行需要缩进，所以将x设置为缩进距离
                    x = left_margin + (Font16.Width * 30);  // 缩进30个字符的宽度
                    continue;  // 继续下一次循环
                }

                int bytes = 1;
                // 检测中文字符（高位字节大于0x80）
                if (c > 0x80 && offset + 1 < g_processed_text_size) {
                    if ((unsigned char)g_processed_text[offset+1] > 0x40) {
                        bytes = 2;
                    }
                }

                int width = (bytes == 2) ? Font12CN.Width : Font16.Width;

                if (x + width > max_x)
                    break;  // 达到行宽限制，换行

                if (bytes == 2) has_cn = 1;
                offset += bytes;
                x += width;
            }

            page_has_content = 1; // 至少有一行内容
                
            int lh = has_cn ? lh_cn : lh_en;
            if (y + lh > text_bottom)
                break;

            y += lh;
        }
        
        // 如果这一页没有内容但是还有剩余文本，说明文本超出了页面空间
        if(!page_has_content && offset < g_processed_text_size) {
            printf("Warning: No content placed on page %d but text remains\n", count);
            break;
        }
    }
    
    // 分配确切大小的页偏移数组
    total_pages = count;
    page_offsets = malloc(sizeof(size_t) * total_pages);
    if (page_offsets) {
        memcpy(page_offsets, temp_offsets, sizeof(size_t) * total_pages);
        printf("Successfully calculated %d pages\n", total_pages);
    } else {
        printf("Error: Could not allocate memory for page offsets\n");
    }
    
    free(temp_offsets);
}

// 获取当前页索引
int get_current_page_index(size_t offset) {
    if (!page_offsets || total_pages == 0) {
        // 如果无法获取准确页数，使用估算方式
        return (offset / 2000) + 1;
    }
    
    // 二分查找当前偏移量所属的页
    int left = 0, right = total_pages - 1;
    int result = 0;
    
    while (left <= right) {
        int mid = left + (right - left) / 2;
        if (page_offsets[mid] <= offset) {
            result = mid;
            if (mid < total_pages - 1) {
                left = mid + 1;
            } else {
                break;  // 已经是最后一页
            }
        } else {
            if (mid > 0) {
                right = mid - 1;
            } else {
                break;  // 已经是第一页
            }
        }
    }
    
    return result + 1; // 页码从1开始
}

// 加载整个 TXT 文件到内存（GB2312 编码）
int load_txt_file(const char* path) {
    FILE* fp = fopen(path, "rb");
    if (!fp) {
        printf("Failed to open TXT: %s (errno=%d)\n", path, errno);
        return -1;
    }

    fseek(fp, 0, SEEK_END);
    long size = ftell(fp);
    if (size <= 0 || size > MAX_BOOK_SIZE) {
        fclose(fp);
        printf("File too large or empty: %ld\n", size);
        return -1;
    }

    if (g_full_text) free(g_full_text);
    g_full_text = (char*)malloc(size + 1);
    if (!g_full_text) {
        fclose(fp);
        printf("Malloc failed for text\n");
        return -1;
    }

    fseek(fp, 0, SEEK_SET);
    size_t read_bytes = fread(g_full_text, 1, size, fp);
    fclose(fp);

    if (read_bytes != (size_t)size) {
        free(g_full_text);
        g_full_text = NULL;
        return -1;
    }
    g_full_text[read_bytes] = '\0';
    g_text_size = read_bytes;

    // 处理文本：去除多余的换行符，合并段落
    if (g_processed_text) free(g_processed_text);
    g_processed_text = process_text_content(g_full_text, g_text_size);
    if (!g_processed_text) {
        printf("Failed to process text content\n");
        return -1;
    }
    g_processed_text_size = strlen(g_processed_text);

    // 重置状态
    g_current_char_offset = 0;
    history_top = -1; // 清空历史
    first_display_done = 0;
    
    // 计算页信息
    calculate_page_info();
    current_page_index = 1;  // 重置为第一页

    printf("Loaded %zu bytes from %s, processed to %zu bytes\n", g_text_size, path, g_processed_text_size);
    return 0;
}

void show_error(const char* msg) {
    printf("ERROR: %s\n", msg);
    if (g_frame_buffer == NULL) return;
    Paint_SelectImage(g_frame_buffer);
    Paint_Clear(WHITE);
    Paint_DrawString_EN(10, 10, "ERROR", &Font16, BLACK, WHITE);
    Paint_DrawString_EN(10, 40, msg, &Font16, BLACK, WHITE);
    EPD_7IN5_V2_Display(g_frame_buffer);
    sleep(3);
}

// 核心：从指定偏移绘制一页，并返回下一页起始偏移
size_t display_txt_page_from_offset(size_t start_offset)
{
    // 使用处理后的文本而不是原始文本
    if (!g_processed_text || start_offset >= g_processed_text_size) {
        Paint_SelectImage(g_frame_buffer);
        Paint_Clear(WHITE);
        EPD_7IN5_V2_Display(g_frame_buffer);
        return g_processed_text_size;
    }

    Paint_SelectImage(g_frame_buffer);

    /* =====================================================
     * 1. 首次显示或书籍切换：全屏初始化
     * ===================================================== */
    if (!first_display_done || book_changed) {
        Paint_Clear(WHITE);

        /* Header —— 永久区 */
        char title[256];
        const char* name = strrchr(current_file, '/');
        name = name ? name + 1 : current_file;

        // 创建副本并去掉后缀
        char display_name[256];
        strncpy(display_name, name, sizeof(display_name)-1);
        display_name[sizeof(display_name)-1] = 0;

        // 查找最后一个点
        char *dot = strrchr(display_name, '.');
        if (dot && dot != display_name) {
            *dot = 0;
        }

        snprintf(title, sizeof(title), "Book: %s", display_name);
        Paint_DrawString_EN(10, 10, title, &Font16, BLACK, WHITE);

        Paint_DrawLine(
            10,
            HEADER_HEIGHT,
            EPD_7IN5_V2_WIDTH - 10,
            HEADER_HEIGHT,
            BLACK,
            DOT_PIXEL_1X1,
            LINE_STYLE_SOLID
        );

        EPD_7IN5_V2_Init_Fast();
        EPD_7IN5_V2_Clear();
        // 减少延时时间以加快初始化速度
        // DEV_Delay_ms(500); // 从1500ms减少到500ms
        EPD_7IN5_V2_Display(g_frame_buffer);
        // 减少延时时间以加快初始化速度
        // DEV_Delay_ms(200); // 从500ms减少到200ms
        EPD_7IN5_V2_Init_Part();

        first_display_done = 1;
        book_changed = 0;
        header_drawn = 1;  // 标记Header已绘制
    }
    else if (!header_drawn) {
        /* =================================================
         * 2. 确保Header区始终存在（即使局部刷新后）
         * ===================================================== */
        char title[256];
        const char* name = strrchr(current_file, '/');
        name = name ? name + 1 : current_file;

        snprintf(title, sizeof(title), "Book: %s", name);
        Paint_DrawString_EN(10, 10, title, &Font16, BLACK, WHITE);

        Paint_DrawLine(
            10,
            HEADER_HEIGHT,
            EPD_7IN5_V2_WIDTH - 10,
            HEADER_HEIGHT,
            BLACK,
            DOT_PIXEL_1X1,
            LINE_STYLE_SOLID
        );

        header_drawn = 1;
    }
    else {
        /* =================================================
         * 3. 翻页：仅清 CONTENT + FOOTER（不碰 Header）
         * ================================================= */
        Paint_ClearWindows(
            0,
            CONTENT_Y_START,
            EPD_7IN5_V2_WIDTH-10,
            EPD_7IN5_V2_HEIGHT - CONTENT_Y_START,
            WHITE
        );
    }

    /* =====================================================
     * 4. 正文排版绘制
     * ===================================================== */
    const int left_margin = 0;
    // const int right_margin = 0;
    const int max_x = EPD_7IN5_V2_WIDTH ;

    const int lh_en = Font16.Height;
    const int lh_cn = Font12CN.Height;

    int y = CONTENT_Y_START+HEADER_HEIGHT +10 ;
    const int text_bottom = FOOTER_Y_START ;

    // 使用处理后的文本
    size_t i = start_offset;

    // 记录进入循环前的起始位置
    size_t initial_i = i;

    while (i < g_processed_text_size && y < text_bottom) {
        int x = left_margin;
        char line[512] = {0};
        int len = 0;
        int has_cn = 0;

        // 构建一行文本，直到达到最大宽度或遇到段落结束标记
        while (i < g_processed_text_size) {
            unsigned char c = (unsigned char)g_processed_text[i];
            // 遇到段落结束标记，换行
            if (c == '\n') {
                i++;  // 跳过段落结束标记
                // 段落后第一行需要缩进，所以将x设置为缩进距离
                x = left_margin + (Font16.Width * 2);  // 缩进两个字符的宽度
                continue;  // 继续下一次循环
            }

            int bytes = 1;
            // 检测中文字符（高位字节大于0x80）
            if (c > 0x80 && i + 1 < g_processed_text_size) {
                if ((unsigned char)g_processed_text[i+1] > 0x40) {
                    bytes = 2;
                }
            } else if (c > 0x80 && i + 2 < g_processed_text_size) {
                // 检查是否是三字节UTF-8字符（中文字符）
                if (((unsigned char)g_processed_text[i] & 0xE0) == 0xE0) {
                    bytes = 3;
                }
            }

            int width = (bytes == 1) ? Font16.Width : 
                       (bytes == 2) ? Font12CN.Width : 
                       Font12CN.Width; // 三字节UTF-8字符也按中文字符宽度处理

            if (x + width > max_x)
                break;  // 达到行宽限制，换行

            for (int k = 0; k < bytes && i + k < g_processed_text_size; k++)
                line[len++] = g_processed_text[i + k];

            if (bytes > 1) has_cn = 1;
            i += bytes;
            x += width;
        }

        if (len > 0) {
            int lh = has_cn ? lh_cn : lh_en;
            if (y + lh > text_bottom)
                break;

            line[len] = '\0';
            if (has_cn)
                Paint_DrawString_CN(left_margin, y, line, &Font12CN, BLACK, WHITE);
            else
                Paint_DrawString_EN(left_margin, y, line, &Font16, BLACK, WHITE);

            y += lh;
        }
        
        // 如果在循环中没有进展（即i没有增加），则跳出以防止无限循环
        if (i == initial_i && i < g_processed_text_size) {
            // 跳过一个字符以防止无限循环
            unsigned char c = (unsigned char)g_processed_text[i];
            if (c > 0x80 && i + 1 < g_processed_text_size) {
                if ((unsigned char)g_processed_text[i+1] > 0x40) {
                    i += 2;  // 跳过双字节字符
                } else {
                    i += 1;  // 跳过单个字节
                }
            } else {
                i += 1;  // 跳过单个字节
            }
        }
    }

    /* =====================================================
     * 5. Footer：页码（使用准确的页数计算）
     * ===================================================== */
    Paint_ClearWindows(
        0,
        FOOTER_Y_START,
        EPD_7IN5_V2_WIDTH,
        EPD_7IN5_V2_HEIGHT - FOOTER_Y_START,
        WHITE
    );

    // 使用准确的页数计算
    int cur_page = get_current_page_index(start_offset);
    int total_pages_calc = total_pages > 0 ? total_pages : (g_processed_text_size / 2000) + 1;

    char page[64];
    snprintf(page, sizeof(page), "Page %d / %d", cur_page, total_pages_calc);

    Paint_DrawString_EN(
        EPD_7IN5_V2_WIDTH - 160,
        FOOTER_Y_START + 10,  // 页码下移10像素
        page,
        &Font16,
        BLACK,
        WHITE
    );

    /* =====================================================
     * 6. 局部刷新（仅 CONTENT + FOOTER）
     * ===================================================== */
    EPD_7IN5_V2_Display_Part(
        g_frame_buffer,
        0,
        CONTENT_Y_START,
        EPD_7IN5_V2_WIDTH,
        EPD_7IN5_V2_HEIGHT - CONTENT_Y_START
    );

    return i;  // 返回实际结束的偏移量
}

// 进入息屏模式
void enter_screen_off_mode() {
    if (screen_off) return; // 如果已经在息屏状态，直接返回

    printf("Entering screen off mode...\n");
    screen_off = 1;

    // 创建息屏画面
    Paint_SelectImage(g_frame_buffer);
    Paint_Clear(WHITE);
    
    // 显示息屏图片，使用GUI_ReadBmp函数
    GUI_ReadBmp("./src/c/pic/2.bmp", 0, 0) ;
    
    // 显示息屏画面
    EPD_7IN5_V2_Init_Fast();
    EPD_7IN5_V2_Display(g_frame_buffer);
    EPD_7IN5_V2_Sleep(); // 进入休眠模式以节省电力
}

// 退出息屏模式
void exit_screen_off_mode() {
    if (!screen_off) return; // 如果不在息屏状态，直接返回

    printf("Exiting screen off mode...\n");
    screen_off = 0;

    // 重新初始化EPD
    // EPD_7IN5_V2_Init();
    // EPD_7IN5_V2_Clear();
    // EPD_7IN5_V2_Init_Part();

    // 设置first_display_done为0，book_changed为0，确保Header区域会被重新绘制
    first_display_done = 0;
    book_changed = 0;
    header_drawn = 0;

    // 重新显示当前页面，这会重新绘制整个界面
    display_txt_page_from_offset(g_current_char_offset);
    
    // 息屏期间可能累积了一些输入事件，这里读取并丢弃它们，避免误操作
    struct input_event ev;
    if (eye_key_fd >= 0) {
        // 清空眼控设备中积压的事件
        while (read(eye_key_fd, &ev, sizeof(ev)) == sizeof(ev)) {
            // 循环读取直到没有更多事件
        }
    }
    
    // 添加短暂延迟，确保系统有时间处理上述事件
    usleep(100000); // 100ms延迟
}

// 切换到下一本书
void next_book() {
    if (book_count > 1) {
        current_book_index = (current_book_index + 1) % book_count;
        snprintf(current_file, sizeof(current_file), "%s", book_list[current_book_index]);
        if (load_txt_file(current_file) == 0) {
            g_current_char_offset = 0;
            display_txt_page_from_offset(0);
            // 新书开始，清空历史，压入第一页
            history_top = -1;
            if (history_top < MAX_HISTORY - 1) {
                history_stack[++history_top] = 0;
            }
            // 重置书名标记，以便在切换到新书时重新绘制书名
            title_drawn = 0;
            current_page_index = 1;  // 重置为第一页
            printf("Switched to book [%d]: %s\n", current_book_index, current_file);
        }
    }
}

// 切换到上一本书
void prev_book() {
    if (book_count > 1) {
        current_book_index = (current_book_index - 1 + book_count) % book_count;
        snprintf(current_file, sizeof(current_file), "%s", book_list[current_book_index]);
        if (load_txt_file(current_file) == 0) {
            g_current_char_offset = 0;
            display_txt_page_from_offset(0);
            // 新书开始，清空历史，压入第一页
            history_top = -1;
            if (history_top < MAX_HISTORY - 1) {
                history_stack[++history_top] = 0;
            }
            // 重置书名标记，以便在切换到新书时重新绘制书名
            title_drawn = 0;
            current_page_index = 1;  // 重置为第一页
            printf("Switched to book [%d]: %s\n", current_book_index, current_file);
        }
    }
}

// 新增：按键状态跟踪结构体
typedef struct {
    struct timeval press_time;
    int pressed;
    int key_id;
} KeyState;

static KeyState key_states[3] = {0}; // 索引0未使用，1=KEY1, 2=KEY2

// 新增：按键事件处理函数
void handle_key_event(int key_id, struct input_event *ev) {
    if (ev->type != EV_KEY) return;

    // 新增：打印实际接收到的按键码，便于调试
    printf("Received key event: id=%d, code=%d, value=%d\n", key_id, ev->code, ev->value);

    // 检查是否是支持的按键码
    if (key_id == 1) {
        if (!(ev->code == KEY_PAGEDOWN || 
              ev->code == BTN_LEFT || 
              ev->code == BTN_RIGHT || 
              ev->code == BTN_MIDDLE || 
              ev->code == KEY_NEXTSONG ||
              ev->code == BTN_EXTRA ||
              ev->code == KEY_VOLUMEDOWN)) {  // 添加实际使用的按键码
            printf("Key1: Ignoring code %d\n", ev->code);
            return;
        }
    } else if (key_id == 2) {
        if (!(ev->code == KEY_PAGEUP || 
              ev->code == BTN_BASE ||
              ev->code == KEY_VOLUMEUP)) {  // 添加实际使用的按键码
            printf("Key2: Ignoring code %d\n", ev->code);
            return;
        }
    }

    if (ev->value == 1) {  // key down
        gettimeofday(&key_states[key_id].press_time, NULL);
        key_states[key_id].pressed = 1;
    }
    else if (ev->value == 0 && key_states[key_id].pressed) { // key up
        struct timeval now;
        gettimeofday(&now, NULL);

        long press_ms =
            (now.tv_sec - key_states[key_id].press_time.tv_sec) * 1000 +
            (now.tv_usec - key_states[key_id].press_time.tv_usec) / 1000;

        key_states[key_id].pressed = 0;

        if (press_ms > 1000) {
            if (key_id == 1) next_book();
            else prev_book();
        } else {
            if (key_id == 1) {
                if (g_current_char_offset < g_processed_text_size) {
                    if (history_top < MAX_HISTORY - 1) {
                        history_stack[++history_top] = g_current_char_offset;
                    }
                    size_t next_offset = display_txt_page_from_offset(g_current_char_offset);
                    g_current_char_offset = next_offset;
                    printf("Next page from key1 at offset %zu\n", g_current_char_offset);
                } else {
                    printf("End of book.\n");
                }
            } else {
                if (history_top > -1) {
                    g_current_char_offset = history_stack[history_top--];
                    display_txt_page_from_offset(g_current_char_offset);
                    printf("Back to page at offset %zu\n", g_current_char_offset);
                } else {
                    printf("Already at first page.\n");
                }
            }
        }
    }
}

// 修改：使用poll机制处理按键事件
void handle_keys(void) {
    struct pollfd fds[3];
    struct input_event ev;

    // 设置poll描述符
    fds[0].fd = key1_fd;
    fds[0].events = POLLIN;
    fds[1].fd = key2_fd;
    fds[1].events = POLLIN;

    // 添加眼动设备到poll
    int num_fds = 2;
    if (eye_key_fd >= 0) {
        fds[2].fd = eye_key_fd;
        fds[2].events = POLLIN;
        num_fds = 3;
    }

    // 非阻塞轮询
    int ret = poll(fds, num_fds, 0);
    if (ret <= 0) return;

    // 处理KEY1事件
    if (fds[0].revents & POLLIN) {
        if (read(key1_fd, &ev, sizeof(ev)) == sizeof(ev)) {
            handle_key_event(1, &ev);
        }
    }

    // 处理KEY2事件
    if (fds[1].revents & POLLIN) {
        if (read(key2_fd, &ev, sizeof(ev)) == sizeof(ev)) {
            handle_key_event(2, &ev);
        }
    }

    // 处理眼动设备事件
    if (num_fds > 2 && (fds[2].revents & POLLIN)) {
        if (read(eye_key_fd, &ev, sizeof(ev)) == sizeof(ev)) {
            if (ev.type == EV_KEY && ev.value == 1) {
                // 检查是否是息屏或唤醒信号
                if (ev.code == CUSTOM_SCREEN_OFF_BTN) {
                    enter_screen_off_mode();
                } else if (ev.code == CUSTOM_SCREEN_ON_BTN) {
                    exit_screen_off_mode();
                } else if (ev.code == KEY_PAGEDOWN) {
                    if (g_current_char_offset < g_processed_text_size) {
                        if (history_top < MAX_HISTORY - 1) {
                            history_stack[++history_top] = g_current_char_offset;
                        }
                        size_t next_offset = display_txt_page_from_offset(g_current_char_offset);
                        g_current_char_offset = next_offset;
                        printf("Next page from eye control at offset %zu\n", g_current_char_offset);
                    } else {
                        printf("End of book.\n");
                    }
                }
            }
        }
    }
}

// 主函数
void EPD_7in5_V2_reader_txt(void) {
    printf("E-Ink Reader: Full Continuity, No Truncation, Exact Page History\n");

#ifndef QUECPI
    if (DEV_Module_Init() != 0) return;
#else
    extern int GPIO_Handle;
    GPIO_Handle = lgGpiochipOpen(4);
    if (GPIO_Handle < 0) {
        printf("Failed to open gpiochip4\n");
        return;
    }
    lgGpioClaimOutput(GPIO_Handle, 0, 47, 0);
    extern int SPI_Handle;
    SPI_Handle = lgSpiOpen(10, 0, 10000000, 0);
    if (SPI_Handle < 0) {
        printf("Failed to open spidev10.0\n");
        return;
    }
    DEV_GPIO_Init();
#endif

    // 打开物理按键设备
    key1_fd = open("/dev/input/event3", O_RDONLY);
    key2_fd = open("/dev/input/event1", O_RDONLY);

    if (key1_fd < 0 || key2_fd < 0) {
        printf("Physical key devices not found\n");
        goto cleanup;
    }

    // 初始化并查找虚拟眼控设备
    init_eye_control_device();
    eye_key_fd = find_eye_control_device();
    if (eye_key_fd < 0) {
        printf("Warning: Failed to find eye control device, attempting to open event9 as fallback\n");
        // 备选方案：尝试打开event9
        eye_key_fd = open("/dev/input/event9", O_RDONLY | O_NONBLOCK);
        if(eye_key_fd < 0) {
            printf("Warning: Failed to open fallback eye control device\n");
        }
    }

    DIR* dir = opendir(BOOK_PATH);
    if (!dir) {
        show_error("Books dir not found");
        goto cleanup;
    }
    struct dirent* entry;
    book_count = 0;
    while ((entry = readdir(dir)) != NULL && book_count < MAX_BOOKS) {
        if (entry->d_type == DT_REG) {
            const char* ext = get_ext(entry->d_name);
            if (strcasecmp(ext, "txt") == 0) {
                snprintf(book_list[book_count], sizeof(book_list[0]), "%s/%s", BOOK_PATH, entry->d_name);
                book_count++;
            }
        }
    }
    closedir(dir);
    if (book_count == 0) {
        show_error("No TXT file found");
        goto cleanup;
    }

    current_book_index = 0;
    snprintf(current_file, sizeof(current_file), "%s", book_list[current_book_index]);

    if (load_txt_file(current_file) != 0) {
        show_error("TXT load failed");
        goto cleanup;
    }

    UDOUBLE Imagesize = ((EPD_7IN5_V2_WIDTH % 8 == 0) ? (EPD_7IN5_V2_WIDTH / 8) : (EPD_7IN5_V2_WIDTH / 8 + 1)) * EPD_7IN5_V2_HEIGHT;
    g_frame_buffer = (UBYTE *)malloc(Imagesize);
    if (!g_frame_buffer) {
        printf("Malloc failed\n");
        goto cleanup;
    }
    // 分配前一帧缓存，用于对比和局部刷新
    g_prev_frame_buffer = (UBYTE *)malloc(Imagesize);
    if (!g_prev_frame_buffer) {
        printf("Malloc for previous frame failed\n");
        free(g_frame_buffer);
        goto cleanup;
    }
    Paint_NewImage(g_frame_buffer, EPD_7IN5_V2_WIDTH, EPD_7IN5_V2_HEIGHT, ROTATE_180, WHITE);

    // 显示第一页
    g_current_char_offset = 0;
    size_t next_offset = display_txt_page_from_offset(g_current_char_offset);
    g_current_char_offset = next_offset;  // 更新g_current_char_offset为下一页的起始位置
    // 压入第一页历史（便于后续回退到开头）
    if (history_top < MAX_HISTORY - 1) {
        history_stack[++history_top] = 0;
    }

    printf("Reader started. Books: %d\n", book_count);
    while (1) {
        handle_keys(); // 处理物理按键和虚拟眼控按键
        usleep(50000);
    }

cleanup:
    free(g_full_text);
    free(g_processed_text);  // 释放处理后的文本
    free(page_offsets);      // 释放页偏移数组
    free(g_frame_buffer);
    free(g_prev_frame_buffer);
    if (key1_fd >= 0) close(key1_fd);
    if (key2_fd >= 0) close(key2_fd);
    if (eye_key_fd >= 0) close(eye_key_fd);
    EPD_7IN5_V2_Sleep();
}