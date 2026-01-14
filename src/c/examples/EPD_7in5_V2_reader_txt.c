// examples/EPD_7in5_V2_reader_txt.c
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
#include "DEV_Config.h"

#define BOOK_PATH "/home/pi/ebook-reader/src/tools/books"
#define MAX_BOOK_SIZE (2 * 1024 * 1024) // 2MB
#define MAX_BOOKS 20
#define MAX_HISTORY 500 // 最多记录500页历史a

// 局部刷新区域定义
#define HEADER_HEIGHT   30
#define FOOTER_HEIGHT   40

#define TEXT_TOP        HEADER_HEIGHT
#define TEXT_BOTTOM     (EPD_7IN5_V2_HEIGHT - FOOTER_HEIGHT)


static char current_file[256] = {0};
static UBYTE *g_frame_buffer = NULL;
static UBYTE *g_prev_frame_buffer = NULL; // 用于比较前后两帧差异，实现真正意义上的局部刷新
static int key1_fd = -1; // event3: next page / long press: next book
static int key2_fd = -1; // event1: prev page / long press: prev book
static int eye_key_fd = -1; // 新增: eye_page_turner 虚拟设备
static int first_display_done = 0;

// 多书支持
static char book_list[MAX_BOOKS][256];
static int book_count = 0;
static int current_book_index = 0;

// 全局文本
static char* g_full_text = NULL;
static size_t g_text_size = 0;

// 当前页起始偏移（字节）
static size_t g_current_char_offset = 0;

// 历史栈：记录每一页的起始偏移（用于精确回退）
static size_t history_stack[MAX_HISTORY];
static int history_top = -1;

