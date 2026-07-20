
# Python 票务价格监控与锁单辅助系统开发需求

请检查当前文件夹中的已有文件，并在**不破坏原有文件**的前提下，使用 Python 开发一个完整、可运行、可配置的票务价格监控与锁单辅助系统。

系统需要支持以下两个票务平台：

1. 票牛：https://www.piaoniu.com/
2. 摩天轮票务：https://m.motianlun.cn/

系统需要支持同时监控多个演出、多个场次、多个票档和多个座位区域。当出现满足价格、区域和数量条件的票时，尝试进入平台正常的锁单流程，并发送微信通知。

请直接在当前文件夹中创建完整项目，不要只返回零散的代码片段。

---

# 一、核心目标

系统需要实现以下完整流程：

```text
启动程序
    ↓
加载配置文件
    ↓
检查各平台登录状态
    ↓
未登录或登录失效
    ↓
自动打开对应平台的官方登录页面
    ↓
用户在可见浏览器中手动完成登录
    ↓
程序自动检测登录成功并保存登录状态
    ↓
启动多个票务监控任务
    ↓
定时查询票务价格、区域和库存
    ↓
发现满足条件的票
    ↓
再次校验票务信息
    ↓
尝试锁单
    ↓
发送微信通知
    ↓
保存监控、价格、通知和锁单记录
```

默认情况下，程序只允许执行到以下阶段：

* 加入购物车；
* 提交订单；
* 锁定库存；
* 进入待支付页面。

**不要自动执行付款。**

涉及验证码、滑块、短信验证码、扫码登录、支付确认或平台风控验证时，必须暂停自动操作，并让用户手动完成。

---

# 二、平台架构要求

票牛和摩天轮应分别实现独立的平台适配器，同时抽象统一的平台接口，方便未来扩展其他票务平台。

建议定义统一接口：

```python
from abc import ABC, abstractmethod
from typing import Sequence


class TicketPlatform(ABC):

    @abstractmethod
    async def initialize(self) -> None:
        """初始化浏览器、HTTP客户端和平台资源。"""
        pass

    @abstractmethod
    async def check_login_status(self) -> bool:
        """检查当前登录状态是否有效。"""
        pass

    @abstractmethod
    async def open_login_page(self) -> None:
        """打开平台官方登录页面，等待用户手动登录。"""
        pass

    @abstractmethod
    async def search_event(self, task):
        """根据任务配置搜索对应演出或赛事。"""
        pass

    @abstractmethod
    async def query_tickets(self, task) -> Sequence:
        """查询当前可购买的票务信息。"""
        pass

    @abstractmethod
    async def match_ticket(self, task, tickets):
        """筛选满足场次、价格、区域和数量要求的票。"""
        pass

    @abstractmethod
    async def lock_order(self, task, ticket):
        """尝试进入平台正常锁单或订单确认流程。"""
        pass

    @abstractmethod
    async def close(self) -> None:
        """释放浏览器和网络资源。"""
        pass
```

平台实现文件建议包括：

```text
app/platforms/
├── base.py
├── piaoniu.py
├── motianlun.py
└── mock.py
```

其中：

* `base.py`：定义统一接口、基础数据结构和公共逻辑；
* `piaoniu.py`：实现票牛平台适配器；
* `motianlun.py`：实现摩天轮平台适配器；
* `mock.py`：模拟票务平台，供无真实网站环境时测试。

如果无法确认真实网站的接口、参数或页面结构，**不要虚构接口**。应将相关代码封装为清晰的待实现适配器，并使用 Mock 数据保证系统整体能够运行。

---

# 三、票务监控功能

## 3.1 监控任务配置

用户需要能够为每个任务配置以下信息：

* 唯一任务编号 `task_id`；
* 是否启用任务；
* 票务平台；
* 演出或赛事名称；
* 演出详情页地址；
* 商品编号或演出编号；
* 目标场次；
* 演出日期；
* 演出时间；
* 目标票档；
* 目标区域；
* 目标看台；
* 目标排数；
* 目标座位位置；
* 是否接受相邻座位；
* 最高可接受单价；
* 最高可接受总价；
* 购买数量；
* 监控刷新间隔；
* 随机请求间隔范围；
* 是否启用锁单；
* 是否启用微信通知；
* 锁单成功后是否停止任务；
* 同一任务最大锁单次数；
* 连续异常告警阈值。

