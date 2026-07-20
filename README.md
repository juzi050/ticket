# Python 票务价格监控与锁单辅助系统

这是一个面向 Python 3.11+、Windows 10/11 的异步票务监控项目。它提供多任务调度、价格和座位条件匹配、独立登录会话、锁单幂等控制、微信通知、SQLite 历史记录以及完整 Mock 演示。

> 重要：程序只辅助进入正常订单确认/锁库存流程，永远不会自动付款。验证码、短信、扫码、实名确认、风控和支付必须由用户人工完成。

## 当前实现状态

已完整可运行的部分：

- 配置校验、金额 `Decimal` 处理、价格/区域/排数/座位/连座匹配；
- `asyncio` 多任务调度、任务异常隔离、动态启停、随机抖动和降频；
- 每个平台独立的持久化浏览器目录、Storage State 和异步登录锁；
- SQLite 任务、价格、匹配、锁单、通知记录；
- 锁单前重新查询、价格复核、SQLite 幂等占位和重试上限；
- 企业微信机器人、Server酱、PushPlus 和控制台通知；
- 通知指数退避、后台发送、日志按天轮转和敏感字段脱敏；
- 不依赖真实网站的 Mock 登录、监控、匹配、锁单、通知完整流程；
- 全部要求的命令行入口和 pytest 测试。

真实平台现状：

- 票牛已使用真实公开页面完成右上角登录弹窗、登录态检测，以及场次、票档、数量、售价、区域、连座和拆单费解析；支持普通场次列表与日历场次；等待验证码时不会刷新关闭登录弹窗；
- 摩天轮已完成“我的”登录态验证，以及详情页场次/人数选择、票品单价/区域/随机座位/保证连座解析；
- 两个平台锁单前都会重新打开页面复核票品和价格，再进入官方订单流程读取最终应付金额；金额超限立即停止；
- 摩天轮当前订单页要求实名观演人且下一步按钮为“立即支付”，程序会在此暂停等待人工处理，不会点击支付；
- 仅当页面明确显示独立的“提交订单/确认订单/确认下单”按钮时，程序才允许提交并停在待支付阶段；验证码、短信、实名补充、人工确认和付款始终暂停；
- 真实页面适配已在 2026-07-20 通过只读查询验证；网站改版后应重新核验选择器。Mock 模式仍可用于完整离线演示。

## 目录结构

```text
.
├── main.py
├── config.example.yaml
├── .env.example
├── requirements.txt
├── app/
│   ├── config.py / models.py / database.py
│   ├── logger.py / retry.py / notifier.py
│   ├── scheduler.py / cli.py
│   ├── platforms/
│   │   ├── base.py / piaoniu.py / motianlun.py / mock.py
│   └── services/
│       ├── session_service.py / login_service.py
│       ├── ticket_matcher.py / monitor_service.py
│       ├── order_service.py / notification_service.py
├── data/
│   ├── browser_states/
│   ├── browser_profiles/
│   └── ticket_monitor.db
├── logs/
└── tests/
```

## Windows 安装

要求 Python 3.11 或更高版本。PowerShell 中执行：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
python main.py validate-config
```

如果 PowerShell 禁止激活脚本，可以仅在当前用户范围执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

默认浏览器渠道是 `msedge`。若机器没有 Edge，请把 `config.yaml` 中的 `browser.channel` 改为 `chromium`；也可以用 `browser.executable_path` 指定浏览器可执行文件。

## 配置

业务任务放在 `config.yaml`，敏感 Token/Webhook 放在 `.env`。这两个实际文件都已被 `.gitignore` 排除。

每个任务支持：平台、演出名称和 ID、场次、日期时间、多票档、多区域、多看台、位置候选、排除词、区域正则、排号/座位号范围、区域优先级、数量、连座、单价/总价上限、独立查询间隔、随机抖动、自动锁单、通知、成功后停止、最大锁单次数和异常阈值。

匹配规则：

- `match_mode: exact` 为完全匹配，`contains` 为包含匹配；
- `target_*` 列表中任一候选命中即可；空列表表示不限制该项；
- `excluded_keywords` 会检查票档、区域、看台、排和座位的组合文本；
- `area_regexes` 中任一正则命中即可；
- `area_priorities` 数值越小优先级越高，同优先级选择实际应付总价更低的票；
- 最终比较金额是 `final_total`；没有该字段时使用票价总额加服务费、配送费和平台费。

### 真实登录态规则

`platforms` 中可以配置：

```yaml
platforms:
  motianlun:
    home_url: https://m.motianlun.cn/
    login_url: https://m.motianlun.cn/package-functional-pages/account-login/account-login
    auth_check_url: https://m.motianlun.cn/pages/mine/mine
    authenticated_selectors:
      - "text=我的订单"
    unauthenticated_selectors:
      - "text=点击登录"
```

选择器必须来自人工核验的当前官方页面。`authenticated_selectors` 为空或没有命中时，系统会保守判定为未登录。

### 微信通知

选择一种渠道：

```yaml
notification:
  enabled: true
  provider: pushplus  # wechat_work / serverchan / pushplus / console
