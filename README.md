# Python 票务价格监控与锁单辅助系统

这是一个面向 Python 3.11+、Windows 10/11 的单用户本地桌面票务监控软件。它使用 Tkinter 管理票牛和摩天轮的多个任务，提前完成确定性配置和预检，发现完全一致的票品后进入官方订单流程，并且最多停在待支付状态。

> 重要：程序只辅助进入正常订单确认/锁库存流程，永远不会自动付款。验证码、短信、扫码、实名确认、风控和支付必须由用户人工完成。

## 当前实现状态

已完整可运行的部分：

- 配置校验、金额 `Decimal` 处理、价格/区域/排数/座位/连座匹配；
- `asyncio` 多任务调度、任务异常隔离、动态启停、随机抖动和降频；
- Tkinter 任务管理、平台状态、实时运行指标和日志界面，后台 asyncio 线程不会阻塞窗口；
- 每个平台固定一个适配器、一个持久化浏览器上下文和一个登录流程；同平台可运行多个任务；
- 平台级优先操作门闩：普通查询互斥，锁单等待时不再放入新查询；
- 每个平台独立的持久化浏览器目录、Storage State 和异步登录锁；
- SQLite 任务唯一配置源、运行快照、票务缓存、价格、匹配、锁单和通知记录；
- 锁单前按场次 ID、稳定票品 ID 和精确数量重新查询，原票品消失时禁止相似替换；
- 平台购票人实时读取、新增与任务多选；本地只保存平台 option ID 和显示标签，不保存实名原始资料；
- 企业微信机器人、Server酱、PushPlus 和控制台通知；
- 通知指数退避、后台发送、日志按天轮转和敏感字段脱敏；
- 不依赖真实网站的 Mock 登录、监控、匹配、锁单、通知完整流程；
- 全部要求的命令行入口和 pytest 测试。

真实平台现状：

- 票牛已使用真实公开页面完成右上角登录弹窗、登录态检测，以及场次、票档、数量、售价、区域、连座和拆单费解析；票牛 PC 账户页尚未核验到带稳定 option ID 的独立购票人入口，因此真实选人会暂停人工处理；
- 摩天轮已完成“我的”登录态、官方“我的观演人”列表与新增表单、详情页场次/精确人数、票品单价/区域/随机座位/保证连座解析；新增资料只在内存中填入官方页面，提交后立即清空；
- 票牛锁单以 `ticket_group_id` 为准；摩天轮页面未提供真实票品 ID 时，使用“场次 ID + 票档 + 价格 + 座位描述 + 卖家标签”的指纹；
- 两个平台锁单前都会重新打开页面复核票品和价格，再进入官方订单流程读取最终应付金额；金额超限立即停止；
- 摩天轮当前观演人管理页和订单页均只暴露姓名与平台脱敏证件，没有稳定 option ID。列表可查看、新增可提交，但真实自动选人按规则暂停人工处理，不按姓名猜测；
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
│   ├── gui/
│   │   ├── application.py / controller.py / async_runner.py / audience_panel.py
│   │   ├── task_list.py / task_editor.py / platform_panel.py / log_panel.py
│   ├── storage/
│   │   ├── task_store.py / ticket_cache.py / cache_cleaner.py
│   ├── platforms/
│   │   ├── base.py / piaoniu.py / motianlun.py / mock.py
│   └── services/
│       ├── session_service.py / login_service.py
│       ├── ticket_matcher.py / monitor_service.py
│       ├── order_service.py / notification_service.py
├── data/
│   ├── browser_states/
│   ├── browser_profiles/
│   ├── cache/
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
python main.py gui
```

如果 PowerShell 禁止激活脚本，可以仅在当前用户范围执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

默认浏览器渠道是 `msedge`。若机器没有 Edge，请把 `config.yaml` 中的 `browser.channel` 改为 `chromium`；也可以用 `browser.executable_path` 指定浏览器可执行文件。

## 配置

全局浏览器、通知和平台页面规则放在 `config.yaml`，敏感 Token/Webhook 放在 `.env`。实际任务以 SQLite `monitor_tasks.config_json` 为唯一权威来源，GUI、`create-task`、启用、暂停和删除操作都直接更新 SQLite。

为了兼容旧配置，数据库第一次初始化时会将 YAML 中的任务迁移一次，并写入 `tasks_initialized` 标记。以后即使 SQLite 任务为空也不会反复从 YAML 导入，因此执行“清理缓存”后任务不会自动复活。

不再要求编辑 `purchase_profiles.yaml`。在 GUI 的“购票人管理”页实时读取或新增平台购票人，再在任务编辑器中选择。姓名、证件号和完整手机号只存在于新增窗口与本次 Playwright 提交流程的内存中，不写入 SQLite、YAML、JSON、缓存或日志。任务仅保存 `platform_audience_ids` 和不含证件信息的 `platform_audience_labels`；锁单前会重新向平台验证 ID。

旧 `purchase_profile_id` 只为迁移保留。仅配置旧档案的任务会被禁用并标记为 `audience_selection_required`，必须重新选择平台购票人。

每个任务支持：任务名称、平台、演出名称和 ID、场次、日期时间、多票档、多区域、多看台、位置候选、排除词、区域正则、排号/座位号范围、区域优先级、数量、连座、单价/总价上限、独立查询间隔、随机抖动、自动锁单、通知、成功后停止和异常阈值。`attempt_count` 仅用于审计，不再因为达到次数上限永久封禁临时错误。

严格自动锁单任务还应由 `discover`/`create-task` 写入 `target_session_id`、`target_listing_id`、票牛的 `target_ticket_group_id`，并从任务编辑器保存平台返回的 `platform_audience_ids`，不要手工猜 ID。

```yaml
strict_lock:
  strict_quantity: true
  strict_session_id: true
  strict_listing_id: true
  strict_audience_count: true
  reject_unknown_final_price: true
  reject_listing_replacement: true
  max_price_slippage: 0
  stop_before_payment: true
  stage_timeout_seconds: 30
