# KL账号批量注册工具 (KL_AI)

## 简介
本工具是一款专业的自动化注册管理系统，专为 Kling 平台设计。它集成了智能 IP 资源管理、加密邮箱资源池管理以及比特浏览器 (BitBrowser) 自动化调度功能。系统支持多线程并发操作，具备完善的任务队列管理和异常处理机制，能够显著提升账号注册效率。

## 核心功能

### 1. 注册任务管理 (自动化核心)
- **智能调度**: 支持多线程并发注册，根据剩余任务量动态调整并发数。
- **任务控制**:
  - **目标设定**: 可设置本次运行的目标注册数量（达到即停）。
  - **实时监控**: 界面实时显示「已成功 / 本次目标 / 最大可注册」数据。
  - **一键启停**: 提供醒目的开始/停止按钮，支持中途安全停止任务。
- **自动化参数**:
  - 可自定义元素超时时间、检测间隔、最大重试轮数。
  - 支持启用/禁用 XPath 自动化流程。

### 2. IP 资源池管理 (智能轮询)
- **循环复用**: 实现 IP 地址的均匀轮询使用，确保资源利用率最大化。
- **灵活导入**: 支持 TXT/CSV 文件批量导入。
- **协议支持**: 全面支持 socks5, http, https 代理协议。

### 3. 邮箱资源池管理 (明文存储)
- **新版格式**: 统一采用 `email----password----auth_code` 格式。
- **配置简化**: 授权码 (Auth Code) 直接明文存储，去除复杂的加密逻辑，避免密钥丢失导致的数据不可用。
- **智能管理**: 支持自动标记无效邮箱、轮询取用、状态同步。
- **验证码接码**: 统一接码接口，支持 IMAP 协议自动收取验证码 (Outlook/Hotmail 等)。

### 4. 目录规范与健康检查
- **标准目录**:
  - `src/`: 源代码
  - `config/`: 配置文件 (IP池, 邮箱池, XPath配置)
  - `logs/`: 运行日志
  - `scripts/`: 一键启动脚本
- **健康检查**: 内置健康检查服务，提供 `GET /health` 接口监控运行状态。

---

## 快速开始

### 1. 环境准备
- **操作系统**: Windows 10/11, macOS, Linux (带 GUI)
- **Python**: >= 3.9
- **依赖服务**: 比特浏览器 (BitBrowser) 需开启 RPA 接口 (默认端口 54345)

### 2. 一键启动 (自动安装依赖)
无需手动安装依赖，脚本会自动检测环境并完成配置。

**macOS / Linux:**
```bash
chmod +x scripts/*.sh
./scripts/start.sh
```

**Windows:**
双击运行 `scripts\start.bat` 即可。
```cmd
scripts\start.bat
```

---

## 配置说明

### 邮箱池格式 (`config/email_pool.csv`)
文件位于 `config/email_pool.csv`，格式如下：
```text
# email----password----auth_code
example1@outlook.com----password123----authcode456
example2@outlook.com----password789----authcode000
```
- **Auth Code**: 若邮箱未开启两步验证，此处填密码即可。
- **注意**: 请直接填入明文，程序不再进行二次加密。

### API 说明 (健康检查)
启动应用后，可访问健康检查接口：
- **URL**: `http://localhost:8080/health` (端口可在 .env 中配置 `HEALTH_PORT`)
- **Method**: `GET`
- **Response**:
  ```json
  {
    "status": "ok",
    "ts": 1700000000,
    "pid": 12345
  }
  ```

---

## 故障排查清单

| 问题现象 | 可能原因 | 解决方案 |
| :--- | :--- | :--- |
| **SSL Error / Unexpected EOF** | 代理连接不稳定或被重置 | 1. 检查 IP 池代理质量<br>2. 确保 BitBrowser 代理配置正确 |
| **BitBrowser 连接失败** | 浏览器未启动或端口不匹配 | 1. 确认 BitBrowser 已运行<br>2. 检查设置中端口是否为 54345 |
| **验证码收取超时** | IMAP 服务未开启或网络阻塞 | 1. 登录邮箱网页版开启 IMAP<br>2. 检查防火墙设置 |
| **GUI 启动报错 (ImportError)** | 依赖未安装或路径错误 | 重新运行 `scripts/start.sh` 或 `scripts\start.bat` |

---

## 文件结构
```
KL_AI/
├── config/              # 配置文件
│   ├── email_pool.csv   # 邮箱池
│   ├── ip_pool.json     # IP 池
│   └── kling_xpaths.json
├── logs/                # 日志文件
├── scripts/             # 脚本
│   └── start.sh         # macOS/Linux 启动脚本
│   └── start.bat        # Windows 启动脚本
├── src/                 # 源代码
│   ├── gui_ctk.py       # GUI 入口
│   ├── email_pool.py    # 邮箱池逻辑
│   ├── health_server.py # 健康检查服务
│   └── ...
├── requirements.txt
└── README.md
```