```

然后在 `.env` 中填写对应的一个值：

```dotenv
WECHAT_WORK_WEBHOOK=
SERVERCHAN_SENDKEY=
PUSHPLUS_TOKEN=
```

日志和通知不会输出完整 Cookie、Token、Webhook、手机号或身份证号。

## 命令

```powershell
# 校验配置
python main.py validate-config

# 查看任务
python main.py list

# 运行所有启用任务或单个任务
python main.py run
python main.py run --task-id piaoniu_001

# 登录与登录状态
python main.py login --platform piaoniu
python main.py login --platform motianlun
python main.py login --platform all
python main.py login-status
python main.py login-status --platform piaoniu

# 通知、历史和动态启停
python main.py test-notification
python main.py history --task-id piaoniu_001
python main.py disable --task-id piaoniu_001
python main.py enable --task-id piaoniu_001

# 完整 Mock 演示
python main.py mock
```

首次运行真实平台时会打开可见浏览器。请人工完成手机号、密码、短信、扫码、验证码或设备验证。程序检测到已配置的登录成功标记后保存状态，并恢复该平台的任务；一个平台登录失败不会影响另一个平台。

按 `Ctrl+C` 退出。调度器会取消监控任务并关闭浏览器和通知客户端。

## Mock 演示

`python main.py mock` 在没有 `config.yaml` 时自动读取 `config.example.yaml`，并执行四轮快速演示：

1. 首次检查模拟未登录，然后模拟人工登录成功；
2. 前两轮返回价格或数量不符合要求的票；
3. 第三轮出现目标票并写入匹配记录；
4. 自动锁单任务会再次查询复核，模拟锁库存成功并停在“待人工支付”状态；
5. 非自动锁单任务在第四轮结束；
6. 所有价格、匹配、通知和锁单结果写入 SQLite。

Mock 会使用控制台通知，不发送真实微信消息，也不打开浏览器。

## 测试

测试完全不依赖真实票务网站：

```powershell
python -m pytest -q
```

覆盖配置解析和字段校验、价格解析、单价/总价、区域/排数/排除词/票档、重试、通知重试、登录并发锁、SQLite、锁单幂等和 Mock 端到端流程。

## 数据、日志与恢复

- 数据库：`data/ticket_monitor.db`；
- 登录 Storage State：`data/browser_states/<platform>_state.json`；
- 持久化浏览器资料：`data/browser_profiles/<platform>/`；
- 轮转日志：`logs/ticket_monitor.log`。

任务运行状态、连续异常和历史会写入数据库。正常重启会重新加载配置及数据库状态；已经完成的锁单任务不会在同一数据库中重复提交，手动执行 `enable` 可将它恢复为待运行状态。

不要分享 `data/browser_states`、`data/browser_profiles`、`.env`、数据库或日志。它们可能包含账号会话或业务信息。

## 异常、频率与人工介入

- 单任务异常在任务内捕获，不会带崩其他任务；平台初始化失败不会影响其他平台；
- 连续失败到达阈值后发送告警并指数降低请求频率；
- 查询间隔加随机抖动，避免任务同时高频访问；
- 发现限流、风控或验证时，真实适配器应抛出对应异常，系统暂停并通知用户；
- 登录等待超时不会结束整个程序，该平台任务保持等待并按间隔重新检查；
- 验证码、滑块、短信、扫码、实名、人工确认和付款始终由用户处理。

## 真实平台适配说明

真实适配器只操作官方网页 DOM，不调用猜测的内部接口。票牛适配器位于 `app/platforms/piaoniu.py`，摩天轮适配器位于 `app/platforms/motianlun.py`；公共的场次文本、订单金额、验证中断和敏感 URL 清理逻辑位于 `app/platforms/page_helpers.py`。

如果网站改版，请用 Playwright 重新核验未登录、已登录、详情页、场次、人数、票档、订单确认页和验证提示，再更新对应选择器。`lock_order` 必须先读取订单确认页最终金额；遇到验证码、短信、实名资料缺失、支付按钮或不确定页面状态时返回人工处理并停留在当前页面。

## 常见问题

**为什么登录状态一直显示未登录？**

登录态选择器没有配置或网站已改版。用可见浏览器人工确认稳定元素后更新 `platforms.<name>.authenticated_selectors`。仅有 Cookie 文件不会被视为登录有效。

**为什么真实平台返回页面结构变化？**

票务网站可能已经改版、票品已下架或当前页面触发了验证。请先在可见浏览器中人工确认页面状态，再按上一节重新核验选择器；程序不会绕过风控或猜测未知接口。

**为什么最终价格被拒绝？**

系统优先比较订单确认信息中的实际应付总额。服务费、配送费或平台费使总额超限时会停止锁单并通知。

**浏览器启动失败怎么办？**

确认已执行 `python -m playwright install chromium`。Edge 不可用时把 `browser.channel` 改为 `chromium`，或填写 `browser.executable_path`。

**网站改版后怎么办？**

更新真实适配器的页面解析和选择器并重新运行测试。不要通过绕过验证码、风控、权限或限流来修复。

## 安全与合规

本项目不绕过验证码、滑块、短信、扫码、风控、访问权限或限流，不伪造登录/支付结果，不批量注册账号，不自动付款。真实平台是否允许自动查询或锁单取决于其当前规则，项目不作保证。使用者必须自行阅读并遵守票务平台协议、当地法律及演出购票规则。