```

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
# 桌面软件
python main.py gui

# 使用独立 Mock 数据库运行多任务桌面演示
python main.py gui --mock

# 校验配置
python main.py validate-config

# 只读发现真实 ID、交互创建任务、执行启动前预检
python main.py discover --platform piaoniu --url <event_url> --quantity 2
python main.py discover --platform motianlun --url <event_url> --quantity 2
python main.py create-task
python main.py preflight --task-id piaoniu_001

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

## 桌面界面

主界面采用“本地值守台”布局：顶部持续显示票牛、摩天轮登录状态和运行任务数量；标签页包含任务管理、购票人管理、票牛、摩天轮和运行日志。

任务管理页支持：

- 新建、编辑、复制和删除任务；
- 启用、暂停、停止和立即查询；
- 输入官方演出链接后只读识别演出、场次、票档、区域和稳定票品 ID；同条件多票品通过含价格、数量和 ID 的下拉项明确选择；
- 配置精确数量、连座、价格上限、查询间隔、通知和自动锁单；
- 按所选平台实时刷新可用购票人，支持多选，并强制购票人数与购买数量一致；
- 实时查看查询次数、最近价格、最低价格、可购数量、匹配原因、异常和锁单结果。

“购票人管理”页可以切换票牛/摩天轮、刷新平台列表、打开官方管理页和新增购票人。新增前的确认框会完整显示本次输入供核对；无论成功、失败还是关闭窗口，输入框和内存请求都会清空。平台若要求验证码或额外身份确认，浏览器会留在前台等待人工完成。

票牛与摩天轮页面没有账号列表或账号切换入口。每个平台的所有任务共享 `PlatformRegistry` 中唯一的浏览器会话。登录、查询和清理都在后台 asyncio 线程执行，Tk 主线程只通过线程安全队列刷新界面。

### 清理缓存

顶部“清理缓存”按钮会先显示不可恢复的二次确认。确认后依次停止任务、取消查询/锁单协程、关闭两个浏览器和 Playwright，然后清理：

- `data/browser_states/` 和 `data/browser_profiles/`；
- SQLite 中的全部任务、运行快照、票价、匹配、锁单、通知和票务缓存；
- `data/cache/`；
- 任务中的购票人引用 ID、项目内的 `.env`、旧购票档案私有文件和当前运行日志。

不会删除票牛或摩天轮账号中已经保存的购票人，也不会删除源代码、虚拟环境、依赖或 Playwright 浏览器程序。平台购票人如需删除，必须前往官方管理页面操作。取消确认不会执行任何删除。

## Mock 演示

`python main.py mock` 在没有 `config.yaml` 时自动读取 `config.example.yaml`，并执行四轮快速演示：

1. 使用 Mock 平台内存中的远程购票人完成登录、通知、场次 ID、票品 ID、精确数量和重复订单预检；
2. 前两轮返回价格或数量不符合要求的票；
3. 第三轮出现目标票并写入匹配记录；
4. 自动锁单任务会再次按稳定 ID 查询复核，依次记录资料选择、金额确认和提交阶段，最终停在 `payment_pending`；
5. 非自动锁单任务在第四轮结束；
6. 所有价格、匹配、通知和锁单结果写入 SQLite。

Mock 会使用控制台通知，不发送真实微信消息，也不打开浏览器。

`python main.py gui --mock` 使用 `data/ticket_monitor_gui_mock.db`，可在桌面界面中同时启停票牛和摩天轮示例任务，不会污染正式数据库。

## 测试

测试完全不依赖真实票务网站：

```powershell
python -m pytest -q
```

覆盖配置迁移、GUI 初始化、平台购票人读取/新增/失效、敏感请求不可序列化、SQLite 只保存 option ID、任务多选和人数校验、实时预检、精确选人、单平台唯一会话、多任务运行、缓存清理但不删除远程购票人，以及 Mock 到待支付端到端流程。

## 数据、日志与恢复

- 数据库：`data/ticket_monitor.db`；
- 登录 Storage State：`data/browser_states/<platform>_state.json`；
- 持久化浏览器资料：`data/browser_profiles/<platform>/`；
- 任务配置和票务缓存：`data/ticket_monitor.db` 中的 `monitor_tasks` 与 `ticket_cache`；
- 轮转日志：`logs/ticket_monitor.log`。

任务运行状态、连续异常、锁单阶段和历史会写入数据库。状态机为：`PREFLIGHT → WATCHING → MATCHED → REVALIDATING → SELECTING_QUANTITY → SELECTING_AUDIENCE → SELECTING_CONTACT → VERIFYING_FINAL_PRICE → READY_TO_SUBMIT → SUBMITTING → PAYMENT_PENDING`。

`success`、`payment_pending`、`order_exists` 永久阻止相同幂等键再次提交；`timeout`、`not_logged_in`、`out_of_stock`、`price_changed`、`page_changed`、`captcha_required`、`manual_profile_missing` 在冷却后允许重试。幂等键包含账号别名、平台、演出 ID、场次 ID、票品 ID 和数量。

不要分享 `data/browser_states`、`data/browser_profiles`、`.env`、数据库或日志。它们可能包含账号会话或业务信息。

业务日志会完整记录任务 ID/名称、演出链接和 ID、场次和 ID、票档和 ID、区域、票品 ID、数量、指定购票人显示名称、连座、价格上限、查询结果、不匹配原因、锁单阶段、订单页面与订单号。密码、完整手机号、完整 Cookie、Authorization、Token、Webhook、验证码、证件号码和支付凭证禁止写入。

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

### 仍需真实订单页人工验证的选择器

以下 DOM 尚未在当前真实页面条件下得到稳定确认，代码不会虚构或启用它们：

- 票牛订单确认页：实名观演人选项 ID/选中态、联系人选项、收货地址选项、购票须知复选框、独立“提交订单”按钮及待支付成功标记；
- 摩天轮订单确认页已确认观演人弹层只有姓名和脱敏证件，没有稳定 option ID；仍待平台提供稳定 ID、联系人/地址选项和区别于“立即支付”的独立提交按钮；
- 两个平台订单列表：用于跨数据库确认“相同待支付订单”的稳定演出、场次、票品和数量字段。

在这些选择器完成真实验证前，相关流程会返回 `manual_profile_missing` 或人工处理状态，不会绕过验证，也不会点击“立即支付”“确认支付”“去支付”或“付款”。SQLite 幂等仍会提前阻止已知重复订单。

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