程序需要判断票务信息是否同时满足：

1. 平台正确；
2. 演出正确；
3. 场次正确；
4. 演出日期和时间正确；
5. 票档正确；
6. 区域、看台、排数或座位位置符合要求；
7. 单价低于或等于配置价格；
8. 总价低于或等于配置价格；
9. 可购买数量满足要求；
10. 连座要求满足配置。

## 3.2 价格判断

票价解析需要考虑：

* 整数价格；
* 小数价格；
* 带人民币符号的价格；
* 带逗号的价格；
* “起”字价格；
* 原价和折后价；
* 单张价格和总价格；
* 服务费；
* 配送费；
* 平台手续费；
* 价格发生变化；
* 页面展示价格与下单页价格不一致。

匹配时应优先使用最终订单确认页显示的实际应付金额。

如果最终价格超过用户设置的阈值，应立即停止锁单，并发送价格变化通知。

## 3.3 区域和位置匹配

支持以下匹配方式：

* 完全匹配；
* 包含匹配；
* 多个候选区域；
* 多个候选票档；
* 多个候选看台；
* 排除指定区域；
* 排除视线遮挡区域；
* 排除站票；
* 排除不连座；
* 排数范围；
* 座位号范围；
* 正则表达式匹配；
* 用户自定义优先级。

例如：

```yaml
target_areas:
  - 内场A区
  - 看台A区
  - 一层正面看台

excluded_keywords:
  - 视线不良
  - 遮挡
  - 站票
  - 随机座
```

---

# 四、多任务并发监控

系统必须支持同时运行多个监控任务，例如：

* 同时监控多个演出；
* 同一演出监控多个场次；
* 同一场次监控多个区域；
* 同一场次监控多个票档；
* 同时监控票牛和摩天轮；
* 不同任务使用不同价格阈值；
* 不同任务使用不同监控频率。

建议使用以下方式之一实现：

* `asyncio`；
* 异步任务队列；
* 线程池；
* APScheduler；
* 其他可靠的任务调度方案。

要求：

1. 每个任务独立运行；
2. 单个任务异常不能导致整个程序退出；
3. 单个平台异常不能影响另一个平台；
4. 支持动态启用和禁用任务；
5. 支持根据 `task_id` 单独运行任务；
6. 支持优雅停止；
7. 支持程序异常重启后恢复任务；
8. 同一个平台的浏览器资源应尽可能复用；
9. 不同任务不得无限制创建浏览器窗口；
10. 查询任务和锁单任务应合理隔离。

---

# 五、登录与会话管理

## 5.1 启动时检查登录状态

程序启动后，应先检查各票务平台的登录状态。

每个平台都需要识别以下情况：

* 从未登录；
* Cookie 不存在；
* Storage State 文件不存在；
* Cookie 已过期；
* Session 已失效；
* 页面跳转到登录页；
* 平台提示需要重新登录；
* 账号被退出；
* 登录状态异常；
* 用户身份信息接口返回未登录；
* 查询或锁单接口返回未授权。

登录状态验证不能只判断 Cookie 文件是否存在，还需要通过访问个人中心、用户信息接口或其他可靠方式确认登录状态是否真实有效。

## 5.2 未登录时自动打开登录页面

当检测到未登录或登录状态失效时，程序应自动执行以下流程：

1. 暂停该平台相关的查询和锁单任务；
2. 保留当前任务状态；
3. 使用 Playwright 启动一个可见的浏览器窗口；
4. 默认设置 `headless=False`；
5. 自动跳转到该平台的官方登录页面；
6. 在命令行提示用户完成登录；
7. 等待用户在浏览器中手动登录；
8. 持续检测当前页面和账号登录状态；
9. 登录成功后保存登录状态；
10. 再次校验登录状态；
11. 自动恢复该平台相关的监控任务。

用户可能需要手动完成：

