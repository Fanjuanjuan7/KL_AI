# KL账号批量注册工具 (KL_AI)

## 简介
本工具是一款专业的自动化注册管理系统，它集成了智能 IP 资源管理、加密邮箱资源池管理以及比特浏览器 (BitBrowser) 自动化调度功能。系统支持多线程并发操作，具备完善的任务队列管理和异常处理机制，能够显著提升账号注册效率。



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
