# 票务监控桌面程序

这是当前版本的票务监控程序，支持本地 Tkinter 图形界面和服务器无界面常驻运行：

- 在 GUI 中登录两个平台并保存本地登录态；
- 管理本地购票人以及每个任务使用的购票人；
- 从官方接口读取演出、场次和精确票品；
- 每个任务独立配置理想订单总价与查询间隔；
- 命中条件后复核官网最终应付金额并创建真实待支付订单；
- 创建成功后停止任务，通过 Server酱发送票务信息与支付链接；
- 只创建待支付订单，绝不自动付款；
- SQLite 审计查询、订单和通知结果。
- 服务器启动时及此后每小时清理超过 24 小时的审计日志。

旧 CLI、Mock 平台、旧 GUI、旧 Playwright DOM 下单实现、复杂票品匹配器和多通知渠道已经删除。

## 启动最新代码

在 PowerShell 中执行：

```powershell
Set-Location D:\tools\ticket
.\.venv\Scripts\python.exe main.py
```

本地图形界面只有这一个启动方式，不再接受 `gui`、`mock`、`run` 等旧 CLI 参数。

如果还没有虚拟环境：

```powershell
Set-Location D:\tools\ticket
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

服务器使用无界面入口：

```bash
python -m app.headless
```

服务器入口读取与 GUI 完全相同的 SQLite 数据库、平台登录态、监控任务和
Server酱配置，不会打开登录窗口。登录失效后应在本地 GUI 重新登录，再更新
服务器上的数据库。

## 配置

`.env` 只需要 Server酱 SendKey：

```dotenv
# Server酱通知
SERVERCHAN_SENDKEY=
```

数据库固定默认为 `data/ticket.db`，浏览器默认使用 Microsoft Edge。任务价格和查询间隔保存在 SQLite 中，不放在 `.env`。

不要提交或分享以下本地文件：

- `.env`；
- `data/ticket.db`；
- `data/browser_profiles/`；
- `data/browser_states/`。

这些文件可能包含通知密钥、购票人资料或平台登录态。

## 使用流程

1. 运行 `main.py`。
2. 在“平台登录”中登录票牛和摩天轮。
3. 在“购票人”中维护购票人。
4. 在“监控任务”中新建任务，选择官方演出链接、场次、精确票品、数量和购票人。
5. 设置理想订单总价和查询间隔后启动任务。
6. 当前预估金额满足条件时，程序进入 `order_preparing` 并访问官方确认订单流程。
7. 官网最终应付金额不超过理想总价时创建真实待支付订单。
8. 成功后任务进入 `payment_pending`，Server酱发送演出、场次、票品、金额、订单号与支付链接。
9. 用户自行打开支付链接并完成付款。

如果登录失效或下单接口失败，任务会记录明确错误并暂停，不会在 `price_matched` 状态反复提交。

## 当前目录

```text
.
├── main.py
├── requirements.txt
├── requirements-server.txt
├── deploy/
│   └── ticket-monitor.service
├── .env.example
├── app/
│   ├── auth/                 # Playwright 登录与 HTTP 会话转换
│   ├── gui/                  # 当前 Tkinter GUI
│   ├── notifications/        # Server酱
│   ├── platforms/            # 票牛、摩天轮真实 HTTP API
│   ├── services/             # 价格监控与订单协调
│   ├── storage/              # SQLite 仓储与审计
│   ├── domain.py
│   ├── headless.py
│   ├── monitor_scheduler.py
│   └── settings.py
├── data/
│   ├── ticket.db
│   ├── browser_profiles/
│   └── browser_states/
└── tests/
    ├── unit/
    └── integration/
```

## 测试

纯逻辑和本地组件：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit -q
```

真实只读接口：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_real_readonly.py -q -s
```

真实创建待支付订单测试默认关闭，必须单独提供明确确认与测试上限；程序和测试都不会自动支付。

## 安全边界

程序不会绕过验证码、短信、扫码、实名验证、风控或平台权限，不会自动付款。创建订单前会重新查询精确票品、复核最终金额并检查幂等记录；创建请求结果不确定时会停止任务，禁止盲目重复提交。