* 手机号登录；
* 密码登录；
* 短信验证码登录；
* 微信扫码登录；
* 支付宝扫码登录；
* 图片验证码；
* 滑块验证码；
* 设备验证；
* 人机验证；
* 平台要求的其他人工验证。

程序不得因为未登录而直接退出整个系统。

## 5.3 登录提示

等待用户登录时，命令行应输出清晰提示：

```text
检测到票牛账号尚未登录或登录状态已经失效。

程序已自动打开票牛官方登录页面。
请在浏览器中手动完成登录、短信验证、扫码或验证码操作。
程序会自动检测登录结果，请不要直接关闭浏览器窗口。
```

登录成功后输出：

```text
票牛账号登录成功。
登录状态已经保存。
正在恢复票牛平台相关的监控任务。
```

## 5.4 登录等待超时

登录等待时间应支持配置，例如默认等待 10 分钟：

```yaml
login:
  timeout_seconds: 600
  retry_interval_seconds: 300
  close_browser_after_login: true
```

登录等待超时后：

* 不关闭整个程序；
* 不影响其他已登录平台；
* 将该平台任务标记为“等待登录”；
* 发送微信通知；
* 按配置间隔重新检查或重新打开登录页面；
* 保留任务配置和运行状态；
* 不进行查询或锁单操作。

## 5.5 多平台独立登录

票牛和摩天轮的登录状态必须独立管理：

* 票牛未登录时，只暂停票牛任务；
* 摩天轮未登录时，只暂停摩天轮任务；
* 一个平台注册状态不得覆盖另一个平台；
* 每个平台使用独立的 Storage State；
* 每个平台使用独立的浏览器配置目录；
* 每个平台使用独立的登录锁。

建议目录：

```text
data/
├── browser_states/
│   ├── piaoniu_state.json
│   └── motianlun_state.json
└── browser_profiles/
    ├── piaoniu/
    └── motianlun/
```

## 5.6 登录状态保存与复用

程序重启后，应优先加载已有登录状态。

流程如下：

```text
启动程序
    ↓
加载平台登录状态
    ↓
验证登录状态
    ↓
登录有效：启动监控任务
    ↓
登录无效：打开登录页面
    ↓
用户手动登录
    ↓
保存登录状态
    ↓
重新验证
    ↓
恢复监控任务
```

可以保存：

* Cookie；
* LocalStorage；
* SessionStorage；
* Playwright Storage State；
* 持久化浏览器上下文。

登录状态文件必须加入 `.gitignore`，不得上传到 Git 仓库。

## 5.7 并发登录控制

当多个任务同时检测到同一个平台未登录时，只允许打开一个登录窗口。

其他任务应等待同一个登录流程完成，不得反复打开多个登录窗口。

建议使用异步锁：

```python
import asyncio


login_locks = {
    "piaoniu": asyncio.Lock(),
    "motianlun": asyncio.Lock(),
}
```

登录成功后，应统一唤醒并恢复该平台全部监控任务。

## 5.8 运行期间登录失效

程序运行期间发现登录失效时，应自动：

1. 暂停该平台新的查询请求；
2. 暂停该平台新的锁单请求；
3. 保存任务当前状态；
4. 自动打开官方登录页面；
5. 发送微信提醒；
6. 等待用户手动完成登录；
7. 保存新的登录状态；
8. 重新校验；
9. 自动恢复任务。

微信通知示例：

```text
【登录状态失效】

平台：票牛
状态：账号登录状态已失效
处理：程序已自动打开官方登录页面
操作：请在运行程序的设备上完成登录
时间：2026-07-20 11:30:00
```

## 5.9 浏览器配置

Playwright 需要支持：

* 默认使用可见浏览器；
* 支持 Chromium；
* 支持 Google Chrome；
* 支持 Microsoft Edge；
* 支持持久化用户目录；
* 支持保存和加载 Storage State；
* 支持页面加载超时；
* 支持浏览器崩溃重启；
* 支持配置登录后是否关闭窗口；
* 支持 Windows 环境；
* 支持手动指定浏览器路径；
* 浏览器异常退出后重新创建上下文。

普通浏览器上下文示例：

