# 真实平台 API 调研记录

更新时间：2026-07-20

本文件只记录由官方网页正常操作实际产生并已验证的请求。鉴权值、Cookie、设备标识、真实购票人和订单响应不会写入本文件。

## 票牛

目标演出：`https://www.piaoniu.com/activity/779707`

### 演出详情与场次

- 用途：读取演出详情和场次。
- 方法：`GET`
- URL：`https://www.piaoniu.com/api/v1/activities/{activity_id}.json`
- 路径参数：`activity_id` 来自官方演出网址 `/activity/{activity_id}`。
- 鉴权：当前公开详情页不要求登录。
- 主要响应字段：`id`、`name`、`events[].id`、`events[].specification`、`events[].start`。
- 业务错误：当前成功响应为 HTTP 200；尚未观察独立业务错误码。
- 验证：已用 activity `779707` 真实请求并确认返回洛天依杭州演出及场次。

### 票档

- 用途：读取某一场次的票面档位。
- 方法：`GET`
- URL：`https://www.piaoniu.com/api/v1/ticketCategories.json`
- 查询参数：`b2c=true`、`eventId={session_id}`。
- 鉴权：当前公开详情页不要求登录。
- 主要响应字段：`id`、`originPrice`、`specification`、`ticketsNum`、`lowPrice`、`hasTicket`。
- 验证：已用 session `14944160` 真实请求，观察到 480、680、980、1080、1280 票面档位。

### 精确票品

- 用途：按场次和票档读取平台的稳定票品。
- 方法：`GET`
- URL：`https://www.piaoniu.com/api/v4/tickets.json`
- 查询参数：`b2c=true`、`eventId={session_id}`、`ticketCategoryId={category_id}`。
- 鉴权：当前公开详情页不要求登录。
- 主要响应字段：`ticketGroups.{quantity}.ticketGroups[].id`、`salePrice`、`areaName`、`addition.numMin`、`addition.numMax`。
- 稳定标识：使用 `ticketGroups[].id` 作为 `listing_id` 和 `ticket_group_id`。
- 验证：已真实读取并再次按相同 ID 查询确认目标票品仍存在。

### 登录、购票人和订单

- 鉴权来源：待从用户已登录的 Edge Profile 监听确认。
- 登录状态 API：待验证。
- 购票人列表/创建 API：待验证。
- 订单预览 API：待验证。
- 创建订单 API：待验证；尚未产生待支付订单。
- 订单详情/列表 API：待验证。

## 摩天轮

目标演出：`https://m.motianlun.cn/pages/show-detail/show-detail?showId=6a2fe62c2608110001207f4d`

官方 H5 当前公共参数：`src=m_web`、`ver=6.76.1`、毫秒时间戳 `time`。版本号来自本次官方页面实际请求，页面升级后需要重新监听确认。

### 演出详情

- 用途：读取演出详情。
- 方法：`GET`
- URL：`https://m.motianlun.cn/showapi/pub/show/{show_id}`
- 查询参数：公共参数、`locationCityOID`、`utmNo`。
- 鉴权：当前公开详情页不要求登录。
- 主要响应字段：`result.data.showOID`、`showName`、`cityOID`、`venueName`、`showStatus`。
- 业务成功码：`statusCode=200`。
- 验证：已用 show `6a2fe62c2608110001207f4d` 真实请求并确认返回洛天依杭州演出。

### 场次

- 用途：读取演出的可售场次。
- 方法：`GET`
- URL：`https://m.motianlun.cn/showapi/pub/v3/show/{show_id}/sessionone`
- 查询参数：公共参数、`locationCityOID={city_id}`、`orderDecision=RANDOM`。
- 鉴权：当前公开详情页不要求登录。
- 主要响应字段：`data[].sessionId`、`sessionName`、`sessionShowTime`、`sessionStatus`、`available`、`limitation`。
- 验证：已真实确认目标演出当前返回 2026-07-25 19:12 场次。

### 精确票品

- 用途：按场次、数量和分页读取精确票品。
- 方法：`POST`
- URL：`https://m.motianlun.cn/showapi/pub/show_session/v2/find_tickets`
- 查询参数：公共参数。
- JSON 请求体：公共参数、`offset`、`length`、`ticketNumber`、`showSessionId`、`locationCityOID`、`ticketSortType=TICKET_PRICE_ASC`、`zoneIdList=[]`、`seatPlanId=""`。
- 鉴权：当前公开选票页不要求登录。
- 主要响应字段：`data.total`、`data.lastOffset`、`data.sessionTicketList[].ticketId`、`seatPlanId`、`ticketTitle`、`price`、`leftStocks`、`zoneName`、`sectorName`。
- 稳定标识：使用 `ticketId` 作为 `listing_id`/`sku_id`，使用 `seatPlanId` 作为 `ticket_group_id`。
- 验证：已按官方分页真实读取 38 个票品，并再次按相同 `ticketId` 查询确认目标票品仍存在。

### 登录、购票人和订单

- 鉴权来源：待从用户已登录的 Edge Profile 监听确认。
- 登录状态 API：待验证。
- 购票人列表/创建 API：待验证。
- 订单预览 API：待验证。
- 创建订单 API：待验证；尚未产生待支付订单。
- 订单详情/列表 API：待验证。

## 已知限制

- 登录后接口必须在复用用户正常登录态后继续监听，禁止猜测 URL、签名或请求体。
- 若平台要求页面无法正常获得的签名或风控参数，不绕过、不伪造，也不回退到 DOM 下单。
- 公开票价不是最终应付金额；创建订单前仍必须通过真实预览接口取得 `final_total`。
- 程序只允许创建待支付订单，绝不调用支付接口。
