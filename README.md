# 📋 悬浮剪贴板 (Floating Clipboard)

一款基于 **Python + PySide6** 开发的 Windows 桌面悬浮剪贴板管理工具，深度致敬并复刻 **uTools 剪贴板** 的高级深色极简 UI 与高效交互体验。

支持**文本、图片、本地文件**自动分类归档，具备**多选按序合并复制、一键上屏粘贴、多种全局手势唤起（组合键 / 双击按键 / 鼠标中键与侧键长按）**等实用功能。

---

## ✨ 核心特性

- **🎯 全类型自动识别与去重**
  - 自动监听并归档 **文本**（字数统计）、**图像**（分辨率识别 + PNG 缩略图缓存，支持展开大图预览）、**文件列表**（多文件彩色类型图标与路径预览）。
  - 基于 `SHA-256` 哈希自动去重：重复复制相同内容自动置顶更新时间，不产生冗余记录；内置双重防回环机制，合并复制不会引发死循环监听。
- **🔢 按序多选与智能合并**
  - 单击卡片即可按点击先后顺序多选（显示 `① ② ③` 顺序徽标），支持自定义合并分隔符（换行符、空行、空格或无缝拼接）。
  - 支持多条文本合并复制，也支持多条文件/图片记录合并为同一批文件列表复制。
- **🚀 一键粘贴到当前活动窗口**
  - 点击右侧纸飞机按钮，自动将选中内容合并写入剪贴板、隐藏悬浮窗，并向唤起前的外部活动窗口发送 `Ctrl + V` 完成一键上屏。
- **⌨️ 强大的全局唤起手势**
  - **标准组合键**：默认 `Alt + V`，支持自定义任意组合快捷键。
  - **键盘进阶手势**：支持 `双击 Alt`、`双击 Ctrl`、`双击 Shift` 等修饰键触发。
  - **鼠标手势触发**：支持 `长按鼠标中键`、`长按鼠标侧键1/2`、`双击鼠标中键` 等鼠标快捷唤起，响应时间（`50 ~ 5000 ms`）自由可调。
- **🎨 高级深色极简悬浮窗**
  - 无边框圆角阴影设计，包含 `全部 / 文本 / 图像 / 文件 / 收藏` 五大无缝切换标签页与实时关键词搜索框。
  - 支持**窗口图钉置顶**（失焦不自动收起）、**星标收藏保护**（一键清空时自动跳过收藏项）、**系统托盘常驻**、**单实例防多开唤醒**与 **Windows 开机自启**。
- **💾 便携式本地存储**
  - 基于 SQLite（`data/clipboard.db`）纯本地存储，零联网，保障隐私安全；便携模式下数据默认跟随程序同级 `data/` 目录，权限受限时自动回退至 `%LOCALAPPDATA%\FloatingClipboard\data`。

---

## 🛠️ 环境要求与运行方式

### 1. 环境准备

- **操作系统**：Windows 10 / Windows 11
- **Python 版本**：Python 3.10 及以上

克隆仓库并安装依赖：

```powershell
git clone https://github.com/Delectatiomorosa/floating-clipboard.git
cd floating-clipboard
pip install -r requirements.txt
```

### 2. 启动程序

```powershell
python main.py
```

除此之外，`main.py` 还提供了便捷的命令行工具模式：

```powershell
python main.py --console                        # 仅在终端监听并实时打印剪贴板变化
python main.py --list                           # 查看所有历史记录
python main.py --list --category favorite       # 仅查看收藏记录
python main.py --list --search "关键词"          # 按关键词检索记录
python main.py --clear                          # 清空所有未收藏记录
python main.py --verify                         # 运行核心存储与监听端到端自检
```

### 3. 打包为独立 `.exe` 程序

项目内置了一键打包脚本，双击或运行 [`build_exe.bat`](build_exe.bat) 即可自动生成图标并使用 PyInstaller 打包单文件无黑框可执行程序：

```powershell
.\build_exe.bat
```

打包完成后，可在项目根目录或 `dist/` 目录下获取生成的 `悬浮剪贴板.exe`。

---

## ⌨️ 快捷键与操作指南

| 快捷键 / 操作 | 功能说明 |
| :--- | :--- |
| **`Alt + V`**（默认，可在设置中修改） | 全局唤起 / 隐藏剪贴板悬浮窗 |
| **单击卡片** | 选中 / 取消选中卡片（多选时自动按点击顺序编号 `① ② ③...`） |
| **`Ctrl + C`**（或右侧 **复制** 按钮） | 将选中项按顺序合并复制到系统剪贴板 |
| **右侧 纸飞机 按钮** | 将选中项合并复制并自动粘贴（`Ctrl + V`）到原活动窗口 |
| **右侧 星标 按钮** | 批量收藏 / 取消收藏选中记录 |
| **右侧 图钉 按钮** | 固定悬浮窗（开启后点击外部窗口不自动隐藏） |
| **`Ctrl + A`** | 全选 / 取消全选当前列表可见记录 |
| **`Delete`** | 删除当前选中的记录 |
| **`Esc`** | 依次清空搜索词 / 取消多选 / 隐藏悬浮窗 |

---

## 📂 项目目录结构

```text
├── main.py                  # 程序主入口（支持 GUI 模式与 CLI 调试命令）
├── requirements.txt         # Python 依赖清单 (PySide6, Pillow, PyInstaller)
├── build_exe.bat            # Windows 一键生成图标并打包 .exe 脚本
├── assets/
│   ├── generate_icon.py     # 应用图标生成脚本
│   └── icon.ico             # 应用窗口与托盘图标
├── core/
│   ├── clipboard_watcher.py # 剪贴板实时监听、SHA-256 去重与合并写入引擎
│   ├── database.py          # SQLite 数据库读写、分类筛选、检索与收藏管理
│   ├── hotkey_manager.py    # Win32 全局组合键与键盘/鼠标长按双击钩子管理器
│   ├── config_manager.py    # JSON 配置文件原子化读写管理
│   ├── autostart.py         # Windows 注册表开机自启管理
│   ├── single_instance.py   # 基于命名管道的单实例运行锁与二次启动唤醒
│   ├── win_input.py         # 外部活动窗口句柄记录与 Ctrl+V 按键模拟投递
│   └── paths.py             # 开发环境与 PyInstaller 打包环境路径自适应工具
├── ui/
│   ├── main_window.py       # 无边框深色悬浮主窗口、搜索栏、分类栏与操作栏
│   ├── item_card.py         # 剪贴板卡片组件（支持文本/图片/文件预览与展开）
│   ├── settings_dialog.py   # 交互式快捷键录制与偏好设置弹窗
│   ├── tray_manager.py      # 系统托盘图标与右键菜单管理
│   ├── icons.py             # 矢量绘制图标生成工具
│   └── theme.py             # 深色主题色板、字体与阴影样式定义
└── tests/                   # 单元测试与分步功能自动化测试套件
```

---

## 🧪 运行测试

```powershell
python -m unittest discover -s tests -p "test_*.py"
```