```python
browser = await playwright.chromium.launch(
    headless=False
)

context = await browser.new_context(
    storage_state=state_file if state_file.exists() else None
)
```

持久化浏览器上下文示例：

```python
context = await playwright.chromium.launch_persistent_context(
    user_data_dir="data/browser_profiles/piaoniu",
    headless=False
)
```

不得要求用户手动复制 Cookie。

---

# 六、锁单功能

## 6.1 锁单前校验

发现满足条件的票后，不要直接根据监控列表中的旧数据下单。

必须再次校验：

* 演出名称；
* 商品编号；
* 场次；
* 日期；
* 时间；
* 票档；
* 区域；
* 看台；
* 排数；
* 座位；
* 单价；
* 总价；
* 服务费；
* 库存；
* 购买数量；
* 连座情况；
* 当前账号登录状态。

只有所有条件依旧满足时，才能尝试锁单。

## 6.2 锁单流程

在网站允许且用户已经授权登录的前提下，锁单流程可以包括：

1. 进入商品详情页；
2. 选择场次；
3. 选择票档；
4. 选择区域或座位；
5. 设置购买数量；
6. 点击购买；
7. 进入订单确认页；
8. 获取最终价格；
9. 再次判断价格阈值；
10. 提交订单或锁定库存；
11. 停留在待支付页面；
12. 发送通知。

默认不要自动支付。

如果需要短信验证、验证码、实名信息确认或人工确认，应暂停并通知用户处理。

## 6.3 防止重复锁单

锁单操作必须具备幂等控制，避免重复生成订单。

建议使用唯一幂等键：

```text
平台 + 账号 + 演出ID + 场次ID + 票档 + 区域 + 任务ID
```

锁单前检查：

* 是否已经存在锁单成功记录；
* 是否已经存在待支付订单；
* 是否正在执行相同锁单任务；
* 是否超过最大锁单次数；
* 是否处于锁单冷却期。

同一个任务在同一时间只允许执行一次锁单。

可以使用：

* `asyncio.Lock`；
* SQLite 唯一索引；
* 锁单状态表；
* 本地文件锁；
* 内存锁与数据库状态组合。

## 6.4 锁单结果

锁单结果至少包括：

* 成功；
* 未登录；
* 价格超过阈值；
* 库存不足；
* 区域不匹配；
* 场次不匹配；
* 数量不足；
* 不支持连座；
* 需要验证码；
* 需要短信验证；
* 需要人工确认；
* 订单已存在；
* 页面结构变化；
* 请求超时；
* 平台拒绝请求；
* 未知错误。

每次锁单结果都需要保存到数据库并发送通知。

---

# 七、微信通知

系统需要实现统一通知接口，并支持以下一种或多种通知渠道：

* 企业微信机器人；
* Server酱；
* PushPlus；
* 其他通过 HTTP Webhook 提供的通知服务。

敏感的 Token 和 Webhook 地址必须放入 `.env`，不得直接写在代码中。

统一通知接口示例：

```python
class Notifier:

    async def send(self, title: str, content: str) -> bool:
        """发送通知并返回是否成功。"""
        ...
```

## 7.1 发现目标票通知

通知内容至少包含：

* 平台名称；
* 任务编号；
* 演出名称；
* 场次；
* 演出时间；
* 票档；
* 区域；
* 看台；
* 排数；
* 座位信息；
* 当前单价；
* 当前总价；
* 目标价格；
* 购买数量；
* 发现时间；
* 商品页面地址；
* 是否开始锁单。

示例：

```text
【发现符合条件的票】

平台：票牛
任务：piaoniu_001
演出：示例演唱会
场次：2026-08-01 19:30
票档：1280元
区域：内场A区
位置：第5排
数量：2张
当前单价：1100元
当前总价：2200元
目标最高单价：1200元
状态：准备尝试锁单
发现时间：2026-07-20 11:30:00
```

## 7.2 锁单成功通知

通知内容至少包含：

* 平台；
* 订单状态；
* 订单号；
* 最终价格；
* 票数；
* 锁单时间；
* 支付截止时间；
* 订单页面链接；
* 提醒用户手动付款。