const char* get_ext(const char* filename) {
    const char* dot = strrchr(filename, '.');
    return (dot && dot != filename) ? dot + 1 : "";
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

    // 重置状态
    g_current_char_offset = 0;
    history_top = -1; // 清空历史
    first_display_done = 0;

    printf("Loaded %zu bytes from %s\n", g_text_size, path);
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
size_t display_txt_page_from_offset(size_t start_offset) {
    static int title_drawn = 0;
    if (!g_full_text || start_offset >= g_text_size) {
        Paint_SelectImage(g_frame_buffer);
        Paint_Clear(WHITE);
        EPD_7IN5_V2_Display(g_frame_buffer);
        return g_text_size;
    }

    Paint_SelectImage(g_frame_buffer);
    // 不清空整个屏幕，只清空内容区域
    if(first_display_done) {
        // 局部刷新：只清空内容区域
        Paint_ClearWindows(
    0,
    TEXT_TOP,
    EPD_7IN5_V2_WIDTH,
    TEXT_BOTTOM,
    WHITE
);
    } else {
        // 首次显示需要清空整个屏幕
        Paint_Clear(WHITE);
    }

    // 显示当前书籍名称
    char book_title[256];
    snprintf(book_title, sizeof(book_title), "Book: %s", current_file + strlen(BOOK_PATH) + 1);
    if (!title_drawn) {
    Paint_DrawString_EN(10, 10, book_title, &Font16, BLACK, WHITE);
    title_drawn = 1;
}

    Paint_DrawString_EN(10, 10, book_title, &Font16, BLACK, WHITE);
    
    // 显示页码信息
    char page_info[100];
    snprintf(page_info, sizeof(page_info), "Page: %d/%d", history_top+1, (int)(g_text_size/3000)+1); // 粗略估算页数
    Paint_DrawString_EN(EPD_7IN5_V2_WIDTH - 150, EPD_7IN5_V2_HEIGHT - 20, page_info, &Font16, BLACK, WHITE);

    const int left_margin = 10;
    const int right_margin = 10;
    const int max_x = EPD_7IN5_V2_WIDTH - right_margin;
    const int line_height_en = Font16.Height* 0.8;   // 英文行高
    const int line_height_cn = Font12CN.Height; // 中文行高
    int y = TEXT_TOP; // 从内容区域开始绘制
    const int max_y = TEXT_BOTTOM - 20; // 留出底部边距

    size_t i = start_offset;
    while (i < g_text_size && y < max_y) {
        // 跳过连续换行符（段落间距）
        while (i < g_text_size && (g_full_text[i] == '\r' || g_full_text[i] == '\n')) {
            i++;
            y += line_height_cn; // 段落间距统一用中文高度
            if (y >= max_y) break;
        }
        if (i >= g_text_size || y >= max_y) break;

        // 构建当前可视行
        int x = left_margin;
        char temp_line[512] = {0};
        int temp_len = 0;
        int has_chinese = 0;

        while (i < g_text_size) {
            unsigned char b1 = (unsigned char)g_full_text[i];
            if (b1 == '\r' || b1 == '\n') {
                i++; // 跳过换行符
                break;
            }

            int char_width, char_bytes;
            if (b1 > 0x80 && i + 1 < g_text_size) {
                // GB2312 中文（双字节）
                char_width = Font12CN.Width; // 通常 16
                char_bytes = 2;
                has_chinese = 1;
            } else {
                // ASCII 字符
                char_width = Font16.Width; // 通常 8
                char_bytes = 1;
            }

            // 检查是否超出屏幕宽度
            if (x + char_width > max_x) {
                break; // 放不下，留到下一行
            }

            // 添加字符到临时行
            for (int k = 0; k < char_bytes; k++) {
                temp_line[temp_len++] = g_full_text[i + k];
            }
            x += char_width;
            i += char_bytes;
        }

        // 绘制当前行
        if (temp_len > 0) {
            temp_line[temp_len] = '\0';
            int current_line_height = has_chinese ? line_height_cn : line_height_en;

            if (has_chinese) {
                Paint_DrawString_CN(left_margin, y, temp_line, &Font12CN, BLACK, WHITE);
            } else {
                Paint_DrawString_EN(left_margin, y, temp_line, &Font16, BLACK, WHITE);
            }
            y += current_line_height;
        }

        if (y >= max_y) break;
    }

    // 显示页面
    if (!first_display_done) {
    printf("First display: full clear\n");

    // ① 全刷模式初始化
    EPD_7IN5_V2_Init();
    EPD_7IN5_V2_Clear();
    DEV_Delay_ms(1500);
    EPD_7IN5_V2_Display(g_frame_buffer);

    // ② 关键：切换到局部刷新 LUT
    DEV_Delay_ms(500);
    EPD_7IN5_V2_Init_Part();

    first_display_done = 1;
}
else {
        // 使用局部刷新模式更新内容区域
        EPD_7IN5_V2_Display_Part(
    g_frame_buffer,
    0,
    TEXT_TOP,
    EPD_7IN5_V2_WIDTH,
    TEXT_BOTTOM
);
    }

    return i; // 返回下一个起始偏移
}

// 切换到下一本书
void next_book() {
    if (book_count > 1) {
        current_book_index = (current_book_index + 1) % book_count;
        snprintf(current_file, sizeof(current_file), "%s", book_list[current_book_index]);
        if (load_txt_file(current_file) == 0) {
            g_current_char_offset = 0;
            size_t next_offset = display_txt_page_from_offset(0);
            // 新书开始，清空历史，压入第一页
            history_top = -1;
            if (history_top < MAX_HISTORY - 1) {
                history_stack[++history_top] = 0;
            }
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
            size_t next_offset = display_txt_page_from_offset(0);
            // 新书开始，清空历史，压入第一页
            history_top = -1;
            if (history_top < MAX_HISTORY - 1) {
                history_stack[++history_top] = 0;
            }
            printf("Switched to book [%d]: %s\n", current_book_index, current_file);
        }
    }
}

// 按键处理
void handle_keys(void) {
    struct input_event ev;

    // KEY2: 上一页 / 上一本书
    while (read(key2_fd, &ev, sizeof(ev)) == sizeof(ev)) {
        if (ev.type == EV_KEY && ev.value == 1) {
            struct timeval press_time, release_time;
            gettimeofday(&press_time, NULL);
            while (1) {
                if (read(key2_fd, &ev, sizeof(ev)) == sizeof(ev) && ev.type == EV_KEY && ev.value == 0) {
                    gettimeofday(&release_time, NULL);
                    break;
                }
                usleep(10000);
            }
            long press_ms = (release_time.tv_sec - press_time.tv_sec) * 1000 +
                           (release_time.tv_usec - press_time.tv_usec) / 1000;

            if (press_ms > 1000) {
                // 长按：上一本书
                prev_book();
            } else {
                // 短按：上一页
                if (history_top > -1) {
                    g_current_char_offset = history_stack[history_top--];
                    display_txt_page_from_offset(g_current_char_offset);
                    printf("Back to page at offset %zu\n", g_current_char_offset);
                } else {
                    printf("Already at first page.\n");
                }
            }
            break;
        }
    }

    // KEY1: 下一页 / 下一本书
    while (read(key1_fd, &ev, sizeof(ev)) == sizeof(ev)) {
        if (ev.type == EV_KEY && ev.value == 1) {
            struct timeval press_time, release_time;
            gettimeofday(&press_time, NULL);
            while (1) {
                if (read(key1_fd, &ev, sizeof(ev)) == sizeof(ev) && ev.type == EV_KEY && ev.value == 0) {
                    gettimeofday(&release_time, NULL);
                    break;
                }
                usleep(10000);
            }
            long press_ms = (release_time.tv_sec - press_time.tv_sec) * 1000 +
                           (release_time.tv_usec - press_time.tv_usec) / 1000;

            if (press_ms > 1000) {
                // 长按：下一本书
                next_book();
            } else {
                // 短按：下一页
                if (g_current_char_offset < g_text_size) {
                    // 保存当前页偏移到历史栈
                    if (history_top < MAX_HISTORY - 1) {
                        history_stack[++history_top] = g_current_char_offset;
                    }
                    size_t next_offset = display_txt_page_from_offset(g_current_char_offset);
                    g_current_char_offset = next_offset;
                    printf("Next page from offset %zu\n", g_current_char_offset);
                } else {
                    printf("End of book.\n");
                }
            }
            break;
        }
    }

    // 眼控设备事件处理 - 只处理来自虚拟设备的事件
    if (eye_key_fd >= 0) {
        while (read(eye_key_fd, &ev, sizeof(ev)) == sizeof(ev)) {
            if (ev.type == EV_KEY && ev.value == 1) {
                // 打印键值，用于调试
                printf("[EYE] Key pressed: %d (%s)\n", ev.code, 
                       ev.code == KEY_PAGEUP ? "KEY_PAGEUP" : 
                       ev.code == KEY_PAGEDOWN ? "KEY_PAGEDOWN" :
                       ev.code == KEY_NEXT ? "KEY_NEXT" :
                       ev.code == KEY_PREVIOUS ? "KEY_PREVIOUS" : "OTHER");

                struct timeval press_time, release_time;
                gettimeofday(&press_time, NULL);
                while (1) {
                    if (read(eye_key_fd, &ev, sizeof(ev)) == sizeof(ev) && ev.type == EV_KEY && ev.value == 0) {
                        gettimeofday(&release_time, NULL);
                        break;
                    }
                    usleep(10000);
                }
                long press_ms = (release_time.tv_sec - press_time.tv_sec) * 1000 +
                               (release_time.tv_usec - press_time.tv_usec) / 1000;

                switch (ev.code) {
                    case KEY_PAGEUP:  // 上一页按键
                        if (press_ms > 1000) {
                            // 长按：上一本书
                            prev_book();
                        } else {
                            // 短按：上一页
                            if (history_top > -1) {
                                g_current_char_offset = history_stack[history_top--];
                                display_txt_page_from_offset(g_current_char_offset);
                                printf("Back to page at offset %zu\n", g_current_char_offset);
                            } else {
                                printf("Already at first page.\n");
                            }
                        }
                        break;
                        
                    case KEY_PAGEDOWN:  // 下一页按键
                        if (press_ms > 1000) {
                            // 长按：下一本书
                            next_book();
                        } else {
                            // 短按：下一页
                            if (g_current_char_offset < g_text_size) {
                                if (history_top < MAX_HISTORY - 1) {
                                    history_stack[++history_top] = g_current_char_offset;
                                }
                                size_t next_offset = display_txt_page_from_offset(g_current_char_offset);
                                g_current_char_offset = next_offset;
                                printf("Next page from offset %zu\n", g_current_char_offset);
                            } else {
                                printf("End of book.\n");
                            }
                        }
                        break;
                        
                    case KEY_NEXT:  // 下一本书按键 (对应头部向下移动)
                        next_book();
                        break;
                        
                    case KEY_PREVIOUS:  // 上一本书按键 (对应头部向上移动)
                        prev_book();
                        break;
                        
                    default:
                        printf("[EYE] Unknown key code: %d\n", ev.code);
                        break;
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
    key1_fd = open("/dev/input/event3", O_RDONLY | O_NONBLOCK);
    key2_fd = open("/dev/input/event1", O_RDONLY | O_NONBLOCK);

    // 打开虚拟眼控设备
    eye_key_fd = open("/dev/input/event9", O_RDONLY | O_NONBLOCK); // 注意：event9 是从 /proc/bus/input/devices 查到的
    if (eye_key_fd < 0) {
        printf("Warning: Failed to open eye control device\n");
        // 继续运行，即使没有眼控设备也能用物理按键
    }

    if (key1_fd < 0 || key2_fd < 0) {
        printf("Key devices not found\n");
        goto cleanup;
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
    free(g_frame_buffer);
    free(g_prev_frame_buffer);
    if (key1_fd >= 0) close(key1_fd);
    if (key2_fd >= 0) close(key2_fd);
    if (eye_key_fd >= 0) close(eye_key_fd);
    EPD_7IN5_V2_Sleep();
}