不得在通知中发送完整 Cookie、Token、密码或身份证信息。

## 7.3 锁单失败通知

通知内容至少包含：

* 平台；
* 任务编号；
* 演出；
* 失败阶段；
* 失败原因；
* 是否会继续监控；
* 下一次重试时间；
* 错误时间。

## 7.4 通知重试

通知失败时应：

* 自动重试；
* 使用指数退避；
* 设置最大重试次数；
* 保存失败记录；
* 不阻塞票务监控主流程。

---

# 八、配置管理

系统需要使用：

* `.env`：保存敏感信息；
* `config.yaml`：保存实际业务配置；
* `.env.example`：提供环境变量示例；
* `config.example.yaml`：提供监控配置示例。

不得将以下内容直接写在源代码中：

* 用户名；
* 密码；
* 手机号；
* Cookie；
* Token；
* Webhook；
* 身份证信息；
* 支付信息；
* 登录状态。

## 8.1 `.env.example` 示例

```env
# 通知配置
WECHAT_WORK_WEBHOOK=
SERVERCHAN_SENDKEY=
PUSHPLUS_TOKEN=

# 浏览器配置
BROWSER_CHANNEL=msedge
BROWSER_EXECUTABLE_PATH=

# 数据库配置
DATABASE_URL=sqlite:///data/ticket_monitor.db
```

## 8.2 `config.example.yaml` 示例

```yaml
application:
  log_level: INFO
  database_path: data/ticket_monitor.db
  timezone: Asia/Shanghai
  mock_mode: false

browser:
  headless: false
  channel: msedge
  page_timeout_seconds: 30
  close_after_login: true

login:
  timeout_seconds: 600
  retry_interval_seconds: 300
  auto_open_login_page: true

notification:
  enabled: true
  provider: pushplus
  max_retries: 3
  retry_interval_seconds: 5

monitor:
  default_interval_seconds: 10
  random_delay_min_seconds: 1
  random_delay_max_seconds: 3
  max_consecutive_errors: 5

tasks:
  - task_id: piaoniu_001
    enabled: true
    platform: piaoniu
    event_name: 示例演唱会
    event_url: https://www.piaoniu.com/example
    event_id: ""
    target_sessions:
      - "2026-08-01 19:30"
    target_ticket_levels:
      - "1280"
      - "1580"
    target_areas:
      - 内场A区
      - 内场B区
    excluded_keywords:
      - 视线不良
      - 遮挡
      - 站票
      - 随机座
    row_min: 1
    row_max: 10
    quantity: 2
    adjacent_seats_required: true
    max_unit_price: 1200
    max_total_price: 2400
    interval_seconds: 10
    auto_lock: true
    notify: true
    stop_after_lock_success: true
    max_lock_attempts: 1

  - task_id: piaoniu_002
    enabled: true
    platform: piaoniu
    event_name: 示例演唱会第二场
    event_url: https://www.piaoniu.com/example2
    target_sessions:
      - "2026-08-02 19:30"
    target_ticket_levels:
      - "880"
    target_areas:
      - 一层正面看台
    quantity: 1
    adjacent_seats_required: false
    max_unit_price: 800
    max_total_price: 800
    interval_seconds: 15
    auto_lock: false
    notify: true

  - task_id: motianlun_001
    enabled: true
    platform: motianlun
    event_name: 示例音乐节
    event_url: https://m.motianlun.cn/example
    event_id: ""
    target_sessions:
      - "2026-09-01"
    target_ticket_levels:
      - "VIP"
      - "普通票"
    target_areas:
      - VIP区
    quantity: 2
    adjacent_seats_required: false
    max_unit_price: 600
    max_total_price: 1200
    interval_seconds: 12
    auto_lock: true
    notify: true
    stop_after_lock_success: true
    max_lock_attempts: 1
```

配置解析时需要进行完整校验，并对无效字段给出清晰错误提示。

---

# 九、异常处理与重试机制

程序需要处理以下异常：

* 网络连接失败；
* DNS 异常；
* 请求超时；
* 页面加载超时；
* HTTP 状态码异常；
* 浏览器启动失败；
* 浏览器意外退出；
* 页面结构变化；
* 元素不存在；
* 接口字段缺失；
* JSON 解析失败；
* 登录状态失效；
* Cookie 过期；
* 商品下架；
* 演出不存在；
* 场次取消；
* 票价变化；
* 库存不足；
* 区域不匹配；
* 锁单失败；
* 通知失败；
* 数据库写入失败；
* 单个任务崩溃。

重试策略要求：

* 使用指数退避；
* 设置最大重试次数；
* 增加适量随机抖动；
* 区分可重试异常和不可重试异常；
* 连续失败达到阈值后降低监控频率；
* 连续失败达到阈值后发送告警；
* 不允许无限高频重试；
* 一个任务失败不能影响其他任务。

---

# 十、请求频率和平台合规

程序应优先使用平台公开、稳定且符合规则的接口。

如果必须通过页面交互实现，应使用 Playwright 操作平台正常页面流程。

必须遵守以下约束：

* 不绕过验证码；
* 不破解滑块验证；
* 不自动读取短信验证码；
* 不绕过扫码确认；
* 不绕过平台风控；
* 不绕过访问权限；
* 不伪造登录状态；
* 不伪造支付结果；
* 不攻击或干扰网站；
* 不使用未经授权的内部接口；
* 不短时间发送大量请求；
* 不绕过平台限流；
* 不自动批量创建账号；
* 不自动执行付款。

程序需要支持合理请求间隔和随机抖动，避免多个任务在完全相同的时间发起请求。

如果平台返回限流、风控或验证提示，应立即暂停该平台的自动请求，并通知用户人工处理。

---

# 十一、日志系统

使用 Python 标准库 `logging` 或兼容的日志框架。

日志需要支持：

* 控制台输出；
* 文件输出；
* 按日期或文件大小轮转；
* 不同日志级别；
* 异常堆栈；
* 任务编号；
* 平台名称；
* 时间戳；
* 敏感数据脱敏。

日志至少记录：

* 程序启动和停止；
* 配置加载结果；
* 任务启动和停止；
* 登录状态检查；
* 登录页面打开；
* 用户登录成功；
* 查询开始和结束；
* 查询结果数量；
* 匹配到的票；
* 价格变化；
* 库存变化；
* 锁单开始；
* 锁单结果；
* 通知结果；
* 重试次数；
* 连续错误次数；
* 完整异常堆栈。

不得在日志中输出：

* 完整密码；
* 完整 Cookie；
* 完整 Token；
* 完整手机号；
* 完整身份证号；
* 支付信息；
* 完整 Webhook 地址。

---

# 十二、数据库和历史记录

使用 SQLite 保存运行数据。

至少创建以下数据表：

## 12.1 监控任务表

保存：

* `task_id`；
* 平台；
* 演出；
* 场次；
* 价格阈值；
* 任务状态；
* 创建时间；
* 更新时间；
* 最近运行时间；
* 连续失败次数。

## 12.2 历史价格表

保存：

* 平台；
* 任务编号；
* 演出；
* 场次；
* 票档；
* 区域；
* 当前价格；
* 总价；
* 数量；
* 查询时间。

## 12.3 匹配记录表

保存：

* 任务编号；
* 匹配票务信息；
* 匹配条件；
* 匹配时间；
* 是否触发锁单。

## 12.4 锁单记录表

保存：

* 幂等键；
* 任务编号；
* 平台；
* 商品编号；
* 场次；
* 区域；
* 价格；
* 数量；
* 锁单状态；
* 订单号；
* 错误原因；
* 创建时间；
* 更新时间。

## 12.5 通知记录表

保存：

* 通知类型；
* 通知渠道；
* 通知内容摘要；
* 发送状态；
* 重试次数；
* 发送时间；
* 错误原因。

数据库操作应进行异常处理，避免数据库写入失败导致监控任务退出。

---

# 十三、命令行功能

程序需要提供以下命令：

```bash
python main.py run
```

运行所有启用的监控任务。

```bash
python main.py run --task-id piaoniu_001
```

运行指定任务。

```bash
python main.py list
```

列出所有任务及其状态。

```bash
python main.py validate-config
```

校验配置文件。

```bash
python main.py test-notification
```

测试微信通知。

```bash
python main.py login --platform piaoniu
```

自动打开票牛登录页面，等待用户登录并保存状态。

```bash
python main.py login --platform motianlun
```

自动打开摩天轮登录页面，等待用户登录并保存状态。

```bash
python main.py login --platform all
```

依次完成所有平台登录。

```bash
python main.py login-status
```

查看全部平台登录状态。

```bash
python main.py login-status --platform piaoniu
```

查看指定平台登录状态。

```bash
python main.py history --task-id piaoniu_001
```

查看任务历史价格和匹配记录。

```bash
python main.py mock
```

使用 Mock 平台运行完整演示流程。

---

# 十四、推荐项目结构

请尽量采用清晰、可维护的目录结构：

```text
project/
├── main.py
├── requirements.txt
├── README.md
├── .gitignore
├── .env.example
├── config.example.yaml
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── models.py
│   ├── database.py
│   ├── scheduler.py
│   ├── notifier.py
│   ├── logger.py
│   ├── exceptions.py
│   ├── retry.py
│   ├── cli.py
│   ├── platforms/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── piaoniu.py
│   │   ├── motianlun.py
│   │   └── mock.py
│   └── services/
│       ├── __init__.py
│       ├── login_service.py
│       ├── session_service.py
│       ├── monitor_service.py
│       ├── ticket_matcher.py
│       ├── order_service.py
│       └── notification_service.py
├── data/
│   ├── browser_states/
│   ├── browser_profiles/
│   └── ticket_monitor.db
├── logs/
└── tests/
    ├── test_config.py
    ├── test_ticket_matcher.py
    ├── test_scheduler.py
    ├── test_retry.py
    ├── test_notifier.py
    ├── test_login_service.py
    └── test_order_idempotency.py
```

---

# 十五、数据模型要求

建议使用 `dataclasses`、Pydantic 或其他类型安全的数据模型。

至少定义：

* `MonitorTask`；
* `TicketInfo`；
* `SessionInfo`；
* `SeatInfo`；
* `MatchResult`；
* `LoginStatus`；
* `LockOrderRequest`；
* `LockOrderResult`；
* `NotificationMessage`；
* `TaskRuntimeState`。

示例：

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class TicketInfo:
    platform: str
    event_id: str
    event_name: str
    session_id: str
    session_name: str
    ticket_level: str
    area: Optional[str]
    row: Optional[str]
    seat: Optional[str]
    unit_price: Decimal
    total_price: Decimal
    available_quantity: int
    detail_url: str
```

金额必须使用 `Decimal`，不要使用浮点数直接比较价格。

---

# 十六、测试要求

需要使用 `pytest` 为以下模块编写测试：

* 配置文件解析；
* 配置字段校验；
* 价格解析；
* 单价判断；
* 总价判断；
* 区域匹配；
* 排数匹配；
* 排除关键词；
* 多票档匹配；
* 多任务调度；
* 单任务异常隔离；
* 重试机制；
* 通知模块；
* 登录状态判断；
* 登录锁并发控制；
* 锁单幂等控制；
* 数据库写入；
* Mock 平台完整流程。

测试不能依赖真实票务网站才能运行。

---

# 十七、Mock 演示模式

由于真实网站接口和页面结构可能无法直接确认，必须提供一个完整的 Mock 平台。

Mock 模式需要模拟以下流程：

1. 程序启动；
2. 模拟未登录；
3. 模拟用户登录成功；
4. 启动多个监控任务；
5. 返回多组不同价格、区域和票档；
6. 前几次查询不满足条件；
7. 后续查询出现满足条件的票；
8. 触发匹配；
9. 再次校验价格；
10. 模拟锁单成功；
11. 发送模拟通知；
12. 将记录保存到 SQLite；
13. 根据配置停止任务或继续监控。

Mock 模式应能通过以下命令直接运行：

```bash
python main.py mock
```

---

# 十八、Windows 运行支持

需要确保项目能够在 Windows 10 或 Windows 11 上运行。

README 中需要提供完整命令：

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

```bash
playwright install chromium
```

```bash
copy .env.example .env
```

```bash
copy config.example.yaml config.yaml
```

```bash
python main.py validate-config
```

```bash
python main.py login --platform piaoniu
```

```bash
python main.py run
```

需要处理 Windows 路径、字符编码和浏览器路径问题。

---

# 十九、代码质量要求

代码需要满足：

* Python 3.11 或更高版本；
* 包含完整类型注解；
* 包含必要的中文注释；
* 模块职责清晰；
* 避免超长函数；
* 避免重复代码；
* 使用异步上下文管理器；
* 正确释放浏览器和网络资源；
* 统一异常类型；
* 统一日志格式；
* 统一配置模型；
* 金额使用 `Decimal`；
* 时间使用带时区的 `datetime`；
* 不在代码中硬编码敏感信息；
* 不提交真实登录状态；
* 不虚构未知的网站接口。

---

# 二十、依赖建议

可以根据实际实现选择以下依赖：

```text
playwright
pydantic
pydantic-settings
PyYAML
python-dotenv
httpx
aiosqlite
APScheduler
tenacity
typer
rich
pytest
pytest-asyncio
```

不要为了简单功能引入不必要的大型依赖。

---

# 二十一、README 要求

README 必须说明：

1. 项目功能；
2. 项目目录结构；
3. Python 版本；
4. 安装方法；
5. Playwright 安装方法；
6. 配置文件说明；
7. 登录方法；
8. 多任务配置方法；
9. 微信通知配置；
10. 启动命令；
11. Mock 演示方法；
12. 测试命令；
13. 日志目录；
14. 数据库目录；
15. 登录状态文件位置；
16. 如何退出程序；
17. 常见问题；
18. 当前限制；
19. 需要人工完成的操作；
20. 安全和合规说明。

需要明确说明：

* 首次运行需要用户手动登录；
* 登录失效时会自动打开登录页面；
* 验证码和短信验证需要人工完成；
* 程序默认不会自动支付；
* 网站改版后平台适配器可能需要更新；
* 不保证真实平台一定允许自动锁单；
* 用户需要自行确认并遵守平台规则。

---

# 二十二、交付要求

请直接在当前文件夹中生成完整项目。

最终必须提供：

1. 完整、可运行的 Python 源代码；
2. `requirements.txt`；
3. `.env.example`；
4. `config.example.yaml`；
5. `.gitignore`；
6. `README.md`；
7. SQLite 初始化逻辑；
8. 基本测试代码；
9. Mock 演示模式；
10. Windows 安装与运行命令；
11. 登录状态管理；
12. 未登录时自动打开登录页面；
13. 多平台独立登录；
14. 多任务并发监控；
15. 微信通知；
16. 锁单幂等控制；
17. 异常重试；
18. 日志轮转和敏感数据脱敏；
19. 无法确认的真实平台逻辑说明；
20. 后续扩展真实适配器的说明。

---

# 二十三、开发执行顺序

请按照以下顺序完成开发：

1. 检查当前文件夹已有文件；
2. 输出准备创建或修改的文件列表；
3. 创建基础项目结构；
4. 实现配置模型；
5. 实现数据模型；
6. 实现日志和异常模块；
7. 实现 SQLite 数据库；
8. 实现平台抽象接口；
9. 实现 Mock 平台；
10. 实现登录状态管理；
11. 实现未登录自动打开登录页；
12. 实现票务匹配逻辑；
13. 实现多任务调度；
14. 实现锁单幂等控制；
15. 实现微信通知；
16. 实现命令行工具；
17. 编写测试；
18. 运行测试；
19. 运行 Mock 演示；
20. 修复测试和运行错误；
21. 编写 README；
22. 汇总实际完成内容和未完成内容。

不要只描述实现方案，必须实际创建文件并完成代码。

如果真实网站页面结构、接口参数或购买流程无法确认，不要伪造可用实现。应明确标记待适配位置，保留可靠的架构和 Mock 流程，并说明需要用户提供哪些页面信息、请求信息或元素选择器后才能继续完成真实平台适配。
